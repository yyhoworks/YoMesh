'use strict';

async function synchronizeToolRevision(scope=spaceId){
  const revision=await api('/api/workspace/revision',undefined,scope);
  if(scope!==spaceId||revision.revision===liveWorkspace.revision)return;
  const snapshot=await api('/api/workspace',undefined,scope);
  if(scope===spaceId&&JSON.stringify(snapshot.profile)===JSON.stringify(data)){
    liveWorkspace.revision=snapshot.revision;liveWorkspace.state={...snapshot};
  }
}
const toolRequest=async(action,extra={},scope=spaceId)=>{await synchronizeToolRevision(scope);return api('/api/workspace/tools',{action,revision:liveWorkspace.revision,...extra},scope)};
const toolError=(id,error)=>{const el=$('#'+id);if(el)el.textContent=error.message||String(error)};
let nativePickedNodes=new Set(),nativeNodeHealth='all',nativePickedScope=null;
const nativeHistoryOpen=new Set();
let nativeProbeSettings={url:'https://www.gstatic.com/generate_204',timeout:5000};

function nativeProbeHistory(node,status){
  if(!status)return '';
  const rows=status.history?.length?status.history:[status];
  return `<details class="it-probe-history" data-it-history="${esc(node.id)}" ${nativeHistoryOpen.has(node.id)?'open':''}><summary>测速记录 · 最近 ${rows.length} 次</summary><p class="preview-note">从 YoMesh 所在设备发起，不代表 Bettbox 所在网络。</p><ol>${rows.map(s=>`<li><div><strong class="${s.state==='failed'?'error-message':''}">${s.state==='success'?esc(s.latency_ms)+' ms':esc(s.error||'测试失败（旧记录未分类）')}</strong><time>${esc(serverTime(s.tested_at))}</time></div><small>${esc(s.location||'YoMesh 所在设备')}${s.test_target?' · 目标：'+esc(s.test_target):''}${s.timeout_ms?' · 超时 '+esc(s.timeout_ms)+' ms':''}</small>${s.transport_note?`<p>${esc(s.transport_note)}</p>`:''}${s.hint?`<p>${esc(s.hint)}</p>`:''}</li>`).join('')}</ol></details>`;
}

