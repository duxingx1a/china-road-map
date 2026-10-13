'use strict';
const appBase = new URL('./', location.href);
const resourceURL = path => new URL(path.replace(/^\//, ''), appBase).href;
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const labels = {national:'普通国道',expressway:'国家高速',provincial:'省级高速',highspeed:'高铁',railway:'普速铁路'};
const isRail=road=>road.kind==='highspeed'||road.kind==='railway';
const routeLabel=road=>isRail(road)?road.name:road.code;
const layerStyles=[{kind:'national',prefix:'national',color:'#b95555',opacity:.85},{kind:'expressway',prefix:'express',color:'#387457',opacity:.9},{kind:'provincial',prefix:'provincial',color:'#387457',opacity:.9},{kind:'highspeed',prefix:'highspeed',color:'#b4232f',opacity:.8},{kind:'railway',prefix:'railway',color:'#292d30',opacity:.8}];
const chinaView = [[73,17],[135.6,54.5]];
const viewNumbers=(new URLSearchParams(location.search).get('view')||'').split(',').map(Number);
const initialView=viewNumbers.length===3&&viewNumbers.every(Number.isFinite)&&viewNumbers[0]>=72&&viewNumbers[0]<=136&&viewNumbers[1]>=3&&viewNumbers[1]<=55&&viewNumbers[2]>=1.3&&viewNumbers[2]<=18?viewNumbers:null;
let map, catalog = [], byId = new Map(), selected = null, kind = 'all', displayed = 80, result = [], toastTimer;
const selections=new Map();
const DISPLAY_VERSION='20261013-rail1';
const routeTier=()=>map?.getZoom()>=12?'detail':map?.getZoom()>=9?'standard':'overview';
const routeKey=id=>id+'@'+routeTier();
const routeLoader=atlasRoutes.create({fetchRoute:(key,signal)=>{const [id,tier]=key.split('@');return readJSON(`/data/display/routes/${tier}/${encodeURIComponent(id)}.geojson?v=${DISPLAY_VERSION}`,{signal,withSize:true});}});
const tileLoader=atlasTiles.create({fetchTile:async(url,signal)=>{
  for(let attempt=0;attempt<3;attempt++){
    try{const response=await fetch(url,{signal});if(!response.ok){const error=new Error(`底图 HTTP ${response.status}`);error.status=response.status;throw error;}return await response.arrayBuffer();}
    catch(error){if(signal.aborted||error.name==='AbortError'||attempt===2||!(error instanceof TypeError||[429,502,503,504].includes(error.status)))throw error;await new Promise(resolve=>setTimeout(resolve,600*(attempt+1)));}
  }
}});
let hoverRoute=null,hoverTimer,geometryRevision=0,renderedGeometryRevision=-1;
const selectionColors={national:['#b4232f','#ca514d','#9b3650','#db6d5c','#8a2533','#b74f66','#b14537','#923846','#cc4c69','#a6645b'],expressway:['#14613f','#368251','#276d60','#55764b','#0c7756','#5c9258','#396a41','#58826a','#416f5b','#457832']};
let nationalVisible=true,expressVisible=true,highspeedVisible=false,railwayVisible=false,precisionTimer;
const kindVisible=kind=>kind==='national'?nationalVisible:kind==='highspeed'?highspeedVisible:kind==='railway'?railwayVisible:expressVisible;
let ready = false, mapLoaded;
const viewBookmarks=atlasViews.bookmarks();
let settingZoomLimit=false;
let provinces = [];
let basemapVisible=true,basemapRegions=new Set(),precisionPending,precisionBounds=null,precisionTier=null,precisionKinds=null;
let railOverviewLoaded=false,railOverviewPending=null;
const empty = () => ({type:'FeatureCollection',features:[]});
const isRouteBusy = entry => Boolean(entry?.loading||entry?.rendering);
const compactProvince = s => s.replace(/壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|省|市/g, '');
const mobileDrawer=atlasMobile.create({sidebar:$('road-drawer'),toggle:$('drawer-toggle'),close:$('drawer-close'),mapPanel:document.querySelector('.map-panel'),search:$('search'),header:document.querySelector('.header'),workspace:document.querySelector('.workspace'),onLayout:()=>map?.resize()});

function roadPresentation(road){
  if(isRail(road))return {title:road.name,subtitle:[labels[road.kind],road.from&&road.to?road.from+'—'+road.to:road.provinceName].filter(Boolean).join(' · ')};
  const clean=value=>String(value||'').replace(/[（(][^（）()]*?(?:方向|段)[）)]/g,'').trim();
  const candidates=[road.name,...(road.aliases||[])].map(clean);
  let title;
  if(road.kind==='national'){
    title=candidates.map(name=>name.replace(/^(?:G\s*)?\d+\s*国道\s*/i,'')).find(name=>/^[\u4e00-\u9fff]{2,12}线$/.test(name));
    title=title||candidates.find(name=>/^(?:G\s*)?\d+\s*国道$/i.test(name))||road.name;
  }else{
    title=candidates.find(name=>/^[\u4e00-\u9fff]+高速(?:公路)?(?:连接线|联络线)?$/.test(name))||road.name;
    title=title.replace(/高速公路/g,'高速');
  }
  const endpoints=road.from&&road.to?(road.from===road.to?road.from:`${road.from}—${road.to}`):(/[—－–-]/.test(road.name)?road.name:'');
  const subtitle=[labels[road.kind],endpoints||road.provinceName].filter(Boolean).join(' · ');
  return {title,subtitle};
}

function roadShield(road,compact=false){
  if(isRail(road))return `<span class="shield ${road.kind}${compact?' compact':''}"><span class="shield-code">${road.kind==='highspeed'?'高铁':'普铁'}</span></span>`;
  const heading=road.kind==='expressway'?'国家高速':road.kind==='provincial'?compactProvince(road.provinceName||'省级')+'高速':'';
  return `<span class="shield ${road.kind}${compact?' compact':''}">${heading&&!compact?`<span class="shield-heading">${escapeHTML(heading)}</span>`:''}<span class="shield-code">${escapeHTML(road.code)}</span></span>`;
}


async function readJSON(url, options={}) {
  const {withSize=false,...requestOptions}=options;
  for(let attempt=0;attempt<3;attempt++){
    try{
      const response=await fetch(resourceURL(url),requestOptions);
      if(!response.ok){const error=new Error(`${url}: HTTP ${response.status}`);error.status=response.status;throw error;}
      if(withSize){const text=await response.text();return {data:JSON.parse(text),bytes:text.length*2};}
      return await response.json();
    }catch(error){
      if(options.signal?.aborted||error.name==='AbortError')throw error;
      if(attempt===2||!(error instanceof TypeError||[502,503,504].includes(error.status)))throw error;
      await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)));
    }
  }
}

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}

