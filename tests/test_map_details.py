from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
import gzip
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'scripts'))


class PreciseNetworkTests(unittest.TestCase):
    def test_independent_road_kind_queries(self):
        for kinds in ('national','expressway,provincial'):
            result=json.load(urlopen('http://127.0.0.1:8766/api/network?bbox=103.85,30.48,104.25,30.83&kinds='+kinds))
            self.assertTrue(result['features'])
            self.assertTrue(all(f['properties']['kind'] in kinds.split(',') for f in result['features']))
        with self.assertRaises(HTTPError) as raised:
            urlopen('http://127.0.0.1:8766/api/network?bbox=103.85,30.48,104.25,30.83&kinds=invalid')
        self.assertEqual(raised.exception.code,400)

    def test_chengdu_keeps_original_vertices_and_adjacency(self):
        result=json.load(urlopen('http://127.0.0.1:8766/api/network?bbox=103.85,30.48,104.25,30.83'))
        self.assertTrue(result['ready'])
        self.assertFalse(result['truncated'])
        count=sum(len(line) for f in result['features'] for line in f['geometry']['coordinates'])
        self.assertGreater(count,10000)
        for feature in result['features']:
            source=json.loads((ROOT / 'data' / 'routes' / (feature['properties']['id']+'.geojson')).read_text(encoding='utf-8'))
            adjacent=set()
            for line in source['features'][0]['geometry']['coordinates']:
                adjacent.update((tuple(a),tuple(b)) for a,b in zip(line,line[1:]))
            for line in feature['geometry']['coordinates']:
                for a,b in zip(line,line[1:]):
                    self.assertIn((tuple(a),tuple(b)),adjacent)

    def test_invalid_bounds_rejected(self):
        for bbox in ('NaN,0,1,2','104,31,103,30','0,0,181,1','1,2,3'):
            with self.assertRaises(HTTPError) as raised:
                urlopen('http://127.0.0.1:8766/api/network?bbox='+bbox)
            self.assertEqual(raised.exception.code,400)


class BasemapClippingTests(unittest.TestCase):
    def test_cross_border_line_is_clipped_and_pois_are_discarded(self):
        from build_basemap import init_worker,process_batch,WORLD
        from shapely.geometry import box
        import mapbox_vector_tile
        init_worker(box(-WORLD/2,-WORLD/2,0,WORLD/2).wkb)
        data=mapbox_vector_tile.encode([
            {'name':'streets','features':[{'geometry':{'type':'LineString','coordinates':[[0,2000],[4096,2000]]},'properties':{'kind':'motorway'}}]},
            {'name':'pois','features':[{'geometry':{'type':'Point','coordinates':[1000,1000]},'properties':{'name':'irrelevant'}}]},
        ])
        rows,stats=process_batch([(0,0,0,data)])
        self.assertEqual(stats['clippedBorderTiles'],1)
        tile=mapbox_vector_tile.decode(gzip.decompress(rows[0][3]))
        self.assertNotIn('pois',tile)
        self.assertEqual(tile['streets']['features'][0]['geometry']['coordinates'],[[0,2000],[2048,2000]])

    def test_entire_foreign_tile_is_removed(self):
        from build_basemap import init_worker,process_batch,WORLD
        from shapely.geometry import box
        init_worker(box(-WORLD/2,-WORLD/2,-WORLD/4,WORLD/2).wkb)
        rows,stats=process_batch([(2,3,1,b'')])
        self.assertFalse(rows)
        self.assertEqual(stats['outsideTilesDiscarded'],1)


class OfflineBasemapTests(unittest.TestCase):
    def test_local_administrative_city_centres_are_separate_from_town_names(self):
        cities=json.load(urlopen('http://127.0.0.1:8766/data/cities.geojson'))['features']
        self.assertGreater(len(cities),300)
        names={f['properties']['name']:f for f in cities}
        self.assertTrue(names['成都市']['properties']['capital'])
        self.assertFalse(names['贺州市']['properties']['capital'])
        self.assertEqual(names['贺州市']['properties']['province'],'广西壮族自治区')
        self.assertTrue(all(f['geometry']['type']=='Point' for f in cities))

    def test_real_city_tiles_have_streets_buildings_and_names(self):
        import mercantile
        import mapbox_vector_tile
        status=json.load(urlopen('http://127.0.0.1:8766/api/map-status'))
        self.assertTrue(status['complete'])
        for region,lon,lat in [('china',104.065,30.66),('china',120.16,30.25),('taiwan',121.5654,25.033)]:
            tile=mercantile.tile(lon,lat,14)
            raw=urlopen(f'http://127.0.0.1:8766/tiles/{region}/{tile.z}/{tile.x}/{tile.y}.pbf').read()
            decoded=mapbox_vector_tile.decode(raw)
            self.assertGreater(len(decoded['streets']['features']),20)
            self.assertGreater(len(decoded['buildings']['features']),20)
            self.assertGreater(len(decoded['street_labels']['features']),10)
            self.assertNotIn('pois',decoded)
            self.assertNotIn('addresses',decoded)

    def test_foreign_city_and_raw_archives_are_not_served(self):
        import mercantile
        tile=mercantile.tile(85.32,27.71,14)  # Kathmandu, outside the China outline.
        raw=urlopen(f'http://127.0.0.1:8766/tiles/china/{tile.z}/{tile.x}/{tile.y}.pbf').read()
        self.assertEqual(raw,b'')
        with self.assertRaises(HTTPError) as raised:
            urlopen('http://127.0.0.1:8766/data/basemap/china.mbtiles')
        self.assertEqual(raised.exception.code,403)


if __name__ == '__main__':
    unittest.main()
