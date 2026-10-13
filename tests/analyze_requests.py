import collections
import json
import re
from pathlib import Path

rows = []
for line in Path('nginx-map-recent.log').read_text(encoding='utf-8-sig').splitlines():
    match = re.search(r'"GET (/map/[^\"]+)" (\d+) (\d+).*request_time=([\d.]+)', line)
    if match:
        path, status, size, duration = match.groups()
        rows.append((path.split(' HTTP/')[0].split('?')[0], status, int(size), float(duration)))
print(json.dumps({'requests':len(rows), 'statuses':dict(collections.Counter(r[1] for r in rows)),
                  'largest':sorted(rows,key=lambda r:r[2],reverse=True)[:10],
                  'cancelled':dict(collections.Counter(r[0].split('/')[2] for r in rows if r[1]=='499')),
                  'notFound':[r[0] for r in rows if r[1]=='404']},indent=2))
