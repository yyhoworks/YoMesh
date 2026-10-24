'use strict';

// Categories and rule ownership come from the backend's Profile view.
let nodeGroupCategory='lines';
const defaultGroupCategoryNames={lines:'线路组',home:'回家组',shared:'共用出口组'};
let groupSectionLabels={};
function updateGroupCategoryLabels(){
  const names=liveWorkspace.state?.group_category_names||spaceCatalog?.group_category_names||defaultGroupCategoryNames;
  const notes={lines:'按订阅来源与筛选条件组织香港、日本等线路。',home:'组织回家线路、手动节点和自建服务。',shared:'维护 Proxy、全球直连等可供多条规则复用的出口。'};
  groupSectionLabels=Object.fromEntries(Object.keys(defaultGroupCategoryNames).map(key=>[key,[names[key],notes[key]]]));
  if(page==='groups')$('#page-description').textContent=`管理${names.lines}、${names.home}和${names.shared}；规则专用出口在“分流规则”中配置。`;
}
function manageGroupCategoryNames(){
  const names=liveWorkspace.state?.group_category_names||defaultGroupCategoryNames;let revision=spaceCatalog.revision;
  showDialog('管理分类',`<form id="group-category-form"><p class="preview-note">名称在所有配置空间共用，并同步到节点分组与出口选择器；节点组名称、成员和规则引用保持不变。</p>${Object.entries(defaultGroupCategoryNames).map(([key,label])=>`<label class="field">${label}的显示名称<input name="${key}" value="${esc(names[key])}" maxlength="12" required></label>`).join('')}<p class="error-message" id="group-category-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="group-category-reset">恢复默认</button><button type="button" class="button" id="group-category-cancel">取消</button><button type="submit" class="button primary">保存名称</button></div></form>`);
  $('#group-category-reset').onclick=()=>{for(const [key,value] of Object.entries(defaultGroupCategoryNames))$(`[name="${key}"]`,$('#group-category-form')).value=value};
  $('#group-category-cancel').onclick=()=>$('#dialog').close();
  let busy=false;const prevent=event=>{if(busy)event.preventDefault()};$('#dialog').addEventListener('cancel',prevent);$('#dialog').addEventListener('close',()=>$('#dialog').removeEventListener('cancel',prevent),{once:true});
  $('#group-category-form').onsubmit=async event=>{event.preventDefault();if(busy)return;const names=Object.fromEntries(new FormData(event.target));busy=true;$$('button,input',event.target).forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;
    try{spaceCatalog=await api('/api/spaces/group-category-names',{names,revision});await reloadWorkspace();$('#dialog').close();toast('分类显示名称已保存，所有空间共用。')}
    catch(error){$('#group-category-error').textContent=error.message;try{revision=(await refreshSpaceCatalog()).revision}catch{}}
    finally{busy=false;$$('button,input',event.target).forEach(el=>el.disabled=false);$('#close-dialog').disabled=false}
  };
}
const groupInCategory=category=>category===nodeGroupCategory;
function groupCategoryTabs(locations){
  updateGroupCategoryLabels();
  const counts={lines:0,home:0,shared:0};locations.forEach(m=>{if(Object.hasOwn(counts,m.category))counts[m.category]++});
  return `<div class="group-category-toolbar"><div class="group-category-tabs" role="tablist" aria-label="节点组分类">${Object.entries(groupSectionLabels).map(([key,[label]])=>`<button type="button" class="button ${nodeGroupCategory===key?'active':''}" id="group-category-${key}" role="tab" aria-controls="group-category-panel" aria-selected="${nodeGroupCategory===key}" tabindex="${nodeGroupCategory===key?'0':'-1'}" data-group-category="${key}">${esc(label)}<span>${counts[key]}</span></button>`).join('')}</div><button type="button" class="text-button" id="manage-group-categories">管理分类</button></div>`;
}
function groupSectionMarkup(section,cards){
  if(!cards.length)return '';
  const [title,note]=groupSectionLabels[section];
  return `<section class="group-category-section" aria-label="${esc(title)}"><div class="group-section-heading"><h2>${esc(title)}<span>${cards.length}</span></h2><p>${note}</p></div><div class="flat-group-grid">${cards.join('')}</div></section>`;
}
function bindGroupCategory(paint){
  $('#manage-group-categories').onclick=manageGroupCategoryNames;
  const select=key=>{nodeGroupCategory=key;paint();$(`#group-category-${key}`).focus()};
  $$('[data-group-category]').forEach(button=>{
    button.onclick=()=>select(button.dataset.groupCategory);
    button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const keys=Object.keys(groupSectionLabels),index=keys.indexOf(nodeGroupCategory);select(event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):keys[(index+(event.key==='ArrowRight'?1:-1)+keys.length)%keys.length])}};
  });
}
function nativeGroupSectionCard(group,location){
  const members=group.proxies||[],sources=group.use||[];
  const overview=nativeGroupOverviewCache?.groups?.[group.name];
  const countBadge=overview?overview.count>0?badge(`匹配 ${overview.count} 节点`,'green'):badge('匹配 0 节点 · 请检查筛选','error'):'';
  const testable=overview&&overview.test_ids.length>0;
  const action=`<label class="scope-switch"><input type="checkbox" data-native-group-toggle="${esc(group.name)}" ${nativeSelected('groups',group.name)?'checked':''}><span class="switch-visual" aria-hidden="true"></span><span>启用此分组</span></label><button class="button" data-group-test="${esc(group.name)}" ${testable?'':'disabled'} title="${testable?'用匹配到的节点发起真实测速':'没有可测速的匹配节点'}">⚡ 测速</button><button class="button" data-group-open="${esc(group.name)}">编辑</button>${resourceMenu(group.name,`<button class="danger-action" data-native-group-delete="${esc(group.name)}">删除节点组</button>`)}`;
  return `<article class="panel flat-group-card ${nativeSelected('groups',group.name)?'chosen':''}" data-group-card="${esc(group.name)}"><div class="section-title"><h3>${esc(group.name)}</h3><span class="flat-group-badges">${countBadge}${badge(modes[group.type]||group.type)}</span></div><div class="native-group-members">${sources.length?`<small>订阅：${sources.map(esc).join('、')}</small>`:''}${members.length?`<small>成员：${members.map(esc).join('、')}</small>`:sources.length||group['include-all']||group['include-all-providers']||group['include-all-proxies']?'<small>成员：按来源动态选用</small>':'<small>尚未设置成员</small>'}${group.filter?`<small>筛选：${esc(group.filter)}</small>`:''}${location.role==='rules'?'<small>未关联规则的保留出口，可继续编辑。</small>':''}</div>${groupUsageMarkup(location,true)}<div class="flat-group-actions">${action}</div></article>`;
}
function groupUsageMarkup(location,native=false){
  const rows=location.used_by_rules||[];
  return rows.length?`<details class="group-rule-usage"><summary>被 ${rows.length} 条规则使用</summary><p class="preview-note">包含直接引用和候选子组关系，不代表当前流量已选择此组。</p><div>${rows.map(row=>`<button type="button" class="text-button" data-group-rule="${esc(row.id)}" data-group-rule-native="${native?'true':'false'}">${esc(row.name)}</button>`).join('')}</div></details>`:'<p class="preview-note group-rule-usage">尚无规则引用</p>';
}
function bindGroupUsage(){
  $$('[data-group-rule]').forEach(button=>button.onclick=()=>{const id=button.dataset.groupRule,isNative=button.dataset.groupRuleNative==='true';location.hash='library';if(isNative)nativePolicyEditor(id);else editPolicy(id)});
}
let nativeGroupOverviewCache={revision:null,space:null,groups:null,pending:false};
async function ensureNativeGroupOverview(){
  if(!nativeMode()||!liveWorkspace.state)return;
  const fresh=nativeGroupOverviewCache.space!==spaceId||nativeGroupOverviewCache.revision!==liveWorkspace.revision;
  if(!fresh||nativeGroupOverviewCache.pending)return;
  nativeGroupOverviewCache={revision:liveWorkspace.revision,space:spaceId,groups:nativeGroupOverviewCache.space===spaceId?nativeGroupOverviewCache.groups:null,pending:true};
  try{
    const result=await toolRequest('group-overview');
    nativeGroupOverviewCache={revision:liveWorkspace.revision,space:spaceId,groups:result?.groups||null,pending:false};
    if(page==='groups'&&nativeMode())nativeRenderGroups();
  }catch{
    // 徽标是辅助信息：内核忙或来源缺缓存时不打扰，本版内不再自动重试。
    nativeGroupOverviewCache={revision:liveWorkspace.revision,space:spaceId,groups:null,pending:false};
  }
}
nativeRenderGroups=function(){
  updateGroupCategoryLabels();
  const locations=nativePolicyView().group_management||[],all=nativeValue().mihomo['proxy-groups']||[],term=query.toLowerCase();
  const visible=locations.filter(m=>groupInCategory(m.category)&&m.name.toLowerCase().includes(term));
  const sections=Object.keys(groupSectionLabels).map(section=>groupSectionMarkup(section,visible.filter(m=>m.category===section).map(m=>nativeGroupSectionCard(all.find(g=>g.name===m.name),m)))).join('');
  $('#content').innerHTML=`${groupCategoryTabs(locations)}<div class="toolbar"><label class="search">${icon('search')}<input id="native-search" aria-label="搜索节点组" placeholder="搜索当前分类的节点组" value="${esc(query)}"></label><span class="summary-line" role="status">当前分类 ${visible.length} 个节点组${nativeGroupOverviewCache.groups?'':' · 匹配数计算中或不可用'}</span></div><div id="group-category-panel" role="tabpanel" aria-labelledby="group-category-${nodeGroupCategory}">${sections||'<p class="empty">当前分类没有符合条件的节点组，可清空搜索或切换分类。</p>'}</div>`;
  bindNativeSearch(nativeRenderGroups);bindGroupCategory(nativeRenderGroups);bindGroupUsage();
  $('#create').innerHTML=icon('plus')+'新建'+esc(groupSectionLabels[nodeGroupCategory][0]);
  $('#create').onclick=()=>nativeGroupEditor(null,nodeGroupCategory==='shared'?'shared':'nodes');
  $$('[data-group-open]').forEach(button=>button.onclick=()=>openNativeGroupLocation(locations.find(m=>m.name===button.dataset.groupOpen).editor));
  $$('[data-group-test]').forEach(button=>button.onclick=async()=>{
    const entry=nativeGroupOverviewCache.groups?.[button.dataset.groupTest];
    if(!entry?.test_ids?.length)return;
    button.disabled=true;
    try{await toolRequest('test-nodes',{ids:entry.test_ids});toast('已开始测速，可在节点库查看结果。')}
    catch(error){toast(error.message);button.disabled=false}
  });
  $$('[data-native-group-toggle]').forEach(el=>el.onchange=()=>nativeToggle('groups',el.dataset.nativeGroupToggle,el.checked).catch(error=>toast(error.message)));
  $$('[data-native-group-delete]').forEach(button=>button.onclick=()=>nativeDelete('删除后仍被使用会阻止生成；不会自动更换出口。',n=>{const name=button.dataset.nativeGroupDelete;n.mihomo['proxy-groups']=n.mihomo['proxy-groups'].filter(g=>g.name!==name);if(n.selection?.groups)n.selection.groups=n.selection.groups.filter(x=>x!==name);if(n.group_roles)delete n.group_roles[name]}));
  bindResourceMenus();
  ensureNativeGroupOverview();
};

