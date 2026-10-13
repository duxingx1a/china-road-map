"""Verify the deployed railway catalog, geometry tiers, kinds and public guards."""
import argparse
import gzip
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def verify(base):
    transfers = {}

    def fetch(path):
        request = Request(base.rstrip('/') + '/' + path, headers={'Accept-Encoding': 'gzip'})
        with urlopen(request, timeout=60) as response:
            body = response.read()
            compressed = response.headers.get('Content-Encoding') == 'gzip'
            transfers[path] = {'bytes': len(body), 'gzip': compressed}
            return gzip.decompress(body) if compressed else body

    catalog = json.loads(fetch('data/catalog.json?v=20261013-rail1'))
    assert catalog == json.loads((ROOT/'data/catalog.json').read_bytes())
    counts = {kind: sum(r['kind'] == kind for r in catalog) for kind in ('highspeed', 'railway')}
    samples = [next(r for r in catalog if r['name'] == name) for name in ('京沪高铁', '京沪线')]
    assert samples[0]['id'] != samples[1]['id']
    for route in samples:
        for tier in ('overview', 'standard', 'detail'):
            path = f"data/display/routes/{tier}/{route['id']}.geojson"
            assert fetch(path) == (ROOT/path).read_bytes(), path
    overview = 'data/display/railways-overview.geojson'
    assert fetch(overview) == (ROOT/overview).read_bytes()
    query_counts = {}
    for kind in ('highspeed', 'railway'):
        value = json.loads(fetch('api/network?bbox=116,31,122,40&zoom=7&kinds='+kind))
        assert value['features']
        assert all(f['properties']['kind'] == kind for f in value['features'])
        query_counts[kind] = len(value['features'])
    for path in ('data/network.sqlite', 'data/display/network.sqlite',
                 f"data/routes/{samples[0]['id']}.geojson", 'data/railway-evidence.sqlite'):
        try:
            fetch(path)
        except HTTPError as error:
            assert error.code == 403, (path, error.code)
        else:
            raise AssertionError('Private resource exposed: '+path)
    return {'base': base, 'railwayCategories': counts, 'checks': 'passed',
            'displayFilesByteIdentical': True, 'separateViewportKinds': query_counts,
            'privateResourcesBlocked': True, 'transfers': transfers}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = verify(args.base)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
