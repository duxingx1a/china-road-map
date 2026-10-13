(function(root){
  'use strict';
  function create({fetchTile,maxConcurrent=3,maxBytes=12*1024*1024,maxEntries=160}){
    const cache=new Map(),queue=[],active=new Set();let bytes=0;
    const aborted=()=>new DOMException('Tile no longer visible','AbortError');
    function remember(url,data){
      if(data.byteLength>maxBytes)return;
      const old=cache.get(url);if(old)bytes-=old.byteLength;
      cache.delete(url);cache.set(url,data.slice(0));bytes+=data.byteLength;
      while(bytes>maxBytes||cache.size>maxEntries){const id=cache.keys().next().value;bytes-=cache.get(id).byteLength;cache.delete(id);}
    }
    function drain(){
      while(active.size<maxConcurrent&&queue.length){
        const job=queue.shift();if(job.signal?.aborted){job.reject(aborted());continue;}
        active.add(job);
        Promise.resolve().then(()=>fetchTile(job.url,job.signal)).then(data=>{
          if(job.signal?.aborted)throw aborted();remember(job.url,data);job.resolve(data);
        }).catch(job.reject).finally(()=>{active.delete(job);job.signal?.removeEventListener('abort',job.cancel);drain();});
      }
    }
    function load(url,signal){
      if(signal?.aborted)return Promise.reject(aborted());
      const hit=cache.get(url);if(hit){cache.delete(url);cache.set(url,hit);return Promise.resolve(hit.slice(0));}
      return new Promise((resolve,reject)=>{
        const job={url,signal,resolve,reject};
        job.cancel=()=>{const i=queue.indexOf(job);if(i>=0){queue.splice(i,1);signal.removeEventListener('abort',job.cancel);reject(aborted());}};
        signal?.addEventListener('abort',job.cancel,{once:true});queue.push(job);drain();
      });
    }
    return {load,stats:()=>({active:active.size,queued:queue.length,bytes,entries:cache.size})};
  }
  if(typeof module==='object'&&module.exports)module.exports={create};else root.atlasTiles={create};
})(typeof window==='object'?window:globalThis);
