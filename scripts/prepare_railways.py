"""Build separate named railway lines from physical tracks, never train services."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import osmium
from shapely.geometry import LineString, shape, mapping
from shapely.ops import unary_union, linemerge
from shapely.strtree import STRtree
from prepare_data import lines_only, save

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
HSR=re.compile(r'高铁|高速(?:铁路|线|$)|客运专线|客专')
LOCAL=re.compile(r'联络|联线|疏解|专用|货场|编组|动车所|动车段|动车走行|车辆段|机务|到达线|出发线|进站线|出站线|轨道交通|地铁|轻轨|磁浮|\d+号线|(?:特大桥|大桥|隧道)$')
TRAIN_SERVICE=re.compile(r'列车|车次|^[GDCZTKLYS]\s*\d{1,5}(?!\d)|^\d{3,5}(?:次|\s)',re.I)

def clean_name(name):
    name=re.sub(r'[（(][^()（）]*[)）]','',name).strip()
    name=re.sub(r'(上行|下行|上下行|正线)$','',name).strip()
    if not re.search(r'[\u4e00-\u9fff]',name) or LOCAL.search(name) or TRAIN_SERVICE.search(name):return ''
    if re.fullmatch(r'(?:南|北|东|西)?(?:环线|支线|正线|铁路|高铁|高速铁路|货运线|机场线|机场铁路)',name):return ''
    return name

def canonical(name,kind):
    name=clean_name(name)
    if not name:return ''
    if kind=='highspeed':return re.sub(r'高速(?:铁路|线)?$','高铁',name)
    return re.sub(r'铁路$','线',name)

def rail_kind(name,tags,track_tags):
    tracks=list(track_tags)
    high=sum(t.get('highspeed')=='yes' for t in tracks)
    return 'highspeed' if tags.get('highspeed')=='yes' or HSR.search(name) or (tracks and high/len(tracks)>=.6) else 'railway'

def main():
    out=DATA/'railways';out.mkdir(exist_ok=True)
    conn=sqlite3.connect(DATA/'railway-evidence.sqlite')
    ways={wid:{'tags':json.loads(tags),'coordinates':coords} for wid,tags,coords in conn.execute('SELECT id,tags,coordinates FROM ways WHERE coordinates IS NOT NULL')}
    relations={rid:{'tags':json.loads(tags),'members':json.loads(members)} for rid,tags,members in conn.execute('SELECT id,tags,members FROM relations')}
    def members(rid,seen=None):
        seen=set() if seen is None else seen
        if rid in seen:return set()
        seen.add(rid);result=set()
        for typ,ref,role in relations[rid]['members']:
            if role in ('platform','stop','yard','depot'):continue
            if typ=='w' and ref in ways:result.add(ref)
            elif typ=='r' and ref in relations:result.update(members(ref,seen))
        return result
    groups=defaultdict(set);aliases=defaultdict(Counter);metadata=defaultdict(list);assigned=set();stats=Counter()
    for rid,rel in relations.items():
        tags=rel['tags'];name=tags.get('name:zh') or tags.get('name','')
        name=clean_name(name)
        if not name or tags.get('state') in ('proposed','construction'):stats['excludedRelations']+=1;continue
        ids=members(rid)
        if not ids:continue
        kind=rail_kind(name,tags,(ways[wid]['tags'] for wid in ids))
        name=canonical(name,kind);key=(kind,name)
        accepted={wid for wid in ids if not (kind=='highspeed' and ways[wid]['tags'].get('highspeed')=='no')}
        if not accepted:continue
        groups[key].update(accepted);assigned.update(accepted)
        metadata[key].append({'id':rid,'memberWayCount':len(accepted),**tags});aliases[key][tags.get('name','')]+=len(accepted)
        for alias in (tags.get('alt_name',''),tags.get('short_name','')):
            for value in alias.split(';'):
                if value:aliases[key][value]+=1
    for wid,way in ways.items():
        if wid in assigned:continue
        tags=way['tags'];name=clean_name(tags.get('name:zh') or tags.get('name',''))
        if not name:continue
        kind=rail_kind(name,tags,[tags])
        if kind=='highspeed' and tags.get('highspeed')=='no':continue
        key=(kind,canonical(name,kind));groups[key].add(wid);aliases[key][name]+=1
    borders=json.loads((DATA/'china.geojson').read_bytes())['features']
    provinces=[shape(f['geometry']) for f in borders];names=[f['properties']['name'] for f in borders]
    country=unary_union(provinces);tree=STRtree(provinces)
    records=[];overview=[];review=[]
    print('Named railway candidates:',len(groups),flush=True)
    for index,((kind,name),ids) in enumerate(sorted(groups.items())):
        parts=[];accepted=[];visited=set()
        for wid in ids:
            coords=json.loads(ways[wid]['coordinates']);geo=LineString(coords)
            if not country.covers(geo):geo=geo.intersection(country)
            clipped=lines_only(geo)
            if not clipped:stats['outsideWays']+=1;continue
            parts.extend(clipped);accepted.append(wid)
            visited.update(names[i] for i in tree.query(geo,predicate='intersects'))
        if not parts:continue
        geo=unary_union(parts)
        if geo.geom_type=='MultiLineString':geo=linemerge(geo)
        lines=lines_only(geo)
        if sum(line.length for line in lines)<.01:stats['shortCandidates']+=1;continue
        ident=kind+'-'+hashlib.sha1(name.encode()).hexdigest()[:12]
        rels=sorted(metadata[(kind,name)],key=lambda r:(r.get('memberWayCount',0),bool(r.get('from') and r.get('to'))),reverse=True)
        origin=next((r['from'] for r in rels if r.get('from')),'');dest=next((r['to'] for r in rels if r.get('to')),'')
        refs=sorted({r['ref'] for r in rels if r.get('ref')})
        props={'id':ident,'kind':kind,'code':refs[0] if len(refs)==1 else name}
        route={'type':'FeatureCollection','features':[{'type':'Feature','properties':props,'geometry':{'type':'MultiLineString','coordinates':[[[round(x,7),round(y,7)] for x,y in line.coords] for line in lines]}}]}
        save(out/'routes'/f'{ident}.geojson',route)
        extra=[name]
        if kind=='railway' and name.endswith('线'):extra.append(name[:-1]+'铁路')
        if kind=='highspeed' and name.endswith('高铁'):extra.append(name[:-2]+'高速铁路')
        record={**props,'name':name,'from':origin,'to':dest,'province':'','aliases':list(dict.fromkeys(extra+[n for n,_ in aliases[(kind,name)].most_common(12) if n]+refs)), 'provinces':sorted(visited),'railwayRef':';'.join(refs),'hasGeometry':True,'bounds':list(geo.bounds),'segmentCount':len(lines),'wayCount':len(accepted),'catalogSource':'OpenStreetMap 铁路基础设施关系与轨道','geometrySource':'OpenStreetMap / Geofabrik','geometryStatus':'已标记的运营铁路主轨；不代表全线完整性或官方通行认证','sourceRelations':[r['id'] for r in rels],'classificationEvidence':'线路名称、高速属性和所属轨道标签；不按车次或仅按时速分类'}
        records.append(record);overview.extend(route['features'])
        if index%100==0:print('Prepared',index,'/',len(groups),name.encode('unicode_escape').decode(),flush=True)
    assert len(records)==len({r['id'] for r in records})
    reader=osmium.io.Reader(str(DATA/'raw/railways-source.osm.pbf'));snapshot=reader.header().get('osmosis_replication_timestamp');reader.close()
    report={'generatedAt':datetime.now(timezone.utc).isoformat(),'osmSnapshot':snapshot,'sourceMD5':(DATA/'raw/railways-source.osm.pbf.md5').read_text().strip(),'routes':len(records),'categories':dict(Counter(r['kind'] for r in records)),'stats':dict(stats),'source':'https://download.geofabrik.de/asia/china.html','license':'ODbL 1.0; © OpenStreetMap contributors','scope':'已映射、有名称的铁路主轨与基础设施线路；排除地铁、有轨电车、废弃/在建轨道和站内服务线。普速分类可能含客货共线与货运干线；不承诺全国完整覆盖。'}
    save(out/'catalog.json',records);save(out/'overview.geojson',{'type':'FeatureCollection','features':overview});save(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=True,indent=2),flush=True)

if __name__=='__main__':main()
