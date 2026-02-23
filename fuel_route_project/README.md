# Fuel Route Optimizer

A Django REST API that finds the cheapest fuel stops along any US road route, built for the Django Developer assessment.

---

## What it does

POST a start and end location anywhere in the USA and get back:

- The full road route as a polyline
- Optimal fuel stops along the way (cost-optimised, not just nearest)
- Price per gallon, gallons purchased, and cost per leg at each stop
- Total fuel cost for the trip based on 10 MPG and actual OPIS price data

---

## Stack

- **Django + Django REST Framework** — API layer
- **OSRM** (router.project-osrm.org) — free routing, no API key needed
- **LocationIQ** — geocoding, with Nominatim as automatic fallback
- **SciPy KD-Tree** — spatial index for fast station lookup
- **OPIS fuel prices CSV** — 8,000+ real station prices

---

## Setup

```bash
git clone https://github.com/YasserRa-fat/Fuel-Route.git
cd fuel-route-optimizer

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Place the `fuel_prices.csv` file in the project root (same level as `manage.py`).

```bash
python manage.py migrate
python manage.py runserver
```

The station KD-Tree builds automatically on first startup — takes a second, then zero cost on every request after.

---

## API

### POST /api/route/

**Request body:**
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "max_range_miles": 500,
  "mpg": 10
}
```

`max_range_miles` and `mpg` are optional — they default to 500 and 10 as per the spec.

**Response:**
```json
{
  "meta": {
    "processing_time_ms": 1843,
    "cache_hit": false,
    "optimization": "dynamic_programming"
  },
  "start": { "input": "New York, NY", "lat": 40.71, "lon": -74.00 },
  "finish": { "input": "Los Angeles, CA", "lat": 34.05, "lon": -118.24 },
  "route": {
    "total_miles": 2791.4,
    "duration_hours": 40.1,
    "polyline": [[40.71, -74.00], "..."]
  },
  "fuel_stops": [
    {
      "name": "LOVES TRAVEL STOP 394",
      "city": "Dayton",
      "state": "OH",
      "price_per_gallon": 3.459,
      "stop_at_route_mile": 550.0,
      "gallons_purchased": 55.0,
      "segment_fuel_cost": 190.25
    }
  ],
  "summary": {
    "number_of_fuel_stops": 5,
    "total_gallons": 279.14,
    "total_fuel_cost_usd": 854.60,
    "average_price_per_gallon": 3.0609
  }
}
```

Repeat the same request and it returns in ~8ms from cache.

### GET /api/health/

```json
{ "status": "ok" }
```

---

## How the optimisation works

**Single API call** — OSRM is called once with `overview=full` and `geometries=polyline`. That returns the complete encoded polyline for the entire route. Everything else is computed locally.

**KD-Tree spatial index** — At startup, all 8,000 stations are loaded and indexed in a KD-Tree using 3D Cartesian coordinates (converted from lat/lon). Each station lookup along the route is O(log n) rather than a linear scan.

**Dynamic programming** — This is the key part. A greedy approach (stop when nearly empty, pick cheapest nearby) isn't globally optimal — it can miss cheaper combinations across multiple legs. Instead the route is modelled as a directed acyclic graph with waypoints every 50 miles. DP finds the globally cheapest set of stops for the whole trip, not just locally optimal choices.

**Two-layer caching** — Geocoding results are cached in-process with Python's LRU cache. Full route results are cached for 6 hours with Django's in-memory cache. Same request twice = instant response.

**Parallel geocoding** — Start and finish are geocoded simultaneously using `ThreadPoolExecutor`, cutting geocoding time roughly in half.

---

## Honest limitations

The OPIS CSV doesn't include GPS coordinates for stations — only city and state. I approximate station positions using state centroids with small random jitter (seeded for reproducibility). This means stop locations are representative but not exact street addresses.

In production I'd:
- Geocode each station address to get real coordinates
- Self-host the OSRM instance instead of using the public demo server
- Swap the in-memory KD-Tree for a PostGIS spatial index in the database
- Move secrets (LocationIQ API key, Django secret key) to environment variables

---

## Project structure

```
fuel_route_project/
├── manage.py
├── fuel_prices.csv
├── fuel_route_project/
│   ├── settings.py
│   └── urls.py
└── fuel_route/
    ├── apps.py        # builds KD-Tree on startup
    ├── views.py       # thin API layer, input validation
    ├── services.py    # all logic lives here
    └── urls.py
```