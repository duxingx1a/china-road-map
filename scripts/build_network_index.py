"""Index original route vertices for viewport queries, without geometric simplification."""
from pathlib import Path
import json
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def main():
    pending = DATA / 'network.pending.sqlite'
    if pending.exists():
        pending.unlink()
    db = sqlite3.connect(pending)
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('CREATE TABLE segments (id INTEGER PRIMARY KEY, route TEXT, code TEXT, kind TEXT, coordinates TEXT)')
    db.execute('CREATE VIRTUAL TABLE spatial USING rtree(id, minx, maxx, miny, maxy)')
    count = vertices = 0
    for path in sorted((DATA / 'routes').glob('*.geojson')):
        data = json.loads(path.read_text(encoding='utf-8'))
        for feature in data['features']:
            props = feature['properties']
            coordinates = feature['geometry']['coordinates']
            if feature['geometry']['type'] == 'LineString':
                coordinates = [coordinates]
            for line in coordinates:
                for start in range(0, len(line)-1, 127):
                    chunk = line[start:start+128]
                    count += 1
                    vertices += len(chunk)
                    xs, ys = zip(*chunk)
                    db.execute('INSERT INTO segments VALUES (?,?,?,?,?)', (count, props['id'], props['code'], props['kind'], json.dumps(chunk, separators=(',', ':'))))
                    db.execute('INSERT INTO spatial VALUES (?,?,?,?,?)', (count,min(xs),max(xs),min(ys),max(ys)))
        if count % 10000 < 100:
            db.commit()
    db.commit()
    db.execute('ANALYZE')
    db.close()
    pending.replace(DATA / 'network.sqlite')
    print('Indexed precise network:', count, 'chunks,', vertices, 'vertices', flush=True)
    summary = {'chunks': count, 'vertices': vertices, 'simplification': 0, 'chunkMaxVertices': 128}
    (DATA / 'network-index.json').write_text(json.dumps(summary), encoding='utf-8')
    report_path = DATA / 'report.json'
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding='utf8'))
        report['preciseNetwork'] = summary
        report_path.write_text(json.dumps(report,ensure_ascii=False,separators=(',',':')),encoding='utf8')


if __name__ == '__main__':
    main()
