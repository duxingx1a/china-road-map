"""Prepare gzip sidecars once, instead of compressing static files per request."""
import gzip
import sys
from pathlib import Path

EXTENSIONS = {'.html', '.css', '.js', '.json', '.geojson', '.svg', '.txt'}


def compress(root):
    files = total = compressed = 0
    for path in Path(root).rglob('*'):
        if not path.is_file() or path.suffix not in EXTENSIONS or path.stat().st_size < 1024:
            continue
        raw = path.read_bytes()
        encoded = gzip.compress(raw, compresslevel=6, mtime=0)
        if len(encoded) >= len(raw):
            continue
        path.with_name(path.name + '.gz').write_bytes(encoded)
        files += 1
        total += len(raw)
        compressed += len(encoded)
    print(f'Precompressed {files} files: {total:,} -> {compressed:,} bytes')


if __name__ == '__main__':
    for root in sys.argv[1:]:
        compress(root)
