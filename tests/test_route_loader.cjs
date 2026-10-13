const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/route-loader.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function controlled(){
  const calls=[];
  const fetchRoute=(id,signal)=>new Promise((resolve,reject)=>{
    calls.push({id,signal,resolve:data=>resolve({data,bytes:4}),reject});
    signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')),{once:true});
    if(signal.aborted)reject(new DOMException('cancelled','AbortError'));
  });
  return {calls,fetchRoute};
}

test('hover and click share one in-flight request and completed data is reused',async()=>{
  const {calls,fetchRoute}=controlled(),loader=create({fetchRoute});
  const hover=loader.prefetch('G5');await tick();
  const clicked=loader.load('G5');await tick();
  assert.equal(calls.length,1);
  loader.cancelPrefetch('G5');assert.equal(calls[0].signal.aborted,false);
  calls[0].resolve({route:'G5'});
  assert.deepEqual(await clicked,{route:'G5'});await hover;await tick();
  assert.deepEqual(await loader.load('G5'),{route:'G5'});
  assert.equal(calls.length,1);
});

test('selection preempts speculative requests and concurrency stays bounded',async()=>{
  const {calls,fetchRoute}=controlled(),loader=create({fetchRoute,maxConcurrent:2});
  const a=loader.prefetch('hover-a'),b=loader.prefetch('hover-b');await tick();
  assert.equal(calls.length,2);
  const selected=loader.load('selected');await tick();
  assert.ok(calls[0].signal.aborted&&calls[1].signal.aborted);
  assert.equal(calls[2].id,'selected');assert.ok(loader.stats().active<=2);
  calls[2].resolve({selected:true});assert.deepEqual(await selected,{selected:true});
  await Promise.all([a,b]);await tick();assert.equal(loader.stats().pending,0);
});

test('cancelling then immediately selecting again cannot erase the newer request',async()=>{
  const {calls,fetchRoute}=controlled(),loader=create({fetchRoute,maxConcurrent:1});
  const old=loader.load('G248');old.catch(()=>{});await tick();
  loader.cancel('G248');const newer=loader.load('G248');await tick();
  assert.equal(calls.length,2);assert.equal(loader.isPending('G248'),true);
  calls[1].resolve({new:true});assert.deepEqual(await newer,{new:true});
  await assert.rejects(old,{name:'AbortError'});await tick();
  assert.equal(loader.isCached('G248'),true);
});

test('failed prefetch does not prevent a later click from retrying',async()=>{
  let calls=0;
  const loader=create({fetchRoute:async()=>{if(++calls===1)throw new Error('offline');return {data:{ok:true},bytes:4};}});
  await loader.prefetch('G318');await tick();
  assert.deepEqual(await loader.load('G318'),{ok:true});assert.equal(calls,2);
});

test('cache respects its byte budget and evicts least recently used routes',async()=>{
  const loader=create({maxBytes:8,maxEntries:10,fetchRoute:async id=>({data:{id},bytes:4})});
  await loader.load('a');await tick();await loader.load('b');await tick();
  await loader.load('a');await loader.load('c');await tick();
  assert.equal(loader.isCached('a'),true);assert.equal(loader.isCached('b'),false);
  assert.equal(loader.stats().bytes,8);
});
