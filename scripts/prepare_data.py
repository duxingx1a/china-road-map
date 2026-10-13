"""Build only Chinese national roads and expressways; no foreign POIs or streets."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
import time
import osmium
from pypdf import PdfReader
from shapely.geometry import shape, mapping, LineString, MultiLineString
from shapely.ops import unary_union, linemerge
from shapely.strtree import STRtree
from shapely.validation import make_valid
from shapely import prepare as prepare_geometry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RAW = DATA / 'raw'
CODE = re.compile(r'(?<![A-Z0-9])([GS])\s*([0-9]{1,6})(?![0-9])', re.I)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    pending.replace(path)


def refs(value):
    return sorted({a.upper() + b for a, b in CODE.findall(value or '')})


def gcj_to_wgs(lon, lat):
    # DataV boundaries use GCJ-02. Invert the standard offset approximately.
    if not (72.004 < lon < 137.8347 and 0.8293 < lat < 55.8271):
        return [lon, lat]
    x, y = lon - 105, lat - 35
    dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlat += (20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi))*2/3
    dlat += (160*math.sin(y/12*math.pi)+320*math.sin(y*math.pi/30))*2/3
    dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlon += (20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi))*2/3
    dlon += (150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    rad = lat/180*math.pi
    magic = 1 - .00669342162296594323*math.sin(rad)**2
    dlat = dlat*180 / ((6378245*(1-.00669342162296594323))/(magic*math.sqrt(magic))*math.pi)
    dlon = dlon*180 / (6378245/math.sqrt(magic)*math.cos(rad)*math.pi)
    return [round(lon-dlon, 6), round(lat-dlat, 6)]


def convert_coords(coords):
    if coords and isinstance(coords[0], (float, int)):
        return gcj_to_wgs(*coords[:2])
    return [convert_coords(c) for c in coords]


def boundaries():
    src = json.loads((RAW / 'china-provinces.json').read_text(encoding='utf-8'))
    features, shapes, names, ids = [], [], [], []
    maritime = []
    for f in src['features']:
        props = f['properties']
        geo = {'type': f['geometry']['type'], 'coordinates': convert_coords(f['geometry']['coordinates'])}
        if not props.get('name'):
            maritime.append({'type': 'Feature', 'properties': {}, 'geometry': geo})
            continue
        geom = make_valid(shape(geo))
        name, code = props['name'], str(props['adcode'])
        center = props.get('centroid') or props.get('center') or list(geom.representative_point().coords)[0]
        label_point = geom.centroid if geom.contains(geom.centroid) else geom.representative_point()
        features.append({'type': 'Feature', 'properties': {'name': name, 'adcode': code, 'center': gcj_to_wgs(*center), 'labelCenter': [round(label_point.x,6),round(label_point.y,6)]}, 'geometry': mapping(geom)})
        shapes.append(geom)
        names.append(name)
        ids.append(code)
    save(DATA / 'china.geojson', {'type': 'FeatureCollection', 'features': features})
    save(DATA / 'maritime.geojson', {'type': 'FeatureCollection', 'features': maritime})
    mask = unary_union(shapes)
    prepare_geometry(mask)
    for province in shapes:
        prepare_geometry(province)
    return mask, shapes, names, ids


def catalog_from_plan():
    result = {}
    reader = PdfReader(RAW / 'national-road-plan-2022.pdf')
    national = False
    for page in reader.pages[17:]:
        text = page.extract_text()
        if '普通国道' in text:
            national = True
        for match in re.finditer(r'\b(G\d{1,6})\s+([^\s]+)', text):
            code, title = match.groups()
            if title in ('路线', '编号') or not re.search('[\u4e00-\u9fff]', title):
                continue
            kind = 'national' if national else 'expressway'
            title = title.replace('－', '—').replace('-', '—')
            ends = title.split('—')
            result[(kind, code, '')] = {
                'code': code, 'name': title, 'from': ends[0], 'to': ends[-1],
                'kind': kind, 'province': '', 'catalogSource': '国家公路网规划（2022年）',
                'planOnly': True,
            }
    return result


class Relations(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.members = defaultdict(list)
        self.count = 0

    def relation(self, r):
        if r.tags.get('route') != 'road':
            return
        codes = refs(r.tags.get('ref', ''))
        if not codes:
            return
        self.count += 1
        item = {'id': r.id, 'codes': codes, 'name': r.tags.get('name', ''), 'network': r.tags.get('network', '')}
        for m in r.members:
            if m.type == 'w':
                self.members[m.ref].append({**item, 'role': m.role})


def lines_only(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == 'LineString':
        return [geom]
    if hasattr(geom, 'geoms'):
        return [line for child in geom.geoms for line in lines_only(child)]
    return []


class Roads(osmium.SimpleHandler):
    def __init__(self, relation_members, catalog, mask, provinces, names, ids):
        super().__init__()
        self.members, self.catalog, self.mask = relation_members, catalog, mask
        self.provinces, self.names, self.ids = provinces, names, ids
        self.tree = STRtree(provinces)
        self.groups = defaultdict(dict)
        self.aliases = defaultdict(Counter)
        self.visited = defaultdict(set)
        self.stats = Counter()

    def way(self, w):
        hw = w.tags.get('highway', '')
        if not hw:
            return
        # Exclude junction ramps, planned roads, and non-public features.
        if hw in ('construction', 'proposed') or hw.endswith('_link'):
            return
        relations = self.members.get(w.id, [])
        codes = set(refs(w.tags.get('ref', '')))
        for rel in relations:
            codes.update(rel['codes'])
        codes = {c for c in codes if c.startswith('G') or (c.startswith('S') and hw in ('motorway', 'trunk'))}
        if not codes:
            return
        try:
            coords = [(n.lon, n.lat) for n in w.nodes]
            if len(coords) < 2:
                return
            original = LineString(coords)
            # Clip every selected line to China. The source's border buffer is discarded.
            geo = original if self.mask.covers(original) else original.intersection(self.mask)
            parts = lines_only(geo)
            if not parts:
                self.stats['outsideChinaExcluded'] += 1
                return
            province_indices = list(self.tree.query(geo, predicate='intersects'))
            if not province_indices:
                return
            road_name = w.tags.get('name:zh', '') or w.tags.get('name', '')
            for code in codes:
                if code.startswith('S'):
                    # S routes with ordinary three-digit province road numbers are not expressways.
                    if hw != 'motorway' and len(code) > 3:
                        continue
                    for idx in province_indices:
                        clipped = lines_only(geo.intersection(self.provinces[idx]))
                        if not clipped:
                            continue
                        key = ('provincial', code, self.ids[idx])
                        self.add(key, w.id, clipped, road_name, relations, [idx])
                else:
                    if ('national', code, '') in self.catalog:
                        kind = 'national'
                    elif ('expressway', code, '') in self.catalog:
                        kind = 'expressway'
                    else:
                        kind = 'expressway' if hw == 'motorway' or len(code) != 4 else 'national'
                    self.add((kind, code, ''), w.id, parts, road_name, relations, province_indices)
            self.stats['selectedWays'] += 1
            if self.stats['selectedWays'] % 20000 == 0:
                print('Extracted', self.stats['selectedWays'], 'road ways;', len(self.groups), 'routes', flush=True)
        except osmium.InvalidLocationError:
            self.stats['invalidLocations'] += 1

    def add(self, key, way_id, parts, name, relations, provinces):
        self.groups[key][way_id] = parts
        if name:
            self.aliases[key][name] += 1
        for rel in relations:
            if key[1] in rel['codes'] and rel['name']:
                self.aliases[key][rel['name']] += 3
        self.visited[key].update(self.names[idx] for idx in provinces)


def prepare():
    start = time.monotonic()
    mask, provinces, names, ids = boundaries()
    catalog = catalog_from_plan()
    print('Official plan:', Counter(k[0] for k in catalog), flush=True)
    relations = Relations()
    relations.apply_file(str(RAW / 'china.osm.pbf'))
    print('Road route relations:', relations.count, 'member ways:', len(relations.members), flush=True)
    roads = Roads(relations.members, catalog, mask, provinces, names, ids)
    roads.apply_file(str(RAW / 'china.osm.pbf'), locations=True, idx='sparse_mem_array')
    records = []
    overview = []
    all_keys = set(catalog) | set(roads.groups)
    for number, key in enumerate(sorted(all_keys)):
        kind, code, province = key
        route_id = f'{kind}-{province+"-" if province else ""}{code}'
        info = dict(catalog.get(key, {'code': code, 'name': '', 'from': '', 'to': '', 'kind': kind, 'province': province, 'catalogSource': 'OpenStreetMap 道路编号', 'planOnly': False}))
        aliases = [n for n, _ in roads.aliases[key].most_common(12)]
        if not info['name']:
            info['name'] = next((n for n in aliases if re.search('[\u4e00-\u9fff]', n)), '') or code
        info.update({'id': route_id, 'aliases': aliases, 'provinces': sorted(roads.visited[key]), 'hasGeometry': key in roads.groups})
        if province:
            info['provinceName'] = names[ids.index(province)]
        if key in roads.groups:
            parts = [p for value in roads.groups[key].values() for p in value]
            merged = unary_union(parts)
            if merged.geom_type == 'MultiLineString':
                merged = linemerge(merged)
            lines = lines_only(merged)
            coordinates = [[[round(x, 6), round(y, 6)] for x, y in line.coords] for line in lines]
            info['bounds'] = list(merged.bounds)
            info['segmentCount'] = len(lines)
            info['wayCount'] = len(roads.groups[key])
            info['geometryStatus'] = '有实际路段；全线完整性待核对'
            info['geometrySource'] = 'OpenStreetMap / Geofabrik'
            route_geo = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {'id': route_id, 'code': code, 'kind': kind}, 'geometry': {'type': 'MultiLineString', 'coordinates': coordinates}}]}
            save(DATA / 'routes' / f'{route_id}.geojson', route_geo)
            # Overview is simplified for fast nationwide display; selection loads precise geometry.
            simple = merged.simplify(.006, preserve_topology=False).intersection(mask)
            overview.append({'type': 'Feature', 'properties': {'id': route_id, 'kind': kind, 'code': code}, 'geometry': mapping(simple)})
        else:
            info['geometryStatus'] = '目录中有编号；尚未取得实际线位'
            info['segmentCount'] = 0
        records.append(info)
        if number % 100 == 0:
            print('Saved routes', number, '/', len(all_keys), flush=True)
    save(DATA / 'overview.geojson', {'type': 'FeatureCollection', 'features': overview})
    save(DATA / 'catalog.json', records)
    generated = datetime.now(timezone.utc).isoformat()
    reader = osmium.io.Reader(str(RAW / 'china.osm.pbf'))
    header = reader.header()
    snapshot = header.get('osmosis_replication_timestamp')
    reader.close()
    report = {
        'generatedAt': generated, 'osmSnapshot': snapshot,
        'routes': len(records), 'withGeometry': sum(r['hasGeometry'] for r in records),
        'withoutGeometry': sum(not r['hasGeometry'] for r in records),
        'categories': dict(Counter(r['kind'] for r in records)),
        'officialCatalogCounts': dict(Counter(k[0] for k in catalog)),
        'provinceCount': len(names), 'processing': dict(roads.stats),
        'sourceFileBytes': (RAW / 'china.osm.pbf').stat().st_size,
        'sourceFileMD5': (RAW / 'china.osm.pbf.md5').read_text().strip(),
        'completeness': '国家目录按2022年规划整理；省级高速为OSM已识别编号，未宣称全国省级目录完整。实际线位不代表全线已建成或连续。',
        'foreignData': '最终展示数据逐段裁剪到中国省级边界；不包含国外道路、地名、兴趣点。',
        'sources': [
            {'name': 'OpenStreetMap / Geofabrik', 'url': 'https://download.geofabrik.de/asia/china.html', 'license': 'ODbL 1.0; © OpenStreetMap contributors'},
            {'name': '中国行政区划轮廓 / 阿里云 DataV', 'url': 'https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json', 'note': '边界资源2021年版本，GCJ-02近似转WGS84；仅供路线浏览，不作精密边界判断'},
            {'name': '国家公路网规划（2022年）', 'url': 'https://www.ndrc.gov.cn/xwdt/tzgg/202207/t20220712_1330359_ext.html'},
        ],
    }
    if (DATA / 'report.json').exists():
        previous = json.loads((DATA / 'report.json').read_text(encoding='utf8'))
        if 'basemap' in previous:
            report['basemap'] = previous['basemap']
        report['sources'] += [s for s in previous.get('sources',[]) if s.get('category') == 'basemap']
    save(DATA / 'report.json', report)
    db = sqlite3.connect(DATA / 'roads.sqlite')
    db.execute('DROP TABLE IF EXISTS roads')
    db.execute('CREATE TABLE roads (id TEXT PRIMARY KEY, code TEXT, kind TEXT, province TEXT, name TEXT, metadata TEXT)')
    db.executemany('INSERT INTO roads VALUES (?,?,?,?,?,?)', [(r['id'], r['code'], r['kind'], r['province'], r['name'], json.dumps(r, ensure_ascii=False)) for r in records])
    db.execute('CREATE INDEX roads_code ON roads(code)')
    db.commit()
    db.close()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print('Finished in', round(time.monotonic()-start), 'seconds', flush=True)


if __name__ == '__main__':
    prepare()
    # Raw ref/relation extraction is only an input, never the final displayed dataset.
    import subprocess
    import sys
    for script in ('extract_road_evidence.py', 'audit_routes.py', 'build_network_index.py'):
        subprocess.run([sys.executable, str(ROOT / 'scripts' / script)], check=True)
