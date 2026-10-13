'use strict';
(function(root){
  const earthCircumference=2*Math.PI*6371008.8;
  // At this limit a 100 px metric scale rounds down to 300 km.
  function minimumZoom(latitude){
    return Math.log2(earthCircumference*Math.cos(Math.max(-85,Math.min(85,latitude))*Math.PI/180)*100/(512*400000));
  }
  function snapshot(map){
    const center=map.getCenter();
    return {center:[center.lng,center.lat],zoom:map.getZoom(),bearing:map.getBearing(),pitch:map.getPitch()};
  }
  function bookmarks(){
    let saved=null;
    return {save(map){saved ||= snapshot(map);},take(){const camera=saved;saved=null;return camera;},has(){return saved!==null;}};
  }
  const api={minimumZoom,snapshot,bookmarks};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.atlasViews=api;
})(typeof window!=='undefined'?window:globalThis);