function initMap(china) {
  maplibregl.addProtocol('atlas',async(params,controller)=>({data:await tileLoader.load(params.url.slice('atlas://'.length),controller.signal),cacheControl:'public, max-age=2592000'}));
  provinces = china.features;
  addSouthSeaInset(china);
  map = new maplibregl.Map({
    container:'map', center:initialView?initialView.slice(0,2):[104,35],zoom:initialView?Math.max(initialView[2],atlasViews.minimumZoom(initialView[1])):atlasViews.minimumZoom(35),minZoom:atlasViews.minimumZoom(initialView?initialView[1]:35),maxZoom:18,renderWorldCopies:false,
    localIdeographFontFamily:'Microsoft YaHei, PingFang SC, sans-serif',
    locale:{'ScaleControl.Kilometers':'公里','ScaleControl.Meters':'米'},
    attributionControl:false,
    style:{version:8,sources:{china:{type:'geojson',data:china},outsideMask:{type:'geojson',data:resourceURL('/data/outside-mask.geojson'),tolerance:0}},layers:[
      {id:'outside',type:'background',paint:{'background-color':'#e1e6eb'}},
      {id:'china-land',type:'fill',source:'china',paint:{'fill-color':'#f6f5f0','fill-opacity':1}},
      {id:'china-border-halo',type:'line',source:'china',paint:{'line-color':'#fafaf8','line-width':['interpolate',['linear'],['zoom'],3,2.5,8,4]}},
      {id:'china-border',type:'line',source:'china',paint:{'line-color':'#939da8','line-width':['interpolate',['linear'],['zoom'],3,.85,8,1.4],'line-dasharray':[3,3],'line-opacity':.8}},
      {id:'foreign-shadow',type:'fill',source:'outsideMask',paint:{'fill-color':'#e1e6eb','fill-opacity':1}},
    ]},
  });
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  map.addControl(new maplibregl.ScaleControl({maxWidth:100,unit:'metric'}),'bottom-right');
  mapLoaded = new Promise(resolve => map.on('load', () => {
    if(!initialView)map.fitBounds(chinaView,{padding:30,retainPadding:false,duration:0});
    updateZoomLimit();
    map.addSource('selected',{type:'geojson',data:empty(),tolerance:0,maxzoom:18,buffer:128});
    map.addLayer({id:'selected-halo',type:'line',source:'selected',paint:{'line-color':'#ffffff','line-width':['interpolate',['linear'],['zoom'],3,5,9,9],'line-opacity':.92},layout:{'line-cap':'round','line-join':'round'}});
    map.addLayer({id:'selected-road',type:'line',source:'selected',paint:{'line-color':['coalesce',['get','selectionColor'],'#14613f'],'line-width':['interpolate',['linear'],['zoom'],3,2.7,9,4.8]},layout:{'line-cap':'round','line-join':'round'}});
    map.addLayer({id:'selected-rail-pattern',type:'line',source:'selected',filter:['in',['get','kind'],['literal',['highspeed','railway']]],paint:{'line-color':'#ffffff','line-width':['interpolate',['linear'],['zoom'],3,2.7,9,4.8],'line-dasharray':[2,2]},layout:{'line-cap':'butt','line-join':'round'}});
    map.addLayer({id:'selected-hit',type:'line',source:'selected',paint:{'line-color':'#000','line-opacity':0,'line-width':14}});
    addLabels();
    $('loading').hidden = true;
    resolve();
  }));
  map.on('error', e => {
    if(e.error?.name==='AbortError'||/abort/i.test(e.error?.message||''))return;
    console.error('Map source:',e.sourceId,e.error);$('loading').hidden=true;
    toast(`地图资源读取失败：${e.sourceId||'本地地图'}。${e.error?.status?`HTTP ${e.error.status}`:'请确认本地服务仍在运行。'}`);
  });
  map.on('move',updateZoomLimit);
  map.on('sourcedata',event=>{
    if(event.sourceId!=='selected'||!event.isSourceLoaded||!map.isSourceLoaded('selected'))return;
    let changed=false;
    for(const entry of selections.values())if(entry.rendering){entry.rendering=false;changed=true;}
    if(changed)syncSelections();
    for(const entry of selections.values())if(entry.geo&&!isRouteBusy(entry)&&entry.tier!==routeTier())selectRoad(entry.road,{focus:false});
  });
  for (const f of china.features) {
    const option = document.createElement('option');
    option.value=f.properties.name;option.textContent=f.properties.name;$('province').append(option);
  }
}

