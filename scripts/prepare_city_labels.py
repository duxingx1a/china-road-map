"""Download administrative city centres once; runtime uses only the local points."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import requests
from prepare_data import gcj_to_wgs

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'

def main():
    provinces = json.loads((DATA / 'china.geojson').read_text(encoding='utf-8'))['features']
    capitals = {'石家庄市','太原市','呼和浩特市','沈阳市','长春市','哈尔滨市','南京市','杭州市','合肥市','福州市','南昌市','济南市','郑州市','武汉市','长沙市','广州市','南宁市','海口市','成都市','贵阳市','昆明市','拉萨市','西安市','兰州市','西宁市','银川市','乌鲁木齐市'}
    def download(province):
        p = province['properties']
        if str(p['adcode'])[:2] in {'11','12','31','50','71','81','82'}:
            return [], None
        url = f"https://geo.datav.aliyun.com/areas_v3/bound/{p['adcode']}_full.json"
        response = requests.get(url, timeout=45)
        response.raise_for_status()
        features = []
        for child in response.json()['features']:
            q = child['properties']
            if q.get('level') != 'city' or not q.get('center'):
                continue
            features.append({'type':'Feature','properties':{'name':q['name'],'adcode':q['adcode'],'province':p['name'],'capital':q['name'] in capitals},'geometry':{'type':'Point','coordinates':gcj_to_wgs(*q['center'])}})
        return features, url
    results = list(ThreadPoolExecutor(max_workers=6).map(download, provinces))
    features = [f for rows, _ in results for f in rows]
    (DATA / 'cities.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features},ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (DATA / 'city-labels-report.json').write_text(json.dumps({'source':'DataV areas_v3 2021 administrative resource','urls':[url for _,url in results if url],'points':len(features),'coordinateSystem':'WGS84, approximately converted from GCJ02','limitations':'Administrative city-level centres include some directly administered county-level units; historical division snapshot.'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Saved {len(features)} local administrative city centres',flush=True)

if __name__ == '__main__':
    main()
