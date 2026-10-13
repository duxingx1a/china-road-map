"""Compare wire sizes; theoretical times use 3 Mbps, not a measured browser speed."""
import gzip,json,re,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
def fetch(base,path):
    with urlopen(Request(base+path,headers={'Accept-Encoding':'gzip'}),timeout=60) as response:
        raw=response.read()
        return len(raw),json.loads(gzip.decompress(raw) if response.headers.get('Content-Encoding')=='gzip' else raw)

rows=[]
for line in (ROOT/'nginx-map-recent.log').read_text(encoding='utf-8-sig').splitlines():
    match=re.search(r'"GET (/map/api/network\?[^ ]+) HTTP/[^\"]+" 200 (\d+)',line)
    if match:rows.append((int(match[2]),match[1].removeprefix('/map')))
path=max(rows)[1]
old_bytes,old=fetch('http://127.0.0.1:8766',path)
new_bytes,new=fetch('http://127.0.0.1:18768',path+'&zoom=7')
assert {f['properties']['id'] for f in old['features']}=={f['properties']['id'] for f in new['features']}
samples=[{'kind':'network zoom 7','oldGzipBytes':old_bytes,'newGzipBytes':new_bytes}]
for name in ('national-G248','national-G318','national-G219','expressway-G5'):
    original=(ROOT/f'data/routes/{name}.geojson').read_bytes()
    display=(ROOT/f'data/display/routes/overview/{name}.geojson.gz').read_bytes()
    samples.append({'kind':name+' overview','oldGzipBytes':len(gzip.compress(original,6)),'newGzipBytes':len(display)})
for s in samples:
    s['reductionPercent']=round((1-s['newGzipBytes']/s['oldGzipBytes'])*100,1)
    s['oldIdealSecondsAt3Mbps']=round(s['oldGzipBytes']*8/3000000,2)
    s['newIdealSecondsAt3Mbps']=round(s['newGzipBytes']*8/3000000,2)
result={'networkPath':path,'sameRouteIds':True,'sizes':samples}
(ROOT/'verification-display-payloads.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
