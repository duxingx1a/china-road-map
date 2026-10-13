import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'
sys.path.insert(0,str(ROOT/'scripts'))
from restore_coastal_crossings import eligible_crossing
from transport_coverage import coverage


def item(points, bridge=True):
    return {'coordinates':points,'nodes':list(range(len(points))),
            'outside':True,'tags':{'bridge':'yes'} if bridge else {}}


class CrossingRulesTests(unittest.TestCase):
    def test_bridge_between_two_chinese_shores_is_allowed(self):
        land = unary_union([box(0,0,.01,.02),box(.02,0,.03,.02)])
        self.assertTrue(eligible_crossing([item([[.005,.01],[.015,.01],[.025,.01]])],land,land)[0])

    def test_bridge_ending_outside_china_and_unverified_road_are_rejected(self):
        land = unary_union([box(0,0,.01,.02),box(.02,0,.03,.02)])
        self.assertFalse(eligible_crossing([item([[.005,.01],[.015,.01],[.04,.01]])],land,land)[0])
        self.assertFalse(eligible_crossing([item([[.005,.01],[.015,.01],[.025,.01]],False)],land,land)[0])


class CoastalDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((DATA/'coastal-crossings-report.json').read_bytes())
        cls.land = unary_union([shape(f['geometry']) for f in json.loads((DATA/'china.geojson').read_bytes())['features']])
        cls.coverage = coverage(DATA)

    def test_restored_structures_retain_the_complete_source_geometry(self):
        evidence = json.loads((DATA/'coastal-crossing-evidence.geojson').read_bytes())
        routes = {rid:unary_union([shape(f['geometry']) for f in json.loads((DATA/'routes'/f'{rid}.geojson').read_bytes())['features']])
                  for rid in self.report['routes']}
        for feature in evidence['features']:
            rid = feature['properties']['id']
            self.assertTrue(routes[rid].buffer(.000002).covers(shape(feature['geometry'])),feature['properties']['way'])
            self.assertTrue(self.coverage.buffer(.000002).covers(shape(feature['geometry'])),rid)

    def test_g2518_contains_the_missing_sea_bridge_and_tunnel_in_every_tier(self):
        with sqlite3.connect(DATA/'crossing-source.sqlite') as db:
            for ident in (1298785019,689375219):
                geometry = LineString(json.loads(db.execute('SELECT coordinates FROM ways WHERE id=?',(ident,)).fetchone()[0]))
                outside = geometry.difference(self.land)
                if outside.geom_type == 'MultiLineString':
                    outside = max(outside.geoms,key=lambda line:line.length)
                point = outside.interpolate(.5,normalized=True)
                self.assertFalse(self.land.covers(point))
                for tier,tolerance in (('overview',.0025),('standard',.00015),('detail',.000015)):
                    path = DATA/'display/routes'/tier/'expressway-G2518.geojson'
                    line = unary_union([shape(f['geometry']) for f in json.loads(path.read_bytes())['features']])
                    self.assertLess(line.distance(point),tolerance,(ident,tier))

    def test_unrelated_routes_are_byte_identical(self):
        baseline = ROOT/'.archive-work/coastal-backup/unaffected-route-hashes.json'
        if not baseline.exists():
            self.skipTest('Local pre-update digest baseline is unavailable.')
        for name,digest in json.loads(baseline.read_bytes()).items():
            self.assertEqual(hashlib.sha256((DATA/name).read_bytes()).hexdigest(),digest,name)
