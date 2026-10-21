'use strict';
// Spaces select shared resources and keep their own generation settings.
Object.assign(pages, {
  spaces:['配置空间','为手机、电脑或网关保存独立配置，按需导入、复制、调整和生成。','新建配置空间'],
  inbounds:['入站','管理接收连接的协议、监听端口与连接参数。','添加入站'],
  dns:['MosDNS','设置当前配置空间的 DNS 上游、监听与分流解析。',''],
  hermes:['Hermes 接入','一行命令将 YoMesh 接入 Hermes Agent，用自然语言查分流、加规则、测速节点与获取订阅链接。','']
});
pages.library[0]='规则';pages.library[2]='添加规则';
pages.library[1]='全部规则统一管理。每条规则选择匹配器或订阅，在分流策略中按需使用。';
pages.groups[1]='选择节点和筛选方式，组成规则可用的出口。';
pages.library[1]='设置匹配内容和出口，按需启用规则。';
pages.policies[0]='分流顺序';
pages.policies[1]='从上到下依次匹配，拖动调整，兜底固定最后。';
Object.assign(iconPaths,{
  spaces:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  hermes:'M12 3l2.4 5.2L20 10.5l-5.6 2.3L12 18l-2.4-5.2L4 10.5l5.6-2.3z',
  inbounds:'M14 4h6v16h-6M3 12h12M10 7l5 5-5 5',
  dns:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c4 4 4 14 0 18M12 3c-4 4-4 14 0 18'
});
Object.assign(actionIconPaths,{copy:'M8 8h13v13H8zM16 8V3H3v13h5',download:'M12 3v12M7 10l5 5 5-5M4 16v5h16v-5'});
const activeSpace=()=>spaceCatalog?.spaces.find(s=>s.id===spaceId);
let spaceSwitchBusy=false, spaceListSerial=0,moreNavigationOpen=false;

nav=function(){
  const countFor=p=>p==='spaces'?spaceCatalog?.spaces?.length:Array.isArray(data[p])?data[p].length:undefined;
  const link=p=>{const c=countFor(p);return `<a href="#${p}" class="nav-item ${page===p?'active':''}" ${page===p?'aria-current="page"':''}>${icon(p)}<span>${pages[p][0]}</span>${c!==undefined?`<span class="nav-count">${c}</span>`:''}</a>`};
  $('#navigation').innerHTML=`${link('spaces')}<div class="nav-group">节点资源</div>${['sources','nodes'].map(link).join('')}<div class="nav-group">代理设置</div>${['groups','library','inbounds'].map(link).join('')}<button type="button" class="nav-item" id="nav-base-config" ${$('#base-config')?.disabled?'disabled':''}>${icon('settings')}<span>基础设置</span></button><div class="nav-group">智能联动</div>${link('hermes')}`;
  $('#nav-base-config').onclick=()=>{if(typeof setNavigation==='function')setNavigation(false);$('#base-config').click()};
};
function paintSpaceSelect(){
  $('#space-select').innerHTML=(spaceCatalog?.spaces||[]).map(s=>`<option value="${esc(s.id)}" ${s.id===spaceId?'selected':''}>${esc(s.name)}</option>`).join('');
}
const renderWithSpaces=render;
render=function(){
  policyView='rules';paintSpaceSelect();
  if(!['spaces','inbounds','dns','hermes'].includes(page)){renderWithSpaces();return}
  nav();$('#breadcrumb').textContent=pages[page][0];$('#page-title').textContent=pages[page][0];$('#page-description').textContent=pages[page][1];
  $('#create').hidden=['dns','hermes'].includes(page);$('#create').innerHTML=icon('plus')+pages[page][2];
  if(page==='spaces'){renderSpaces();return}
  if(page==='inbounds'){renderInbounds();return}
  if(page==='hermes'){if(typeof renderHermesPage==='function')renderHermesPage();return}
  renderDns();
};
// These panels are now independent navigation destinations.
planToolbar=function(){
  if(page!=='policies')return '';
  return `<div class="plan-toolbar"><div class="filter-row">${plans().length?`<label class="plan-select">共享分流模板<select id="active-plan">${plans().map(p=>`<option value="${esc(p.id)}" ${p.id===data.active_plan?'selected':''}>${esc(planLabel(p))}</option>`).join('')}</select></label>`:'<span class="summary-line">当前配置空间</span>'}${resourceMenu('分流模板','<button id="migration-report">迁移核对</button>')}</div></div>`;
};
bindPlanToolbar=function(){
  if($('#active-plan'))$('#active-plan').onchange=async e=>{
    e.target.disabled=true;
    try{if(dirty())await persist();adoptWorkspace(await api('/api/workspace/select-plan',{id:e.target.value,revision:liveWorkspace.revision}))}
    catch(error){render();toast(error.message)}
  };
  if($('#migration-report'))$('#migration-report').onclick=showMigrationReport;
};
route=function(){const next=location.hash.slice(1);page=pages[next]&&next!=='dns'?next:'spaces';query='';filter='all';selectedNodes.clear();render()};
page=pages[location.hash.slice(1)]&&location.hash.slice(1)!=='dns'?location.hash.slice(1):'spaces';
$('#create').onclick=()=>({spaces:()=>editSpace(),sources:editSource,nodes:addNode,groups:addGroup,library:addLibrary,policies:editPolicy,inbounds:editInbound})[page]?.();
$('#space-select').onchange=e=>switchSpace(e.target.value).catch(error=>{paintSpaceSelect();toast(error.message)});

