"""Audit every extracted assignment. Quarantine ambiguity; never invent connecting lines."""
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from shapely.geometry import LineString, MultiLineString, mapping, shape
from shapely.ops import unary_union, linemerge
from shapely.affinity import scale
from shapely import STRtree
from prepare_data import DATA, refs, lines_only, save

RULE_VERSION = 2
ALLOWED = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street', 'road'}
PROVINCE_NETWORKS = dict(zip(('110000','120000','130000','140000','150000','210000','220000','230000','310000','320000','330000','340000','350000','360000','370000','410000','420000','430000','440000','450000','460000','500000','510000','520000','530000','540000','610000','620000','630000','640000','650000'),('BJ','TJ','HE','SX','NM','LN','JL','HL','SH','JS','ZJ','AH','FJ','JX','SD','HA','HB','HN','GD','GX','HI','CQ','SC','GZ','YN','XZ','SN','GS','QH','NX','XJ')))


def membership_reasons(code, tags, relations, kind=None, province=None):
    reasons = []
    if tags.get('highway') not in ALLOWED or tags.get('area') == 'yes':
        reasons.append('non_main_road')
    if tags.get('access') in ('private', 'no'):
        reasons.append('restricted_access')
    if kind == 'provincial' and tags.get('highway') != 'motorway' and not any('expressway' in r.get('network','').lower() or '高速' in r.get('name','') for r in relations if code in r['codes']):
        reasons.append('expressway_class_unverified')
    explicit = set(refs(tags.get('ref', '')))
    name = tags.get('name:zh') or tags.get('name','')
    number = re.escape(code[1:])
    old_pattern = r'(?:旧|老)\s*[GS]?'+number+r'\s*(?:国道|线)|[GS]?'+number+r'\s*(?:国道)?\s*(?:老|旧)线'
    old_name = re.search(old_pattern,name,re.I)
    matching = [r for r in relations if code in r['codes']]
    if kind == 'provincial' and province in PROVINCE_NETWORKS:
        networks = [r.get('network','') for r in matching] + [tags.get('network','')]
        owners = [m.group(1).upper() for network in networks if (m:=re.match(r'CN:([A-Z]{2}):expressway',network,re.I))]
        if owners and PROVINCE_NETWORKS[province] not in owners:
            reasons.append('provincial_network_conflict')
    only_old_relations = bool(matching) and all(re.search(old_pattern,r.get('name',''),re.I) for r in matching)
    if (code in refs(tags.get('old_ref','')) and code not in explicit) or old_name or only_old_relations or re.search(r'旧|曾用|\bold\b',tags.get('ref',''),re.I):
        reasons.append('historical_alignment')
    if explicit and code not in explicit:
        reasons.append('relation_ref_conflict')
    def family(value):
        return 'national' if value.startswith('G') and len(value)==4 else value[0]
    if code in explicit and not matching and any(
            any(family(other)==family(code) and other!=code for other in r['codes'])
            for r in relations):
        reasons.append('relation_ref_conflict')
    if code not in explicit and matching and all(r.get('role') not in ('', 'forward', 'backward') for r in matching):
        reasons.append('relation_non_route_role')
    return reasons