const renderPortableGroupsUnsectioned=renderGroups;
renderGroups=function(){
  updateGroupCategoryLabels();
  renderPortableGroupsUnsectioned();
  const locations=liveWorkspace.state?.group_management||[],cards=$$('[data-group-card]'),oldGrid=$('.flat-group-grid');
  if(!oldGrid)return;
  const panel=document.createElement('div');panel.id='group-category-panel';panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','group-category-'+nodeGroupCategory);
  const sections=Object.keys(groupSectionLabels).map(section=>{
    const rows=cards.filter(card=>locations.some(m=>m.id===card.dataset.groupCard&&m.category===section)&&groupInCategory(section));
    const html=groupSectionMarkup(section,rows.map(()=>''));if(!html)return null;
    const container=document.createElement('div');container.innerHTML=html;const element=container.firstElementChild,grid=$('.flat-group-grid',element);
    grid.classList.toggle('group-compact',document.documentElement.dataset.density==='compact');rows.forEach(card=>{grid.append(card);const m=locations.find(m=>m.id===card.dataset.groupCard);card.insertAdjacentHTML('beforeend',groupUsageMarkup(m));const heading=$('h2',card);if(heading){const h3=document.createElement('h3');h3.textContent=heading.textContent;heading.replaceWith(h3)}});return element;
  }).filter(Boolean);
  if(sections.length)sections.forEach(section=>panel.append(section));else panel.innerHTML='<p class="empty">当前分类没有符合条件的节点组，可调整搜索、选用筛选或切换分类。</p>';
  oldGrid.replaceWith(panel);$('#content').insertAdjacentHTML('afterbegin',groupCategoryTabs(locations));bindGroupCategory(renderGroups);bindGroupUsage();
};
