import json
from pathlib import Path
import sys
import unittest
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_railways import canonical,clean_name,rail_kind

class RailwayClassificationTests(unittest.TestCase):
    def test_parallel_jinghu_lines_remain_distinct(self):
        self.assertEqual(canonical('京沪高速铁路（上海方向）','highspeed'),'京沪高铁')
        self.assertEqual(canonical('京沪铁路','railway'),'京沪线')
        self.assertEqual(rail_kind('京沪线',{},[{'maxspeed':'200'}]),'railway')
        self.assertEqual(rail_kind('京沪高铁',{},[{'highspeed':'yes'}]),'highspeed')

    def test_services_and_station_tracks_do_not_become_line_names(self):
        for name in ['G1234','京沪上行进站线','京沪京广联络线','车辆段专用线','地铁1号线','南环线','丹昆特大桥','丰台动车走行A线']:
            self.assertEqual(clean_name(name),'',name)

class PreparedRailwayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records=json.loads((ROOT/'data/railways/catalog.json').read_bytes())
        cls.by_name={r['name']:r for r in cls.records}

    def test_source_relations_and_endpoints(self):
        h=self.by_name['京沪高铁'];r=self.by_name['京沪线']
        self.assertEqual((h['kind'],h['from'],h['to']),('highspeed','北京南','上海虹桥'))
        self.assertEqual((r['kind'],r['from'],r['to']),('railway','北京','上海'))
        self.assertIn(356778,h['sourceRelations']);self.assertIn(163022,r['sourceRelations'])
        self.assertNotEqual(h['id'],r['id'])
        self.assertGreater(h['wayCount'],1000);self.assertGreater(r['wayCount'],2000)

    def test_geometry_stays_in_china_and_has_no_missing_routes(self):
        border=json.loads((ROOT/'data/china.geojson').read_bytes())
        mask=unary_union([shape(f['geometry']) for f in border['features']]).buffer(.000002)
        self.assertEqual(len(self.records),len({r['id'] for r in self.records}))
        for r in self.records:
            value=json.loads((ROOT/'data/railways/routes'/f"{r['id']}.geojson").read_bytes())
            for f in value['features']:
                self.assertEqual(f['properties']['kind'],r['kind'])
                self.assertTrue(mask.covers(shape(f['geometry'])),r['name'])

if __name__=='__main__':unittest.main()
