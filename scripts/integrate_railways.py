"""Add prepared railways while retaining every original road route and vertex."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
from prepare_data import save
from build_display_data import save_json,display_geojson,compact_line,TIERS

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
RAIL={'highspeed','railway'}

def update_index(source,routes,display=False):
    pending=source.with_suffix('.railways.pending.sqlite')
    shutil.copy2(source,pending)
    db=sqlite3.connect(pending)
    db.execute('DELETE FROM spatial WHERE id IN (SELECT id FROM segments WHERE kind IN (\'highspeed\',\'railway\'))')
    db.execute('DELETE FROM segments WHERE kind IN (\'highspeed\',\'railway\')')
    count=db.execute('SELECT COALESCE(MAX(id),0) FROM segments').fetchone()[0]
    added=0
    for route in routes:
        for feature in route['features']:
            props=feature['properties']
            for line in feature['geometry']['coordinates']:
                for start in range(0,len(line)-1,127):
                    chunk=line[start:start+128];count+=1;added+=1
                    encoded=[json.dumps(compact_line(chunk,TIERS[t]),separators=(',',':')) for t in ('detail','overview','standard')] if display else [json.dumps(chunk,separators=(',',':'))]
                    db.execute('INSERT INTO segments VALUES('+','.join(['?']*(4+len(encoded)))+')',(count,props['id'],props['code'],props['kind'],*encoded))
                    xs,ys=zip(*chunk);db.execute('INSERT INTO spatial VALUES(?,?,?,?,?)',(count,min(xs),max(xs),min(ys),max(ys)))
    db.commit();assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok';db.close()
    pending.replace(source);return added

def main():
    for path in (DATA/'network.sqlite',DATA/'display/network.sqlite',DATA/'display/manifest.json'):
        if not path.exists():
            raise SystemExit('Prepare the road network and display data before integrating railways: '+str(path))
    rails=json.loads((DATA/'railways/catalog.json').read_bytes())
    assert rails and all(r['kind'] in RAIL for r in rails)
    previous=json.loads((DATA/'catalog.json').read_bytes())
    roads=[r for r in previous if r['kind'] not in RAIL]
    road_digests={r['id']:hashlib.sha256((DATA/'routes'/f"{r['id']}.geojson").read_bytes()).hexdigest() for r in roads if r['hasGeometry']}
    routes=[]
    for record in rails:
        path=DATA/'railways/routes'/f"{record['id']}.geojson"
        raw=json.loads(path.read_bytes());routes.append(raw)
        shutil.copy2(path,DATA/'routes'/path.name)
        for tier in TIERS:save_json(DATA/'display/routes'/tier/path.name,display_geojson(raw,tier))
    current_ids={r['id'] for r in rails}
    for old in previous:
        if old['kind'] in RAIL and old['id'] not in current_ids:
            for path in [DATA/'routes'/f"{old['id']}.geojson",*[DATA/'display/routes'/t/f"{old['id']}.geojson" for t in TIERS]]:
                if path.exists():path.unlink()
                if path.with_suffix(path.suffix+'.gz').exists():path.with_suffix(path.suffix+'.gz').unlink()
    for source,is_display in [(DATA/'network.sqlite',False),(DATA/'display/network.sqlite',True)]:
        print('Indexed railway chunks:',update_index(source,routes,is_display),source.name,flush=True)
    overview={'type':'FeatureCollection','features':[]}
    for route in routes:overview['features'].extend(display_geojson(route,'overview')['features'])
    save_json(DATA/'display/railways-overview.geojson',overview)
    catalog=roads+rails;save_json(DATA/'catalog.json',catalog)
    assert [r for r in json.loads((DATA/'catalog.json').read_bytes()) if r['kind'] not in RAIL]==roads
    for ident,digest in road_digests.items():assert hashlib.sha256((DATA/'routes'/f'{ident}.geojson').read_bytes()).hexdigest()==digest
    report=json.loads((DATA/'report.json').read_bytes())
    report['routes']=len(catalog);report['withGeometry']=sum(r['hasGeometry'] for r in catalog);report['withoutGeometry']=len(catalog)-report['withGeometry']
    report['categories']=dict(Counter(r['kind'] for r in catalog));report['railways']=json.loads((DATA/'railways/report.json').read_bytes())
    with sqlite3.connect(DATA/'network.sqlite') as db:
        chunks,vertices=db.execute('SELECT count(*),sum(json_array_length(coordinates)) FROM segments').fetchone()
    report['preciseNetwork']={**report.get('preciseNetwork',{}),'chunks':chunks,'vertices':vertices}
    save(DATA/'report.json',report)
    manifest=json.loads((DATA/'display/manifest.json').read_bytes());manifest['version']='20261013-rail1';manifest['railwayRoutes']=len(rails)
    manifest['railwayRouteBytes']={tier:sum((DATA/'display/routes'/tier/f"{r['id']}.geojson").stat().st_size for r in rails) for tier in TIERS}
    manifest['railwayOverviewBytes']=(DATA/'display/railways-overview.geojson').stat().st_size
    manifest['railwayOverviewGzipBytes']=(DATA/'display/railways-overview.geojson.gz').stat().st_size
    save_json(DATA/'display/manifest.json',manifest)
    verification={'roadRoutesUnchanged':len(roads),'originalRoadFilesByteIdentical':len(road_digests),'railwayRoutes':len(rails),'categories':dict(Counter(r['kind'] for r in rails))}
    save(DATA/'railways/integration-verification.json',verification)
    print(json.dumps(verification),flush=True)

if __name__=='__main__':main()
