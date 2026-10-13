"""Exercise the Rust deployment against the same local data and Python baseline."""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def fetch(base, path, *, gzip_ok=False, method='GET'):
    headers = {'Accept-Encoding': 'gzip' if gzip_ok else 'identity'}
    with urlopen(Request(base.rstrip('/') + '/' + path.lstrip('/'), headers=headers, method=method), timeout=30) as response:
        data = response.read()
        metadata = response.headers
        if response.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        return data, metadata


def tile_at(lon, lat, z=14):
    side = 1 << z
    x = int((lon + 180) / 360 * side)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * side)
    return z, x, y


def normalized_network(value):
    return sorted((feature['properties']['id'], feature['properties']['code'], feature['properties']['kind'],
                   sorted(json.dumps(line, separators=(',', ':')) for line in feature['geometry']['coordinates']))
                  for feature in value['features'])


def verify(base, baseline, public=False):
    health = json.loads(fetch(base, 'api/health')[0])
    assert health['ready'] and health['backend'] == 'rust'
    status = json.loads(fetch(base, 'api/map-status')[0])
    assert status['complete'] and status['preciseNetworkReady']
    paths = ['', 'app.js', 'route-loader.js', 'tile-loader.js', 'style.css', 'vendor/maplibre-gl.js', 'data/catalog.json']
    paths += ['data/display/overview.geojson','data/display/routes/overview/national-G248.geojson','data/display/routes/detail/expressway-G5.geojson'] if public else ['data/overview.geojson','data/routes/national-G248.geojson','data/routes/expressway-G5.geojson']
    for path in paths:
        plain, headers = fetch(base, path)
        compressed, compressed_headers = fetch(base, path, gzip_ok=True)
        assert plain == compressed, path
        assert 'Content-Security-Policy' in headers, path
        local = ROOT / ('data/' + path[5:] if path.startswith('data/') else 'web/' + (path or 'index.html'))
        assert hashlib.sha256(plain).digest() == hashlib.sha256(local.read_bytes()).digest(), path
        if len(plain) > 1024:
            assert compressed_headers.get('Content-Encoding') == 'gzip', path
        _, head_headers = fetch(base, path, method='HEAD')
        assert int(head_headers['Content-Length']) == len(plain), path
    for region, lon, lat in [('china', 104.065, 30.66), ('china', 120.16, 30.25), ('taiwan', 121.5654, 25.033)]:
        z, x, y = tile_at(lon, lat)
        path = f'tiles/{region}/{z}/{x}/{y}.pbf'
        raw, _ = fetch(base, path)
        compressed, _ = fetch(base, path, gzip_ok=True)
        assert raw and raw == compressed, path
        if baseline:
            assert raw == fetch(baseline, path)[0], path
    z, x, y = tile_at(85.32, 27.71)
    assert fetch(base, f'tiles/china/{z}/{x}/{y}.pbf')[0] == b''
    for path, expected in [
        ('api/network?bbox=NaN,0,1,2', 400),
        ('api/network?bbox=104,31,103,30', 400),
        ('api/network?bbox=0,0,181,1', 400),
        ('api/network?bbox=103,30,105,31&kinds=invalid', 400),
        ('tiles/china/15/0/0.pbf', 400),
        ('data/network.sqlite', 403), ('data/basemap/china.mbtiles', 403),
        ('data/raw/test.pdf', 403), ('data/%2e%2e/server.py', (403, 404)), ('missing.js', 404),
        *([('data/routes/national-G248.geojson',403),('data/%72outes/national-G248.geojson',403),('data/display/network.sqlite',403),('api/network?bbox=73,17,136,55&zoom=18',400)] if public else []),
    ]:
        try:
            fetch(base, path)
            raise AssertionError(f'{path}: unexpectedly allowed')
        except HTTPError as error:
            # Nginx normalizes dot segments before proxying; both statuses deny access.
            assert error.code in (expected if isinstance(expected, tuple) else (expected,)), (path, error.code)
    network_paths = [
        'api/network?bbox=103.85,30.48,104.25,30.83',
        'api/network?bbox=103.85,30.48,104.25,30.83&kinds=national',
        'api/network?bbox=103.85,30.48,104.25,30.83&kinds=expressway,provincial',
        'api/network?bbox=111.3,24.1,112.3,24.9',
    ]
    if public:network_paths=[path+'&zoom=7' for path in network_paths]
    for path in network_paths:
        data = json.loads(fetch(base, path, gzip_ok=True)[0])
        assert data['ready'] and not data['truncated'], path
        if baseline:
            original = json.loads(fetch(baseline, path)[0])
            assert normalized_network(data) == normalized_network(original), path
        if 'kinds=' in path:
            allowed = parse_qs(urlsplit(path).query)['kinds'][0].split(',')
            assert all(feature['properties']['kind'] in allowed for feature in data['features']), path
    load_paths = [network_paths[1], 'data/display/routes/overview/national-G248.geojson' if public else 'data/routes/national-G248.geojson',
                  'data/catalog.json', 'tiles/china/%d/%d/%d.pbf' % tile_at(104.065, 30.66)]

    def request(index):
        started = time.perf_counter()
        data, _ = fetch(base, load_paths[index % len(load_paths)], gzip_ok=True)
        assert data
        return time.perf_counter() - started

    started = time.perf_counter()
    if public:
        result={'base':base,'publicMode':True,'baseline_parity':bool(baseline),'functionalChecks':'passed','loadTest':'separate rate-limit test; do not mix synthetic bursts with functional checks'}
        print(json.dumps(result,ensure_ascii=False,indent=2));return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        durations = list(pool.map(request, range(96)))
    result = {'base': base, 'requests': 96, 'concurrency': 16, 'successful': len(durations),
              'seconds': round(time.perf_counter() - started, 3),
              'median_ms': round(statistics.median(durations) * 1000, 1),
              'p95_ms': round(sorted(durations)[math.ceil(len(durations) * .95) - 1] * 1000, 1),
              'baseline_parity': bool(baseline)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8767')
    parser.add_argument('--baseline')
    parser.add_argument('--output')
    parser.add_argument('--public',action='store_true')
    args = parser.parse_args()
    result = verify(args.base, args.baseline,args.public)
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
