"""
FuelRouteService - Optimized
Key upgrades over v1:
  1. KD-Tree spatial index → O(log n) station lookup vs O(n) vectorised scan
  2. Dynamic programming → globally optimal stop selection vs greedy
  3. LRU geocoding cache → same city never geocoded twice
  4. overview=full + encoded polyline → accuracy without payload bloat
  5. Station index pre-built at startup → zero cost on first request
"""

import math
import logging
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd
import requests
from django.conf import settings
from django.core.cache import cache
from scipy.spatial import KDTree
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
EARTH_RADIUS_MILES = 3_958.8
TANK_FILL_THRESHOLD = 0.80      # Stop at 80% of range for safety buffer
MAX_DETOUR_MILES = 35           # Max miles off-route we'll accept for a station
CACHE_TIMEOUT = 60 * 60 * 6    # Cache results for 6 hours
LOCATIONIQ_API_KEY = settings.LOCATIONIQ_API_KEY


# Persistent HTTP session — reuses TCP connections
_session = requests.Session()
_session.headers.update({'User-Agent': 'FuelRouteOptimizer/1.0 (test@gmail.com)'})

# Station data + KD-Tree (built once at startup)
_stations_df: Optional[pd.DataFrame] = None
_kdtree: Optional[KDTree] = None
_stations_xyz: Optional[np.ndarray] = None  # Cartesian coords for KD-Tree


# ── Startup: load stations + build spatial index ───────────────────────────────
def _load_stations() -> tuple[pd.DataFrame, KDTree, np.ndarray]:
    """
    Load CSV once, deduplicate, assign coordinates, build KD-Tree.
    Called once in AppConfig.ready() — zero cost on every request after that.
    """
    global _stations_df, _kdtree, _stations_xyz
    if _stations_df is not None:
        return _stations_df, _kdtree, _stations_xyz

    df = pd.read_csv(settings.FUEL_PRICES_CSV)
    df.columns = df.columns.str.strip()
    df['City'] = df['City'].str.strip()
    df['State'] = df['State'].str.strip()
    df['Retail Price'] = pd.to_numeric(df['Retail Price'], errors='coerce')
    df = df.dropna(subset=['Retail Price', 'State'])

    # Keep the single cheapest price per physical station
    df = df.sort_values('Retail Price')
    df = df.drop_duplicates(subset=['OPIS Truckstop ID'], keep='first')

    # Assign approximate coordinates from state centroids + deterministic jitter
    sc = _state_centroids()
    df['lat'] = df['State'].map(lambda s: sc.get(s, (39.5, -98.35))[0])
    df['lon'] = df['State'].map(lambda s: sc.get(s, (39.5, -98.35))[1])
    rng = np.random.default_rng(42)
    n = len(df)
    df = df.copy()
    df['lat'] += rng.uniform(-1.0, 1.0, n)
    df['lon'] += rng.uniform(-1.5, 1.5, n)
    df = df.reset_index(drop=True)

    # ── Build KD-Tree in 3D Cartesian space ───────────────────────────────────
    # Converting to (x,y,z) lets us use Euclidean distance in the tree,
    # which is proportional to great-circle distance for nearby points.
    lats_r = np.radians(df['lat'].values)
    lons_r = np.radians(df['lon'].values)
    xs = np.cos(lats_r) * np.cos(lons_r)
    ys = np.cos(lats_r) * np.sin(lons_r)
    zs = np.sin(lats_r)
    xyz = np.column_stack([xs, ys, zs])

    tree = KDTree(xyz)

    _stations_df = df
    _kdtree = tree
    _stations_xyz = xyz

    logger.info("Loaded %d stations · KD-Tree built (%d nodes)", len(df), len(df))
    return _stations_df, _kdtree, _stations_xyz