function applyInteractiveGroupFields(e){
  if(!e.interactive?.active)return;
  if(e.interactive.pending||!e.interactive.result)throw Error('请等待原生内核完成筛选预览后再保存。');
  const g=e.interactive.result.group;
  e.filter=g.filter||'';for(const key of ['exclude-filter','exclude-type']){if(Object.hasOwn(g,key))e.advanced[key]=g[key];else delete e.advanced[key]}
}
async function enhanceInteractiveGroup(e,read){
  const form=$('#native-group-form'),field=$('#ng-filter').closest('label');
  field.insertAdjacentHTML('beforebegin',`<div class="fields it-group-filters"><label class="field">地区（按节点名称）<select id="it-region"><option value="">不限</option>${[['JP','日本'],['HK','香港'],['SG','新加坡'],['US','美国'],['TW','台湾']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label class="field">协议<select id="it-protocol"><option value="">不限</option>${[['ss','Shadowsocks'],['vless','VLESS'],['vmess','VMess'],['trojan','Trojan'],['hysteria2','Hysteria2'],['wireguard','WireGuard']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label></div><label class="field">排除名称包含<input id="it-exclude" placeholder="例如：高倍率, 剩余流量"><small>多个词用逗号分隔；与现有高级筛选同时生效。</small></label>`);
  const filterDetails=document.createElement('details');filterDetails.className='advanced-options';filterDetails.innerHTML='<summary>高级包含正则</summary>';field.before(filterDetails);filterDetails.append(field);
  $('summary',filterDetails).textContent='其他包含条件（高级正则）';
  field.insertAdjacentHTML('beforeend','<small>与地区条件同时生效。复杂正则会完整显示在这里。</small>');
  filterDetails.after(Object.assign(document.createElement('div'),{id:'it-filter-effective',className:'it-filter-effective',role:'status',textContent:'正在读取已有筛选…'}));
  const summary=document.createElement('section');summary.className='native-member-section';summary.innerHTML='<div class="section-title"><h3>匹配结果</h3><button type="button" class="button" id="it-group-test">测速匹配节点</button></div><p id="it-group-preview-state" role="status">正在准备原生预览…</p><div id="it-group-members" class="it-member-result"></div><p id="it-group-error" class="error-message" role="alert"></p>';
  const sourceSection=field.closest('section');sourceSection.after(summary);
  const explicit=$('#ng-member-choices').closest('section'),explicitDetails=document.createElement('details');explicitDetails.className='it-explicit-members';explicitDetails.open=e.members.length>0;explicitDetails.innerHTML=`<summary>另外选择手动节点或子组 · 已选 ${e.members.length} 个</summary>`;explicit.before(explicitDetails);explicitDetails.append(explicit);
  const originalFilter=e.filter;let serial=0,timer=null,alive=true,result=null,view=null,filtersTouched=false,lastAdvanced=$('#ng-advanced').value;
  $('.preview-note',sourceSection).textContent='选择订阅来源，再按地区、协议或名称筛选；下方显示实际匹配结果。';
  $('.it-group-filters').insertAdjacentHTML('afterend','<p class="preview-note">下方列出全部生效条件；手动节点与子组遵循内核原生筛选行为。</p>');
  $('#it-exclude').nextElementSibling.textContent='多个词用逗号分隔；已有排除正则会在下方明确列出。';
  e.interactive={active:true,pending:true,result:null};$('button[type=submit]',form).disabled=true;$('#it-group-test').disabled=true;
  $('#dialog').addEventListener('close',()=>{alive=false;clearTimeout(timer)},{once:true});
  const paintFilters=effective=>{const labels={'filter':'包含条件','exclude-filter':'排除名称','exclude-type':'排除协议'};$('#it-filter-effective').innerHTML='<strong>全部生效条件</strong>'+Object.entries(effective).filter(([,v])=>v).map(([k,v])=>`<div><span>${labels[k]}</span><code>${esc(v)}</code></div>`).join('')+(!Object.values(effective).some(Boolean)?'<p>没有名称或协议限制。</p>':'')};
  const preview=()=>{if(!view)return;clearTimeout(timer);const version=++serial;e.interactive.pending=true;$('button[type=submit]',form).disabled=true;$('#it-group-test').disabled=true;timer=setTimeout(async()=>{
    const save=$('button[type=submit]',form);let raw;
    try{
      const advanced=JSON.parse($('#ng-advanced').value);if(!advanced||Array.isArray(advanced)||typeof advanced!=='object')throw Error('高级字段需要 JSON 对象。');
      const current={...advanced,name:$('#ng-name').value,type:$('#ng-type').value,filter:originalFilter,use:$$('[data-ng-source]:checked').map(x=>x.dataset.ngSource),proxies:clone(e.members)};
      if(lastAdvanced!==$('#ng-advanced').value){const fresh=await toolRequest('group-filters',{group:current});if(!alive||version!==serial)return;$('#it-protocol').value=fresh.protocol;lastAdvanced=$('#ng-advanced').value}
      const extra={group:current};if(filtersTouched)extra.filters={replace_known:true,region:$('#it-region').value,protocol:$('#it-protocol').value,exclude:$('#it-exclude').value,regex:$('#ng-filter').value};
      e.interactive.pending=true;save.disabled=true;$('#it-group-preview-state').textContent='原生内核正在计算…';
      raw=await toolRequest('group-preview',extra);
      if(!alive||version!==serial)return;
      result=raw;e.interactive={active:true,pending:false,result:raw};paintFilters(raw.filter_view.effective);$('#it-region option[value=""]').textContent=raw.filter_view.custom_include&&!$('#it-region').value?'自定义条件（见下方）':'不限';$('#it-protocol option[value=""]').textContent=raw.filter_view.custom_protocol?'自定义排除（见下方）':'不限';$('#it-group-error').textContent='';$('#it-group-preview-state').textContent=`${raw.count} 个成员 · ${raw.note}`;
      $('#it-group-members').innerHTML=raw.members.length?raw.members.map(n=>`<div><span>${esc(n.name)}</span><small>${esc(n.provider||'')} · ${esc(n.type||'')}</small></div>`).join(''):'<p class="empty">没有匹配成员，请调整来源或筛选。保存空组前请确认客户端行为。</p>';
    }catch(error){if(alive&&version===serial){e.interactive.pending=false;e.interactive.result=null;toolError('it-group-error',error);$('#it-group-preview-state').textContent='预览未完成，原配置仍保留。'}}
    finally{if(alive&&version===serial){e.interactive.pending=false;save.disabled=!e.interactive.result;$('#it-group-test').disabled=!e.interactive.result}}
  },400)};
  const changed=event=>{if(['ng-member-search','ng-show-rule-outlets'].includes(event.target.id))return;if(['it-region','it-protocol','it-exclude','ng-filter','ng-advanced'].includes(event.target.id))filtersTouched=true;preview()};
  form.addEventListener('input',changed);form.addEventListener('change',changed);form.addEventListener('click',event=>{if(event.target.closest('[data-ng-move]'))preview()});
  try{view=await toolRequest('group-filters',{group:{...JSON.parse(lastAdvanced),filter:originalFilter}});if(!alive)return;$('#it-region').value=view.region;$('#it-protocol').innerHTML='<option value="">不限</option>'+view.protocols.map(p=>`<option value="${esc(p.value)}">${esc(p.label)}</option>`).join('');$('#it-protocol').value=view.protocol;$('#ng-filter').value=view.regex;filterDetails.open=view.custom_include;paintFilters(view.effective);if(view.custom_include&&!view.region)$('#it-region option[value=""]').textContent='自定义条件（见下方）';preview()}catch(error){if(alive){toolError('it-group-error',error);$('#it-group-preview-state').textContent='无法读取筛选，请关闭后重试。'}}
  $('#it-group-test').onclick=async()=>{try{if(!result)throw Error('请先完成筛选预览。');const ids=[...new Set(result.members.map(n=>n.id).filter(Boolean))];if(!ids.length)throw Error('当前匹配结果中没有可测速节点。');await toolRequest('test-nodes',{ids});toast('已开始测速，可在节点库查看状态。')}catch(error){toolError('it-group-error',error)}};
}

