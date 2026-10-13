"""Create China-only offline basemaps; strip irrelevant layers and clip border tiles."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from collections import Counter
import argparse
import gzip
import json
import math
import os
import sqlite3
import time
import numpy as np
import shapely
from shapely.geometry import shape, mapping, box
from shapely.ops import unary_union
from mapbox_vector_tile.Mapbox import vector_tile_pb2
import mapbox_vector_tile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RAW = DATA / 'raw'
WORLD = 40075016.68557849
KEEP = {'water_polygons','water_polygons_labels','water_lines','water_lines_labels','land','sites','buildings','streets','street_polygons','street_labels','street_labels_points','place_labels','bridges','ferries','dam_lines','dam_polygons','pier_lines','pier_polygons'}
COUNTRY = None


def to_mercator(x, y, z=None):
    return np.asarray(x)*WORLD/360, np.arcsinh(np.tan(np.radians(y)))*WORLD/(2*math.pi)


def init_worker(wkb):
    global COUNTRY
    COUNTRY = shapely.from_wkb(wkb)
    shapely.prepare(COUNTRY)


def clip_type(geometry, dimension):
    if geometry.is_empty:
        return None
    if shapely.get_dimensions(geometry) == dimension and geometry.geom_type != 'GeometryCollection':
        return geometry
    if hasattr(geometry, 'geoms'):
        parts = [p for g in geometry.geoms if (p := clip_type(g,dimension)) is not None]
        return unary_union(parts) if parts else None
    return None


def process_batch(rows):
    saved, stats = [], Counter()
    for z,x,tms_y,blob in rows:
        y = (1 << z) - 1 - tms_y
        size = WORLD/(1 << z)
        left, top = x*size-WORLD/2, WORLD/2-y*size
        bottom = top-size
        # The padded box includes vector-tile buffers, so copied interior tiles stay in China.
        bounds = box(left-size/8,bottom-size/8,left+size*1.125,top+size/8)
        if not COUNTRY.intersects(bounds):
            stats['outsideTilesDiscarded'] += 1
            continue
        raw = gzip.decompress(blob) if blob[:2] == b'\x1f\x8b' else blob
        tile = vector_tile_pb2.tile()
        tile.ParseFromString(raw)
        filtered = vector_tile_pb2.tile()
        for layer in tile.layers:
            if layer.name in KEEP:
                filtered.layers.add().CopyFrom(layer)
        if not filtered.layers:
            continue
        if COUNTRY.covers(bounds):
            raw = filtered.SerializeToString()
            stats['interiorTiles'] += 1
        else:
            local_country = COUNTRY.intersection(bounds)
            decoded = mapbox_vector_tile.decode(filtered.SerializeToString())
            layers = []
            options = {}
            for name, layer in decoded.items():
                extent = layer['extent']
                factor = extent/size
                tile_country = shapely.transform(local_country,lambda xx,yy: ((xx-left)*factor,(yy-bottom)*factor),interleaved=False)
                shapely.prepare(tile_country)
                features = []
                for feature in layer['features']:
                    geometry = shape(feature['geometry'])
                    if geometry.is_empty:
                        continue
                    dimension = shapely.get_dimensions(geometry)
                    if not geometry.is_valid:
                        geometry = clip_type(shapely.make_valid(geometry),dimension)
                        if geometry is None or geometry.is_empty:
                            continue
                    if not tile_country.covers(geometry):
                        geometry = clip_type(geometry.intersection(tile_country), dimension)
                    if geometry is None or geometry.is_empty:
                        stats['outsideFeaturesDiscarded'] += 1
                        continue
                    features.append({**feature,'geometry':mapping(geometry)})
                if features:
                    layers.append({'name':name,'features':features})
                    options[name] = {'extents':extent}
            if not layers:
                continue
            raw = mapbox_vector_tile.encode(layers,per_layer_options=options)
            stats['clippedBorderTiles'] += 1
        saved.append((z,x,tms_y,gzip.compress(raw,compresslevel=2,mtime=0)))
    return saved, dict(stats)


def build(region, features):
    target = DATA / 'basemap' / f'{region}.mbtiles'
    target.parent.mkdir(exist_ok=True)
    summary = target.with_suffix('.json')
    if target.exists() and summary.exists():
        previous = json.loads(summary.read_text(encoding='utf-8'))
        source_record = RAW / (region+'-shortbread.mbtiles.source.json')
        if source_record.exists() and previous.get('source',{}).get('sha256') == json.loads(source_record.read_text())['sha256']:
            print('Reuse processed basemap', region, flush=True)
            return previous
    country = unary_union([shape(f['geometry']) for f in features])
    mask = shapely.transform(country,to_mercator,interleaved=False)
    source = sqlite3.connect(f'file:{(RAW / (region+"-shortbread.mbtiles")).as_posix()}?mode=ro',uri=True)
    integrity = source.execute('PRAGMA quick_check').fetchone()[0]
    if integrity != 'ok':
        raise RuntimeError(f'{region} source integrity failed: {integrity}')
    print(region, 'source integrity:', integrity, flush=True)
    metadata = dict(source.execute('SELECT name,value FROM metadata'))
    count = source.execute('SELECT count(*) FROM tiles').fetchone()[0]
    print(region, 'source tiles', count, 'metadata', metadata.get('maxzoom'), flush=True)
    pending = target.with_suffix('.pending.mbtiles')
    if pending.exists():
        pending.unlink()
    output = sqlite3.connect(pending)
    output.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE tiles (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB, PRIMARY KEY (zoom_level,tile_column,tile_row)); CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT);')
    metadata.update({'name':f'China offline / {region}','description':'China-only clipped Shortbread basemap','attribution':'© OpenStreetMap contributors · ODbL','bounds':','.join(str(n) for n in country.bounds)})
    output.executemany('INSERT INTO metadata VALUES (?,?)',metadata.items())
    started = time.monotonic()
    stats = Counter()
    cursor = source.execute('SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles')
    batch_size = 256
    with ProcessPoolExecutor(max_workers=8,initializer=init_worker,initargs=(mask.wkb,)) as executor:
        active = []
        processed = saved_count = 0
        exhausted = False
        while not exhausted or active:
            while len(active) < 16 and not exhausted:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    exhausted=True
                    break
                active.append((len(rows),executor.submit(process_batch,rows)))
            size, future = active.pop(0)
            rows, counters = future.result()
            output.executemany('INSERT INTO tiles VALUES (?,?,?,?)',rows)
            stats.update(counters)
            processed += size
            saved_count += len(rows)
            if processed % 25600 == 0 or processed == count:
                output.commit()
                print(f'{region}: {processed}/{count} ({processed/count:.1%}), saved {saved_count}, {time.monotonic()-started:.0f}s',flush=True)
    output.commit()
    check = output.execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        raise RuntimeError(check)
    output.close()
    source.close()
    pending.replace(target)
    record = {'region':region,'tiles':saved_count,'inputTiles':count,'bytes':target.stat().st_size,'minzoom':int(metadata.get('minzoom',0)),'maxzoom':int(metadata.get('maxzoom',14)),'layers':sorted(KEEP),'processing':dict(stats),'source':json.loads((RAW / (region+'-shortbread.mbtiles.source.json')).read_text())}
    summary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Finished',region,record,flush=True)
    return record


def main():
    features = json.loads((DATA / 'china.geojson').read_text(encoding='utf-8'))['features']
    results=[]
    for region in ('china','taiwan'):
        subset=[f for f in features if (f['properties']['adcode']=='710000') == (region=='taiwan')]
        results.append(build(region,subset))
    report={'ready':True,'schema':'Shortbread 1.0','offline':True,'archives':results,'maplibreVersion':'5.24.0','maxzoom':14,'displayMaxzoom':18,'features':'普通道路、河流湖泊、绿地、建筑轮廓、城市与街道名称；境外瓦片和要素已剔除'}
    (DATA / 'basemap-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
