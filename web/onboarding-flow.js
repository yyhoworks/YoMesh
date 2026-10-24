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
  const regions=(typeof spacePresetsCache==='object'&&spacePresetsCache?.regions?.length?spacePresetsCache.regions:[]);
  return `<div class="notice source-relay" role="status"><span>${text}</span><button type="button" class="button" data-relay-nodes>查看节点</button><button type="button" class="button primary" data-relay-regions>🧭 一键建齐地区组 ▾</button></div>${regions.length?`<div class="relay-regions" id="relay-regions" hidden>
    <div class="relay-regions-head">勾选要创建的地区组（自动测速，筛选正则自动带好；已存在同名组会跳过）：</div>
    ${regions.map(r=>`<label class="check-row"><input type="checkbox" data-relay-region="${esc(r.key)}" checked>${esc(r.badge||r.name)}</label>`).join('')}
    <div class="filter-row"><button type="button" class="button primary" id="relay-region-create">创建所选组</button><button type="button" class="text-button" id="relay-region-cancel">取消</button></div>
    <p id="relay-region-error" class="error-message" role="alert"></p>
  </div>`:''}`;
}
function bindRelayButtons(fresh){
  const nodes=$('[data-relay-nodes]');
  if(nodes)nodes.onclick=()=>{
    nativeProviderRelay=null;portableSourceRelay=null;
    if(nativeMode())nativeNodeProvider=fresh.name||'all';
    location.hash='nodes';
    if(!nativeMode())setTimeout(()=>{filter=fresh.id||'all';renderNodes()},0);
  };
  const toggle=$('[data-relay-regions]');
  if(toggle)toggle.onclick=()=>{
    const panel=$('#relay-regions');
    if(panel)panel.hidden=!panel.hidden;
  };
  const cancel=$('#relay-region-cancel');
  if(cancel)cancel.onclick=()=>{const panel=$('#relay-regions');if(panel)panel.hidden=true};
  const create=$('#relay-region-create');
  if(create)create.onclick=async()=>{
    const keys=$$('[data-relay-region]:checked').map(x=>x.dataset.relayRegion);
    const error=$('#relay-region-error');
    if(!keys.length){if(error)error.textContent='请至少勾选一个地区。';return}
    create.disabled=true;const label=create.textContent;create.textContent='正在创建…';if(error)error.textContent='';
    try{
      const result=await api('/api/workspace/quick-region-groups',{regions:keys},spaceId,60000);
      if(result.workspace)adoptWorkspace(result.workspace,{keepDraft:true});
      nativeProviderRelay=null;portableSourceRelay=null;
      toast(result.count?`已创建 ${result.count} 个地区组：${(result.added||[]).join('、')}。`:'没有需要新建的地区组（同名组已存在）。');
      if(page==='groups')render();else location.hash='groups';
    }catch(err){if(error)error.textContent=err.message;create.disabled=false;create.textContent=label}
  };
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