nativeRenderNodes=function(){
  const rows=liveWorkspace.state.native_view?.nodes||[],providers=Object.keys(nativeMap('proxy-providers'));
  if(!providers.includes(nativeNodeProvider))nativeNodeProvider='all';
  const manual=rows.filter(n=>n.origin==='manual'),term=query.toLowerCase(),statuses=liveWorkspace.state.node_status||{},job=liveWorkspace.state.group_status?.native_nodes||{};
  const visible=rows.filter(n=>(nodeOrigin==='all'||n.origin===nodeOrigin)&&(nativeNodeProvider==='all'||n.origin==='manual'||n.provider===nativeNodeProvider)&&(n.name+' '+n.provider+' '+n.type+' '+(n.server||'')).toLowerCase().includes(term)&&(nativeNodeHealth==='all'||statuses[n.id]?.state===nativeNodeHealth));
  const status=n=>{const s=statuses[n.id];return s?`${s.state==='success'?s.latency_ms+' ms':s.error||'测试失败（旧记录未分类）'} · ${serverTime(s.tested_at)}`:'尚未测速'};
  $('#content').innerHTML=nodeOriginTabs(manual.length,rows.length)+`<div class="toolbar"><label class="search">${icon('search')}<input id="native-search" aria-label="搜索节点" placeholder="搜索节点、协议或来源" value="${esc(query)}"></label><select id="native-node-provider" aria-label="按订阅来源筛选"><option value="all">全部订阅来源</option>${providers.map(p=>`<option value="${esc(p)}" ${p===nativeNodeProvider?'selected':''}>${esc(p)}</option>`).join('')}</select><select id="it-node-health" aria-label="按测速状态筛选"><option value="all">全部状态</option><option value="failed">仅测速失败</option><option value="success">仅测速成功</option></select></div><div class="toolbar"><label class="check-row"><input type="checkbox" id="it-select-nodes">选择当前筛选结果</label><button type="button" class="button primary" id="it-test-nodes" ${job.test_state==='running'?'disabled':''}>${job.test_state==='running'?'测速中…':'测速已选节点'}</button><button type="button" class="button" id="it-node-import">粘贴导入节点 / 订阅</button></div><p class="preview-note" role="status">${job.test_state==='running'?`正在测速 ${job.test_finished||0} / ${job.test_total} · `:''}执行位置：YoMesh 所在设备。${job.error?esc(job.error):'测速失败不会删除节点。'}</p><div class="native-resource-list">${visible.map(n=>`<article class="panel native-resource-row"><label class="check-row native-resource-name"><input type="checkbox" data-it-node="${esc(n.id)}" ${nativePickedNodes.has(n.id)?'checked':''}><span><strong>${esc(n.name)}</strong><small>${esc(n.type)} · ${esc(n.provider||'手动')}</small><small class="${statuses[n.id]?.state==='failed'?'error-message':''}">${esc(status(n))}</small></span></label><div class="filter-row"><button class="button" data-it-test="${esc(n.id)}" ${job.test_state==='running'?'disabled':''}>${statuses[n.id]?'重新测速':'测速'}</button>${n.origin==='manual'?`<button class="button" data-native-node-edit="${esc(n.name)}">编辑</button>`:''}</div></article>`).join('')||'<p class="empty">没有符合筛选条件的节点，可调整筛选或导入。</p>'}</div>`;
  $('#it-node-health').value=nativeNodeHealth;bindNodeOrigin(nativeRenderNodes);bindNativeSearch(nativeRenderNodes);$('#native-node-provider').onchange=e=>{nativeNodeProvider=e.target.value;nativeRenderNodes()};$('#it-node-health').onchange=e=>{nativeNodeHealth=e.target.value;nativeRenderNodes()};
  for(const n of visible){const row=$(`[data-it-node="${n.id}"]`).closest('article');row.insertAdjacentHTML('beforeend',nativeProbeHistory(n,statuses[n.id]))}
  $$('[data-it-history]').forEach(details=>details.ontoggle=()=>details.open?nativeHistoryOpen.add(details.dataset.itHistory):nativeHistoryOpen.delete(details.dataset.itHistory));
  $('#it-test-nodes').textContent=job.test_state==='running'?'测速中…':nativePickedNodes.size?`测速已选（${nativePickedNodes.size}）`:`测速当前结果（${visible.length}）`;
  const failed=visible.filter(n=>statuses[n.id]?.state==='failed');
  $('#it-test-nodes').insertAdjacentHTML('afterend',`${failed.length?`<button type="button" class="button" id="it-retest-failed" ${job.test_state==='running'?'disabled':''}>重测失败项（${failed.length}）</button>`:''}<details class="it-probe-settings"><summary>测试目标与超时</summary><label class="field">测试地址<input id="it-probe-url" type="url" value="${esc(nativeProbeSettings.url)}"></label><label class="field">超时（毫秒）<input id="it-probe-timeout" type="number" min="1000" max="30000" value="${nativeProbeSettings.timeout}"></label></details>`);
  $('#it-probe-url').oninput=e=>nativeProbeSettings.url=e.target.value;$('#it-probe-timeout').oninput=e=>nativeProbeSettings.timeout=Number(e.target.value);
  $$('[data-it-node]').forEach(c=>c.onchange=()=>{c.checked?nativePickedNodes.add(c.dataset.itNode):nativePickedNodes.delete(c.dataset.itNode);$('#it-test-nodes').textContent=nativePickedNodes.size?`测速已选（${nativePickedNodes.size}）`:`测速当前结果（${visible.length}）`;$('#it-select-nodes').checked=!!visible.length&&visible.every(n=>nativePickedNodes.has(n.id))});$('#it-select-nodes').checked=!!visible.length&&visible.every(n=>nativePickedNodes.has(n.id));$('#it-select-nodes').onchange=e=>{visible.forEach(n=>e.target.checked?nativePickedNodes.add(n.id):nativePickedNodes.delete(n.id));nativeRenderNodes()};
  const test=async ids=>{try{await toolRequest('test-nodes',{ids,...nativeProbeSettings});await reloadWorkspace();toast('已开始真实测速；展开节点下方记录可查看结果与失败原因。')}catch(error){toast(error.message)}};
  $('#it-test-nodes').onclick=()=>test(nativePickedNodes.size?[...nativePickedNodes].filter(id=>rows.some(n=>n.id===id)):visible.map(n=>n.id));if($('#it-retest-failed'))$('#it-retest-failed').onclick=()=>test(failed.map(n=>n.id));$$('[data-it-test]').forEach(b=>b.onclick=()=>test([b.dataset.itTest]));$$('[data-native-node-edit]').forEach(b=>b.onclick=()=>nativeNodeEditor(b.dataset.nativeNodeEdit));$('#it-node-import').onclick=openUnifiedImport;
};

