"""Keep active named railway infrastructure, with source tags for offline review."""
from pathlib import Path
from collections import Counter
import json
import sqlite3
import osmium

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/raw/railways-source.osm.pbf'

def main():
    out=ROOT/'data/railway-evidence.sqlite'
    pending=out.with_suffix('.pending.sqlite')
    if pending.exists():pending.unlink()
    db=sqlite3.connect(pending)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE relations(id INTEGER PRIMARY KEY,tags TEXT,members TEXT); CREATE TABLE ways(id INTEGER PRIMARY KEY,tags TEXT,nodes TEXT,coordinates TEXT);')
    relations=osmium.FileProcessor(str(SOURCE),osmium.osm.RELATION).with_filter(osmium.filter.TagFilter(('route','railway'),('route','tracks')))
    n=0
    for r in relations:
        db.execute('INSERT INTO relations VALUES(?,?,?)',(r.id,json.dumps(dict(r.tags),ensure_ascii=False),json.dumps([(m.type,m.ref,m.role) for m in r.members])))
        n+=1
    db.commit();print('Infrastructure relations:',n,flush=True)
    required=set();stats=Counter()
    ways=osmium.FileProcessor(str(SOURCE),osmium.osm.WAY).with_filter(osmium.filter.TagFilter(('railway','rail')))
    for w in ways:
        tags=dict(w.tags)
        if tags.get('service') in ('spur','siding','yard','crossover') or tags.get('usage') in ('industrial','tourism') or tags.get('disused')=='yes' or tags.get('abandoned')=='yes':
            stats['excludedServiceTracks']+=1;continue
        nodes=[n.ref for n in w.nodes]
        if len(nodes)<2:continue
        db.execute('INSERT INTO ways VALUES(?,?,?,NULL)',(w.id,json.dumps(tags,ensure_ascii=False),json.dumps(nodes)))
        required.update(nodes);stats['mainlineWays']+=1
    db.commit();print('Active ways:',stats['mainlineWays'],'required nodes:',len(required),flush=True)
    coordinates={}
    nodes=osmium.FileProcessor(str(SOURCE),osmium.osm.NODE).with_filter(osmium.filter.IdFilter(required))
    for node in nodes:
        if node.location.valid():coordinates[node.id]=[node.location.lon,node.location.lat]
        if len(coordinates)%500000==0:print('Read railway nodes:',len(coordinates),flush=True)
    for ident,node_ids in db.execute('SELECT id,nodes FROM ways').fetchall():
        ids=json.loads(node_ids)
        if any(n not in coordinates for n in ids):stats['missingNodeWays']+=1;continue
        db.execute('UPDATE ways SET coordinates=? WHERE id=?',(json.dumps([coordinates[n] for n in ids],separators=(',',':')),ident))
    db.commit();assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok';db.close();pending.replace(out)
    print(json.dumps(dict(stats)),flush=True)

if __name__=='__main__':main()
