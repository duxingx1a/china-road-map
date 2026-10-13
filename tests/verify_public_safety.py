"""Small empty-viewport bursts verify rate limiting without consuming route bandwidth."""
import argparse,collections,concurrent.futures,json,time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path

def get(base,path):
    try:
        with urlopen(Request(base.rstrip('/')+'/'+path,headers={'Accept-Encoding':'gzip'}),timeout=20) as response:
            return response.status,response.headers.get('Retry-After'),len(response.read())
    except HTTPError as error:return error.code,error.headers.get('Retry-After'),0

parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1/map');parser.add_argument('--output');args=parser.parse_args()
normal=[get(args.base,p) for p in ['data/display/routes/overview/expressway-G5.geojson','data/display/routes/standard/expressway-G5.geojson','data/display/routes/detail/expressway-G5.geojson','tiles/china/14/12928/6731.pbf','api/network?bbox=104,30,104.01,30.01&zoom=12']]
assert all(r[0]==200 for r in normal),normal
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    burst=list(pool.map(lambda _:get(args.base,'api/network?bbox=0,0,0.001,0.001&zoom=18'),range(64)))
counts=dict(collections.Counter(r[0] for r in burst));assert counts.get(429,0)>0,counts
assert all(r[1]=='2' for r in burst if r[0]==429)
time.sleep(2.2)
assert get(args.base,'api/network?bbox=0,0,0.001,0.001&zoom=18')[0]==200
assert get(args.base,'data/routes/expressway-G5.geojson')[0]==403
result={'normalReads':len(normal),'normalSuccessful':len(normal),'burstStatuses':counts,'retryAfterSeconds':2,'recoversAfterWait':True,'originalDownloadDenied':True}
print(json.dumps(result,indent=2))
if args.output:Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
