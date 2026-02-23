// ============================================================
// POSTMAN POST-RESPONSE SCRIPT — Interactive Map Visualizer
//
// HOW TO USE:
//   1. Open your POST /api/route/ request in Postman
//   2. Click the "Scripts" tab → "Post-response"
//   3. Paste this entire file in
//   4. Hit Send → click the "Visualization" tab (NOT Preview)
//
// WHY: Postman's Preview tab runs with sandbox="" (JS fully
// disabled). The Visualization tab is the only place in Postman
// where JavaScript actually executes. Buttons, scroll zoom, and
// drag all work there.
// ============================================================

const result = pm.response.json();

const template = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
body{background:#f3f4f6;display:flex;height:100vh;overflow:hidden}
#sidebar{width:280px;background:white;padding:14px;overflow-y:auto;flex-shrink:0;box-shadow:2px 0 10px rgba(0,0,0,.08)}
#map-area{flex:1;display:flex;flex-direction:column;overflow:hidden}
#hdr{background:#1e40af;color:white;padding:10px 14px;font-size:13px;font-weight:600;flex-shrink:0}
#wrap{flex:1;position:relative;overflow:hidden;background:#e8f4f8}
#ms{position:absolute;top:0;left:0;width:100%;height:100%;display:block;cursor:grab}
#ms.drag{cursor:grabbing}
#ctrl{position:absolute;bottom:14px;right:14px;display:flex;flex-direction:column;gap:5px;z-index:10}
.cb{width:36px;height:36px;background:white;border:2px solid #2563eb;border-radius:8px;
    font-size:22px;font-weight:700;line-height:1;cursor:pointer;color:#1e40af;
    display:flex;align-items:center;justify-content:center;
    box-shadow:0 2px 8px rgba(0,0,0,.2);user-select:none}
.cb:hover{background:#eff6ff}.cb:active{background:#bfdbfe}
#hint{position:absolute;top:8px;left:50%;transform:translateX(-50%);font-size:10px;color:#6b7280;
      background:rgba(255,255,255,.9);padding:3px 10px;border-radius:4px;pointer-events:none;white-space:nowrap}
.stat{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #f3f4f6;font-size:12px}
.lbl{color:#6b7280}.val{font-weight:600;color:#111827}
.cost{color:#16a34a!important;font-size:14px!important}
h1{font-size:14px;font-weight:700;color:#111827;margin-bottom:4px}
h2{font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;margin:12px 0 7px}
.sc{display:flex;align-items:flex-start;gap:9px;margin-bottom:9px;padding:7px;
    background:#fff7ed;border-radius:7px;border-left:3px solid #f97316}
.sn{background:#f97316;color:white;border-radius:50%;width:20px;height:20px;
    display:flex;align-items:center;justify-content:center;font-weight:700;font-size:10px;flex-shrink:0;margin-top:1px}
</style>
</head>
<body>
<div id="sidebar">
  <h1>&#9981; Fuel Route Optimizer</h1>
  <div style="font-size:10px;color:#9ca3af;margin-bottom:10px" id="rl"></div>
  <div class="stat"><span class="lbl">Total Distance</span><span class="val" id="sd"></span></div>
  <div class="stat"><span class="lbl">Est. Drive Time</span><span class="val" id="st"></span></div>
  <div class="stat"><span class="lbl">Fuel Stops</span><span class="val" id="ss"></span></div>
  <div class="stat"><span class="lbl">Total Gallons</span><span class="val" id="sg"></span></div>
  <div class="stat"><span class="lbl">Avg $/Gal</span><span class="val" id="sa"></span></div>
  <div class="stat"><span class="lbl">&#128176; Total Cost</span><span class="val cost" id="sc2"></span></div>
  <h2>Fuel Stops</h2>
  <div id="sl"></div>
  <div style="margin-top:10px;padding:7px;background:#f0fdf4;border-radius:6px;font-size:10px;color:#166534;line-height:1.5">
    &#10003; Lowest price within detour range<br/>&#128506; OSRM routing &middot; OPIS prices
  </div>
</div>
<div id="map-area">
  <div id="hdr">&#128506; <span id="hl"></span><span style="float:right;font-weight:400;opacity:.85;font-size:11px" id="hs"></span></div>
  <div id="wrap">
    <svg id="ms" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 480" preserveAspectRatio="xMidYMid meet">
      <rect width="900" height="480" fill="#f0f9ff"/>
      <g id="zg"></g>
      <g id="leg">
        <rect x="10" y="402" width="160" height="68" rx="6" fill="white" fill-opacity=".92" stroke="#e5e7eb"/>
        <circle cx="25" cy="420" r="6" fill="#16a34a"/>
        <text id="ls" x="37" y="424" font-size="10" fill="#374151"></text>
        <circle cx="25" cy="438" r="6" fill="#dc2626"/>
        <text id="le" x="37" y="442" font-size="10" fill="#374151"></text>
        <circle cx="25" cy="456" r="6" fill="#f97316"/>
        <text x="37" y="460" font-size="10" fill="#374151">Fuel Stop (cheapest)</text>
      </g>
    </svg>
    <div id="ctrl">
      <button class="cb" id="bi">+</button>
      <button class="cb" id="br" style="font-size:13px">&#8962;</button>
      <button class="cb" id="bo">&#8722;</button>
    </div>
    <div id="hint">Scroll to zoom &nbsp;&middot;&nbsp; Drag to pan &nbsp;&middot;&nbsp; Double-click to zoom in</div>
  </div>
</div>
<script>
pm.getData(function(err, data) {
  if (err || !data) { document.body.innerHTML = '<p style="padding:20px;color:red">Error: ' + err + '</p>'; return; }

  var r = data.result;
  var poly = r.route.polyline;
  var stops = r.fuel_stops;
  var sum = r.summary;
  var S = r.start, F = r.finish;

  // Sidebar
  document.getElementById('rl').textContent  = S.input + ' \u2192 ' + F.input;
  document.getElementById('sd').textContent  = r.route.total_miles + ' mi';
  document.getElementById('st').textContent  = r.route.duration_hours + ' hrs';
  document.getElementById('ss').textContent  = sum.number_of_fuel_stops;
  document.getElementById('sg').textContent  = sum.total_gallons + ' gal';
  document.getElementById('sa').textContent  = '$' + sum.average_price_per_gallon;
  document.getElementById('sc2').textContent = '$' + sum.total_fuel_cost_usd;
  document.getElementById('hl').textContent  = S.input + ' \u2192 ' + F.input;
  document.getElementById('hs').textContent  = r.route.total_miles + ' mi \u00b7 ' + sum.number_of_fuel_stops + ' stops \u00b7 $' + sum.total_fuel_cost_usd;
  document.getElementById('ls').textContent  = 'Start: ' + S.input;
  document.getElementById('le').textContent  = 'End: ' + F.input;

  var sl = document.getElementById('sl');
  stops.forEach(function(s, i) {
    sl.innerHTML +=
      '<div class="sc"><div class="sn">'+(i+1)+'</div><div>'+
      '<div style="font-size:12px;font-weight:600;color:#1f2937">'+s.name+'</div>'+
      '<div style="font-size:11px;color:#6b7280">'+s.city+', '+s.state+'</div>'+
      '<div style="font-size:11px;color:#f97316;font-weight:600">$'+s.price_per_gallon+'/gal \u00b7 $'+s.segment_fuel_cost+' this leg</div>'+
      '<div style="font-size:10px;color:#9ca3af">Mile '+s.stop_at_route_mile+' \u00b7 '+s.gallons_purchased+' gal</div>'+
      '</div></div>';
  });

  // Build SVG
  var ns = 'http://www.w3.org/2000/svg';
  var W=900, H=480, pad=40;
  var lats=poly.map(function(p){return p[0];}), lons=poly.map(function(p){return p[1];});
  var minLat=Math.min.apply(null,lats), maxLat=Math.max.apply(null,lats);
  var minLon=Math.min.apply(null,lons), maxLon=Math.max.apply(null,lons);
  var rLat=Math.max(maxLat-minLat,0.001), rLon=Math.max(maxLon-minLon,0.001);

  function coord(lat,lon){
    return [pad+(lon-minLon)/rLon*(W-pad*2), pad+(maxLat-lat)/rLat*(H-pad*2)];
  }

  var zg=document.getElementById('zg');
  function el(tag,attrs){var e=document.createElementNS(ns,tag);Object.keys(attrs).forEach(function(k){e.setAttribute(k,attrs[k]);});return e;}

  // Grid
  var g=el('g',{stroke:'#e0f2fe','stroke-width':'1'});
  for(var i=0;i<=5;i++){var x=pad+i*(W-2*pad)/5;g.appendChild(el('line',{x1:x,y1:pad,x2:x,y2:H-pad}));}
  for(var i=0;i<=4;i++){var y=pad+i*(H-2*pad)/4;g.appendChild(el('line',{x1:pad,y1:y,x2:W-pad,y2:y}));}
  zg.appendChild(g);

  // Route
  var step=Math.max(1,Math.floor(poly.length/300)), pts=[];
  for(var i=0;i<poly.length;i+=step){var c=coord(poly[i][0],poly[i][1]);pts.push(c[0].toFixed(1)+','+c[1].toFixed(1));}
  var ps=pts.join(' ');
  zg.appendChild(el('polyline',{points:ps,fill:'none',stroke:'#bfdbfe','stroke-width':'7','stroke-linecap':'round'}));
  zg.appendChild(el('polyline',{points:ps,fill:'none',stroke:'#2563eb','stroke-width':'3.5','stroke-linecap':'round'}));

  // Stop markers
  stops.forEach(function(s,i){
    var c=coord(s.lat,s.lon), cx=c[0].toFixed(1), cy=c[1].toFixed(1);
    zg.appendChild(el('circle',{cx:cx,cy:cy,r:'10',fill:'#f97316',stroke:'white','stroke-width':'2'}));
    var nt=el('text',{x:cx,y:(+cy+4).toFixed(1),'text-anchor':'middle','font-size':'8',fill:'white','font-weight':'bold'});nt.textContent=i+1;zg.appendChild(nt);
    var pt=el('text',{x:cx,y:(+cy-14).toFixed(1),'text-anchor':'middle','font-size':'9',fill:'#c2410c','font-weight':'bold'});pt.textContent='$'+s.price_per_gallon;zg.appendChild(pt);
  });

  // Start / Finish
  function marker(lat,lon,fill,label,letter){
    var c=coord(lat,lon),cx=c[0].toFixed(1),cy=c[1].toFixed(1);
    zg.appendChild(el('circle',{cx:cx,cy:cy,r:'11',fill:fill,stroke:'white','stroke-width':'2.5'}));
    var lt=el('text',{x:cx,y:(+cy+4).toFixed(1),'text-anchor':'middle','font-size':'10',fill:'white','font-weight':'bold'});lt.textContent=letter;zg.appendChild(lt);
    var lb=el('text',{x:cx,y:(+cy-16).toFixed(1),'text-anchor':'middle','font-size':'10',fill:fill,'font-weight':'bold'});lb.textContent=label;zg.appendChild(lb);
  }
  marker(S.lat,S.lon,'#16a34a','START','S');
  marker(F.lat,F.lon,'#dc2626','END','F');

  // Pan / Zoom
  var svg=document.getElementById('ms'), wrap=document.getElementById('wrap');
  var sc=1, tx=0, ty=0, MIN=0.4, MAX=20, ZF=1.3;

  function draw(){zg.setAttribute('transform','translate('+tx+','+ty+') scale('+sc+')');}
  function clamp(){var m=60,sw=W*sc,sh=H*sc;
    if(tx>W-m)tx=W-m;if(tx<-sw+m)tx=-sw+m;
    if(ty>H-m)ty=H-m;if(ty<-sh+m)ty=-sh+m;}
  function zoomAt(px,py,f){var ns=Math.min(MAX,Math.max(MIN,sc*f)),rv=ns/sc;tx=px-rv*(px-tx);ty=py-rv*(py-ty);sc=ns;clamp();draw();}
  function toV(cx,cy){var r=svg.getBoundingClientRect();return[(cx-r.left)/r.width*W,(cy-r.top)/r.height*H];}

  wrap.addEventListener('wheel',function(e){e.preventDefault();var p=toV(e.clientX,e.clientY);zoomAt(p[0],p[1],e.deltaY<0?ZF:1/ZF);},{passive:false});

  var drag=false,ox,oy,otx,oty;
  svg.addEventListener('mousedown',function(e){if(e.button!==0)return;drag=true;svg.classList.add('drag');var p=toV(e.clientX,e.clientY);ox=p[0];oy=p[1];otx=tx;oty=ty;e.preventDefault();});
  window.addEventListener('mousemove',function(e){if(!drag)return;var p=toV(e.clientX,e.clientY);tx=otx+(p[0]-ox);ty=oty+(p[1]-oy);clamp();draw();});
  window.addEventListener('mouseup',function(){drag=false;svg.classList.remove('drag');});
  svg.addEventListener('dblclick',function(e){var p=toV(e.clientX,e.clientY);zoomAt(p[0],p[1],ZF*ZF);});

  document.getElementById('bi').addEventListener('click',function(){zoomAt(W/2,H/2,ZF);});
  document.getElementById('bo').addEventListener('click',function(){zoomAt(W/2,H/2,1/ZF);});
  document.getElementById('br').addEventListener('click',function(){sc=1;tx=0;ty=0;draw();});

  draw();
});
</script>
</body>
</html>`;

pm.visualizer.set(template, { result: result });