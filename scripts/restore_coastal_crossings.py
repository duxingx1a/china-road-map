"""Restore source bridge/tunnel chains anchored on Chinese land at both ends.

Province outlines describe land, not the reach of domestic transport structures.
This keeps separately evidenced crossings without extending a country's border.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import sqlite3
from pathlib import Path
from shapely.geometry import LineString, Point, shape, mapping
from shapely.ops import unary_union
from shapely import prepare
from prepare_data import refs, lines_only, save
from audit_routes import membership_reasons
from extract_crossing_source import structural, COAST

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'


def eligible_crossing(items, land, coastal_land):
    """Require actual structures, Chinese shore anchors and bounded coastal extent."""
    outside = [item for item in items if item['outside']]
    if not outside or not any(structural(item['tags']) for item in outside):
        return False, 'no_offshore_structure'
    degrees = Counter(node for item in items for node in (item['nodes'][0],item['nodes'][-1]))
    points = {node:point for item in items for node,point in zip(item['nodes'],item['coordinates'])}
    ends = [points[node] for node,degree in degrees.items() if degree == 1]
    if len(ends) < 2 or not all(land.covers(Point(point)) for point in ends):
        return False, 'shore_anchors_unverified'
    geometry = unary_union([LineString(item['coordinates']) for item in outside])
    # Conservative vicinity guard; this is not a territorial-sea polygon.
    if not coastal_land.buffer(.25).covers(geometry):
        return False, 'outside_coastal_vicinity'
    minx,miny,maxx,maxy = geometry.bounds
    diameter = math.hypot((maxx-minx)*111320*math.cos(math.radians((miny+maxy)/2)),(maxy-miny)*111320)
    if diameter > 100000:
        return False, 'offshore_extent_too_large'
    outside_parts = [(item,LineString(item['coordinates']).difference(land)) for item in outside]
    total = sum(geometry.length for _,geometry in outside_parts)
    structural_length = sum(geometry.length for item,geometry in outside_parts if structural(item['tags']))
    if total <= 0 or structural_length/total < .8:
        return False, 'structure_evidence_insufficient'
    return True, 'accepted'


def components(items):
    parents = list(range(len(items)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    nodes = {}
    for index,item in enumerate(items):
        for node in (item['nodes'][0],item['nodes'][-1]):
            if node in nodes:
                parents[root(index)] = root(nodes[node])
            else:
                nodes[node] = index
    result = defaultdict(list)
    for index,item in enumerate(items):
        result[root(index)].append(item)
    return result.values()


def main(dry_run=False):
    catalog = json.loads((DATA/'catalog.json').read_bytes())
    records = {r['id']:r for r in catalog if r['kind'] in ('national','expressway','provincial')}
    by_code = defaultdict(list)
    for record in records.values():
        if COAST.intersection(record['provinces']):
            by_code[record['code']].append(record)
    features = json.loads((DATA/'china.geojson').read_bytes())['features']
    provinces = {str(f['properties']['adcode']):shape(f['geometry']) for f in features}
    land = unary_union(list(provinces.values()))
    coastal_land = unary_union([shape(f['geometry']) for f in features if f['properties']['name'] in COAST])
    prepare(land)
    source = sqlite3.connect(DATA/'crossing-source.sqlite')
    metadata = dict(source.execute('SELECT key,value FROM metadata'))
    grouped = defaultdict(list)
    for ident,tags,relations,nodes,coordinates,outside in source.execute('SELECT * FROM ways'):
        tags,relations,nodes,coordinates = map(json.loads,(tags,relations,nodes,coordinates))
        codes = set(refs(tags.get('ref','')))
        codes.update(code for relation in relations for code in relation['codes'])
        item = {'way':ident,'tags':tags,'relations':relations,'nodes':nodes,'coordinates':coordinates,'outside':bool(outside)}
        for code in codes:
            for record in by_code[code]:
                if not membership_reasons(code,tags,relations,record['kind'],record.get('province')):
                    grouped[record['id']].append(item)
    source.close()
    restored = {}; rejected = Counter(); corridors = []; evidence = []
    for rid,items in grouped.items():
        record = records[rid]
        anchor_land = provinces[record['province']] if record['kind'] == 'provincial' else land
        parts = []; accepted = []
        for component in components(items):
            ok,reason = eligible_crossing(component,anchor_land,coastal_land)
            if not ok:
                if reason != 'no_offshore_structure':
                    rejected[reason] += 1
                continue
            for item in component:
                if not item['outside']:
                    continue
                geometry = LineString(item['coordinates'])
                outside = lines_only(geometry.difference(land))
                if not outside:
                    continue
                # Retain the complete source way so land-mask rounding cannot
                # leave a seam between a restored structure and its shore.
                parts.append(geometry);accepted.append(item)
                evidence.append({'type':'Feature','properties':{'id':rid,**{key:item[key] for key in ('way','tags','relations','nodes')}},'geometry':mapping(geometry)})
        if not parts:
            continue
        restored[rid] = {'ways':len(accepted),'wayIds':sorted({item['way'] for item in accepted}),
                         'addedVertices':sum(len(part.coords) for part in parts),
                         'names':sorted({value for item in accepted for value in
                                         (item['tags'].get('name:zh') or item['tags'].get('name',''),
                                          item['tags'].get('bridge:name:zh') or item['tags'].get('bridge:name',''),
                                          item['tags'].get('tunnel:name:zh') or item['tags'].get('tunnel:name','')) if value}),
                         'bounds':list(unary_union(parts).bounds)}
        props = {'id':rid,'code':record['code'],'kind':record['kind']}
        corridors.append({'type':'Feature','properties':{'id':rid,'sourceWays':restored[rid]['wayIds']},
                          'geometry':mapping(unary_union(parts).buffer(.00003))})
        if dry_run:
            continue
        path = DATA/'routes'/f'{rid}.geojson'
        original = json.loads(path.read_bytes())
        geometry = unary_union([shape(f['geometry']) for f in original['features']]+parts)
        lines = lines_only(geometry)
        geometry = {'type':'MultiLineString','coordinates':[[[round(x,7),round(y,7)] for x,y in line.coords] for line in lines]}
        save(path,{'type':'FeatureCollection','features':[{'type':'Feature','properties':props,'geometry':geometry}]})
        record['bounds'] = list(shape(geometry).bounds)
        record['segmentCount'] = len(lines)
        record['coastalCrossings'] = {**restored[rid],'sourceSnapshot':metadata['snapshot'],
                                     'method':'原始桥隧链两端锚定中国陆地；仅恢复有证据的实际坐标，不跨缺口补线'}
        record['aliases'] = list(dict.fromkeys(record['aliases']+restored[rid]['names']))
        # Preserve unsimplified crossing vertices in the original overview.
        restored[rid]['outsideGeometry'] = {'type':'MultiLineString','coordinates':[[[round(x,7),round(y,7)] for x,y in line.coords] for line in parts]}
    report = {'generatedAt':datetime.now(timezone.utc).isoformat(),'sourceSnapshot':metadata['snapshot'],
              'routesRestored':len(restored),'routes':restored,'rejectedComponents':dict(rejected),
              'method':'真实桥隧占离岸段至少80%；整个桥隧链的所有端点在中国陆地，且位于沿海陆地邻域；跨海原始坐标单独保留，不扩大行政国界，不添加连接直线。'}
    print(json.dumps({'routesRestored':len(restored),'routes':list(restored),
                      'rejectedComponents':dict(rejected),'sourceSnapshot':metadata['snapshot']},ensure_ascii=True),flush=True)
    if dry_run:
        save(DATA/'coastal-crossings-preview.json',report)
        return
    overview = json.loads((DATA/'overview.geojson').read_bytes())
    for rid,details in restored.items():
        for feature in overview['features']:
            if feature['properties']['id'] == rid:
                combined = unary_union([shape(feature['geometry']),shape(details['outsideGeometry'])])
                feature['geometry'] = mapping(combined)
                break
        details.pop('outsideGeometry',None)
    save(DATA/'catalog.json',catalog)
    save(DATA/'overview.geojson',overview)
    save(DATA/'transport-corridors.geojson',{'type':'FeatureCollection','features':corridors})
    save(DATA/'coastal-crossing-evidence.geojson',{'type':'FeatureCollection','features':evidence})
    save(DATA/'coastal-crossings-report.json',report)
    health = json.loads((DATA/'report.json').read_bytes())
    health['coastalCrossings'] = {key:report[key] for key in ('generatedAt','sourceSnapshot','routesRestored','method')}
    save(DATA/'report.json',health)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run',action='store_true')
    main(parser.parse_args().dry_run)