async function openUnifiedImport(){
  const scope=spaceId;
  showDialog('粘贴自动识别导入',`<form id="it-import-form"><div class="filter-row" style="justify-content:space-between;margin-bottom:12px"><span class="preview-note">自动识别订阅 URL、ss:// / vless:// 节点链接或完整 YAML / JSON</span><button type="button" class="text-button" id="it-import-to-reuse">从其他空间复用订阅</button></div><label class="field">名称<input id="it-import-name" value="新导入" required></label><label class="field">链接或配置内容<textarea id="it-import-text" rows="7" spellcheck="false" required placeholder="粘贴订阅链接、节点链接或完整配置"></textarea></label><div class="dialog-actions"><button type="submit" class="button primary" id="it-import-preview">识别并预览</button></div><section id="it-import-result" hidden><p class="notice" id="it-import-note"></p><div id="it-import-items" class="it-member-result"></div><label class="check-row" id="it-import-draft-label" hidden><input type="checkbox" id="it-import-draft">仅保存原生草稿，补齐依赖后再原生校验导出</label><p class="preview-note">节点和订阅保存在当前空间。</p><button type="button" class="button primary" id="it-import-commit">确认导入</button></section><p id="it-import-error" class="error-message" role="alert"></p></form>`);
  $('#it-import-to-reuse').onclick=()=>openResourceReuse('proxy-providers');
  let preview=null,busy=false;
  const importDialog=$('#dialog'),preventImportClose=e=>{if(busy)e.preventDefault()};importDialog.addEventListener('cancel',preventImportClose);importDialog.addEventListener('close',()=>importDialog.removeEventListener('cancel',preventImportClose),{once:true});
  const lockImport=value=>{busy=value;$('#close-dialog').disabled=value;$('#it-import-name').disabled=value;$('#it-import-text').disabled=value;$('#it-import-preview').disabled=value;$('#it-import-preview').textContent=value?'正在读取和识别…':'识别并预览'};
  const reset=()=>{preview=null;$('#it-import-result').hidden=true};$('#it-import-text').oninput=reset;$('#it-import-name').oninput=reset;
  $('#it-import-form').onsubmit=async event=>{event.preventDefault();if(busy)return;lockImport(true);try{preview=await toolRequest('import-preview',{text:$('#it-import-text').value,name:$('#it-import-name').value,fetch:true},scope);$('#it-import-error').textContent='';$('#it-import-result').hidden=false;$('#it-import-note').textContent=preview.note||`识别到 ${preview.items.length} 项，${preview.items.filter(i=>i.duplicate).length} 项重复或同名。重复项默认跳过。`;$('#it-import-items').innerHTML=preview.items.map((n,i)=>`<label class="check-row"><input type="checkbox" data-it-import-index="${i}" ${n.duplicate?'':'checked'} ${preview.kind==='subscription'?'disabled':''}>${esc(n.name)} · ${esc(n.type)}${n.duplicate?' · 重复或同名':''}</label>`).join('');$('#it-import-draft-label').hidden=preview.kind!=='configuration'}catch(error){toolError('it-import-error',error)}finally{lockImport(false)}};
  $('#it-import-commit').onclick=async()=>{if(!preview||busy)return;busy=true;$('#it-import-commit').disabled=true;try{await refreshSpaceCatalog();const result=await toolRequest('import-commit',{token:preview.token,name:$('#it-import-name').value,indices:$$('[data-it-import-index]:checked').map(c=>Number(c.dataset.itImportIndex)),draft:$('#it-import-draft').checked,catalog_revision:spaceCatalog.revision},scope);if(result.workspace)adoptWorkspace(result.workspace);if(result.spaces){spaceCatalog=result.spaces;await refreshSpaceCatalog();render()}$('#dialog').close();toast('导入完成，原生字段已保留。')}catch(error){toolError('it-import-error',error)}finally{busy=false;if($('#it-import-commit'))$('#it-import-commit').disabled=false}};
}

function openResourceReuse(section='rule-providers',url=''){
  const scope=spaceId;let found=null;
  showDialog('复用已有资源',`<form id="it-resource-form"><p class="preview-note">默认复制到当前空间独立维护；只有明确选择共享后才会联动。</p><label class="field">资源类型<select id="it-resource-section"><option value="rule-providers">规则订阅</option><option value="proxy-providers">节点订阅</option></select></label><label class="field">订阅地址<input id="it-resource-url" type="url" value="${esc(url)}" required></label><button class="button" type="submit">查找已有资源</button><section id="it-resource-result" hidden><label class="field">找到的来源<select id="it-resource-match"></select></label><label class="field">当前空间中的名称<input id="it-resource-name"></label><label class="field">如何复用<select id="it-resource-mode"><option value="copy">独立复制到当前空间</option><option value="share">建立共享引用</option></select></label><p id="it-resource-impact" class="notice"></p><button id="it-resource-apply" type="button" class="button primary">使用已有资源</button></section><p class="error-message" id="it-resource-error" role="alert"></p></form>`);$('#it-resource-section').value=section;
  const impact=()=>{const item=found?.matches[Number($('#it-resource-match').value)];if(!item)return;$('#it-resource-name').value=item.name;$('#it-resource-impact').textContent=$('#it-resource-mode').value==='share'?`将影响「${item.space_name}」和「${activeSpace()?.name}」。今后修改共享来源时可同步，或另存当前空间独立副本。`:`仅复制到「${activeSpace()?.name}」；不会修改「${item.space_name}」。`};
  $('#it-resource-form').onsubmit=async e=>{e.preventDefault();try{found=await toolRequest('resource-find',{url:$('#it-resource-url').value,section:$('#it-resource-section').value},scope);found.matches=found.matches.filter(m=>m.space!==scope);$('#it-resource-result').hidden=!found.matches.length;$('#it-resource-error').textContent=found.matches.length?'':'其他空间没有相同地址的资源。当前空间已存在时可直接选择。';$('#it-resource-match').innerHTML=found.matches.map((m,i)=>`<option value="${i}">${esc(m.space_name)} · ${esc(m.name)}</option>`).join('');impact()}catch(error){toolError('it-resource-error',error)}};$('#it-resource-mode').onchange=impact;$('#it-resource-match').onchange=impact;
  for(const id of ['it-resource-url','it-resource-section'])$('#'+id).addEventListener('input',()=>{found=null;$('#it-resource-result').hidden=true});
  $('#it-resource-apply').onclick=async()=>{const m=found?.matches[Number($('#it-resource-match').value)];if(!m)return;$('#it-resource-apply').disabled=true;try{const result=await toolRequest('resource-apply',{section:found.section,catalog_revision:found.catalog_revision,source_space:m.space,source_name:m.name,source_revision:m.revision,name:$('#it-resource-name').value,mode:$('#it-resource-mode').value},scope);adoptWorkspace(result.workspace);$('#dialog').close();toast('资源复用已保存。')}catch(error){toolError('it-resource-error',error)}finally{if($('#it-resource-apply'))$('#it-resource-apply').disabled=false}};
}

