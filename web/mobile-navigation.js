'use strict';
const navigationMedia=matchMedia('(max-width:780px)');
const navigationShade=document.createElement('button');navigationShade.type='button';navigationShade.className='navigation-shade';navigationShade.setAttribute('aria-label','关闭导航');document.body.append(navigationShade);
const mobileActions=document.createElement('div');mobileActions.className='mobile-sidebar-actions';mobileActions.innerHTML='<button class="button primary" data-mobile-control="export">生成配置</button><button class="text-button" data-mobile-control="account-btn">账号与密码设置</button><button class="text-button" data-mobile-control="appearance">外观设置</button>';$('.sidebar-bottom').before(mobileActions);
iconPaths.menu='M4 6h16M4 12h16M4 18h16';$('#open-navigation').innerHTML=icon('menu');
$('#edit-current-space').innerHTML=actionIcon('edit');
let navigationReturnFocus=null;
function setNavigation(open){
  open=!!open&&navigationMedia.matches;document.body.classList.toggle('navigation-open',open);$('#open-navigation').setAttribute('aria-expanded',open);$('.sidebar').inert=navigationMedia.matches&&!open;
  if(open){navigationReturnFocus=document.activeElement;$('.sidebar').setAttribute('role','dialog');$('.sidebar').setAttribute('aria-label','主导航');$('.sidebar').setAttribute('aria-modal','true');$('.sidebar .nav-item.active')?.focus()}
  else{$('.sidebar').removeAttribute('role');$('.sidebar').removeAttribute('aria-modal');if(navigationReturnFocus?.isConnected&&navigationMedia.matches)navigationReturnFocus.focus();navigationReturnFocus=null}
}
$('#open-navigation').onclick=()=>setNavigation(true);navigationShade.onclick=()=>setNavigation(false);
$('#navigation').addEventListener('click',e=>{if(e.target.closest('a'))setNavigation(false)});
$$('[data-mobile-control]').forEach(b=>b.onclick=()=>{setNavigation(false);$('#'+b.dataset.mobileControl).click()});
$('#edit-current-space').onclick=()=>{if(liveWorkspace.state)editSpace(spaceId)};
document.addEventListener('keydown',e=>{
  if(!document.body.classList.contains('navigation-open'))return;
  if(e.key==='Escape'){e.preventDefault();setNavigation(false)}
  if(e.key==='Tab'){const focusable=$$('a,button,select,input', $('.sidebar')).filter(el=>!el.disabled&&el.getClientRects().length),first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}
});
navigationMedia.addEventListener('change',()=>setNavigation(false));setNavigation(false);
