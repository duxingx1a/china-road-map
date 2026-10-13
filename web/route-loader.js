(function(root){
  'use strict';
  function create({fetchRoute,maxConcurrent=2,maxEntries=12,maxBytes=8*1024*1024}){
    const cache=new Map(),pending=new Map(),active=new Set(),queue=[];
    let cacheBytes=0;
    const abortError=()=>new DOMException('Route request cancelled','AbortError');
    function remember(id,result){
      if(result.bytes>maxBytes)return;
      cache.set(id,result);cacheBytes+=result.bytes;
      while(cache.size>maxEntries||cacheBytes>maxBytes){
        const oldest=cache.keys().next().value;
        cacheBytes-=cache.get(oldest).bytes;cache.delete(oldest);
      }
    }
    function drain(){
      while(active.size<maxConcurrent&&queue.length){
        const foreground=[...pending.values()].some(job=>!job.prefetch);
        const index=queue.findIndex(job=>!job.prefetch||!foreground);
        if(index<0)return;
        const [job]=queue.splice(index,1);active.add(job);
        Promise.resolve().then(()=>fetchRoute(job.id,job.controller.signal)).then(result=>{
          if(job.controller.signal.aborted)throw abortError();
          remember(job.id,result);job.resolve(result.data);
        }).catch(job.reject).finally(()=>{
          active.delete(job);
          if(pending.get(job.id)===job)pending.delete(job.id);
          drain();
        });
      }
    }
    function cancel(id,onlyPrefetch=false){
      const job=pending.get(id);
      if(!job||(onlyPrefetch&&!job.prefetch))return;
      job.controller.abort();pending.delete(id);
      const index=queue.indexOf(job);
      if(index>=0){queue.splice(index,1);job.reject(abortError());}
      drain();
    }
    function prioritize(id){
      for(const job of [...pending.values()])if(job.prefetch&&job.id!==id)cancel(job.id,true);
    }
    function load(id,{prefetch=false}={}){
      const hit=cache.get(id);
      if(hit){cache.delete(id);cache.set(id,hit);return Promise.resolve(hit.data);}
      let job=pending.get(id);
      if(job){
        if(!prefetch){job.prefetch=false;prioritize(id);drain();}
        return job.promise;
      }
      job={id,prefetch,controller:new AbortController()};
      job.promise=new Promise((resolve,reject)=>{job.resolve=resolve;job.reject=reject;});
      pending.set(id,job);
      if(prefetch)queue.push(job);else{queue.unshift(job);prioritize(id);}
      drain();return job.promise;
    }
    return {
      load,
      prefetch:id=>load(id,{prefetch:true}).catch(()=>undefined),
      cancel,
      cancelPrefetch:id=>cancel(id,true),
      isCached:id=>cache.has(id),
      isPending:id=>pending.has(id),
      stats:()=>({active:active.size,pending:pending.size,entries:cache.size,bytes:cacheBytes}),
    };
  }
  if(typeof module==='object'&&module.exports)module.exports={create};
  else root.atlasRoutes={create};
})(typeof window==='object'?window:globalThis);