# ── Geometry helpers ───────────────────────────────────────────────────────────
def _haversine(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _cumulative_distances(polyline: list) -> list:
    cum = [0.0]
    for i in range(1, len(polyline)):
        cum.append(cum[-1] + _haversine(*polyline[i-1], *polyline[i]))
    return cum


def _point_at_distance(polyline, cum_dist, target):
    if target <= 0:
        return polyline[0]
    if target >= cum_dist[-1]:
        return polyline[-1]
    for i in range(1, len(cum_dist)):
        if cum_dist[i] >= target:
            frac = (target - cum_dist[i-1]) / (cum_dist[i] - cum_dist[i-1])
            lat = polyline[i-1][0] + frac * (polyline[i][0] - polyline[i-1][0])
            lon = polyline[i-1][1] + frac * (polyline[i][1] - polyline[i-1][1])
            return (lat, lon)
    return polyline[-1]


def _decode_polyline(encoded: str) -> list:
    """Decode OSRM encoded polyline format."""
    points = []
    index = lat = lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            value = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += value
            else:
                lat += value
        points.append((lat / 1e5, lng / 1e5))
    return points


# ── KD-Tree station lookup ─────────────────────────────────────────────────────
def _find_best_station(lat: float, lon: float, radius_miles: float = MAX_DETOUR_MILES) -> Optional[dict]:
    """
    O(log n) station lookup using KD-Tree.
    Converts radius to Euclidean chord length for tree query,
    then scores candidates by price + small distance penalty.
    """
    df, tree, _ = _load_stations()

    # Convert query point to Cartesian
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    qx = math.cos(lat_r) * math.cos(lon_r)
    qy = math.cos(lat_r) * math.sin(lon_r)
    qz = math.sin(lat_r)

    # Chord length for radius (slightly > arc for safety)
    chord = 2 * math.sin(math.radians(radius_miles / 69.0) / 2)

    indices = tree.query_ball_point([qx, qy, qz], chord)

    if not indices:
        # Expand search once if nothing found
        chord2 = 2 * math.sin(math.radians(radius_miles * 2 / 69.0) / 2)
        indices = tree.query_ball_point([qx, qy, qz], chord2)

    if not indices:
        return None

    nearby = df.iloc[indices].copy()

    # Compute exact haversine distance for scoring
    lats_r = np.radians(nearby['lat'].values)
    lons_r = np.radians(nearby['lon'].values)
    dlat = lats_r - lat_r
    dlon = lons_r - lon_r
    a = np.sin(dlat/2)**2 + np.cos(lat_r)*np.cos(lats_r)*np.sin(dlon/2)**2
    dists = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))

    nearby = nearby.copy()
    nearby['_dist'] = dists
    nearby['_score'] = nearby['Retail Price'] + nearby['_dist'] * 0.002
    best = nearby.loc[nearby['_score'].idxmin()]

    return {
        'station_id': int(best['OPIS Truckstop ID']),
        'name': best['Truckstop Name'],
        'address': best['Address'],
        'city': best['City'],
        'state': best['State'],
        'price_per_gallon': round(float(best['Retail Price']), 4),
        'lat': round(float(best['lat']), 6),
        'lon': round(float(best['lon']), 6),
        'detour_miles': round(float(best['_dist']), 2),
    }


# ── Dynamic programming: globally optimal stops ────────────────────────────────
def _optimal_stops_dp(
    polyline: list,
    cum_dist: list,
    total_miles: float,
    max_range: float,
    mpg: float,
) -> list:
    """
    Find the globally cheapest set of fuel stops using dynamic programming.

    Instead of greedily stopping every 400 miles at the cheapest nearby station,
    DP evaluates ALL feasible stop combinations and finds the one with
    minimum total fuel cost for the whole trip.

    Nodes = candidate waypoints every 50 miles along the route.
    Edges = valid jumps (≤ max_range miles apart).
    Weight = gallons × price_at_destination_station.

    This is a shortest-path problem on a DAG — O(N²) where N = waypoints.
    For a 3000 mile trip N = 60, so it's extremely fast.
    """
    interval = 50  # candidate waypoint every 50 miles
    waypoint_miles = [0.0]
    d = interval
    while d < total_miles:
        waypoint_miles.append(d)
        d += interval
    waypoint_miles.append(total_miles)
    N = len(waypoint_miles)

    # Pre-fetch best station at each waypoint
    stations_at = {}
    for i, miles in enumerate(waypoint_miles):
        if miles == 0 or miles == total_miles:
            stations_at[i] = None
            continue
        wp = _point_at_distance(polyline, cum_dist, miles)
        stations_at[i] = _find_best_station(wp[0], wp[1])

    # DP: dp[i] = (min_total_cost, prev_stop_index, station_used)
    INF = float('inf')
    dp_cost = [INF] * N
    dp_prev = [-1] * N
    dp_station = [None] * N
    dp_cost[0] = 0.0

    for i in range(N):
        if dp_cost[i] == INF:
            continue
        for j in range(i + 1, N):
            segment = waypoint_miles[j] - waypoint_miles[i]
            if segment > max_range:
                break  # Can't reach j from i

            gallons = segment / mpg

            if waypoint_miles[j] == total_miles:
                # Final leg — use average of available stations as estimate
                prices = [
                    stations_at[k]['price_per_gallon']
                    for k in range(1, N-1)
                    if stations_at[k]
                ]
                price = sum(prices) / len(prices) if prices else 3.20
                cost = dp_cost[i] + gallons * price
                if cost < dp_cost[j]:
                    dp_cost[j] = cost
                    dp_prev[j] = i
                    dp_station[j] = None
            else:
                station = stations_at[j]
                if not station:
                    continue
                cost = dp_cost[i] + gallons * station['price_per_gallon']
                if cost < dp_cost[j]:
                    dp_cost[j] = cost
                    dp_prev[j] = i
                    dp_station[j] = station

    # Reconstruct path
    path = []
    cur = N - 1
    while cur != 0:
        prev = dp_prev[cur]
        if prev == -1:
            break
        if dp_station[cur]:
            path.append((waypoint_miles[cur], waypoint_miles[prev], dp_station[cur]))
        cur = prev
    path.reverse()

    # Build fuel_stops list with cost details
    fuel_stops = []
    total_cost = 0.0
    for (stop_miles, prev_miles, station) in path:
        segment_miles = stop_miles - prev_miles
        gallons = segment_miles / mpg
        seg_cost = gallons * station['price_per_gallon']
        total_cost += seg_cost
        fuel_stops.append({
            **station,
            'stop_at_route_mile': round(stop_miles, 1),
            'miles_since_last_stop': round(segment_miles, 1),
            'gallons_purchased': round(gallons, 2),
            'segment_fuel_cost': round(seg_cost, 2),
        })

    # Add final leg cost
    if fuel_stops:
        last_stop_miles = fuel_stops[-1]['stop_at_route_mile']
    else:
        last_stop_miles = 0.0
    final_miles = total_miles - last_stop_miles
    final_gallons = final_miles / mpg
    prices = [s['price_per_gallon'] for s in fuel_stops]
    avg_price = sum(prices) / len(prices) if prices else 3.20
    total_cost += final_gallons * avg_price

    return fuel_stops, total_cost