const originalProviderEditor=nativeProviderEditor;
nativeProviderEditor=function(section,name=null){
  originalProviderEditor(section,name);
  const form=$('#native-provider-form'),shared=(liveWorkspace.state.shared_resources||[]).find(r=>r.section===section&&r.name===name);
  if(shared)form.insertAdjacentHTML('afterbegin',`<div class="notice it-share-note">共享资源，关联空间：${esc(shared.spaces.join('、'))}<label class="field">本次修改范围<select id="it-share-scope"><option value="current">另存为当前空间独立副本</option><option value="shared">同步到上述共享空间</option></select></label></div>`);
  if(shared)$('.scope-editor-note',form).textContent='此来源已共享，请在上方确认本次修改范围；其他原生字段保留。';
  const quick=document.createElement('div');quick.className='filter-row it-subscription-top';
  quick.innerHTML=`<span class="preview-note">快捷填入或复用已有${section==='rule-providers'?'规则来源':'节点订阅'}：</span><div class="filter-row"><button type="button" class="button" id="it-provider-clipboard">粘贴剪贴板 URL</button>${section==='proxy-providers'?'<button type="button" class="button" id="it-provider-paste">批量识别导入</button>':''}<button type="button" class="button" id="it-provider-reuse">从其他方案复用</button></div>`;
  form.prepend(quick);
  $('#it-provider-clipboard').onclick=async()=>{try{const text=(await navigator.clipboard.readText()).trim();if(text&&$('#nr-url')){$('#nr-url').value=text;$('#nr-url').dispatchEvent(new Event('input'));if($('#nr-name')&&!$('#nr-name').value.trim()){try{$('#nr-name').value=new URL(text).hostname.replace(/^www\./,'').split('.')[0];$('#nr-name').dispatchEvent(new Event('input'))}catch{}}}}catch{toast('浏览器未授权直接读取剪贴板，请按 Ctrl+V 粘贴到地址栏。')}};
  if($('#it-provider-paste'))$('#it-provider-paste').onclick=openUnifiedImport;
  $('#it-provider-reuse').onclick=()=>openResourceReuse(section,$('#nr-url')?.value||'');
};
const originalPortableEditSource=editSource;
editSource=function(sourceId){
  originalPortableEditSource(sourceId);
  const form=$('#source-form');if(!form)return;
  const quick=document.createElement('div');quick.className='filter-row it-subscription-top';
  quick.innerHTML='<span class="preview-note">支持一键读取剪贴板中的订阅地址：</span><button type="button" class="button" id="it-portable-clipboard">粘贴剪贴板 URL</button>';
  form.prepend(quick);
  $('#it-portable-clipboard').onclick=async()=>{try{const text=(await navigator.clipboard.readText()).trim();const urlInput=$('[name=url]',form),nameInput=$('[name=name]',form);if(text&&urlInput){urlInput.value=text;if(nameInput&&!nameInput.value.trim()){try{nameInput.value=new URL(text).hostname.replace(/^www\./,'').split('.')[0]}catch{}}}}catch{toast('浏览器未授权直接读取剪贴板，请按 Ctrl+V 粘贴到订阅地址。')}};
};