async function addSouthSeaInset(china) {
  const svg=$('south-sea-svg');
  const pathFor=ring=>ring.map(([lon,lat],i)=>`${i?'L':'M'}${((lon-105)/20*110).toFixed(2)},${((25-lat)/22*142).toFixed(2)}`).join(' ')+'Z';
  const getPolygons=geometry=>geometry.type==='MultiPolygon'?geometry.coordinates:geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='GeometryCollection'?geometry.geometries.flatMap(getPolygons):[];
  const append=(features,dashed=false)=>{
    for(const f of features){
      const polygons=getPolygons(f.geometry);
      for(const polygon of polygons){
        if(!polygon.some(ring=>ring.some(([x,y])=>x>=105&&x<=125&&y>=3&&y<=25)))continue;
        const path=document.createElementNS('http://www.w3.org/2000/svg','path');
        path.setAttribute('d',polygon.map(pathFor).join(' '));path.setAttribute('fill',dashed?'none':'#f6f5f0');
        path.setAttribute('stroke',dashed?'#8996a4':'#b0bac4');path.setAttribute('stroke-width',dashed?'1':'.4');path.setAttribute('fill-rule','evenodd');
        svg.append(path);
      }
    }
  };
  append(china.features);
  try{const maritime=await readJSON('/data/maritime.geojson');append(maritime.features,true);}catch(e){console.warn('South Sea inset:',e);}
}

function addLabels() {
  const capitals = [
    ['北京',116.4074,39.9042],['天津',117.2008,39.0842],['石家庄',114.5149,38.0428],['太原',112.5489,37.8706],
    ['呼和浩特',111.7492,40.8426],['沈阳',123.4315,41.8057],['长春',125.3235,43.8171],['哈尔滨',126.535,45.802],
    ['上海',121.4737,31.2304],['南京',118.7969,32.0603],['杭州',120.1551,30.2741],['合肥',117.2272,31.8206],
    ['福州',119.2965,26.0745],['南昌',115.8582,28.6829],['济南',117.1201,36.6512],['郑州',113.6254,34.7466],
    ['武汉',114.3055,30.5928],['长沙',112.9388,28.2282],['广州',113.2644,23.1291],['南宁',108.3669,22.817],
    ['海口',110.1983,20.044],['重庆',106.5516,29.563],['成都',104.0665,30.5723],['贵阳',106.6302,26.647],
    ['昆明',102.8329,24.8801],['拉萨',91.1409,29.6456],['西安',108.9398,34.3416],['兰州',103.8343,36.0611],
    ['西宁',101.7782,36.6171],['银川',106.2309,38.4872],['乌鲁木齐',87.6168,43.8256],['台北',121.5654,25.033],
    ['香港',114.1694,22.3193],['澳门',113.5439,22.1987],
  ];
  const font=['Microsoft YaHei','PingFang SC','sans-serif'];
  const labelPaint={'text-color':'#4c5e70','text-halo-color':'#ffffff','text-halo-width':2};
  const cityPoints=capitals.map(([name,lon,lat])=>({type:'Feature',properties:{name},geometry:{type:'Point',coordinates:[lon,lat]}}));
  map.addSource('capital-labels',{type:'geojson',data:{type:'FeatureCollection',features:cityPoints}});
  map.addLayer({id:'capital-points',type:'circle',source:'capital-labels',minzoom:2.5,maxzoom:13,paint:{'circle-radius':3,'circle-color':'#6f7380','circle-stroke-color':'#ffffff','circle-stroke-width':1.5}});
  map.addLayer({id:'capital-names',type:'symbol',source:'capital-labels',minzoom:2.5,maxzoom:13,layout:{'text-field':['get','name'],'text-font':font,'text-size':12,'text-variable-anchor':['left','right','top','bottom'],'text-radial-offset':.65,'text-padding':7},paint:labelPaint});
  const provincePoints=provinces.filter(f=>!['北京市','天津市','上海市','重庆市','香港特别行政区','澳门特别行政区'].includes(f.properties.name)).map(f=>({type:'Feature',properties:{name:compactProvince(f.properties.name)},geometry:{type:'Point',coordinates:f.properties.labelCenter||f.properties.center}}));
  map.addSource('province-labels',{type:'geojson',data:{type:'FeatureCollection',features:provincePoints}});
  map.addLayer({id:'province-names',type:'symbol',source:'province-labels',minzoom:1.8,maxzoom:5.8,layout:{'text-field':['get','name'],'text-font':font,'text-size':13,'text-variable-anchor':['center','top','bottom'],'text-padding':10},paint:{...labelPaint,'text-color':'#7b8793'}});
}


