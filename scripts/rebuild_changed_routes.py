"""Refresh only changed route files and spatial rows, retaining unrelated data."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
from prepare_data import save
from build_display_data import save_json, display_geojson, compact_line, TIERS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'


def update_index(source, routes, display=False):
    pending = source.with_suffix('.changed.pending.sqlite')
    shutil.copy2(source,pending)
    db = sqlite3.connect(pending)
    for route in routes:
        rid = route['features'][0]['properties']['id']
        db.execute('DELETE FROM spatial WHERE id IN (SELECT id FROM segments WHERE route=?)',(rid,))
        db.execute('DELETE FROM segments WHERE route=?',(rid,))
    count = db.execute('SELECT COALESCE(MAX(id),0) FROM segments').fetchone()[0]
    added = 0
    for route in routes:
        for feature in route['features']:
            props = feature['properties']
            lines = feature['geometry']['coordinates']
            if feature['geometry']['type'] == 'LineString':
                lines = [lines]
            for line in lines:
                for start in range(0,len(line)-1,127):
                    chunk = line[start:start+128];count += 1;added += 1
                    encoded = ([json.dumps(compact_line(chunk,TIERS[tier]),separators=(',',':'))
                                for tier in ('detail','overview','standard')] if display
                               else [json.dumps(chunk,separators=(',',':'))])
                    db.execute('INSERT INTO segments VALUES('+','.join(['?']*(4+len(encoded)))+')',
                               (count,props['id'],props['code'],props['kind'],*encoded))
                    xs,ys = zip(*chunk)
                    db.execute('INSERT INTO spatial VALUES(?,?,?,?,?)',(count,min(xs),max(xs),min(ys),max(ys)))
    db.commit()
    assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    db.close();pending.replace(source)
    return added


def main(version):
    report = json.loads((DATA/'coastal-crossings-report.json').read_bytes())
    ids = set(report['routes'])
    routes = [json.loads((DATA/'routes'/f'{rid}.geojson').read_bytes()) for rid in sorted(ids)]
    for rid,route in zip(sorted(ids),routes):
        raw = DATA/'routes'/f'{rid}.geojson'
        raw.with_suffix('.geojson.gz').write_bytes(gzip.compress(raw.read_bytes(),compresslevel=9,mtime=0))
        for tier in TIERS:
            save_json(DATA/'display/routes'/tier/raw.name,display_geojson(route,tier))
    for path,display in [(DATA/'network.sqlite',False),(DATA/'display/network.sqlite',True)]:
        print('Updated spatial rows:',update_index(path,routes,display),path,flush=True)
    original = json.loads((DATA/'overview.geojson').read_bytes())
    overview = json.loads((DATA/'display/overview.geojson').read_bytes())
    overview['features'] = [feature for feature in overview['features'] if feature['properties']['id'] not in ids]
    for feature in original['features']:
        if feature['properties']['id'] in ids:
            overview['features'].extend(display_geojson({'type':'FeatureCollection','features':[feature]},'overview')['features'])
    save_json(DATA/'display/overview.geojson',overview)
    save_json(DATA/'catalog.json',json.loads((DATA/'catalog.json').read_bytes()))
    manifest = json.loads((DATA/'display/manifest.json').read_bytes())
    manifest['version'] = version
    manifest['routeBytes'] = {tier:sum(path.stat().st_size for path in (DATA/'display/routes'/tier).glob('*.geojson')) for tier in TIERS}
    manifest['overviewOriginalBytes'] = (DATA/'overview.geojson').stat().st_size
    manifest['overviewDisplayBytes'] = (DATA/'display/overview.geojson').stat().st_size
    manifest['coastalRoutesRestored'] = len(ids)
    save_json(DATA/'display/manifest.json',manifest)
    health = json.loads((DATA/'report.json').read_bytes())
    with sqlite3.connect(DATA/'network.sqlite') as db:
        chunks,vertices = db.execute('SELECT count(*),sum(json_array_length(coordinates)) FROM segments').fetchone()
    health['preciseNetwork'] = {**health.get('preciseNetwork',{}),'chunks':chunks,'vertices':vertices}
    save(DATA/'report.json',health)
    print('Updated routes:',len(ids),'version:',version,flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version',required=True)
    main(parser.parse_args().version)