let nativeDiagnostic=null;
const originalRuleSettings=nativeRuleSettings;
nativeRuleSettings=function(){
  originalRuleSettings();
  $('.np-rule-toolbar').insertAdjacentHTML('beforeend','<button type="button" class="button" id="it-rule-diagnosis">查域名 / IP 与重复项</button>');
  $('#it-rule-diagnosis').onclick=()=>{
    if($('#it-diagnostics')){$('#it-diagnostics').remove();return}
    $('.np-rule-toolbar').insertAdjacentHTML('afterend',`<section class="panel it-diagnostics" id="it-diagnostics"><h2>规则诊断</h2><form id="it-diagnose-form"><label class="field">域名或 IP<input id="it-diagnose-query" value="${esc(nativeDiagnostic?.query||'')}" placeholder="例如 chat.openai.com" required></label><button class="button primary" type="submit">推演匹配结果</button><button type="button" class="button" id="it-diagnose-scan">检查重复与缺失引用</button><div class="fields"><label class="field">推演协议（可选）<select id="it-diagnose-network"><option value="">未知</option><option value="tcp">TCP</option><option value="udp">UDP</option></select></label><label class="field">目标端口（可选）<input id="it-diagnose-port" type="number" min="1" max="65535" placeholder="例如 443"></label></div></form><p class="preview-note">使用当前空间已保存配置与缓存，不执行 DNS 查询；不是客户端路由实测。</p><div id="it-diagnose-result"></div><p class="error-message" id="it-diagnose-error" role="alert"></p></section>`);
    let diagnosticPending=false;const diagnosticScope=spaceId,diagnosticForm=$('#it-diagnose-form');
    const run=async scan=>{if(diagnosticPending)return;diagnosticPending=true;$$('button,input,select',diagnosticForm).forEach(el=>el.disabled=true);try{const result=await toolRequest('diagnose',{scan,context:scan?{}:{network:document.querySelector('#it-diagnose-network').value,port:document.querySelector('#it-diagnose-port').value?Number(document.querySelector('#it-diagnose-port').value):null},query:scan?'example.com':$('#it-diagnose-query').value},diagnosticScope);if(spaceId!==diagnosticScope||$('#it-diagnose-form')!==diagnosticForm)return;nativeDiagnostic=result;$('#it-diagnose-error').textContent='';paintDiagnosis(result,scan)}catch(error){toolError('it-diagnose-error',error)}finally{diagnosticPending=false;$$('button,input,select',diagnosticForm).forEach(el=>el.disabled=false)}};
    $('#it-diagnose-form').onsubmit=e=>{e.preventDefault();run(false)};$('#it-diagnose-scan').onclick=()=>run(true);
  };
};
function paintDiagnosis(result,scan){
  $('#it-diagnose-result').innerHTML=(scan?'':`<p class="notice">${result.match?result.definitive?'配置推演命中：'+esc(result.match.name)+' → '+esc(result.match.target):'尚不能确定最终出口：较早规则需要缺失数据或运行上下文。下方匹配仅为候选结果。':'未找到可确定的匹配结果。'}</p><div class="it-diagnostic-path">${result.path.filter(p=>p.state!=='miss').map(p=>`<div><small>第 ${p.position} 条 · ${p.state==='unknown'?'无法确定':'匹配候选'}</small><strong>${esc(p.name)} → ${esc(p.target)}</strong><p>${esc(p.detail)}</p><button class="text-button" type="button" data-it-diagnose-edit="${esc(p.id)}">定位规则</button></div>`).join('')}</div>`)+`<h3>缺失引用 · ${result.missing.length}</h3>${result.missing.map((m,i)=>`<div class="it-diagnostic-item"><span>${esc(m.rule_name)} → 缺少 ${esc(m.ref)}</span><button type="button" class="button" data-it-repair="${i}">定位并修复</button></div>`).join('')||'<p class="preview-note">未发现直接引用缺失。</p>'}<h3>相同条件 · ${result.duplicates.length} 组</h3>${result.duplicates.map(rows=>`<div class="it-diagnostic-item">${rows.map(r=>`<p>第 ${r.position} 条 · ${esc(r.name)} → ${esc(r.target)}${r.enabled?'':'（停用）'}</p>`).join('')}<small>前一条优先；出口与启停可能不同，不自动合并或删除。</small></div>`).join('')||'<p class="preview-note">未发现完全相同的条件；语义相似但写法不同的规则不作重复判断。</p>'}<p class="preview-note">${esc(result.note)}</p>`;
  if(result.entry_scan){const s=result.entry_scan;$('#it-diagnose-result').insertAdjacentHTML('beforeend',`<h3>规则集内部与跨来源重复 · ${s.duplicate_count} 组</h3><p class="preview-note">已扫描 ${s.scanned_entries} 条内容、${s.scanned_providers} / ${s.total_providers} 个来源。${s.complete?'本次缓存扫描完整。':'有未扫描内容，不能据此判断全部无重复。'}${s.duplicate_count>s.groups.length?`显示前 ${s.groups.length} 组。`:''}</p>${s.groups.length?`<details class="it-duplicate-results"><summary>展开重复明细</summary>${s.groups.map(g=>`<div class="it-diagnostic-item"><code>${esc(g.expression)}</code><small>共 ${g.count} 处${g.count>g.occurrences.length?'，仅展示前 '+g.occurrences.length+' 处':''}</small>${g.occurrences.map(r=>r.kind==='policy'?`<p>主规则第 ${r.position} 条 · ${esc(r.name)} → ${esc(r.target)}${r.enabled?'':'（停用）'} <button type="button" class="text-button" data-it-diagnose-edit="${esc(r.id)}">定位规则</button></p>`:`<p>来源 ${esc(r.provider)} · 第 ${r.position} 项 <button type="button" class="text-button" data-it-provider-edit="${esc(r.provider)}">查看来源</button><small>${r.routes.length?'主规则直接引用位置：'+r.routes.map(t=>`第 ${t.position} 条 → ${esc(t.target)}${t.enabled?'':'（停用）'}`).join('；'):'无主规则直接引用，可能由其他来源间接使用。'}</small></p>`).join('')}</div>`).join('')}</details>`:'<p class="preview-note">本次已扫描内容未发现完全相同的条件。</p>'}${s.skipped.length?`<details><summary>未扫描来源 · ${s.skipped.length}</summary>${s.skipped.map(p=>`<p>${esc(p.name)}：${esc(p.reason)}</p>`).join('')}</details>`:''}<p class="preview-note">${esc(s.note)}</p>`)}
  $$('[data-it-provider-edit]').forEach(b=>b.onclick=()=>nativeProviderEditor('rule-providers',b.dataset.itProviderEdit));
  $$('[data-it-repair]').forEach(b=>b.onclick=()=>openReferenceRepair(result.missing[Number(b.dataset.itRepair)]));
  $$('[data-it-diagnose-edit]').forEach(b=>b.onclick=()=>{const part=nativeUnitRows().find(u=>u.policy_ids?.includes(b.dataset.itDiagnoseEdit));if(part)nativePolicyEditor(part.id)});
}
function openReferenceRepair(item){
  const scope=spaceId;
  showDialog('修复规则引用',`<form id="it-repair-form"><p class="notice">${esc(item.rule_name)} → 缺少 ${esc(item.ref)}</p><pre class="code-preview">${esc(item.expression)} → ${esc(item.target)}</pre><label class="field">处理方式<select id="it-repair-mode"><option value="restore">补回缺失来源</option><option value="replace">替换为已有来源</option><option value="disable">停用整条规则</option></select></label><label class="field" id="it-repair-url-field">来源地址<input id="it-repair-url" type="url" placeholder="https://…/rules.yaml" required></label><label class="field" id="it-repair-replacement-field" hidden>替换来源<select id="it-repair-replacement">${Object.keys(nativeMap('rule-providers')).map(n=>`<option>${esc(n)}</option>`).join('')}</select></label><p class="preview-note" id="it-repair-impact">补回来源并检查格式和依赖，条件与出口保持原样。</p><p class="error-message" id="it-repair-error" role="alert"></p><div class="dialog-actions"><button type="submit" class="button primary" id="it-repair-save">检查并保存</button></div></form>`);
  $('#it-repair-mode').onchange=()=>{const op=$('#it-repair-mode').value;$('#it-repair-url-field').hidden=op!=='restore';$('#it-repair-url').disabled=op!=='restore';$('#it-repair-replacement-field').hidden=op!=='replace';$('#it-repair-impact').textContent=op==='disable'?'停用整条规则；流量继续匹配后续规则，不会只删子条件扩大命中。':op==='replace'?'只替换来源名称；请核对新来源的实际内容。':'补回来源并检查格式和依赖，条件与出口保持原样。'};
  $('#it-repair-form').onsubmit=async e=>{e.preventDefault();$('#it-repair-save').disabled=true;try{const result=await toolRequest('repair',{id:item.rule_id,ref:item.ref,operation:$('#it-repair-mode').value,url:$('#it-repair-url').value,replacement:$('#it-repair-replacement').value},scope);adoptWorkspace(result.workspace);$('#dialog').close();toast('引用处理已保存，导出仍须原生内核校验。')}catch(error){toolError('it-repair-error',error)}finally{if($('#it-repair-save'))$('#it-repair-save').disabled=false}};
}

