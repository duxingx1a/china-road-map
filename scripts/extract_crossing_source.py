"""Retain unclipped numbered bridge/tunnel evidence for Chinese coastal crossings."""
from collections import defaultdict
from pathlib import Path
import json
import sqlite3
import osmium
from shapely.geometry import LineString, shape
from shapely.ops import unary_union
from shapely import prepare
from prepare_data import refs
from audit_routes import ALLOWED

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'
COAST = {'辽宁省','河北省','天津市','山东省','江苏省','上海市','浙江省','福建省',
         '广东省','广西壮族自治区','海南省','香港特别行政区','澳门特别行政区','台湾省'}


def structural(tags):
    return any(tags.get(key, 'no') not in ('no', '', 'false', '0') for key in ('bridge','tunnel'))


def main():
    sources = []
    for path in (DATA/'raw/railways-source.osm.pbf',DATA/'raw/china.osm.pbf'):
        if path.exists():
            reader = osmium.io.Reader(str(path))
            sources.append((reader.header().get('osmosis_replication_timestamp'),path))
            reader.close()
    if not sources:
        raise SystemExit('Download a China OSM PBF before extracting crossings.')
    source = max(sources,key=lambda pair:pair[0])[1]
    catalog = json.loads((DATA/'catalog.json').read_bytes())
    codes = {r['code'] for r in catalog if r['kind'] in ('national','expressway','provincial')
             and COAST.intersection(r['provinces'])}
    members = defaultdict(list)
    relations = osmium.FileProcessor(str(source), osmium.osm.RELATION).with_filter(osmium.filter.TagFilter(('route','road')))
    for relation in relations:
        tags = dict(relation.tags)
        assigned = refs(tags.get('ref',''))
        if not codes.intersection(assigned):
            continue
        props = {'id':relation.id,'codes':assigned,'name':tags.get('name',''),'network':tags.get('network','')}
        for member in relation.members:
            if member.type == 'w':
                members[member.ref].append({**props,'role':member.role})
    print('Coastal route codes:',len(codes),'relation member ways:',len(members),flush=True)
    required = set()
    ways = {}
    for way in osmium.FileProcessor(str(source),osmium.osm.WAY).with_filter(osmium.filter.KeyFilter('highway')):
        tags = dict(way.tags)
        if tags.get('highway') not in ALLOWED or tags.get('area') == 'yes':
            continue
        relations = members.get(way.id, [])
        assigned = set(refs(tags.get('ref','')))
        assigned.update(code for relation in relations for code in relation['codes'])
        if not codes.intersection(assigned):
            continue
        nodes = [node.ref for node in way.nodes]
        if len(nodes) < 2:
            continue
        ways[way.id] = (tags,relations,nodes)
        required.update(nodes)
    print('Candidate ways:',len(ways),'required nodes:',len(required),flush=True)
    coordinates = {}
    for node in osmium.FileProcessor(str(source),osmium.osm.NODE).with_filter(osmium.filter.IdFilter(required)):
        if node.location.valid():
            coordinates[node.id] = [node.location.lon,node.location.lat]
    print('Nodes read:',len(coordinates),flush=True)
    mask = unary_union([shape(f['geometry']) for f in json.loads((DATA/'china.geojson').read_bytes())['features']])
    prepare(mask)
    pending = DATA/'crossing-source.pending.sqlite'
    if pending.exists():
        pending.unlink()
    db = sqlite3.connect(pending)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE ways(id INTEGER PRIMARY KEY,tags TEXT,relations TEXT,nodes TEXT,coordinates TEXT,outside INTEGER); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);')
    count = outside = missing = 0
    for ident,(tags,relations,nodes) in ways.items():
        if any(node not in coordinates for node in nodes):
            missing += 1
            continue
        points = [coordinates[node] for node in nodes]
        off_land = not mask.covers(LineString(points))
        if not off_land and not structural(tags):
            continue
        db.execute('INSERT INTO ways VALUES(?,?,?,?,?,?)',
                   (ident,json.dumps(tags,ensure_ascii=False),json.dumps(relations,ensure_ascii=False),
                    json.dumps(nodes),json.dumps(points,separators=(',',':')),int(off_land)))
        count += 1
        outside += int(off_land)
    reader = osmium.io.Reader(str(source))
    snapshot = reader.header().get('osmosis_replication_timestamp')
    reader.close()
    db.execute('INSERT INTO metadata VALUES(?,?)',('snapshot',snapshot))
    db.execute('INSERT INTO metadata VALUES(?,?)',('source',source.name))
    db.commit()
    assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    db.close()
    pending.replace(DATA/'crossing-source.sqlite')
    print(json.dumps({'retainedWays':count,'outsideLandWays':outside,'missingNodes':missing,'snapshot':snapshot}),flush=True)


if __name__ == '__main__':
    main()
