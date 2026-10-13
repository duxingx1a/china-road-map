"""Download country-specific offline vector archives once, never during app startup."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(30, 90)) as response:
        response.raise_for_status()
        size = int(response.headers['Content-Length'])
        modified = response.headers.get('Last-Modified')
    identity = {'url': url, 'size': size, 'lastModified': modified}
    record = target.with_suffix(target.suffix + '.source.json')
    if target.exists() and target.stat().st_size == size and record.exists():
        prior = json.loads(record.read_text())
        if all(prior.get(k) == v for k, v in identity.items()):
            print('Reuse verified archive:', target.name, flush=True)
            return
    parts = RAW / (target.name + '.parts')
    parts.mkdir(exist_ok=True)
    part_record = parts / 'identity.json'
    if part_record.exists() and json.loads(part_record.read_text()) != identity:
        raise RuntimeError('Source version changed. Remove the old named .parts directory before retrying.')
    part_record.write_text(json.dumps(identity), encoding='utf-8')
    chunk = 32 * 1024 * 1024
    count = (size + chunk - 1) // chunk
    started = time.monotonic()

    def get_part(i):
        start, stop = i * chunk, min((i + 1) * chunk, size) - 1
        path = parts / f'{i:05}.part'
        if path.exists() and path.stat().st_size == stop - start + 1:
            return path.stat().st_size
        for attempt in range(5):
            try:
                headers = {'Range': f'bytes={start}-{stop}', 'Accept-Encoding': 'identity'}
                if modified:
                    headers['If-Range'] = modified
                with requests.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
                    response.raise_for_status()
                    if response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {start}-{stop}/{size}':
                        raise RuntimeError('Source changed or incorrect HTTP byte range')
                    with path.open('wb') as output:
                        for block in response.iter_content(1024 * 1024):
                            output.write(block)
                if path.stat().st_size != stop - start + 1:
                    raise RuntimeError('Incomplete chunk')
                return path.stat().st_size
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(attempt + 1)

    print('Downloading', target.name, f'{size/1e9:.2f} GB', flush=True)
    completed = 0
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(get_part, i) for i in range(count)]
        for number, future in enumerate(as_completed(futures), 1):
            completed += future.result()
            if number % 8 == 0 or number == count:
                print(f'{target.name}: {completed/size:.1%}, {completed/1e9:.2f} GB, {time.monotonic()-started:.0f}s', flush=True)
    pending = target.with_suffix('.pending')
    digest = hashlib.sha256()
    with pending.open('wb') as out:
        for i in range(count):
            with (parts / f'{i:05}.part').open('rb') as inp:
                while block := inp.read(4 * 1024 * 1024):
                    out.write(block)
                    digest.update(block)
    if pending.stat().st_size != size:
        raise RuntimeError('Archive size mismatch')
    pending.replace(target)
    record.write_text(json.dumps({**identity, 'sha256': digest.hexdigest()}, indent=2), encoding='utf-8')
    for i in range(count):
        (parts / f'{i:05}.part').unlink()
    part_record.unlink()
    parts.rmdir()
    print('Saved', target.name, 'SHA256', digest.hexdigest(), flush=True)


def main():
    for name in ('china', 'taiwan'):
        download(f'https://download.geofabrik.de/asia/{name}-shortbread-1.0.mbtiles', RAW / f'{name}-shortbread.mbtiles')
    for filename in ('maplibre-gl.js', 'maplibre-gl.css', 'LICENSE.txt'):
        response = requests.get(f'https://unpkg.com/maplibre-gl@5.24.0/dist/{filename}' if filename != 'LICENSE.txt' else 'https://unpkg.com/maplibre-gl@5.24.0/LICENSE.txt', timeout=90)
        response.raise_for_status()
        target = ROOT / 'web' / 'vendor' / (filename if filename != 'LICENSE.txt' else 'maplibre-LICENSE.txt')
        pending = target.with_suffix('.pending')
        pending.write_bytes(response.content)
        pending.replace(target)
    print('Local-font capable MapLibre 5.24.0 saved.', flush=True)


if __name__ == '__main__':
    main()