const renderBeforeInteractiveTools=render;
render=function(){if(nativePickedScope!==spaceId){nativePickedScope=spaceId;nativePickedNodes.clear();nativeHistoryOpen.clear();nativeDiagnostic=null}renderBeforeInteractiveTools();document.querySelector('#subscription-link').disabled=!liveWorkspace.state||liveWorkspace.saving;$('#it-toolbar-import')?.remove()};

const renderProvidersBeforeSharing=nativeRenderProviders;
nativeRenderProviders=function(section){renderProvidersBeforeSharing(section);const shared=(liveWorkspace.state.shared_resources||[]).filter(r=>r.section===section&&r.spaces.length>1);if(shared.length)$('#content').insertAdjacentHTML('afterbegin',`<p class="notice">共享来源刷新会同步关联空间：${esc([...new Set(shared.flatMap(r=>r.spaces))].join('、'))}。只改当前空间时，请在来源编辑器另存独立副本。</p>`)};

// Persist appearance tweaks (theme, blur, density, font-style) and auto-close panel on outside click.
(function initAppearanceAndHelp(){
  const APPEARANCE_STORAGE_ID='yomesh-appearance-v1';
  let prefs={theme:'dark-glass',blur:'18',density:'comfortable',font:'sans'};
  try{const raw=localStorage.getItem(APPEARANCE_STORAGE_ID);if(raw)prefs={...prefs,...JSON.parse(raw)}}catch{}
  const applyPrefs=()=>{
    document.documentElement.dataset.theme=prefs.theme;
    document.documentElement.style.setProperty('--glass-blur',prefs.blur+'px');
    document.documentElement.dataset.density=prefs.density;
    document.documentElement.dataset.font=prefs.font;
    document.documentElement.dataset.serif=String(prefs.font==='serif');
    if($('#theme'))$('#theme').value=prefs.theme;
    if($('#glass-blur'))$('#glass-blur').value=prefs.blur;
    if($('#density'))$('#density').value=prefs.density;
    if($('#font-style'))$('#font-style').value=prefs.font;
  };
  const savePrefs=()=>{try{localStorage.setItem(APPEARANCE_STORAGE_ID,JSON.stringify(prefs))}catch{}};
  applyPrefs();
  if($('#theme'))$('#theme').onchange=e=>{prefs.theme=e.target.value;applyPrefs();savePrefs()};
  if($('#glass-blur'))$('#glass-blur').oninput=e=>{prefs.blur=e.target.value;applyPrefs();savePrefs()};
  if($('#density'))$('#density').onchange=e=>{prefs.density=e.target.value;applyPrefs();savePrefs();if(liveWorkspace.state)render()};
  if($('#font-style'))$('#font-style').onchange=e=>{prefs.font=e.target.value;applyPrefs();savePrefs()};
  document.addEventListener('click',e=>{
    const tweaks=$('#tweaks');
    if(tweaks&&!tweaks.hidden&&!tweaks.contains(e.target)&&!e.target.closest('#appearance'))tweaks.hidden=true;
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&$('#tweaks')&&!$('#tweaks').hidden)$('#tweaks').hidden=true;
  });
  if($('#show-plan'))$('#show-plan').onclick=()=>showDialog('YoMesh 快速上手指南',`
    <div class="notice"><strong>四步完成日常代理与分流管理：</strong></div>
    <ol style="line-height:1.9;padding-left:20px;color:var(--ink)">
      <li><strong>1. 选定配置方案</strong>：在「配置方案」中为客户端或旁路由选择独立方案，点「切换到此方案」进入编辑。</li>
      <li><strong>2. 接入节点资源</strong>：在左侧「节点资源 → 节点订阅」中添加机场订阅（只需填名称与 URL，缓存路径自动生成）。</li>
      <li><strong>3. 组织分组与分流规则</strong>：在「节点分组」按地区/用途归类节点；在「分流规则」顶部可随时切换「分流规则」与「规则来源库」，编辑规则时支持「直接走单个出口」或「组合多个候选出口」。</li>
      <li><strong>4. 导出配置或复制订阅链接</strong>：点击右上角「订阅链接」复制固定客户端订阅地址（无需登录即可由客户端自动拉取更新），或点击「导出代理配置」下载经内核原生校验的配置包。</li>
    </ol>
    <div class="dialog-actions"><button type="button" class="button primary" id="help-close-btn">我知道了</button></div>
  `),$('#help-close-btn')&&($('#help-close-btn').onclick=()=>$('#dialog').close());
})();

