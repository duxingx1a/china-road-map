"""Check restored sea crossings against local source witnesses and public tiers."""
import argparse
import gzip
import json
from pathlib import Path
from urllib.request import Request,urlopen
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'


def verify(base):
    def fetch(path):
        with urlopen(Request(base.rstrip('/')+'/'+path,headers={'Accept-Encoding':'gzip'}),timeout=60) as response:
            raw = response.read()
            return gzip.decompress(raw) if response.headers.get('Content-Encoding') == 'gzip' else raw
    catalog = json.loads(fetch('data/catalog.json?v=20261013-coast1'))
    local = json.loads((DATA/'catalog.json').read_bytes())
    assert catalog == local
    report = json.loads((DATA/'coastal-crossings-report.json').read_bytes())
    assert sum('coastalCrossings' in route for route in catalog) == report['routesRestored']
    land = unary_union([shape(f['geometry']) for f in json.loads((DATA/'china.geojson').read_bytes())['features']])
    evidence = json.loads((DATA/'coastal-crossing-evidence.geojson').read_bytes())['features']
    witnesses = []
    for rid,structure in [('expressway-G2518','bridge'),('expressway-G2518','tunnel'),
                          ('expressway-G40','tunnel'),('provincial-370000-S85','bridge')]:
        candidates = [f for f in evidence if f['properties']['id'] == rid and f['properties']['tags'].get(structure) == 'yes']
        feature = max(candidates,key=lambda f:shape(f['geometry']).difference(land).length)
        offshore = shape(feature['geometry']).difference(land)
        if offshore.geom_type == 'MultiLineString':
            offshore = max(offshore.geoms,key=lambda line:line.length)
        point = offshore.interpolate(.5,normalized=True)
        assert not land.covers(point)
        bbox = [point.x-.005,point.y-.005,point.x+.005,point.y+.005]
        kind = rid.split('-')[0]
        value = json.loads(fetch('api/network?bbox='+','.join(map(str,bbox))+'&zoom=14&kinds='+kind))
        pieces = [shape(f['geometry']) for f in value['features'] if f['properties']['id'] == rid]
        assert pieces and unary_union(pieces).distance(point) < .00002,(rid,structure)
        witnesses.append({'route':rid,'structure':structure,'sourceWay':feature['properties']['way'],'viewportRestored':True})
    for tier in ('overview','standard','detail'):
        path = f'data/display/routes/{tier}/expressway-G2518.geojson'
        assert fetch(path) == (ROOT/path).read_bytes()
    return {'base':base,'checks':'passed','restoredRoutes':report['routesRestored'],
            'g2518DisplayTiersByteIdentical':True,'offshoreViewportWitnesses':witnesses}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base',required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = verify(args.base)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.output:
        Path(args.output).write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
