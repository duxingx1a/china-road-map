"""Apply final boundary checks to overview and record source provenance."""
from pathlib import Path
import json
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from prepare_data import save, DATA, RAW


def main():
    boundaries = json.loads((DATA / 'china.geojson').read_text(encoding='utf-8'))
    mask = unary_union([shape(f['geometry']) for f in boundaries['features']])
    overview = json.loads((DATA / 'overview.geojson').read_text(encoding='utf-8'))
    for feature in overview['features']:
        feature['geometry'] = mapping(shape(feature['geometry']).intersection(mask))
    save(DATA / 'overview.geojson', overview)
    report = json.loads((DATA / 'report.json').read_text(encoding='utf-8'))
    report['sourceFileBytes'] = (RAW / 'china.osm.pbf').stat().st_size
    report['sourceFileMD5'] = (RAW / 'china.osm.pbf.md5').read_text().strip()
    report['runtimeDataBytes'] = sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file() and RAW not in p.parents)
    save(DATA / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
