'use strict';
// Selection forms render server summaries; no rule or group resolution in JS.
let spaceEditor=null,spaceEditorSerial=0;
const editorKinds={policies:'分流策略',groups:'节点组',rules:'规则',settings:'生成设置'};
function spaceEditorRows(kind){const e=spaceEditor,p=e.profile;return kind==='rules'?p.library:kind==='policies'&&e.selection.plan?p.routing_plans.find(plan=>plan.id===e.selection.plan)?.policies||[]:p[kind]||[]}
function editorSelected(kind,ident){return spaceEditor.selection[kind]==null||spaceEditor.selection[kind].includes(ident)}
function editorTargetOptions(selected){const p=spaceEditor.profile;return [['DIRECT','直连'],...p.groups.map(g=>[g.id,g.name]),...p.nodes.map(n=>[n.id,n.name])].map(([ident,name])=>`<option value="${esc(ident)}" ${ident===selected?'selected':''}>${esc(name)}</option>`).join('')}
function paintSpaceCards(){
  const entries=spaceCatalog?.spaces||[];
  const currentDns=nativeMode()?nativeDnsName():'MosDNS';
  $('#content').innerHTML=`<div class="configuration-overview"><div><h2>代理配置</h2><p>节点订阅 / 全部节点 → 节点分组 → 分流规则 → 接入与基础设置</p><span>按方案独立保存与组合，导出 Mihomo YAML 或 sing-box JSON。</span></div><div><h2>${currentDns} 配置</h2><p>独立上游、解析规则、缓存与监听；原生空间保留原内核</p><button type="button" class="text-button" id="space-open-dns">配置当前 ${currentDns} ${icon('arrow')}</button></div></div><div id="space-list-status" class="error-message" role="status"></div><div class="space-grid">${entries.map(s=>{
    const current=s.id===spaceId,counts=s.counts||{},g=s.generation||{},issues=g.excluded_policies?.length||0,dnsLabel=s.dns_engine==='localdns'?'LocalDNS':s.dns_engine==='mosdns'||!s.native?'MosDNS':'DNS';
    const actions=current
      ?`<button class="button primary" data-space-export="${esc(s.id)}">${actionIcon('download')}导出代理</button><button class="button" data-space-subscription="${esc(s.id)}">订阅链接</button><button class="button" data-space-configure="${esc(s.id)}">去配置规则</button><button class="text-button" data-space-dns="${esc(s.id)}">${dnsLabel} 配置</button>${resourceMenu(s.name,`<button data-space-edit="${esc(s.id)}">${s.native?'修改名称与说明':'编辑方案选用'}</button><button data-space-copy="${esc(s.id)}">复制为新方案</button><button class="danger-action" data-space-delete="${esc(s.id)}" disabled>${s.id==='default'?'默认方案保留':'切换后可删除'}</button>`)}`
      :`<button class="button primary" data-space-use="${esc(s.id)}">切换到此方案</button><button class="button" data-space-edit="${esc(s.id)}">${s.native?'改名 / 说明':'编辑选用'}</button><button class="button" data-space-copy="${esc(s.id)}">复制为新方案</button>${resourceMenu(s.name,`<button data-space-subscription="${esc(s.id)}">切换并查看订阅链接</button><button data-space-export="${esc(s.id)}">切换并导出代理</button><button data-space-dns="${esc(s.id)}">切换并配置 ${dnsLabel}</button><button class="danger-action" data-space-delete="${esc(s.id)}" ${s.id==='default'?'disabled':''}>${s.id==='default'?'默认方案保留':'删除此方案'}</button>`)}`;
    return `<article class="panel space-card ${current?'selected':''}" aria-label="${esc(s.name)}配置空间"><div class="space-card-heading"><div><h2>${esc(s.name)}</h2><p>${s.native?'Mihomo YAML':s.proxy==='sing-box'?'sing-box JSON':'Mihomo YAML'} · ${s.native?'原生引用与缓存':{inline:'内联规则',local:'本地文件引用',remote:'远程订阅引用'}[s.rule_output]||'内联规则'}</p></div>${current?badge('当前使用中','accent'):badge('未激活')}</div><p class="space-description">${esc(s.description||'节点组 + 规则 + 分流策略的生成方案')}</p><div class="space-counts"><span><b>${counts.groups||0}</b> 节点组</span><span><b>${counts.library||0}</b> 规则</span><span><b>${counts.policies||0}</b> 策略</span></div><p class="space-readiness ${g.error?'error-message':'generation-ready'}">${g.error?esc(g.error):g.valid?'代理选用检查通过':'等待代理检查'}${issues?` · 跳过 ${issues} 条策略`:''}</p><div class="space-card-actions">${actions}</div></article>`;
  }).join('')}</div><p class="preview-note">“当前使用中”表示左侧导航正在编辑该方案。切换方案或导出前都会自动执行原生内核校验。</p>`;
  $('#space-open-dns').onclick=()=>{location.hash='dns'};
  $$('[data-space-configure]').forEach(b=>b.onclick=()=>{location.hash='library'});
  $$('[data-space-dns]').forEach(b=>b.onclick=async()=>{try{await switchSpace(b.dataset.spaceDns);location.hash='dns'}catch(error){toast(error.message)}});
  $$('[data-space-use]').forEach(b=>b.onclick=()=>switchSpace(b.dataset.spaceUse).catch(error=>toast(error.message)));
  $$('[data-space-edit]').forEach(b=>b.onclick=()=>editSpace(b.dataset.spaceEdit));
  $$('[data-space-copy]').forEach(b=>b.onclick=()=>editSpace(null,b.dataset.spaceCopy));
  $$('[data-space-export]').forEach(b=>b.onclick=async()=>{try{await switchSpace(b.dataset.spaceExport);openConfigurationExport('proxy')}catch(error){toast(error.message)}});
  $$('[data-space-subscription]').forEach(b=>b.onclick=async()=>{try{await switchSpace(b.dataset.spaceSubscription);await openSubscriptionLink()}catch(error){toast(error.message)}});
  $$('[data-space-delete]').forEach(b=>b.onclick=()=>deleteSpace(b.dataset.spaceDelete));bindResourceMenus();
}
async function editSpace(ident=null,copyFrom=null){
  try{
    await saveSpaceDrafts();await refreshSpaceCatalog();
    const original=spaceCatalog.spaces.find(s=>s.id===ident),sourceId=ident||copyFrom||'default';
    const source=spaceCatalog.spaces.find(s=>s.id===sourceId),snapshot=await api('/api/workspace',undefined,sourceId);
    const e={id:ident,copyFrom,sourceId,revision:snapshot.revision,catalogRevision:spaceCatalog.revision,profile:snapshot.profile,usage:snapshot.usage,selection:clone(snapshot.profile.composition||{}),tab:'policies',query:'',only:false,pending:false,saving:false,preview:null,timer:null,serial:0,fields:{name:original?.name||(copyFrom?source.name+' 副本':''),description:original?.description||'',proxy:source?.proxy||'mihomo',rule_output:source?.rule_output||'inline'}};
    if(!original&&!copyFrom)e.selection={groups:[],rules:[],policies:[],fallback:'DIRECT'};
    spaceEditor=e;
    showDialog(ident?'编辑方案 · '+original.name:copyFrom?'复制配置空间':'新建配置空间',`<form id="space-form" class="space-editor-form"><div class="space-editor-scope">${badge('仅此方案','accent')} 选用共享内容，组合此设备需要的配置。</div><div class="space-editor-tabs" role="tablist" aria-label="方案编辑分区"></div><div class="space-editor-body"><section class="space-editor-main" id="space-editor-panel" role="tabpanel"></section><aside class="space-editor-summary" aria-label="生成内容检查"></aside></div><p id="space-form-error" class="error-message" role="alert"></p><footer class="space-editor-footer"><span>选用关系只保存到此方案</span><button type="button" class="button" id="space-form-cancel">取消</button><button type="submit" class="button" value="save">${ident?'保存方案':'创建方案'}</button><button type="submit" class="button primary" id="space-save-generate" value="generate" disabled>保存并生成</button></footer></form>`);
    const dialog=$('#dialog');dialog.classList.add('space-editor-dialog');
    const prevent=e=>{if(spaceEditor?.saving)e.preventDefault()};dialog.addEventListener('cancel',prevent);
    dialog.addEventListener('close',()=>{dialog.classList.remove('space-editor-dialog');dialog.removeEventListener('cancel',prevent);clearTimeout(e.timer);e.serial++;if(spaceEditor===e)spaceEditor=null},{once:true});
    $('#space-form-cancel').onclick=()=>dialog.close();$('#space-form').onsubmit=saveSpaceForm;
    paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview();
  }catch(error){toast(error.message)}
}
function paintSpaceEditorTabs(){
  const e=spaceEditor;
  $('.space-editor-tabs').innerHTML=Object.entries(editorKinds).map(([kind,label])=>`<button type="button" id="space-tab-${kind}" role="tab" aria-controls="space-editor-panel" aria-selected="${e.tab===kind}" tabindex="${e.tab===kind?'0':'-1'}" data-space-tab="${kind}">${label}${kind==='settings'?'':`<span>${e.selection[kind]?.length??spaceEditorRows(kind).length}</span>`}</button>`).join('');
  $$('[data-space-tab]').forEach(b=>{b.onclick=()=>{e.tab=b.dataset.spaceTab;e.query='';e.only=false;paintSpaceEditorTabs();paintSpaceEditorPanel();$('#space-tab-'+e.tab).focus()};b.onkeydown=ev=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key))return;ev.preventDefault();const keys=Object.keys(editorKinds),index=keys.indexOf(e.tab),next=ev.key==='Home'?0:ev.key==='End'?keys.length-1:(index+(ev.key==='ArrowRight'?1:-1)+keys.length)%keys.length;$$('[data-space-tab]')[next].click()}});
}
function paintSpaceEditorPanel(){
  const e=spaceEditor,key=e.tab,panel=$('#space-editor-panel');panel.setAttribute('aria-labelledby','space-tab-'+key);
  if(key==='settings'){
    panel.innerHTML=`<section class="space-form-section"><h3>方案信息</h3><label class="field">名称<input id="space-name" required maxlength="80" placeholder="例如：手机 / 电脑 / 家庭网关" value="${esc(e.fields.name)}"></label><label class="field">说明<input id="space-description" maxlength="500" value="${esc(e.fields.description)}"></label></section><section class="space-form-section"><h3>默认输出</h3><div class="fields"><label class="field">配置内核<select id="space-proxy"><option value="mihomo" ${e.fields.proxy==='mihomo'?'selected':''}>Mihomo · YAML</option><option value="sing-box" ${e.fields.proxy==='sing-box'?'selected':''}>sing-box · JSON</option></select></label><label class="field">规则输出方式<select id="space-output">${[['remote','远程订阅引用'],['local','本地文件引用'],['inline','内联规则']].map(([k,label])=>`<option value="${k}" ${e.fields.rule_output===k?'selected':''}>${label}</option>`).join('')}</select></label></div><p class="preview-note">生成时仍可重新选择；不兼容的内核能力会明确阻止导出。</p></section><section class="space-form-section"><h3>更多配置</h3><p>基础配置和入站属于代理方案；MosDNS 在独立 DNS 入口编辑与导出。</p></section>`;
    $('#space-name').oninput=ev=>{e.fields.name=ev.target.value};$('#space-description').oninput=ev=>e.fields.description=ev.target.value;$('#space-proxy').onchange=ev=>e.fields.proxy=ev.target.value;$('#space-output').onchange=ev=>e.fields.rule_output=ev.target.value;return;
  }
  const descriptions={policies:'先选择策略，再检查所需的节点组和规则。',groups:'选用出口；所需子组由服务端统一包含。',rules:'停用规则时，引用它的整条策略将跳过。'};
  panel.innerHTML=`${key==='policies'?`<div class="fields space-plan-fields"><label class="field">共享分流模板<select id="space-plan"><option value="">跟随共享默认模板</option>${(e.profile.routing_plans||[]).map(p=>`<option value="${esc(p.id)}" ${e.selection.plan===p.id?'selected':''}>${esc(planLabel(p))}</option>`).join('')}</select></label><label class="field">兜底出口<select id="space-fallback">${editorTargetOptions(e.selection.fallback||e.profile.routing_plans?.find(p=>p.id===e.selection.plan)?.fallback||e.profile.fallback)}</select></label></div>`:''}<div class="space-editor-banner"><span>${descriptions[key]}</span><button type="button" class="button" id="space-select-dependencies" disabled>选用所需资源</button></div><div class="toolbar"><label class="search">${icon('search')}<input type="search" id="space-search" aria-label="搜索${editorKinds[key]}" placeholder="搜索${editorKinds[key]}" value="${esc(e.query)}"></label><button type="button" class="scope-chip" id="space-only" aria-pressed="${e.only}">仅看已选</button></div><div class="space-selection-tools"><label class="check-row"><input type="checkbox" id="space-follow-all" ${e.selection[key]==null?'checked':''}>全部（含以后新增）</label><button type="button" class="text-button" id="space-select-visible">选中此列表</button></div><div id="space-options"></div>`;
  if($('#space-plan'))$('#space-plan').onchange=ev=>{const plan=e.profile.routing_plans?.find(p=>p.id===ev.target.value);if(plan)e.selection.plan=plan.id;else delete e.selection.plan;e.selection.policies=null;e.selection.fallback=plan?.fallback||e.profile.fallback;paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview()};
  if($('#space-fallback'))$('#space-fallback').onchange=ev=>{e.selection.fallback=ev.target.value;requestSpacePreview()};
  $('#space-search').oninput=ev=>{e.query=ev.target.value;paintSpaceOptions()};
  $('#space-only').onclick=()=>{e.only=!e.only;$('#space-only').setAttribute('aria-pressed',e.only);paintSpaceOptions()};
  $('#space-follow-all').onchange=ev=>{e.selection[key]=ev.target.checked?null:spaceEditorRows(key).map(row=>row.id);paintSpaceEditorTabs();paintSpaceOptions();requestSpacePreview()};
  $('#space-select-visible').onclick=()=>{const chosen=new Set(e.selection[key]??spaceEditorRows(key).map(row=>row.id));filteredSpaceOptions().forEach(row=>chosen.add(row.id));e.selection[key]=spaceEditorRows(key).filter(row=>chosen.has(row.id)).map(row=>row.id);$('#space-follow-all').checked=false;paintSpaceEditorTabs();paintSpaceOptions();requestSpacePreview()};
  $('#space-select-dependencies').onclick=()=>{if(e.pending||!e.preview?.recommended_selection)return;e.selection=clone(e.preview.recommended_selection);paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview()};
  paintSpaceOptions();paintSpacePreview();
}
function filteredSpaceOptions(){const e=spaceEditor;return spaceEditorRows(e.tab).filter(row=>row.name.toLowerCase().includes(e.query.toLowerCase())&&(!e.only||editorSelected(e.tab,row.id)))}
function paintSpaceOptions(){
  const e=spaceEditor,key=e.tab,plan=e.selection.plan||e.profile.active_plan,views=e.usage?.plans.find(p=>p.id===plan)?.policies||[];
  $('#space-options').innerHTML=filteredSpaceOptions().map(row=>{const summary=key==='policies'?views.find(v=>v.id===row.id)?.summary||'分流策略':key==='rules'?`${row.entries.length.toLocaleString()} 条 · ${row.source?.type==='url'?'订阅规则':'匹配器 / 已存内容'}`:modes[row.mode];return `<label class="space-selection-row"><input type="checkbox" data-space-option="${esc(row.id)}" ${editorSelected(key,row.id)?'checked':''}><span><strong>${esc(row.name)}</strong><small>${esc(summary)}</small></span>${badge(key==='groups'?modes[row.mode]:key==='rules'?(row.source?.type==='url'?'订阅':'匹配器'):'策略')}</label>`}).join('')||'<div class="empty">没有符合条件的内容。</div>';
  $$('[data-space-option]').forEach(el=>el.onchange=()=>{const chosen=new Set(e.selection[key]??spaceEditorRows(key).map(row=>row.id));if(el.checked)chosen.add(el.dataset.spaceOption);else chosen.delete(el.dataset.spaceOption);e.selection[key]=spaceEditorRows(key).filter(row=>chosen.has(row.id)).map(row=>row.id);$('#space-follow-all').checked=false;paintSpaceEditorTabs();if(e.only&&!el.checked)paintSpaceOptions();requestSpacePreview()});
}
function requestSpacePreview(){
  const e=spaceEditor;if(!e)return;clearTimeout(e.timer);e.pending=true;const serial=++e.serial;paintSpacePreview();
  e.timer=setTimeout(async()=>{try{const preview=await api('/api/workspace/preview-selection',{selection:clone(e.selection),revision:e.revision,blank:!e.id&&!e.copyFrom},e.sourceId);if(spaceEditor!==e||serial!==e.serial)return;e.preview=preview;$('#space-form-error').textContent=''}catch(error){if(spaceEditor!==e||serial!==e.serial)return;e.preview={valid:false,error:error.message,counts:{}};$('#space-form-error').textContent=error.message}finally{if(spaceEditor===e&&serial===e.serial){e.pending=false;paintSpacePreview()}}},160);
}
function paintSpacePreview(){
  const e=spaceEditor;if(!e)return;
  const summary=$('.space-editor-summary');if(summary)summary.innerHTML=`<h3>此方案的生成内容</h3>${e.preview?generationSummaryMarkup(e.preview):'<p>正在检查选用关系…</p>'}${e.pending?'<p class="preview-note" role="status">正在更新检查结果…</p>':''}<p class="preview-note">保存后，生成时读取共享内容的最新版本。</p>`;
  const save=$('#space-save-generate');if(save)save.disabled=e.pending||e.saving||!e.preview?.valid;
  const dependencies=$('#space-select-dependencies');if(dependencies)dependencies.disabled=e.pending||!e.preview?.recommended_selection;
}
async function saveSpaceForm(ev){
  ev.preventDefault();const e=spaceEditor;if(!e||e.saving)return;
  if(!e.fields.name.trim()){e.tab='settings';paintSpaceEditorTabs();paintSpaceEditorPanel();$('#space-name').reportValidity();$('#space-name').focus();return}
  const generate=ev.submitter?.value==='generate';if(generate&&(e.pending||!e.preview?.valid))return;
  e.saving=true;$$('#space-form button,#space-form input,#space-form select').forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;
  try{
    const request={space:{...e.fields,composition:clone(e.selection)},revision:e.catalogRevision,workspace_revision:e.revision};if(e.id)request.id=e.id;else if(e.copyFrom)request.copy_from=e.copyFrom;
    const result=await api('/api/spaces/save',request,e.sourceId);spaceCatalog=result;const ident=e.id||result.id;
    $('#dialog').close();if(ident===spaceId)await reloadWorkspace();else if(!e.id||generate)await switchSpace(ident);else render();
    if(generate)$('#export').click();else toast('生成方案已保存。');
  }catch(error){if($('#space-form-error'))$('#space-form-error').textContent=error.message;else toast(error.message)}
  finally{e.saving=false;$('#close-dialog').disabled=false;if(spaceEditor===e){$$('#space-form button,#space-form input,#space-form select').forEach(el=>el.disabled=false);paintSpacePreview()}}
}