def remote_components(geometries, route_names=None):
    """300 m near-connection allowance; flag small components >20 km from large ones.

    Equirectangular metres with route-centre latitude, for screening only. This is
    a review flag, not evidence of an incorrect road number or an exact distance.
    """
    if len(geometries) < 2:
        return {}, 1
    lat = sum(g.centroid.y for g in geometries) / len(geometries)
    projected = [scale(g, xfact=111320*math.cos(math.radians(lat)), yfact=111320, origin=(0,0)).simplify(20) for g in geometries]
    tree = STRtree(projected)
    parents = list(range(len(projected)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for i, g in enumerate(projected):
        for j in tree.query(g, predicate='dwithin', distance=300):
            parents[root(int(j))] = root(i)
    groups = {}
    for i in range(len(projected)):
        groups.setdefault(root(i), []).append(i)
    sizes = {key: sum(projected[i].length for i in values) for key, values in groups.items()}
    threshold = max(20000, sum(sizes.values())*.01)
    anchors = {k for k, length in sizes.items() if length >= threshold}
    if not anchors:
        anchors = {max(sizes, key=sizes.get)}
    anchor_parts = [projected[i] for k in anchors for i in groups[k]]
    anchor_tree = STRtree(anchor_parts)
    suspects = {}
    dominant = max(sizes, key=sizes.get)
    dominant_names = Counter(route_names[i] for i in groups[dominant] if route_names and route_names[i])
    principal_name = dominant_names.most_common(1)[0][0] if dominant_names else ''
    dominant_parts = [projected[i] for i in groups[dominant]]
    dominant_tree = STRtree(dominant_parts)
    for key, indices in groups.items():
        names = Counter(route_names[i] for i in indices if route_names and route_names[i])
        other_name = names.most_common(1)[0][0] if names else ''
        if key != dominant and principal_name and other_name and principal_name != other_name and sizes[dominant] > sizes[key]*2:
            gap = min(projected[i].distance(dominant_parts[int(dominant_tree.nearest(projected[i]))]) for i in indices)
            if gap > 50000:
                for i in indices:
                    suspects[i] = {'distanceKm':round(gap/1000,1),'reason':'disconnected_name_conflict','principalName':principal_name,'otherName':other_name}
                continue
        if key in anchors:
            continue
        distance = min(projected[i].distance(anchor_parts[int(anchor_tree.nearest(projected[i]))]) for i in indices)
        if distance > 20000:
            for i in indices:
                suspects[i] = {'distanceKm':round(distance/1000, 1),'reason':'remote_component'}
    return suspects, len(groups)


def merged_geometry(coordinates):
    geom = unary_union([LineString(line) for lines in coordinates for line in lines])
    if geom.geom_type == 'MultiLineString':
        geom = linemerge(geom)
    lines = lines_only(geom)
    return {'type':'MultiLineString', 'coordinates':[[[round(x,6), round(y,6)] for x,y in line.coords] for line in lines]}


def main():
    db = sqlite3.connect(DATA / 'road-evidence.sqlite')
    catalog = json.loads((DATA / 'catalog.json').read_text(encoding='utf8'))
    original_overview = json.loads((DATA / 'overview.geojson').read_text(encoding='utf8'))
    overview_by_id = {f['properties']['id']:f for f in original_overview['features']}
    mask = unary_union([shape(f['geometry']) for f in json.loads((DATA / 'china.geojson').read_text(encoding='utf8'))['features']])
    audit_dir = DATA / 'quality'
    audit_dir.mkdir(exist_ok=True)
    totals = Counter()
    reason_counts = Counter()
    reports = []
    overview = []
    for count, record in enumerate(catalog):
        if record['kind'] in ('highspeed','railway'):
            continue
        rid = record['id']
        rows = db.execute('SELECT way,tags,relations,coordinates,provinces FROM evidence WHERE route=? ORDER BY way', (rid,)).fetchall()
        if not rows:
            reports.append({'id':rid,'sourceWays':0,'acceptedWays':0,'reviewWays':0,'excludedWays':0,'componentCount':0})
            continue
        items = []
        for way,tags,relations,coords,provinces in rows:
            tags,relations,coords,provinces = json.loads(tags),json.loads(relations),json.loads(coords),json.loads(provinces)
            items.append({'way':way,'tags':tags,'relations':relations,'coordinates':coords,'provinces':provinces,
                          'reasons':membership_reasons(record['code'],tags,relations,record['kind'],record.get('province'))})
        eligible = [i for i,item in enumerate(items) if not item['reasons']]
        geoms = [MultiLineString(items[i]['coordinates']) for i in eligible]
        route_names = []
        for i in eligible:
            name=items[i]['tags'].get('name:zh') or items[i]['tags'].get('name','')
            route_names.append(name if '高速' in name else '')
        remote, components = remote_components(geoms,route_names if record['kind']=='provincial' else None) if geoms else ({},0)
        for j,finding in remote.items():
            items[eligible[j]]['reasons'].append(finding['reason'])
            items[eligible[j]].update({k:v for k,v in finding.items() if k!='reason'})
        accepted = [item for item in items if not item['reasons']]
        uncertain = [item for item in items if item['reasons'] and 'non_main_road' not in item['reasons']]
        excluded = [item for item in items if 'non_main_road' in item['reasons']]
        detail = {'id':rid,'sourceWays':len(items),'acceptedWays':len(accepted),'reviewWays':len(uncertain),
                  'excludedWays':len(excluded),'componentCount':components,'reasons':dict(Counter(r for item in items for r in item['reasons']))}
        if uncertain:
            points = [p for item in uncertain for line in item['coordinates'] for p in line]
            detail['reviewBounds'] = [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
        details = []
        for item in uncertain+excluded:
            props = {k:item[k] for k in ('way','tags','relations','reasons')}
            props.update({'id':rid,'code':record['code'],'status':'excluded' if 'non_main_road' in item['reasons'] else 'review',
                          'distanceKm':item.get('distanceKm'),'principalName':item.get('principalName'),'otherName':item.get('otherName'), 'osmUrl':f'https://www.openstreetmap.org/way/{item["way"]}'})
            details.append({'type':'Feature','properties':props,'geometry':{'type':'MultiLineString','coordinates':item['coordinates']}})
        if details:
            save(audit_dir / f'{rid}.geojson', {'type':'FeatureCollection','features':details})
        record['quality'] = detail
        record['wayCount'] = len(accepted)
        record['hasGeometry'] = bool(accepted)
        aliases = Counter()
        for item in accepted:
            name = item['tags'].get('name:zh') or item['tags'].get('name')
            if name:
                aliases[name] += 1
            for rel in item['relations']:
                if record['code'] in rel['codes'] and rel['name']:
                    aliases[rel['name']] += 3
        record['aliases'] = [name for name,_ in aliases.most_common(12)]
        record['provinces'] = sorted({p for item in accepted for p in item['provinces']})
        path = DATA / 'routes' / f'{rid}.geojson'
        if uncertain or excluded:
            geom = merged_geometry([item['coordinates'] for item in accepted]) if accepted else {'type':'MultiLineString','coordinates':[]}
            if accepted:
                save(path, {'type':'FeatureCollection','features':[{'type':'Feature','properties':{'id':rid,'code':record['code'],'kind':record['kind']},'geometry':geom}]})
            elif path.exists():
                path.unlink()  # Original and evidence remain in the audit backup/database.
            if accepted:
                g = shape(geom)
                record['bounds'] = list(g.bounds)
                record['segmentCount'] = len(geom['coordinates'])
                simplified = g.simplify(.006, preserve_topology=False).intersection(mask)
                overview.append({'type':'Feature','properties':{'id':rid,'code':record['code'],'kind':record['kind']},'geometry':mapping(simplified)})
            else:
                record.pop('bounds',None)
                record['segmentCount'] = 0
        elif rid in overview_by_id:
            overview.append(overview_by_id[rid])
        record['geometryStatus'] = '通过自动归属筛查；全线完整性仍待核验' if accepted else '原始线位存在疑点；等待核实'
        totals.update({k:detail[k] for k in ('sourceWays','acceptedWays','reviewWays','excludedWays')})
        reason_counts.update(detail['reasons'])
        reports.append(detail)
        if count % 100 == 0:
            print('Audited',count,'/',len(catalog),dict(totals),flush=True)
    save(DATA / 'catalog.json',catalog)
    save(DATA / 'overview.geojson', {'type':'FeatureCollection','features':overview})
    report = {'generatedAt':datetime.now(timezone.utc).isoformat(),'ruleVersion':RULE_VERSION,
              'routesChecked':len(reports),'routesWithSource':sum(r['sourceWays']>0 for r in reports),
              'routesWithFindings':sum(bool(r['reviewWays']+r['excludedWays']) for r in reports),
              'totals':dict(totals),'reasons':dict(reason_counts),'routes':reports,
              'method':'全库逐路段检查highway/access/ref、路线关系编号、角色和省级网络归属；300米邻近连接筛查，小分量距主要分量超过20公里标记待核对；省级高速远距异名分量超过50公里且主分量长度超过其2倍时隔离。空间距离为近似筛查值。',
              'limitations':'自动检查不等于官方逐段认证。离散路段可能是缺段、改线或编号错误，待核对数据隔离保留，不按距离直接认定错误。'}
    save(DATA / 'quality-report.json',report)
    reason_names = {'non_main_road':'非干线道路/区域要素','restricted_access':'通行权限受限',
                    'relation_ref_conflict':'道路编号与路线关系冲突','relation_non_route_role':'非主线路角色',
                    'remote_component':'远距离孤立分量','expressway_class_unverified':'省道高速属性未确认','historical_alignment':'旧编号或旧线标记','provincial_network_conflict':'省级高速网络归属冲突','disconnected_name_conflict':'远距离异名路线被同号合并'}
    text = ['# 中国公路路线归属自动检查', '', f'生成时间：{report["generatedAt"]}', '',
            f'检查 {len(catalog)} 个编号，{report["routesWithSource"]} 个有原始线位；{report["routesWithFindings"]} 个编号发现需要排除或核对的路段。', '',
            f'原始归属记录 {totals["sourceWays"]:,}；保留 {totals["acceptedWays"]:,}；待核对 {totals["reviewWays"]:,}；排除非干线 {totals["excludedWays"]:,}。同一物理道路可能属于多个编号，因此这里计数的是道路与编号的归属记录。', '',
            '## 检查依据', '',report['method'], '',report['limitations'], '',
            '待核对路段不进入默认选中路线、全国概览或精细空间索引。可以在路线详情中以紫色虚线单独查看。排除的非干线要素仅保留在审计文件中。', '',
            '## 问题类别', '', '| 检查项 | 记录数 |','|---|---:|']
    text += [f'| {reason_names.get(k,k)} | {v:,} |' for k,v in reason_counts.most_common()]
    text += ['', '一条记录可能同时触发多种检查项，检查项数量不能直接相加。', '',
             '## G312 截图中的孤立路段', '',
             '额济纳附近原始 OSM way 132210568 的 ref=G312，但所在 relation 8085327 的 ref=G331、network=CN:national。旧提取器把自身编号和关系编号直接合并，导致此段进入 G312 和 G331。新版将冲突记录隔离，并保留原始标签与坐标供核对。', '',
             '这里证明了输入存在编号冲突；未据此修改 OSM，也没有把关系标签作为官方逐段认证。', '',
             '## 广西 S60 的跨省同号异名问题', '',
             '部分连贺高速道路自身标记 S60，其中部分路线关系网络标记 CN:GD:expressway。旧提取器按路段几何所在省份分组，导致进入广西 S60。新增规则检查省级网络归属，并隔离远距离异名分量。逐段依据保存在 quality/provincial-450000-S60.geojson，不把隔离段自动分配给另一个省份或编号。', '',
             '## 各路线检查结果', '', '| 编号/本地ID | 原始归属记录 | 保留 | 待核对 | 排除 |', '|---|---:|---:|---:|---:|']
    text += [f'| {r["id"]} | {r["sourceWays"]} | {r["acceptedWays"]} | {r["reviewWays"]} | {r["excludedWays"]} |' for r in reports]
    (DATA / 'quality-report.md').write_text('\n'.join(text)+'\n',encoding='utf8')
    health=json.loads((DATA / 'report.json').read_text(encoding='utf8'))
    health['withGeometry']=sum(r['hasGeometry'] for r in catalog)
    health['withoutGeometry']=len(catalog)-health['withGeometry']
    health['routeQuality']={k:report[k] for k in ('generatedAt','routesChecked','routesWithSource','routesWithFindings','totals','reasons','limitations')}
    health['sourcePbfRetained']=(DATA / 'raw/china.osm.pbf').exists()
    save(DATA / 'report.json',health)
    roads=sqlite3.connect(DATA / 'roads.sqlite')
    roads.executemany('UPDATE roads SET metadata=? WHERE id=?',[(json.dumps(r,ensure_ascii=False),r['id']) for r in catalog])
    roads.commit();roads.close();db.close()
    print(json.dumps({k:v for k,v in report.items() if k!='routes'},ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    main()
