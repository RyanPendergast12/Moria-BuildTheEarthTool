"""Shared OSM parsing -> shapely geometries (lon/lat) with tags."""
import json
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import polygonize, unary_union

def load(path):
    j = json.load(open(path)); nodes = {e['id']: (e['lon'], e['lat']) for e in j['elements'] if e['type'] == 'node'}
    ways = {e['id']: e for e in j['elements'] if e['type'] == 'way'}
    feats = []
    for w in ways.values():
        t = w.get('tags') or {}; pts = [nodes[n] for n in w['nodes'] if n in nodes]
        if len(pts) < 2: continue
        closed = w['nodes'][0] == w['nodes'][-1] and len(pts) >= 4
        if ('building' in t or 'building:part' in t or 'area:highway' in t) and closed:
            feats.append(dict(id=f"w{w['id']}", tags=t, geom=Polygon(pts)))
        elif 'highway' in t:
            feats.append(dict(id=f"w{w['id']}", tags=t, geom=Polygon(pts) if (closed and t.get('area') == 'yes') else LineString(pts)))
    for r in (e for e in j['elements'] if e['type'] == 'relation'):
        t = r.get('tags') or {}
        if t.get('type') != 'multipolygon' or not ('building' in t or 'building:part' in t): continue
        outer = [LineString([nodes[n] for n in ways[m['ref']]['nodes'] if n in nodes]) for m in r['members'] if m['type'] == 'way' and m['ref'] in ways and m.get('role') == 'outer']
        inner = [LineString([nodes[n] for n in ways[m['ref']]['nodes'] if n in nodes]) for m in r['members'] if m['type'] == 'way' and m['ref'] in ways and m.get('role') == 'inner']
        o = unary_union(list(polygonize(outer))); i = unary_union(list(polygonize(inner))) if inner else None
        g = o.difference(i) if i is not None else o
        if not g.is_empty: feats.append(dict(id=f"r{r['id']}", tags=t, geom=g))
    return feats
