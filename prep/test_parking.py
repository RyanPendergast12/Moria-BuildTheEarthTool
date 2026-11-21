"""test_parking.py - offline check of v0.7 parking generation in roadgen.chunk_roads (no network, flat stub terrain).
A 40 x 20 m surface lot with an east-west aisle through its middle: the aisle is paved as parking-aisle, stall lines are
generated beside it at STALL_W spacing within STALL_D, the rest of the lot is paved, and a parking=underground lot is reported, not built."""
import math
from shapely.geometry import LineString, Polygon
import roadgen as R

LAT0, LON0 = 36.15, -115.30
KX, KZ = 1 / (111320 * math.cos(math.radians(LAT0))), 1 / 111320     # 1 block = 1 m


class Geo:
    def inv(self, pts): return [(LON0 + x * KX, LAT0 - z * KZ) for x, z in pts]
    def fwd(self, pts): return [((lon - LON0) / KX, (LAT0 - lat) / KZ) for lon, lat in pts]
    def heights(self, ll): return [100.0] * len(ll)


def ll(pts): return Geo().inv(pts)


def run():
    lot = dict(id='w1', kind='parking', tags={'amenity': 'parking', 'parking': 'surface'}, geom=Polygon(ll([(-12, 2), (28, 2), (28, 22), (-12, 22)])))
    under = dict(id='w2', kind='parking', tags={'amenity': 'parking', 'parking': 'underground'}, geom=Polygon(ll([(0, 0), (4, 0), (4, 1), (0, 1)])))
    aisle = dict(id='w3', kind='highway', tags={'highway': 'service', 'service': 'parking_aisle'}, geom=LineString(ll([(-12, 12), (28, 12)])))
    ov = dict(deck={}, upper=set(), lower=set(), sidewalks=set(), why={}, skip_cols=set())
    r = R.chunk_roads(0, 0, Geo(), [lot, under, aisle], ov, dict(proj={}, EY={}), approach=False)
    by = {}
    for x, z, y, blk, role, fid, lab in r['cells']: by.setdefault(role, []).append((x, z))
    assert set(by) == {'parking', 'parking-aisle', 'parking-stripe'}, by.keys()
    assert all(abs(z + 0.5 - 12) <= 3.6 + 0.01 for x, z in by['parking-aisle']), 'aisle = 7.2 m service carriageway'
    stripes = by['parking-stripe']; xs = sorted({x for x, z in stripes if z == 5})
    assert all(3.6 < abs(z + 0.5 - 12) <= 3.6 + R.STALL_D for x, z in stripes), 'stall lines only within one stall depth of the aisle'
    assert len(xs) >= 4 and all(2 <= b - a <= 3 for a, b in zip(xs, xs[1:])), f'stall lines every ~{R.STALL_W} m: {xs}'
    assert all(z >= 2 for x, z in by['parking']), 'lot cells inside the mapped boundary only'
    assert any(u['id'] == 'w2' for u in r['unsupported']), 'non-surface parking is reported, not generated'
    print('parking ok', {k: len(v) for k, v in by.items()})
    # v0.7.1: an aisle with no lot outline -> INFERRED lot one stall depth each side; ground beyond that and past its ends stays untouched
    r = R.chunk_roads(0, 0, Geo(), [aisle], ov, dict(proj={}, EY={}), approach=False)
    by = {}
    for x, z, y, blk, role, fid, lab in r['cells']: by.setdefault(role, []).append((x, z))
    assert set(by) == {'parking-inferred', 'parking-aisle', 'parking-stripe'}, by.keys()
    assert all(3.6 < abs(z + 0.5 - 12) <= 3.6 + R.STALL_D for x, z in by['parking-inferred'] + by['parking-stripe']), 'one stall depth only'
    assert any('INFERRED' in c[6] for c in r['cells'] if c[4] == 'parking-inferred'), 'labelled INFERRED'
    assert not any(z + 0.5 < 12 - 3.6 - R.STALL_D - 0.01 for x, z in by['parking-inferred']), 'nothing beyond one stall depth'
    # an aisle inside a mapped lot is never extended past that lot's measured boundary
    r = R.chunk_roads(0, 0, Geo(), [lot, aisle], ov, dict(proj={}, EY={}), approach=False)
    assert not any(c[4] == 'parking-inferred' for c in r['cells']), 'mapped lot: no inference'
    print('inferred lot ok', {k: len(v) for k, v in by.items()})


if __name__ == '__main__':
    run()
