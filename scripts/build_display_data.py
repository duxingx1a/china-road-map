"""Offline display-only geometry and basemap LODs. Original data is never edited."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import math
from pathlib import Path
import sqlite3
import time

from shapely.geometry import LineString, shape, mapping
import mapbox_vector_tile

ROOT=Path(__file__).resolve().parents[1]
TIERS={'overview':200,'standard':10,'detail':0.4}

def compact_line(line,meters):
    if len(line)<2:return line
    lat=sum(p[1] for p in line)/len(line)
    scale=max(0.2,math.cos(math.radians(lat)))
    metric=LineString([(p[0]*111320*scale,p[1]*111320) for p in line])
    simplified=metric.simplify(meters,preserve_topology=False)
    digits=5 if meters>=100 else 6
    points=[[round(x/(111320*scale),digits),round(y/111320,digits)] for x,y in simplified.coords]
    result=[points[0]]
    for p in points[1:]:
        if p!=result[-1]:result.append(p)
    return result if len(result)>1 else [points[0],points[-1]]

def lines_of(geometry):
    if geometry['type']=='LineString':return [geometry['coordinates']]
    if geometry['type']=='MultiLineString':return geometry['coordinates']
    if geometry['type']=='GeometryCollection':return [line for g in geometry['geometries'] for line in lines_of(g)]
    return []

def display_geojson(value,tier):
    result={'type':'FeatureCollection','features':[]}
    for f in value['features']:
        lines=lines_of(f['geometry'])
        props={k:f['properties'][k] for k in ('id','code','kind') if k in f['properties']}
        result['features'].append({'type':'Feature','properties':props,'geometry':{'type':'MultiLineString','coordinates':[compact_line(line,TIERS[tier]) for line in lines if len(line)>1]}})
    return result

def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    path.write_bytes(raw)
    path.with_suffix(path.suffix+'.gz').write_bytes(gzip.compress(raw,compresslevel=6,mtime=0))
    return len(raw)

def tile_batch(rows):
    output=[]
    for z,x,y,blob in rows:
        decoded=mapbox_vector_tile.decode(gzip.decompress(blob) if blob[:2]==b'\x1f\x8b' else blob)
        layers=[]; options={}
        keep={'water_polygons','streets'}
        if z>=6:keep.add('land')
        if z>=8:keep.add('water_lines')
        if z>=9:keep.add('place_labels')
        if z>=10:keep.add('water_polygons_labels')
        if z>=11:keep.add('sites')
        for name,layer in decoded.items():
            if name not in keep:continue
            features=[]; extent=layer['extent']
            for f in layer['features']:
                kind=f['properties'].get('kind')
                if name=='streets':
                    kinds={'motorway','trunk'}
                    if z>=8:kinds.add('primary')
                    if z>=10:kinds.update(('secondary','rail','light_rail','tram','subway','narrow_gauge'))
                    if kind not in kinds:continue
                if name=='place_labels' and kind not in ({'city'}|({'town'} if z>=11 else set())):continue
                geom=shape(f['geometry'])
                if not geom.is_valid:continue
                if geom.geom_type not in ('Point','MultiPoint'):
                    geom=geom.simplify(extent/512*0.6,preserve_topology=geom.geom_type in ('Polygon','MultiPolygon'))
                if geom.is_empty:continue
                props={k:v for k,v in f['properties'].items() if k=='kind' or (name.endswith('labels') and k in ('name','ref'))}
                features.append({'geometry':mapping(geom),'properties':props})
            if features:
                layers.append({'name':name,'features':features});options[name]={'extents':extent}
        raw=mapbox_vector_tile.encode(layers,per_layer_options=options) if layers else b''
        output.append((z,x,y,gzip.compress(raw,compresslevel=6,mtime=0) if raw else b''))
    return output

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'data');parser.add_argument('--workers',type=int,default=4);parser.add_argument('--skip-tiles',action='store_true');parser.add_argument('--skip-routes',action='store_true');args=parser.parse_args()
    data=args.data; out=data/'display';out.mkdir(exist_ok=True)
    report={'version':'20261013','tiersMeters':TIERS,'routeBytes':{},'basemap':{}}
    for tier in TIERS:
        total=0
        for path in sorted((data/'routes').glob('*.geojson')):
            target=out/'routes'/tier/path.name
            total+=target.stat().st_size if args.skip_routes else save_json(target,display_geojson(json.loads(path.read_bytes()),tier))
        report['routeBytes'][tier]=total
        print('Routes',tier,total,flush=True)
    original=json.loads((data/'overview.geojson').read_bytes())
    report['overviewOriginalBytes']=(data/'overview.geojson').stat().st_size
    report['overviewDisplayBytes']=save_json(out/'overview.geojson',display_geojson(original,'overview'))
    src=sqlite3.connect(data/'network.sqlite'); pending=out/'network.pending.sqlite'
    if pending.exists():pending.unlink()
    dest=sqlite3.connect(pending)
    dest.executescript('PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;CREATE TABLE segments(id INTEGER PRIMARY KEY,route TEXT,code TEXT,kind TEXT,coordinates TEXT,z7 TEXT,z10 TEXT);CREATE VIRTUAL TABLE spatial USING rtree(id,minx,maxx,miny,maxy);')
    for index,row in enumerate(src.execute('SELECT id,route,code,kind,coordinates FROM segments')):
        ident,route,code,kind,coords=row;line=json.loads(coords)
        encoded=[json.dumps(compact_line(line,TIERS[t]),separators=(',',':')) for t in ('detail','overview','standard')]
        dest.execute('INSERT INTO segments VALUES(?,?,?,?,?,?,?)',(ident,route,code,kind,*encoded))
        # The spatial index describes original geometry, so LODs cannot lose border candidates.
        spatial=src.execute('SELECT minx,maxx,miny,maxy FROM spatial WHERE id=?',(ident,)).fetchone()
        dest.execute('INSERT INTO spatial VALUES(?,?,?,?,?)',(ident,*spatial))
        if index%10000==0:dest.commit()
    dest.commit();dest.close();src.close();pending.replace(out/'network.sqlite')
    print('Display index ready',flush=True)
    if not args.skip_tiles:
        for region in ('china','taiwan'):
            src=sqlite3.connect(data/'basemap'/f'{region}.mbtiles');pending=out/f'{region}.pending.mbtiles'
            if pending.exists():pending.unlink()
            dest=sqlite3.connect(pending);dest.executescript('PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;CREATE TABLE tiles(zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB,PRIMARY KEY(zoom_level,tile_column,tile_row));')
            rows=src.execute('SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles WHERE zoom_level<=11'); started=time.monotonic();before=after=count=0
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                active=[];done=False
                while active or not done:
                    while len(active)<args.workers*2 and not done:
                        batch=rows.fetchmany(64)
                        if not batch:done=True;break
                        before+=sum(len(r[3]) for r in batch);active.append(pool.submit(tile_batch,batch))
                    if not active:continue
                    batch=active.pop(0).result();dest.executemany('INSERT INTO tiles VALUES(?,?,?,?)',batch)
                    count+=len(batch);after+=sum(len(r[3]) for r in batch)
                    if count%2048==0:dest.commit();print(region,count,round(time.monotonic()-started),'s',flush=True)
            dest.commit();assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok';dest.close();src.close();pending.replace(out/f'{region}.mbtiles')
            report['basemap'][region]={'tiles':count,'beforeGzipBytes':before,'afterGzipBytes':after}
            print(region,report['basemap'][region],flush=True)
    save_json(out/'manifest.json',report);print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