async function loadBasemap() {
  try {
    const status=await readJSON('/api/map-status');
    for(const archive of status.archives){
      if(basemapRegions.has(archive.region))continue;
      const source='basemap-'+archive.region;
      if(!map.getSource(source))map.addSource(source,{type:'vector',tiles:[`atlas://${appBase.href}tiles/${archive.region}/{z}/{x}/{y}.pbf?v=${DISPLAY_VERSION}`],bounds:archive.bounds||(archive.region==='taiwan'?[118,20,123,27]:[73,3,136,55]),minzoom:archive.minzoom,maxzoom:archive.maxzoom});
      for(const layer of window.chinaBasemapLayers(archive.region)){
        if(!basemapVisible)layer.layout={...layer.layout,visibility:'none'};
        if(!map.getLayer(layer.id))map.addLayer(layer,'china-border-halo');
      }
      basemapRegions.add(archive.region);
    }
    if(!status.complete)setTimeout(loadBasemap,15000);
  } catch(error){console.warn('Local basemap:',error);setTimeout(loadBasemap,15000);}
}

async function updateDetailedNetwork() {
  if(!map.getSource('network-precise'))return;
  if(map.getZoom()<7||!layerStyles.some(layer=>kindVisible(layer.kind))){precisionPending?.abort();return;}
  const bounds=map.getBounds();
  const w=bounds.getWest(),s=bounds.getSouth(),e=bounds.getEast(),n=bounds.getNorth();
  const tier=routeTier(),kinds=layerStyles.filter(layer=>kindVisible(layer.kind)).map(layer=>layer.kind),kindKey=kinds.join(',');
  if(precisionBounds&&tier===precisionTier&&kindKey===precisionKinds&&w>=precisionBounds[0]&&s>=precisionBounds[1]&&e<=precisionBounds[2]&&n<=precisionBounds[3])return;
  precisionPending?.abort();
  const controller=precisionPending=new AbortController();
  const padX=(e-w)*.12,padY=(n-s)*.12;
  const bbox=[Math.max(-180,w-padX),Math.max(-85,s-padY),Math.min(180,e+padX),Math.min(85,n+padY)];
  if(bbox[0]>=bbox[2]||bbox[1]>=bbox[3])return;
  try{
    const data=await readJSON('/api/network?bbox='+bbox.map(v=>v.toFixed(5)).join(',')+'&kinds='+kindKey+'&zoom='+Math.floor(map.getZoom())+'&v='+DISPLAY_VERSION,{signal:controller.signal});
    if(controller.signal.aborted)return;
    map.getSource('network-precise').setData(data);precisionBounds=bbox;precisionTier=tier;precisionKinds=kindKey;
    if(data.truncated)toast('精细路网范围较大，请继续放大以查看完整细节。');
  }catch(error){if(!controller.signal.aborted&&error.name!=='AbortError'){console.error(error);precisionBounds=null;toast(`精细路网读取失败：${error.message}。移动地图可重试。`);}}
}

function scheduleNetwork(){clearTimeout(precisionTimer);precisionTimer=setTimeout(updateDetailedNetwork,450);}

function updateNetworkVisibility(){
  for(const {kind,prefix} of layerStyles){
    const visible=kindVisible(kind);
    for(const suffix of ['network','network-pattern','hit','precise','precise-pattern','precise-hit']){
      const id=prefix+'-'+suffix;if(map?.getLayer(id))map.setLayoutProperty(id,'visibility',visible?'visible':'none');
    }
  }
  updateRailLegend();
  if(highspeedVisible||railwayVisible)loadRailOverview();
  precisionBounds=null;scheduleNetwork();
}

async function loadRailOverview(){
  if(!map?.getSource('rail-network'))return;
  if(railOverviewLoaded)return;
  if(railOverviewPending)return railOverviewPending;
  const control=document.querySelector('.rail-actions');control.setAttribute('aria-busy','true');
  railOverviewPending=readJSON('/data/display/railways-overview.geojson?v='+DISPLAY_VERSION).then(data=>{map.getSource('rail-network').setData(data);railOverviewLoaded=true;}).catch(error=>{console.error('Railway overview:',error);toast('铁路概览加载失败，点击铁路开关可重试。');}).finally(()=>{railOverviewPending=null;control.setAttribute('aria-busy','false');});
  return railOverviewPending;
}

function updateRailLegend(){
  for(const type of ['highspeed','railway'])document.querySelector(`[data-rail-legend="${type}"]`).hidden=!kindVisible(type)&&![...selections.values()].some(entry=>entry.road.kind===type);
}

function updateNetworkEmphasis(){
  const factor=[...selections.values()].some(entry=>entry.road.hasGeometry&&!entry.error) ? .07 : 1;
  for(const {prefix} of layerStyles){
    const overview=prefix+'-network';
    for(const id of [overview,overview+'-pattern'])if(map.getLayer(id))map.setPaintProperty(id,'line-opacity',['interpolate',['linear'],['zoom'],3,.52*factor,5,.7*factor,7,.85*factor]);
  }
  for(const {prefix,opacity} of layerStyles){
    const precise=prefix+'-precise';
    for(const id of [precise,precise+'-pattern'])if(map.getLayer(id))map.setPaintProperty(id,'line-opacity',opacity*factor);
  }
}