// Account & Password Authentication UI
(function initAuthUI(){
  const userIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M5 20c1.5-4 12.5-4 14 0"/></svg>';
  const btn=$('#account-btn');
  if(btn){
    btn.innerHTML=userIcon;
    btn.onclick=()=>openAccountSettingsModal();
  }
  let loginModalOpen=false;
  window.openAuthLoginModal=function(info={}){
    if(loginModalOpen)return;
    loginModalOpen=true;
    const defaultUser=info?.username||'admin';
    showDialog('登录 YoMesh 工作台',`<form id="auth-login-form"><p class="preview-note" style="margin-top:0">工作台已启用密码保护。客户端订阅链接（<code>/s/...</code>）不受影响，仍可免密自动更新。</p><label class="field">用户名<input id="auth-login-user" autocomplete="username" value="${esc(defaultUser)}" required></label><label class="field">密码<input id="auth-login-pass" type="password" autocomplete="current-password" placeholder="请输入工作台密码" required></label><p class="error-message" id="auth-login-error" role="alert"></p><div class="dialog-actions"><button type="submit" class="button primary" id="auth-login-submit" style="width:100%">登录工作台</button></div></form>`);
    const dialog=$('#dialog');
    dialog.classList.add('auth-login-dialog');
    const blockCancel=e=>{if(loginModalOpen)e.preventDefault()};
    dialog.addEventListener('cancel',blockCancel);
    dialog.addEventListener('close',()=>{dialog.classList.remove('auth-login-dialog');dialog.removeEventListener('cancel',blockCancel);loginModalOpen=false},{once:true});
    setTimeout(()=>$('#auth-login-pass')?.focus(),50);
    $('#auth-login-form').onsubmit=async e=>{
      e.preventDefault();
      const submit=$('#auth-login-submit');submit.disabled=true;submit.textContent='正在验证…';
      $('#auth-login-error').textContent='';
      try{
        await api('/api/auth/login',{username:$('#auth-login-user').value.trim(),password:$('#auth-login-pass').value});
        loginModalOpen=false;
        dialog.close();
        liveWorkspace.initializing=false;
        await initializeWorkspace();
        toast('欢迎回来，已登录 YoMesh 工作台。');
      }catch(error){
        $('#auth-login-error').textContent=error.message;
        submit.disabled=false;submit.textContent='登录工作台';
      }
    };
  };

  async function openAccountSettingsModal(){
    let status={enabled:false,authenticated:true,username:'admin'};
    try{status=await api('/api/auth/status')}catch{}
    showDialog('账号与密码设置',`
      <div class="auth-status-banner">
        <span>当前状态：<strong>${status.enabled?'已启用密码登录保护':'尚未设置密码（局域网免密访问）'}</strong></span>
        ${status.enabled?`<span class="badge green">账号：${esc(status.username)}</span>`:'<span class="badge">免密模式</span>'}
      </div>
      <form id="auth-settings-form">
        <p class="preview-note" style="margin-top:0">启用密码后，打开网页工作台需验证密码；客户端拉取订阅链接（<code>/s/...</code>）始终免密放行，不影响路由器或手机更新。</p>
        ${status.enabled?'<label class="field">当前密码<input id="auth-cur-pass" type="password" autocomplete="current-password" placeholder="验证当前密码" required></label>':''}
        <div class="fields">
          <label class="field">登录用户名<input id="auth-new-user" autocomplete="username" value="${esc(status.username||'admin')}" maxlength="32" required></label>
          <label class="field">${status.enabled?'新密码（至少 4 位）':'设置登录密码（至少 4 位）'}<input id="auth-new-pass" type="password" autocomplete="new-password" minlength="4" placeholder="输入新密码" required></label>
        </div>
        <p class="error-message" id="auth-settings-error" role="alert"></p>
        <div class="dialog-actions" style="justify-content:space-between;flex-wrap:wrap">
          <div class="filter-row">
            ${status.enabled?'<button type="button" class="button" id="auth-logout-btn">退出当前设备</button><button type="button" class="button danger-button" id="auth-disable-btn">关闭密码保护</button>':''}
          </div>
          <div class="filter-row">
            <button type="button" class="button" id="auth-cancel-btn">取消</button>
            <button type="submit" class="button primary" id="auth-save-btn">${status.enabled?'保存新账号密码':'启用密码保护'}</button>
          </div>
        </div>
      </form>
    `);
    $('#auth-cancel-btn').onclick=()=>$('#dialog').close();
    if($('#auth-logout-btn'))$('#auth-logout-btn').onclick=async()=>{
      await api('/api/auth/logout',{});
      $('#dialog').close();
      liveWorkspace.state=null;render();
      window.openAuthLoginModal({username:status.username});
    };
    if($('#auth-disable-btn'))$('#auth-disable-btn').onclick=async()=>{
      const cur=$('#auth-cur-pass')?.value||'';
      if(!cur){$('#auth-settings-error').textContent='请先在上方输入当前密码，再关闭密码保护。';$('#auth-cur-pass')?.focus();return}
      try{
        await api('/api/auth/update',{action:'disable',current_password:cur});
        $('#dialog').close();
        toast('已关闭工作台密码保护。');
      }catch(error){$('#auth-settings-error').textContent=error.message}
    };
    $('#auth-settings-form').onsubmit=async e=>{
      e.preventDefault();
      const saveBtn=$('#auth-save-btn');saveBtn.disabled=true;
      $('#auth-settings-error').textContent='';
      try{
        const res=await api('/api/auth/update',{
          current_password:$('#auth-cur-pass')?.value||'',
          username:$('#auth-new-user').value.trim(),
          new_password:$('#auth-new-pass').value
        });
        $('#dialog').close();
        toast(`密码保护已生效（当前账号：${res.username}）。`);
      }catch(error){
        $('#auth-settings-error').textContent=error.message;
        saveBtn.disabled=false;
      }
    };
  }
})();
