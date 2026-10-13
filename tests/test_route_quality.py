from pathlib import Path
import json
import sqlite3
import sys
import unittest
from urllib.request import urlopen
from shapely.geometry import LineString, shape, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'scripts'))
from audit_routes import membership_reasons, remote_components


class MembershipRulesTests(unittest.TestCase):
    def test_road_relation_does_not_override_conflicting_ref(self):
        self.assertIn('relation_ref_conflict',membership_reasons('G312',{'highway':'primary','ref':'G213'},[{'codes':['G312'],'role':''}]))
        self.assertIn('relation_ref_conflict',membership_reasons('G312',{'highway':'trunk','ref':'G312'},[{'codes':['G331'],'role':''}]))
        self.assertFalse(membership_reasons('G312',{'highway':'primary','ref':'G312;G213'},[]))
        self.assertFalse(membership_reasons('G312',{'highway':'primary'},[{'codes':['G312'],'role':''}]))

    def test_non_road_features_are_excluded(self):
        for hw in ('footway','track','cycleway','service','pedestrian','path'):
            self.assertIn('non_main_road',membership_reasons('G312',{'highway':hw,'ref':'G312'},[]))
        self.assertIn('non_main_road',membership_reasons('G312',{'highway':'primary','area':'yes','ref':'G312'},[]))

    def test_old_number_and_old_alignment_need_review(self):
        self.assertIn('historical_alignment',membership_reasons('G312',{'highway':'primary','old_ref':'G312'},[{'codes':['G312'],'role':''}]))
        self.assertIn('historical_alignment',membership_reasons('G312',{'highway':'primary','ref':'G312','name':'旧312国道'},[]))
        self.assertIn('historical_alignment',membership_reasons('G312',{'highway':'primary','ref':'G312','name':'G312老线'},[]))
        self.assertIn('historical_alignment',membership_reasons('G312',{'highway':'trunk'},[{'codes':['G312'],'role':'','name':'G312老线'}]))
        self.assertFalse(membership_reasons('G312',{'highway':'trunk'},[{'codes':['G312'],'role':'','name':'G312老线'},{'codes':['G312'],'role':'','name':'312国道 沪霍线'}]))
        self.assertFalse(membership_reasons('G312',{'highway':'primary','ref':'G312','old_ref':'S100'},[]))

    def test_remote_small_island_is_flagged_but_gap_and_parallel_road_are_kept(self):
        main=LineString([(100,39),(101,39)])
        continuation=LineString([(101.1,39),(102,39)])
        parallel=LineString([(100,39.001),(101,39.001)])
        island=LineString([(100.5,42),(100.51,42)])
        remote,count=remote_components([main,continuation,parallel,island])
        self.assertEqual(set(remote),{3})
        self.assertGreater(remote[3]['distanceKm'],200)
        self.assertEqual(count,3)

    def test_cross_province_network_and_large_distant_names_are_not_merged(self):
        self.assertIn('provincial_network_conflict',membership_reasons('S60',{'highway':'motorway','ref':'S60'},[{'codes':['S60'],'role':'','name':'连贺高速','network':'CN:GD:expressway'}],'provincial','450000'))
        main=LineString([(106,22),(109,22)])
        other=LineString([(111,24),(111.6,24)])
        findings,_=remote_components([main,other],['合那高速','连贺高速'])
        self.assertEqual(findings[1]['reason'],'disconnected_name_conflict')
        self.assertGreater(findings[1]['distanceKm'],100)


class WholeDatasetQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=json.loads((ROOT / 'data/quality-report.json').read_text(encoding='utf8'))
        cls.catalog=json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf8'))

    def test_every_source_assignment_has_one_audit_outcome(self):
        r=self.report
        self.assertEqual(r['routesChecked'],len(self.catalog))
        with sqlite3.connect(ROOT / 'data/road-evidence.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],r['totals']['sourceWays'])
        for row in r['routes']:
            self.assertEqual(row['sourceWays'],row['acceptedWays']+row['reviewWays']+row['excludedWays'],row['id'])

    def test_g312_ejina_island_is_quarantined_and_not_in_default_geometry(self):
        region=box(100.55,41.8,100.75,42.1)
        route=json.loads((ROOT / 'data/routes/national-G312.geojson').read_text(encoding='utf8'))
        self.assertFalse(any(shape(f['geometry']).intersects(region) for f in route['features']))
        review=json.loads((ROOT / 'data/quality/national-G312.geojson').read_text(encoding='utf8'))
        found=[f for f in review['features'] if shape(f['geometry']).intersects(region)]
        self.assertTrue(found)
        self.assertTrue(all('remote_component' in f['properties']['reasons'] or 'relation_ref_conflict' in f['properties']['reasons'] for f in found))
        self.assertTrue(all(f['properties']['way'] and f['properties']['tags'] for f in found))

    def test_g312_island_is_absent_from_overview_and_precise_endpoint(self):
        region=box(100.55,41.8,100.75,42.1)
        overview=json.loads((ROOT / 'data/overview.geojson').read_text(encoding='utf8'))
        for f in overview['features']:
            if f['properties']['id']=='national-G312':
                self.assertFalse(shape(f['geometry']).intersects(region))
        precise=json.load(urlopen('http://127.0.0.1:8766/api/network?bbox=100.55,41.8,100.75,42.1'))
        self.assertFalse(any(f['properties']['id']=='national-G312' for f in precise['features']))

    def test_guangxi_s60_does_not_include_lianhe_in_hezhou(self):
        rid='provincial-450000-S60'
        record=next(r for r in self.catalog if r['id']==rid)
        self.assertLess(record['bounds'][3],23.5)
        self.assertNotIn('连贺高速',record['aliases'])
        review=json.loads((ROOT / 'data/quality' / (rid+'.geojson')).read_text(encoding='utf8'))
        self.assertTrue(any('disconnected_name_conflict' in f['properties']['reasons'] for f in review['features']))
        for f in json.load(urlopen('http://127.0.0.1:8766/api/network?bbox=111.4,23.9,112.1,24.6'))['features']:
            self.assertNotEqual(f['properties']['id'],rid)

    def test_all_default_routes_have_only_eligible_source_ways(self):
        with sqlite3.connect(ROOT / 'data/road-evidence.sqlite') as db:
            for record in self.catalog:
                rid=record['id'];q=record.get('quality')
                if not q:
                    continue
                flagged=set()
                path=ROOT / 'data/quality' / (rid+'.geojson')
                if q['reviewWays'] or q['excludedWays']:
                    flagged={f['properties']['way'] for f in json.loads(path.read_text(encoding='utf8'))['features']}
                eligible=0
                for way,tags,relations in db.execute('SELECT way,tags,relations FROM evidence WHERE route=?',(rid,)):
                    if way not in flagged:
                        self.assertFalse(membership_reasons(record['code'],json.loads(tags),json.loads(relations),record['kind'],record.get('province')),f'{rid}/{way}')
                        eligible+=1
                self.assertEqual(eligible,q['acceptedWays'],rid)


if __name__ == '__main__':
    unittest.main()
