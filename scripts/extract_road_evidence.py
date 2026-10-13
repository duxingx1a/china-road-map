"""Keep Chinese numbered-way evidence so route membership can be audited offline."""
import json
import sqlite3
from collections import Counter
import osmium
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from shapely import prepare
from prepare_data import DATA, RAW, Relations, Roads, catalog_from_plan


class Evidence(Roads):
    def __init__(self, members, db):
        features = json.loads((DATA / 'china.geojson').read_text(encoding='utf8'))['features']
        shapes = [shape(f['geometry']) for f in features]
        mask = unary_union(shapes)
        prepare(mask)
        for g in shapes:
            prepare(g)
        super().__init__(members, catalog_from_plan(), mask, shapes,
                         [f['properties']['name'] for f in features],
                         [str(f['properties']['adcode']) for f in features])
        self.db = db
        self.tags = {}

    def way(self, w):
        if not w.tags.get('highway'):
            return
        self.tags = dict(w.tags)
        super().way(w)

    def add(self, key, way_id, parts, name, relations, provinces):
        kind, code, province = key
        rid = f'{kind}-{province+"-" if province else ""}{code}'
        self.db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?)',
            (rid, way_id, json.dumps(self.tags, ensure_ascii=False),
             json.dumps(relations, ensure_ascii=False),
             json.dumps([list(p.coords) for p in parts], separators=(',', ':')),
             json.dumps([self.names[i] for i in provinces], ensure_ascii=False)))
        if way_id % 10000 == 0:
            self.db.commit()


def main():
    target = DATA / 'road-evidence.pending.sqlite'
    if target.exists():
        target.unlink()
    db = sqlite3.connect(target)
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('CREATE TABLE evidence (route TEXT, way INTEGER, tags TEXT, relations TEXT, coordinates TEXT, provinces TEXT)')
    relations = Relations()
    relations.apply_file(str(RAW / 'china.osm.pbf'))
    print('Relations loaded:', relations.count, flush=True)
    handler = Evidence(relations.members, db)
    handler.apply_file(str(RAW / 'china.osm.pbf'), locations=True, idx='sparse_mem_array')
    db.execute('CREATE INDEX evidence_route ON evidence(route)')
    db.execute('CREATE INDEX evidence_way ON evidence(way)')
    db.commit()
    print('Evidence rows:', db.execute('SELECT count(*) FROM evidence').fetchone()[0], flush=True)
    db.close()
    target.replace(DATA / 'road-evidence.sqlite')


if __name__ == '__main__':
    main()