async function addPrefectureLabels(){
  try{
    const cities=await readJSON('/data/cities.geojson');
    map.addSource('cities',{type:'geojson',data:cities});
    map.addLayer({id:'prefecture-names',type:'symbol',source:'cities',minzoom:6,maxzoom:13,filter:['==',['get','capital'],false],layout:{'text-field':['get','name'],'text-font':['Microsoft YaHei','PingFang SC','sans-serif'],'text-size':['interpolate',['linear'],['zoom'],6,11,10,14],'text-variable-anchor':['top','bottom','left','right'],'text-padding':12},paint:{'text-color':'#4c5e70','text-halo-color':'#ffffff','text-halo-width':2}});
    const names=cities.features.map(f=>f.properties.name);
    for(const region of basemapRegions){
      const id='basemap-'+region+'-county-names';
      if(map.getLayer(id))map.setFilter(id,['all',['==',['get','kind'],'city'],['!', ['in',['get','name'],['literal',names]]]]);
    }
  }catch(error){console.warn('Administrative city labels:',error);toast('地级市名称文件读取失败；道路数据仍可使用。');}
}

async function loadDataset() {
  [catalog] = await Promise.all([readJSON('/data/catalog.json?v='+DISPLAY_VERSION),mapLoaded]);
  byId = new Map(catalog.map(r => [r.id,r]));
  catalog.forEach(r => {
    r.searchText = [r.code,r.name,roadPresentation(r).title,r.railwayRef,r.from,r.to,r.provinceName,...r.aliases,...r.provinces].join(' ').toUpperCase().replace(/\s/g,'');
  });
  ready=true;
  updateList();
  await loadBasemap();
  await addPrefectureLabels();
  map.addSource('network',{type:'geojson',data:resourceURL('/data/display/overview.geojson?v='+DISPLAY_VERSION),tolerance:.7});
  map.addSource('rail-network',{type:'geojson',data:empty(),tolerance:.7});
  const rounded={'line-cap':'round','line-join':'round'};
  for(const {kind:type,prefix,color} of layerStyles){
    const filter=['==',['get','kind'],type];
    const minzoom=type==='provincial'?5.5:0;
    const source=type==='highspeed'||type==='railway'?'rail-network':'network';
    const rail=type==='highspeed'||type==='railway';
    const width=rail?['interpolate',['linear'],['zoom'],3,1.8,5,2.2,7,2.8]:['interpolate',['linear'],['zoom'],3,.5,5,.85,7,1.5];
    const paint={'line-color':color,'line-opacity':['interpolate',['linear'],['zoom'],3,.52,5,.7,7,.85],'line-width':width};
    map.addLayer({id:prefix+'-network',type:'line',source,minzoom,maxzoom:7,layout:rounded,filter,paint},'selected-halo');
    if(rail)map.addLayer({id:prefix+'-network-pattern',type:'line',source,minzoom,maxzoom:7,layout:{'line-cap':'butt','line-join':'round'},filter,paint:{...paint,'line-color':'#ffffff','line-dasharray':[2,2]}},'selected-halo');
    map.addLayer({id:prefix+'-hit',type:'line',source,minzoom,maxzoom:7,filter,paint:{'line-color':'#000','line-opacity':0,'line-width':10}},'selected-halo');
  }
  map.addSource('network-precise',{type:'geojson',data:empty(),tolerance:0,maxzoom:18,buffer:128});
  for(const {kind,prefix,color,opacity} of layerStyles){
    const filter=['==',['get','kind'],kind];
    const rail=kind==='highspeed'||kind==='railway';
    const paint={'line-color':color,'line-width':['interpolate',['linear'],['zoom'],7,rail?2.8:kind==='national'?1:1.4,14,rail?4:2.8,18,6],'line-opacity':opacity};
    map.addLayer({id:prefix+'-precise',type:'line',source:'network-precise',minzoom:7,layout:rounded,filter,paint},'selected-halo');
    if(rail)map.addLayer({id:prefix+'-precise-pattern',type:'line',source:'network-precise',minzoom:7,layout:{'line-cap':'butt','line-join':'round'},filter,paint:{...paint,'line-color':'#ffffff','line-dasharray':[2,2]}},'selected-halo');
    map.addLayer({id:prefix+'-precise-hit',type:'line',source:'network-precise',minzoom:7,filter,paint:{'line-color':'#000','line-opacity':0,'line-width':12}},'selected-halo');
  }
  updateNetworkVisibility();
  const hitLayers=['selected-hit',...layerStyles.flatMap(({prefix})=>[prefix+'-hit',prefix+'-precise-hit'])];
  map.on('click',e=>{
    const hits=map.queryRenderedFeatures(e.point,{layers:hitLayers});
    const f=hits.find(f=>f.layer.id==='selected-hit')||hits[0];
    if(f){const id=f.properties.id;if(selections.has(id))removeSelection(id);else selectRoad(byId.get(id));}
  });
  for(const id of hitLayers){
    map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',id,()=>{map.getCanvas().style.cursor='';});
  }
  map.on('moveend',()=>{scheduleNetwork();for(const entry of selections.values())if(entry.road.hasGeometry&&entry.tier!==routeTier()&&!isRouteBusy(entry))selectRoad(entry.road,{focus:false});});
  map.on('mousemove',e=>{
    if(map.isMoving()||e.originalEvent.buttons){clearHoverPrefetch();return;}
    const feature=map.queryRenderedFeatures(e.point,{layers:hitLayers})[0];
    scheduleHoverPrefetch(feature?byId.get(feature.properties.id):null);
  });
  map.getCanvas().addEventListener('pointerleave',clearHoverPrefetch);
  updateDetailedNetwork();
  const ids=decodeURIComponent(location.hash.slice(1)).split(',').slice(0,10);
  await Promise.all(ids.map(id=>byId.has(id)?selectRoad(byId.get(id)):null));
}

