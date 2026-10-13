'use strict';
// All sources are local country archives. Names use fonts installed on this computer.
window.chinaBasemapLayers = (region) => {
  const source='basemap-'+region;
  const layer=(name,type,sourceLayer,paint,layout={},extra={})=>({id:source+'-'+name,type,source,'source-layer':sourceLayer,paint,layout,...extra});
  const name=['coalesce',['get','name'],['get','ref'],''];
  const font=['Microsoft YaHei','PingFang SC','sans-serif'];
  const roadWidth=['interpolate',['linear'],['zoom'],5,.6,10,1.2,13,3,16,7,18,16];
  const minorWidth=['interpolate',['linear'],['zoom'],10,.5,13,1.4,16,4,18,9];
  const major=['in',['get','kind'],['literal',['motorway','trunk','primary','secondary']]];
  const visibleMajor=['any',['in',['get','kind'],['literal',['motorway','trunk']]],['all',['>=',['zoom'],8],['==',['get','kind'],'primary']],['all',['>=',['zoom'],10],['==',['get','kind'],'secondary']]];
  const rails=['in',['get','kind'],['literal',['rail','light_rail','tram','subway','narrow_gauge']]];
  return [
    layer('land','fill','land',{'fill-color':['match',['get','kind'],'forest','#e3eadf','park','#e7ede3','grass','#eef1e8','grassland','#eef1e8','sand','#f0eadf','beach','#f0eadf','scrub','#ecefe6','#edf0e7'],'fill-opacity':.5},{},{minzoom:6}),
    layer('sites','fill','sites',{'fill-color':['match',['get','kind'],'industrial','#eaecef','residential','#eef0f2','commercial','#e8ebee','#edf0f2'],'fill-opacity':.4},{},{minzoom:11}),
    layer('water','fill','water_polygons',{'fill-color':['match',['get','kind'],'glacier','#e3eef3','#c9dfe9'],'fill-opacity':.9}),
    layer('water-lines','line','water_lines',{'line-color':'#bbd6e3','line-width':['interpolate',['linear'],['zoom'],8,.7,13,1.7,17,4]}, {'line-cap':'round','line-join':'round'}),
    layer('buildings','fill','buildings',{'fill-color':'#e2e5e9','fill-outline-color':'#d2d8df','fill-opacity':['interpolate',['linear'],['zoom'],13,0,14,.8]}, {},{minzoom:14}),
    layer('minor-casing','line','streets',{'line-color':'#dce1e6','line-width':['interpolate',['linear'],['zoom'],10,1.7,13,2.6,16,5.2,18,10.2]}, {'line-cap':'round','line-join':'round'},{minzoom:12,filter:['all',['!',major],['!',rails]]}),
    layer('minor-roads','line','streets',{'line-color':'#ffffff','line-width':minorWidth}, {'line-cap':'round','line-join':'round'},{minzoom:12,filter:['all',['!',major],['!',rails]]}),
    layer('major-casing','line','streets',{'line-color':'#d8dee5','line-width':['interpolate',['linear'],['zoom'],5,2.2,10,2.8,13,4.6,16,8.6,18,17.6]}, {'line-cap':'round','line-join':'round'},{minzoom:5,filter:visibleMajor}),
    layer('major-roads','line','streets',{'line-color':['match',['get','kind'],'motorway','#edf1f5','trunk','#f5f6f8','#ffffff'],'line-width':roadWidth}, {'line-cap':'round','line-join':'round'},{minzoom:5,filter:visibleMajor}),
    layer('rail','line','streets',{'line-color':'#a3adba','line-width':1,'line-dasharray':[3,2]}, {},{minzoom:10,filter:rails}),
    layer('water-names','symbol','water_lines_labels',{'text-color':'#7e9dac','text-halo-color':'#ffffff','text-halo-width':1},{'text-field':name,'text-font':font,'text-size':11,'symbol-placement':'line','text-max-angle':30},{minzoom:12}),
    layer('lake-names','symbol','water_polygons_labels',{'text-color':'#7e9dac','text-halo-color':'#ffffff','text-halo-width':1},{'text-field':name,'text-font':font,'text-size':11},{minzoom:10}),
    layer('street-names','symbol','street_labels',{'text-color':'#8a96a5','text-halo-color':'#ffffff','text-halo-width':1.5},{'text-field':name,'text-font':font,'text-size':['interpolate',['linear'],['zoom'],12,10,16,12],'symbol-placement':'line','text-max-angle':30,'symbol-spacing':300},{minzoom:12}),
    ...[['county',9,['city']],['town',11,['town']],['district',12,['suburb','quarter']],['village',13,['village','hamlet']],['neighbourhood',14,['neighbourhood']]].map(([category,minzoom,kinds])=>layer(category+'-names','symbol','place_labels',{'text-color':'#667b91','text-halo-color':'#ffffff','text-halo-width':1.6},{'text-field':name,'text-font':font,'text-size':category==='county'?12:11,'text-variable-anchor':['center','top','bottom','left','right'],'text-padding':8},{minzoom,filter:['in',['get','kind'],['literal',kinds]]})),
    layer('capital-detail','symbol','place_labels',{'text-color':'#516a83','text-halo-color':'#ffffff','text-halo-width':2},{'text-field':name,'text-font':font,'text-size':14,'text-padding':10},{minzoom:13,filter:['in',['get','kind'],['literal',['capital','state_capital']]]}),
  ];
};