async function saveSpaceDrafts(){
  if(liveWorkspace.saving||liveWorkspace.dragging||policyBusy)throw Error('请等待当前保存或拖动完成。');
  if(dirty())await persist();
  if(dnsFormDraft){
    const state=await api('/api/workspace/settings',{settings:dnsFormDraft,revision:liveWorkspace.revision});
    dnsFormDraft=null;dnsEdited=false;liveWorkspace.dnsEdited=false;adoptWorkspace(state,{repaint:false});
  }
}
async function refreshSpaceCatalog(){spaceCatalog=await api('/api/spaces');paintSpaceSelect();return spaceCatalog}
async function switchSpace(ident){
  if(spaceSwitchBusy)throw Error('正在切换配置空间。');
  if(ident===spaceId)return;
  spaceSwitchBusy=true;
  const previous=spaceId;
  try{
    await saveSpaceDrafts();
    for(const selector of ['#workspace','#navigation','.topbar'])$(selector).inert=true;
    await refreshSpaceCatalog();
    const catalog=await api('/api/spaces/select',{id:ident,revision:spaceCatalog.revision});
    const state=await api('/api/workspace',undefined,ident);
    spaceId=ident;spaceCatalog=catalog;
    try{localStorage.setItem('yomesh-space',ident)}catch{}
    draft=null;selectedGroup=null;selectedNodes.clear();query='';filter='all';policyUndo=null;
    dnsFormDraft=null;dnsEdited=false;liveWorkspace.dnsEdited=false;
    nodeProtocol='all';nodeState='all';nodeSort='original';libraryUsageFilter='all';
    groupSelectionFilter='all';ruleSelectionFilter='all';ruleChecks.clear();
    liveWorkspace.previewSerial++;rulePreviewSerial++;clearTimeout(liveWorkspace.previewTimer);clearTimeout(rulePreviewTimer);
    adoptWorkspace(state);toast(`已切换到「${activeSpace().name}」`);
  }catch(error){spaceId=previous;paintSpaceSelect();throw error}
  finally{spaceSwitchBusy=false;for(const selector of ['#workspace','#navigation','.topbar'])$(selector).inert=false}
}

function renderSpaces(){
  paintSpaceCards();const serial=++spaceListSerial;
  refreshSpaceCatalog().then(()=>{if(page==='spaces'&&serial===spaceListSerial)paintSpaceCards()}).catch(error=>{if(page==='spaces'){$('#space-list-status').textContent=error.message}});
}
function deleteSpace(ident){
  const item=spaceCatalog.spaces.find(s=>s.id===ident);
  showDialog('删除配置空间',`<p>删除「${esc(item.name)}」的生成方案？共享节点组、规则与分流策略会保留。</p><p class="error-message" id="space-delete-error" role="alert"></p><div class="dialog-actions"><button class="button" id="space-delete-cancel">取消</button><button class="button danger-button" id="space-delete-confirm">删除配置空间</button></div>`);
  $('#space-delete-cancel').onclick=()=>$('#dialog').close();
  $('#space-delete-confirm').onclick=async e=>{e.target.disabled=true;try{spaceCatalog=await api('/api/spaces/delete',{id:ident,revision:spaceCatalog.revision});$('#dialog').close();render()}catch(error){$('#space-delete-error').textContent=error.message;e.target.disabled=false}};
}
