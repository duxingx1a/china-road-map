const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../web/tile-loader.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('tiles outside the viewport are removed from the queue before a request starts',async()=>{
  const calls=[];const loader=create({maxConcurrent:1,fetchTile:(url,signal)=>new Promise((resolve,reject)=>{calls.push({url,resolve});signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')));})});
  const a=new AbortController(),b=new AbortController();const first=loader.load('a',a.signal),queued=loader.load('b',b.signal);queued.catch(()=>{});
  await tick();b.abort();await assert.rejects(queued,{name:'AbortError'});assert.equal(calls.length,1);
  calls[0].resolve(new Uint8Array([1,2]).buffer);await first;await tick();assert.equal(loader.stats().queued,0);
});
test('cached tile buffers survive worker transfer and respect the memory budget',async()=>{
  let calls=0;const loader=create({maxBytes:4,fetchTile:async()=>{calls++;return new Uint8Array([1,2]).buffer;}});
  const a=await loader.load('a');await tick();structuredClone(a,{transfer:[a]});
  assert.deepEqual(new Uint8Array(await loader.load('a')),new Uint8Array([1,2]));assert.equal(calls,1);
  await loader.load('b');await tick();await loader.load('c');await tick();assert.equal(loader.stats().bytes,4);
  await loader.load('a');assert.equal(calls,4);
});
test('real tile errors are propagated and a later load can retry',async()=>{
  let calls=0;const loader=create({fetchTile:async()=>{if(++calls===1)throw new Error('HTTP 503');return new ArrayBuffer(0);}});
  await assert.rejects(loader.load('a'),/503/);await tick();await loader.load('a');assert.equal(calls,2);
});