function updateList(reset=true) {
  if (!ready) return;
  if (reset) { displayed=80;$('road-list').scrollTop=0; }
  const query=$('search').value.toUpperCase().replace(/\s/g,'');
  const roadQuery=/铁路|高铁|客专/.test(query)?query:query.replace(/国道|高速公路|高速/g,'');
  const province=$('province').value;
  result=catalog.filter(r => (kind==='all'||r.kind===kind) && (!province||r.provinces.includes(province)||r.provinceName===province) && (!query||r.searchText.includes(query)||r.searchText.includes(roadQuery)));
  const exact = r => r.code===query||r.code==='G'+query||r.code==='S'+query;
  result.sort((a,b)=>(query ? Number(exact(b))-Number(exact(a)) : 0)||({national:0,expressway:1,provincial:2,highspeed:3,railway:4}[a.kind]-{national:0,expressway:1,provincial:2,highspeed:3,railway:4}[b.kind])||(isRail(a)&&isRail(b)?a.name.localeCompare(b.name,'zh'):a.code.localeCompare(b.code,'en',{numeric:true}))||(a.provinceName||'').localeCompare(b.provinceName||'','zh'));
  renderList();
}

function renderList() {
  const list=$('road-list');
  if(!result.length){list.innerHTML='<div class="no-results">没有找到匹配道路。<br>试试其他编号、名称，或放宽筛选。</div>';return;}
  list.innerHTML=result.slice(0,displayed).map(r=>{
    const {title,subtitle}=roadPresentation(r);
    const badges=r.hasGeometry?'':'<span class="missing-badge">暂无走向数据</span>';
    const entry=selections.get(r.id),loading=isRouteBusy(entry);
    return `<button class="road-card ${entry?'selected':''} ${loading?'is-loading':''}" data-id="${escapeHTML(r.id)}" aria-pressed="${Boolean(entry)}" aria-busy="${loading}">${roadShield(r)}<span class="road-info"><span class="road-name" title="${escapeHTML(title)}">${escapeHTML(title)}</span><span class="road-meta" title="${escapeHTML(subtitle)}">${escapeHTML(subtitle)}</span>${badges}</span><span class="row-arrow">${loading?'<span class="spinner" aria-hidden="true"></span>':entry?'✓':'+'}</span></button>`;
  }).join('');
  if(result.length>displayed){const more=document.createElement('button');more.className='more-button';more.textContent=`加载更多 · 剩余 ${result.length-displayed} 条`;more.addEventListener('click',()=>{displayed+=80;renderList();});list.append(more);}
}

function syncSelections(){
  if(renderedGeometryRevision!==geometryRevision){
    const features=[];
    for(const entry of selections.values())for(const f of entry.geo?.features||[])features.push({...f,properties:{...f.properties,id:entry.road.id,selectionColor:entry.color}});
    map.getSource('selected').setData({type:'FeatureCollection',features});
    renderedGeometryRevision=geometryRevision;
  }
  updateNetworkEmphasis();
  updateRailLegend();
  $('selections').hidden=!selections.size;
  $('selected-count').textContent=`${selections.size} / 10`;
  $('selected-list').innerHTML=[...selections.values()].map(entry=>{
    const {title,subtitle}=roadPresentation(entry.road);
    return `<div class="selected-row ${selected?.id===entry.road.id?'active':''}"><button class="selection-focus" data-focus="${escapeHTML(entry.road.id)}" aria-busy="${isRouteBusy(entry)}"><i style="background:${entry.color}"></i>${roadShield(entry.road,true)}<span class="selection-name" title="${escapeHTML(title+' · '+subtitle)}"><span>${escapeHTML(title)}</span><small>${escapeHTML(subtitle)}</small></span>${entry.error?'<em>读取失败 · 点击重试</em>':isRouteBusy(entry)?'<em class="selection-loading"><span class="spinner" aria-hidden="true"></span>正在加载路线…</em>':''}</button><button data-remove="${escapeHTML(entry.road.id)}" aria-label="移除 ${escapeHTML(routeLabel(entry.road))}">×</button></div>`;
  }).join('');
  history.replaceState(null,'',location.pathname+location.search+(selections.size?'#'+[...selections.keys()].join(','):''));
  updateSelectionStates();
}

