import importlib.util
import math
from pathlib import Path
import unittest
from shapely.geometry import LineString,Point

path=Path(__file__).resolve().parents[1]/'scripts/build_display_data.py'
spec=importlib.util.spec_from_file_location('display',path);display=importlib.util.module_from_spec(spec);spec.loader.exec_module(display)

class DisplayTests(unittest.TestCase):
    def test_fine_geometry_preserves_bends_with_sub_meter_error(self):
        line=[[104+i*.00001,30+(i%7)*.000003] for i in range(100)]
        result=display.compact_line(line,.4)
        scale=math.cos(math.radians(30));metric=lambda p:(p[0]*111320*scale,p[1]*111320)
        simplified=LineString([metric(p) for p in result])
        self.assertLess(max(simplified.distance(Point(metric(p))) for p in line),.6)
        self.assertEqual(result[0],line[0]);self.assertEqual(result[-1],line[-1])

    def test_disconnected_lines_are_never_joined_and_collection_points_are_ignored(self):
        value={'features':[{'properties':{'id':'x','kind':'national','private':'secret'},'geometry':{'type':'GeometryCollection','geometries':[{'type':'LineString','coordinates':[[104,30],[104.1,30.1]]},{'type':'LineString','coordinates':[[105,31],[105.1,31.1]]},{'type':'Point','coordinates':[0,0]}]}}]}
        result=display.display_geojson(value,'overview')['features'][0]
        self.assertEqual(len(result['geometry']['coordinates']),2)
        self.assertNotIn('private',result['properties'])

if __name__=='__main__':unittest.main()
