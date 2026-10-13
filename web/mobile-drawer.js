'use strict';
window.atlasMobile={create({sidebar,toggle,close,mapPanel,search,header,workspace,onLayout}){
  const media=matchMedia('(max-width:680px), (max-width:1000px) and (max-height:500px)');
  let expanded=false,gesture=null,suppressClick=false,layoutFrame;
  function setOpen(value){
    expanded=media.matches&&value;
    sidebar.classList.toggle('drawer-open',expanded);
    sidebar.classList.remove('drawer-peeking','drawer-dragging');
    sidebar.style.removeProperty('height');
    toggle.setAttribute('aria-expanded',String(expanded));
    toggle.setAttribute('aria-label',expanded?'收起路线列表':'展开路线列表');
    mapPanel.inert=expanded;
    if(!expanded&&media.matches&&sidebar.contains(document.activeElement))document.activeElement.blur();
  }
  function updateLayout(){
    cancelAnimationFrame(layoutFrame);
    layoutFrame=requestAnimationFrame(()=>{
      if(media.matches&&window.visualViewport){
        const height=Math.max(0,window.visualViewport.height-header.getBoundingClientRect().bottom);
        workspace.style.setProperty('--mobile-workspace-height',height+'px');
      }else workspace.style.removeProperty('--mobile-workspace-height');
      onLayout();
    });
  }
  toggle.addEventListener('click',()=>{if(suppressClick){suppressClick=false;return;}setOpen(!expanded);});
  close.addEventListener('click',()=>{setOpen(false);toggle.focus({preventScroll:true});});
  search.addEventListener('focus',()=>{if(media.matches)setOpen(true);});
  search.addEventListener('keydown',event=>{if(media.matches&&event.key==='Enter'){event.preventDefault();search.blur();}});
  toggle.addEventListener('pointerdown',event=>{
    if(!media.matches||event.button!==0)return;
    suppressClick=false;
    gesture={id:event.pointerId,y:event.clientY,height:sidebar.getBoundingClientRect().height,expanded};
    toggle.setPointerCapture(event.pointerId);
  });
  toggle.addEventListener('pointermove',event=>{
    if(!gesture||gesture.id!==event.pointerId)return;
    const delta=gesture.y-event.clientY;
    if(Math.abs(delta)<6)return;
    event.preventDefault();
    const min=workspace.clientHeight-mapPanel.clientHeight;
    const height=Math.max(min,Math.min(workspace.clientHeight,gesture.height+delta));
    sidebar.classList.add('drawer-dragging','drawer-peeking');
    sidebar.style.height=height+'px';
  });
  toggle.addEventListener('pointerup',event=>{
    if(!gesture||gesture.id!==event.pointerId)return;
    const delta=gesture.y-event.clientY;
    const previous=gesture.expanded;gesture=null;
    if(Math.abs(delta)>6){suppressClick=true;setOpen(Math.abs(delta)>40?delta>0:previous);}
    else{sidebar.classList.remove('drawer-peeking','drawer-dragging');sidebar.style.removeProperty('height');}
  });
  toggle.addEventListener('pointercancel',()=>{gesture=null;setOpen(expanded);});
  media.addEventListener('change',()=>{gesture=null;setOpen(false);updateLayout();});
  window.addEventListener('resize',updateLayout);
  window.visualViewport?.addEventListener('resize',updateLayout);
  setOpen(false);updateLayout();
  return {collapse:()=>setOpen(false),isMobile:()=>media.matches,isOpen:()=>expanded};
}};