function updateSelectionStates(){
  for(const button of $('road-list').querySelectorAll('[data-id]')){
    const entry=selections.get(button.dataset.id),loading=isRouteBusy(entry);
    button.classList.toggle('selected',Boolean(entry));button.classList.toggle('is-loading',loading);
    button.setAttribute('aria-pressed',String(Boolean(entry)));button.setAttribute('aria-busy',String(loading));
    button.querySelector('.row-arrow').innerHTML=loading?'<span class="spinner" aria-hidden="true"></span>':entry?'✓':'+';
  }
  const pending=[...selections.values()].filter(isRouteBusy);
  $('route-loading').hidden=!pending.length;
  $('route-loading-text').textContent=pending.length?'正在加载 '+pending.map(entry=>routeLabel(entry.road)).join('、')+'…':'';
  const entry=selected&&selections.get(selected.id),status=$('detail').querySelector('.route-status');
  $('detail').setAttribute('aria-busy',String(isRouteBusy(entry)));
  if(status){
    status.hidden=!isRouteBusy(entry)&&!entry?.error;status.classList.toggle('error',Boolean(entry?.error));
    status.innerHTML=entry?.error?'加载失败，请点击左侧路线重试。':'<span class="spinner" aria-hidden="true"></span><span>正在加载路线…</span>';
  }
}

function clearHoverPrefetch(){
  clearTimeout(hoverTimer);
  if(hoverRoute)routeLoader.cancelPrefetch(hoverRoute);
  hoverRoute=null;
}

function scheduleHoverPrefetch(road){
  const id=road?.hasGeometry?routeKey(road.id):null;
  if(id===hoverRoute)return;
  clearHoverPrefetch();
  if(!id||routeLoader.isCached(id)||selections.has(road.id))return;
  hoverRoute=id;
  hoverTimer=setTimeout(()=>routeLoader.prefetch(id),160);
}

function focusSelection(entry){
  selected=entry.road;
  showDetail(entry.road);
  syncSelections();
}

async function selectRoad(road,{focus=true}={}){
  if(!road)return;
  let entry=selections.get(road.id);
  if(entry&&!entry.error&&(isRouteBusy(entry)||entry.tier===routeTier())){if(focus)focusSelection(entry);return;}
  if(!entry){
    if(selections.size>=10){toast('最多可选 10 条路线，请先在左侧移除一条。');return;}
    const used=new Set([...selections.values()].map(e=>e.color));
    entry={road,color:isRail(road)?layerStyles.find(layer=>layer.kind===road.kind).color:(selectionColors[road.kind]||selectionColors.expressway).find(c=>!used.has(c)),controller:new AbortController()};
    selections.set(road.id,entry);
  }else{entry.controller=new AbortController();entry.error=null;}
  const key=routeKey(road.id);entry.requestKey=key;
  entry.loading=Boolean(road.hasGeometry&&!routeLoader.isCached(key));
  if(focus)focusSelection(entry);else syncSelections();
  if(!road.hasGeometry){toast('该线路已收录，暂未取得走向数据，无法在地图上显示。');return;}
  try{
    const data=await routeLoader.load(key);
    if(selections.get(road.id)!==entry||entry.controller.signal.aborted)return;
    entry.geo=data;entry.tier=key.split('@')[1];geometryRevision++;entry.loading=false;entry.rendering=true;syncSelections();
  }catch(error){
    if(error.name!=='AbortError'&&selections.get(road.id)===entry){entry.error=error;console.error('Route geometry:',error);toast(`${routeLabel(road)} 走向读取失败：${error.message}。点击左侧已选路线可重试。`);}
  }finally{entry.loading=false;if(selections.get(road.id)===entry)syncSelections();}
}

function removeSelection(id){
  const entry=selections.get(id);entry?.controller.abort();if(entry?.requestKey)routeLoader.cancel(entry.requestKey);
  if(entry?.geo)geometryRevision++;
  selections.delete(id);
  if(selected?.id===id){const next=[...selections.values()].at(-1);if(next)focusSelection(next);else closeDetail();}
  syncSelections();
}

function closeDetail(){
  selected=null;$('detail').hidden=true;$('south-sea').hidden=false;
  document.querySelector('.map-panel').classList.remove('has-detail');
  $('overview-route').disabled=true;
  if(ready)syncSelections();
}

function updateZoomLimit(){
  if(settingZoomLimit)return;
  settingZoomLimit=true;
  try{
    const limit=atlasViews.minimumZoom(map.getCenter().lat);
    if(Number.isFinite(limit)&&Math.abs(map.getMinZoom()-limit)>.002)map.setMinZoom(limit);
  }finally{settingZoomLimit=false;}
}

function rememberView(){
  viewBookmarks.save(map);$('restore-view').disabled=false;
}

function overviewRoute(){
  if(!selected?.bounds)return;
  rememberView();
  const [w,s,e,n]=selected.bounds;
  const right=mobileDrawer.isMobile()?20:Math.min(360,map.getContainer().clientWidth*.4);
  map.fitBounds([[w,s],[e,n]],{padding:{top:mobileDrawer.isMobile()?65:110,bottom:mobileDrawer.isMobile()?125:60,left:mobileDrawer.isMobile()?20:35,right},retainPadding:false,maxZoom:10,duration:650});
}

