"""Checks real output coverage and China-only clipping, rather than mocked paths."""
from pathlib import Path
import gzip
import json
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((DATA / 'catalog.json').read_text(encoding='utf-8'))
        cls.report = json.loads((DATA / 'report.json').read_text(encoding='utf-8'))
        cls.by_id = {r['id']: r for r in cls.catalog}
        cls.mask = unary_union([shape(f['geometry']) for f in json.loads((DATA / 'china.geojson').read_text(encoding='utf-8'))['features']]).buffer(.000002)

    def test_catalog_and_geometry_counts(self):
        self.assertEqual(len(self.catalog), len(self.by_id))
        self.assertEqual(len(self.catalog), self.report['routes'])
        self.assertEqual(sum(r['hasGeometry'] for r in self.catalog), self.report['withGeometry'])
        self.assertEqual(len(list((DATA / 'routes').glob('*.geojson'))), self.report['withGeometry'])
        self.assertEqual(self.report['provinceCount'], 34)

    def test_actual_major_routes(self):
        for rid in ['national-G318', 'national-G108', 'expressway-G4', 'expressway-G30']:
            r = self.by_id[rid]
            self.assertTrue(r['hasGeometry'], rid)
            self.assertGreater(r['wayCount'], 100, rid)
            self.assertGreater(len(r['provinces']), 3, rid)
            geo = json.loads((DATA / 'routes' / f'{rid}.geojson').read_text(encoding='utf-8'))
            self.assertGreater(len(geo['features'][0]['geometry']['coordinates']), 0)

    def test_all_output_lines_inside_china(self):
        for path in (DATA / 'routes').glob('*.geojson'):
            geo = json.loads(path.read_text(encoding='utf-8'))
            for feature in geo['features']:
                line = shape(feature['geometry'])
                self.assertTrue(self.mask.covers(line), path.name)
                self.assertFalse(line.is_empty, path.name)
        overview = json.loads((DATA / 'overview.geojson').read_text(encoding='utf-8'))
        for f in overview['features']:
            self.assertTrue(self.mask.covers(shape(f['geometry'])), f['properties']['id'])

    def test_provincial_numbers_are_scoped(self):
        groups = {}
        for r in self.catalog:
            if r['kind'] != 'provincial':
                continue
            self.assertTrue(r['province'])
            self.assertTrue(r['provinceName'])
            groups.setdefault(r['code'], set()).add(r['province'])
        self.assertTrue(any(len(v) > 1 for v in groups.values()))


class OfflineServerTests(unittest.TestCase):
    def test_page_is_map_and_runtime_is_local(self):
        with urlopen('http://127.0.0.1:8766/') as response:
            self.assertIn('中国公路图谱', response.read().decode('utf-8'))
            self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
        with urlopen('http://127.0.0.1:8766/api/health') as response:
            health = json.loads(response.read())
            self.assertTrue(health['ready'])
            self.assertTrue(health['offline'])

    def test_gzip_local_route(self):
        request = Request('http://127.0.0.1:8766/data/routes/national-G318.geojson', headers={'Accept-Encoding': 'gzip'})
        with urlopen(request) as response:
            self.assertEqual(response.headers['Content-Encoding'], 'gzip')
            route = json.loads(gzip.decompress(response.read()))
            self.assertEqual(route['features'][0]['properties']['code'], 'G318')

    def test_source_and_traversal_not_exposed(self):
        for url in ['/data/raw/china.osm.pbf', '/data/roads.sqlite', '/data/%2e%2e/server.py']:
            with self.assertRaises(HTTPError) as context:
                urlopen('http://127.0.0.1:8766' + url)
            self.assertEqual(context.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