# ── Geocoding with LRU cache ───────────────────────────────────────────────────
@lru_cache(maxsize=512)
def _geocode_cached(location: str) -> tuple:
    """
    LRU-cached geocoding — same city string never hits the network twice
    for the lifetime of the server process.
    Returns (lat, lon, display_name) tuple (hashable for lru_cache).
    """
    # Try LocationIQ first
    try:
        resp = _session.get(
            'https://us1.locationiq.com/v1/search',
            params={
                'key': LOCATIONIQ_API_KEY,
                'q': location,
                'format': 'json',
                'limit': 1,
                'countrycodes': 'us',
            },
            timeout=3,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            r = results[0]
            return (float(r['lat']), float(r['lon']), r['display_name'])
    except Exception:
        pass

    # Fallback: Nominatim
    resp = _session.get(
        'https://nominatim.openstreetmap.org/search',
        params={'q': location, 'format': 'json', 'limit': 1, 'countrycodes': 'us'},
        headers={'User-Agent': 'FuelRouteOptimizer/1.0 (test@gmail.com)'},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode location: '{location}'")
    r = results[0]
    return (float(r['lat']), float(r['lon']), r['display_name'])


def geocode(location: str) -> dict:
    lat, lon, display_name = _geocode_cached(location.strip().lower())
    return {'lat': lat, 'lon': lon, 'display_name': display_name}


# ── OSRM routing ───────────────────────────────────────────────────────────────
def get_route(start_lat, start_lon, end_lat, end_lon) -> dict:
    """Single OSRM call. overview=full for accuracy, encoded polyline for speed."""
    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
    )
    resp = _session.get(url, params={
        'overview': 'full',
        'geometries': 'polyline',
        'steps': 'false',
        'generate_hints': 'false',
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get('code') != 'Ok':
        raise ValueError(f"OSRM error: {data.get('message')}")
    route = data['routes'][0]
    return {
        'distance_miles': route['distance'] * 0.000621371,
        'duration_seconds': route['duration'],
        'polyline': _decode_polyline(route['geometry']),
    }


# ── Main entry point ───────────────────────────────────────────────────────────
def calculate_route(start: str, finish: str, max_range: float = None, mpg: float = None) -> dict:
    max_range = max_range or settings.VEHICLE_MAX_RANGE_MILES
    mpg = mpg or settings.VEHICLE_MPG

    # Response cache — identical request = instant return
    cache_key = hashlib.md5(
        f"{start.lower().strip()}|{finish.lower().strip()}|{max_range}|{mpg}".encode()
    ).hexdigest()
    t0 = time.perf_counter()
    cached = cache.get(cache_key)
    if cached:
        cached['meta']['cache_hit'] = True
        cached['meta']['processing_time_ms'] = round((time.perf_counter() - t0) * 1000, 1)

        return cached

    

    # Geocode in parallel — both requests fire simultaneously
    with ThreadPoolExecutor(max_workers=2) as pool:
        fs = pool.submit(geocode, start)
        ff = pool.submit(geocode, finish)
        start_geo = fs.result()
        finish_geo = ff.result()

    # Single routing API call
    route = get_route(start_geo['lat'], start_geo['lon'], finish_geo['lat'], finish_geo['lon'])
    total_miles = route['distance_miles']
    polyline = route['polyline']
    cum_dist = _cumulative_distances(polyline)

    # Ensure KD-Tree is ready (no-op if already built)
    _load_stations()

    # DP-optimal stop selection
    fuel_stops, total_fuel_cost = _optimal_stops_dp(polyline, cum_dist, total_miles, max_range, mpg)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    step = max(1, len(polyline) // 500)
    sampled = [[p[0], p[1]] for p in polyline[::step]]

    avg_price = round(
        total_fuel_cost / (total_miles / mpg) if total_miles > 0 else 0, 4
    )

    result = {
        'meta': {
            'processing_time_ms': elapsed_ms,
            'cache_hit': False,
            'vehicle_max_range_miles': max_range,
            'vehicle_mpg': mpg,
            'optimization': 'dynamic_programming',
            'api_calls': {'geocoding': 2, 'routing': 1, 'fuel_station_lookup': 0},
        },
        'start': {
            'input': start,
            'display_name': start_geo['display_name'],
            'lat': start_geo['lat'],
            'lon': start_geo['lon'],
        },
        'finish': {
            'input': finish,
            'display_name': finish_geo['display_name'],
            'lat': finish_geo['lat'],
            'lon': finish_geo['lon'],
        },
        'route': {
            'total_miles': round(total_miles, 1),
            'duration_hours': round(route['duration_seconds'] / 3600, 2),
            'polyline': sampled,
        },
        'fuel_stops': fuel_stops,
        'summary': {
            'number_of_fuel_stops': len(fuel_stops),
            'total_gallons': round(total_miles / mpg, 2),
            'total_fuel_cost_usd': round(total_fuel_cost, 2),
            'average_price_per_gallon': avg_price,
        },
    }

    cache.set(cache_key, result, CACHE_TIMEOUT)
    return result


# ── State centroids ────────────────────────────────────────────────────────────
def _state_centroids() -> dict:
    return {
        'AL': (32.806671, -86.791130), 'AK': (61.370716, -152.404419),
        'AZ': (33.729759, -111.431221), 'AR': (34.969704, -92.373123),
        'CA': (36.116203, -119.681564), 'CO': (39.059811, -105.311104),
        'CT': (41.597782, -72.755371), 'DE': (39.318523, -75.507141),
        'FL': (27.766279, -81.686783), 'GA': (33.040619, -83.643074),
        'HI': (21.094318, -157.498337), 'ID': (44.240459, -114.478828),
        'IL': (40.349457, -88.986137), 'IN': (39.849426, -86.258278),
        'IA': (42.011539, -93.210526), 'KS': (38.526600, -96.726486),
        'KY': (37.668140, -84.670067), 'LA': (31.169960, -91.867805),
        'ME': (44.693947, -69.381927), 'MD': (39.063946, -76.802101),
        'MA': (42.230171, -71.530106), 'MI': (43.326618, -84.536095),
        'MN': (45.694454, -93.900192), 'MS': (32.741646, -89.678696),
        'MO': (38.456085, -92.288368), 'MT': (46.921925, -110.454353),
        'NE': (41.125370, -98.268082), 'NV': (38.313515, -117.055374),
        'NH': (43.452492, -71.563896), 'NJ': (40.298904, -74.521011),
        'NM': (34.840515, -106.248482), 'NY': (42.165726, -74.948051),
        'NC': (35.630066, -79.806419), 'ND': (47.528912, -99.784012),
        'OH': (40.388783, -82.764915), 'OK': (35.565342, -96.928917),
        'OR': (44.572021, -122.070938), 'PA': (40.590752, -77.209755),
        'RI': (41.680893, -71.511780), 'SC': (33.856892, -80.945007),
        'SD': (44.299782, -99.438828), 'TN': (35.747845, -86.692345),
        'TX': (31.054487, -97.563461), 'UT': (40.150032, -111.862434),
        'VT': (44.045876, -72.710686), 'VA': (37.769337, -78.169968),
        'WA': (47.400902, -121.490494), 'WV': (38.491226, -80.954453),
        'WI': (44.268543, -89.616508), 'WY': (42.755966, -107.302490),
        'DC': (38.897438, -77.026817),
    }