"""One-time preparation. The application never imports this network module."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
WEB = ROOT / 'web'


def fetch(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        print('Reuse', path.name, flush=True)
        return
    r = requests.get(url, timeout=(30, 120))
    r.raise_for_status()
    path.write_bytes(r.content)
    print('Saved', path.name, len(r.content), flush=True)


def download_pbf(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    size = int(r.headers['Content-Length'])
    r.close()
    checksum_url = url + '.md5'
    expected = requests.get(checksum_url, timeout=30)
    expected.raise_for_status()
    expected = expected.text.split()[0]
    if dest.exists() and dest.stat().st_size == size:
        if hashlib.file_digest(dest.open('rb'), 'md5').hexdigest() == expected:
            print('Verified existing PBF', flush=True)
            return
    parts = dest.parent / 'download-parts'
    parts.mkdir(exist_ok=True)
    chunk = 32 * 1024 * 1024
    count = (size + chunk - 1) // chunk
    start = time.monotonic()

    def part(i):
        first, last = i * chunk, min((i + 1) * chunk, size) - 1
        target = parts / f'{i:04}.part'
        if target.exists() and target.stat().st_size == last - first + 1:
            return target
        for attempt in range(5):
            try:
                with requests.get(url, headers={'Range': f'bytes={first}-{last}'}, stream=True, timeout=(30, 90)) as response:
                    response.raise_for_status()
                    if response.status_code != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {first}-{last}/'):
                        raise RuntimeError('Server did not honor exact Range; refusing corrupted download')
                    with target.open('wb') as output:
                        for block in response.iter_content(1024 * 1024):
                            output.write(block)
                if target.stat().st_size != last - first + 1:
                    raise RuntimeError('Incomplete range')
                return target
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(attempt + 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [pool.submit(part, i) for i in range(count)]
        completed = 0
        for task in as_completed(tasks):
            task.result()
            completed += 1
            print(f'PBF {completed}/{count} parts, {time.monotonic()-start:.0f}s', flush=True)
    pending = dest.with_suffix('.pending')
    with pending.open('wb') as out:
        for i in range(count):
            with (parts / f'{i:04}.part').open('rb') as inp:
                while block := inp.read(4 * 1024 * 1024):
                    out.write(block)
    with pending.open('rb') as inp:
        actual = hashlib.file_digest(inp, 'md5').hexdigest()
    if actual != expected:
        raise RuntimeError(f'PBF checksum mismatch: {actual} != {expected}')
    pending.replace(dest)
    dest.with_suffix(dest.suffix + '.md5').write_text(expected, encoding='utf-8')
    for i in range(count):
        (parts / f'{i:04}.part').unlink()
    parts.rmdir()
    print('PBF verified:', size, 'bytes, MD5', actual, flush=True)


def main():
    assets = [
        ('https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json', RAW / 'china-provinces.json'),
        ('https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.js', WEB / 'vendor' / 'maplibre-gl.js'),
        ('https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.css', WEB / 'vendor' / 'maplibre-gl.css'),
        ('https://unpkg.com/maplibre-gl@5.10.0/LICENSE.txt', WEB / 'vendor' / 'maplibre-LICENSE.txt'),
        ('https://www.ndrc.gov.cn/xwdt/tzgg/202207/P020220712350107027421.pdf', RAW / 'national-road-plan-2022.pdf'),
    ]
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch, url, path) for url, path in assets]
        for future in as_completed(futures):
            future.result()
    download_pbf('https://download.geofabrik.de/asia/china-latest.osm.pbf', RAW / 'china.osm.pbf')


if __name__ == '__main__':
    main()
