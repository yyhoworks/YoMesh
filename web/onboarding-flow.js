'use strict';
// Onboarding relays and quick region group creation. Presentation only:
// member matching stays with the native kernel preview and backend Profile.
const RELAY_TTL=120000;
let nativeProviderRelay=null,portableSourceRelay=null;
function subscriptionFacts(){
  if(nativeMode())return{
    subs:Object.keys(nativeMap('proxy-providers')).length,
    groups:(nativeValue().mihomo?.['proxy-groups']||[]).length,
    rules:(typeof nativeUnitRows==='function'?nativeUnitRows():[]).filter(p=>!p.fallback).length
  };
  return{subs:(data.sources||[]).length,groups:(data.groups||[]).length,rules:(data.library||[]).length};
}
function quickCreateRegionGroup(region='HK'){
  if(nativeMode()){
    nativeGroupEditor(null);
    const index=NODE_GROUP_TEMPLATES.findIndex(t=>t.region===region),pill=index>=0?$(`[data-ng-template="${index}"]`):null;
    if(pill)pill.click();
    else if(region==='HK'&&$('#ng-filter')){$('#ng-filter').value='(?i)(港|HK|Hong\\s*Kong)';$('#ng-filter').dispatchEvent(new Event('input',{bubbles:true}))}
  }else{
    addGroup();
    if(region==='HK'&&$('[data-ag-template="0"]'))$('[data-ag-template="0"]').click();
  }
}
function relayNodeCount(fresh){
  return nativeMode()
    ?(liveWorkspace.state?.native_view?.nodes||[]).filter(n=>n.provider===fresh.name).length
    :(data.nodes||[]).filter(n=>n.source===fresh.id).length;
}
function relayMarkup(fresh){
  let text;
  if(nativeMode())text=`「${esc(fresh.name)}」当前有 ${relayNodeCount(fresh)} 个节点。`;
  else if(sourceStatus(fresh.id)?.refresh_state==='running')text=`「${esc(fresh.name)}」正在拉取节点，完成后这里显示数量。`;
  else text=`「${esc(fresh.name)}」已刷新，当前保留 ${relayNodeCount(fresh)} 个节点。`;
  return `<div class="notice source-relay" role="status"><span>${text}</span><button type="button" class="button" data-relay-nodes>查看节点</button><button type="button" class="button primary" data-relay-group>按地区快速建组</button></div>`;
}
function bindRelayButtons(fresh){
  const nodes=$('[data-relay-nodes]');
  if(nodes)nodes.onclick=()=>{
    nativeProviderRelay=null;portableSourceRelay=null;
    if(nativeMode())nativeNodeProvider=fresh.name||'all';
    location.hash='nodes';
    if(!nativeMode())setTimeout(()=>{filter=fresh.id||'all';renderNodes()},0);
  };
  const group=$('[data-relay-group]');
  if(group)group.onclick=()=>{nativeProviderRelay=null;portableSourceRelay=null;quickCreateRegionGroup('HK')};
}
const relayedNativeRefresh=nativeRefresh;
nativeRefresh=async function(section,name){
  await relayedNativeRefresh(section,name);
  if(section==='proxy-providers'){nativeProviderRelay={name,at:Date.now()};if(page==='sources')render()}
};
const relayNativeRenderProviders=nativeRenderProviders;
nativeRenderProviders=function(section){
  relayNativeRenderProviders(section);
  if(section!=='proxy-providers')return;
  const fresh=nativeProviderRelay&&Date.now()-nativeProviderRelay.at<RELAY_TTL?nativeProviderRelay:null;
  if(!fresh){nativeProviderRelay=null;return}
  $('#content').insertAdjacentHTML('afterbegin',relayMarkup(fresh));
  bindRelayButtons(fresh);
};
const relayedSourceAction=sourceAction;
sourceAction=async function(id,action,body={}){
  const result=await relayedSourceAction(id,action,body);
  if(action==='refresh'){const source=find('sources',id);portableSourceRelay={id,name:source?.name||'订阅',at:Date.now()};if(page==='sources')render()}
  return result;
};
const relayRenderSources=renderSources;
renderSources=function(){
  relayRenderSources();
  const fresh=portableSourceRelay&&Date.now()-portableSourceRelay.at<RELAY_TTL?portableSourceRelay:null;
  if(!fresh){portableSourceRelay=null;return}
  $('#content').insertAdjacentHTML('afterbegin',relayMarkup(fresh));
  bindRelayButtons(fresh);
};
window.addEventListener('hashchange',()=>{nativeProviderRelay=null;portableSourceRelay=null});