function showDetail(r){
  const {title,subtitle}=roadPresentation(r);
  $('detail').hidden=false;$('south-sea').hidden=true;$('overview-route').disabled=!r.bounds;
  document.querySelector('.map-panel').classList.add('has-detail');
  $('detail').innerHTML=`<button class="detail-close" aria-label="关闭路线详情">×</button><div class="detail-top">${roadShield(r)}</div><h2>${escapeHTML(title)}</h2><p class="detail-subtitle">${escapeHTML(subtitle)}</p><div class="route-status" role="status" aria-live="polite" hidden></div><div class="province-tags">${r.provinces.map(p=>`<span>${escapeHTML(compactProvince(p))}</span>`).join('')}</div>${r.hasGeometry?'':'<p class="empty-route">暂无走向数据</p>'}`;
  $('detail').querySelector('.detail-close').addEventListener('click',closeDetail);
}

function resetSelection(){
  for(const entry of selections.values()){entry.controller.abort();if(entry.requestKey)routeLoader.cancel(entry.requestKey);if(entry.geo)geometryRevision++;}
  selections.clear();closeDetail();
}

function showError(error) {
  console.error(error);$('loading').hidden=true;
  $('road-list').innerHTML=`<div class="list-state error-state">本地数据读取失败。<br>${escapeHTML(error.message)}<br><button id="retry-data" class="more-button">重新加载本地数据</button></div>`;
  $('retry-data').addEventListener('click',()=>location.reload());
}

$('search').addEventListener('input',()=>updateList());
$('province').addEventListener('change',()=>updateList());
document.querySelectorAll('[data-kind]').forEach(button=>button.addEventListener('click',()=>{
  kind=button.dataset.kind;document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',b===button));updateList();
  if(kind==='highspeed'||kind==='railway'){
    if(kind==='highspeed')highspeedVisible=true;else railwayVisible=true;
    $('toggle-'+kind).setAttribute('aria-pressed','true');updateNetworkVisibility();
  }
}));
document.querySelectorAll('[data-query]').forEach(button=>button.addEventListener('click',()=>{
  $('search').value=button.dataset.query;kind='all';$('province').value='';
  document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',b.dataset.kind==='all'));
  updateList();const exact=result.find(r=>r.code===button.dataset.query);if(exact){selectRoad(exact);mobileDrawer.collapse();}
}));
$('road-list').addEventListener('click',event=>{const button=event.target.closest('[data-id]');if(button){if(selections.has(button.dataset.id))removeSelection(button.dataset.id);else{selectRoad(byId.get(button.dataset.id));mobileDrawer.collapse();}}});
$('road-list').addEventListener('pointerover',event=>{const button=event.target.closest('[data-id]');scheduleHoverPrefetch(button?byId.get(button.dataset.id):null);});
$('road-list').addEventListener('pointerleave',clearHoverPrefetch);
$('road-list').addEventListener('focusin',event=>{const button=event.target.closest('[data-id]');if(button)scheduleHoverPrefetch(byId.get(button.dataset.id));});
$('road-list').addEventListener('focusout',clearHoverPrefetch);
$('reset-map').addEventListener('click',()=>{rememberView();map.fitBounds(chinaView,{padding:30,retainPadding:false,duration:650});});
$('overview-route').addEventListener('click',overviewRoute);
$('restore-view').addEventListener('click',()=>{const camera=viewBookmarks.take();if(camera)map.easeTo({...camera,padding:0,duration:650});$('restore-view').disabled=true;});
for(const [id,type] of [['toggle-national','national'],['toggle-express','express']])$(id).addEventListener('click',()=>{
  if(type==='national')nationalVisible=!nationalVisible;else expressVisible=!expressVisible;
  $(id).setAttribute('aria-pressed',String(type==='national'?nationalVisible:expressVisible));updateNetworkVisibility();
});
for(const type of ['highspeed','railway'])$('toggle-'+type).addEventListener('click',()=>{
  if(type==='highspeed')highspeedVisible=!highspeedVisible;else railwayVisible=!railwayVisible;
  $('toggle-'+type).setAttribute('aria-pressed',String(kindVisible(type)));updateNetworkVisibility();
});
$('clear-selections').addEventListener('click',resetSelection);
$('selected-list').addEventListener('click',e=>{
  const remove=e.target.closest('[data-remove]');if(remove){removeSelection(remove.dataset.remove);return;}
  const focus=e.target.closest('[data-focus]');if(focus){selectRoad(byId.get(focus.dataset.focus));mobileDrawer.collapse();}
});
$('toggle-basemap').addEventListener('click',()=>{
  if(!basemapRegions.size){toast('离线底图正在准备，完成后会自动载入。');return;}
  basemapVisible=!basemapVisible;$('toggle-basemap').setAttribute('aria-pressed',String(basemapVisible));
  for(const region of basemapRegions)for(const layer of window.chinaBasemapLayers(region))map.setLayoutProperty(layer.id,'visibility',basemapVisible?'visible':'none');
});
document.addEventListener('keydown',event=>{
  if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}
  if(event.key==='Escape'){if(mobileDrawer.isOpen())mobileDrawer.collapse();else closeDetail();}
});

(async()=>{
  try {
    if(!window.maplibregl)throw new Error('Local MapLibre library missing');
    const china=await readJSON('/data/china.geojson');initMap(china);await loadDataset();
  }catch(error){showError(error);}
})();
