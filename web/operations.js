'use strict';
// Daily operations use the server's Profile usage view and atomic write APIs.
let libraryUsageFilter = 'all', pickerKind = 'local', pickerQuery = '';
let policyBusy = false, policyUndo = null, dnsEdited = false;
let dnsFormDraft = null;
window.addEventListener('beforeunload',e=>{if(dnsFormDraft){e.preventDefault();e.returnValue=''}});
const usageView = () => liveWorkspace.state?.usage || {plans:[],library:{},settings:{},included:[],excluded:[]};
const currentPolicyViews = () => usageView().plans.find(p=>p.current)?.policies || [];
actionIconPaths.grip='M8 5h1M15 5h1M8 12h1M15 12h1M8 19h1M15 19h1';

renderLibrary = function() {
  const usage = usageView();
  const items = data.library.filter(l => {
    const refs = usage.library[l.id]?.references || [];
    return l.name.toLowerCase().includes(query.toLowerCase()) &&
      (libraryUsageFilter==='all' || libraryUsageFilter==='current'&&refs.some(r=>r.current&&r.enabled) || libraryUsageFilter==='other'&&refs.some(r=>!r.current) || libraryUsageFilter==='unused'&&!refs.length);
  });
  $('#content').innerHTML = `<div class="toolbar library-controls"><strong class="summary-line">全部规则 · ${data.library.length}</strong><label class="field inline-field">使用情况<select id="library-usage">${[['all','全部'],['current','当前方案使用'],['other','其他方案引用'],['unused','未引用']].map(([key,label])=>`<option value="${key}" ${libraryUsageFilter===key?'selected':''}>${label}</option>`).join('')}</select></label><label class="search">${icon('search')}<input id="library-search" aria-label="搜索规则" placeholder="搜索规则" value="${esc(query)}"></label></div>
    <p class="preview-note">每条规则选择匹配器或订阅。分流策略按需引用，未选中的规则不参与。</p>
    <div class="panel table-wrap managed-library"><table><thead><tr><th>规则集 / 来源</th><th>规则数量</th><th>使用情况</th><th>更新状态</th><th>操作</th></tr></thead><tbody>${items.map(l=>{
      const remote=l.source?.type==='url', status=liveWorkspace.state?.library_status?.[l.id]||{}, refs=usage.library[l.id]?.references||[];
      let origin=l.source?.type==='file'?'文件导入':'规则匹配器';if(remote){try{origin=new URL(l.source.url).host}catch{origin='规则订阅'}}
      const current=refs.filter(r=>r.current&&r.enabled).length, other=refs.filter(r=>!r.current).length;
      return `<tr><td><strong>${esc(l.name)}</strong><small>${esc(origin)} · ${esc(ruleFormats[status.detected_format||l.source?.format]||ruleKind(l.kind))}</small></td><td>${l.entries.length.toLocaleString()} 条</td><td><button class="text-button" data-library-usage="${esc(l.id)}">${current?`当前方案 ${current} 条策略`:'当前方案未使用'}</button><small>${other?`其他方案 ${other} 处引用`:'无其他方案引用'}${!refs.length?' · 未引用':''}</small></td><td>${status.refresh_state==='running'?badge('更新中','accent'):status.refresh_state==='error'?badge('更新失败','error'):badge('已保存','green')}<small>${status.last_updated?serverTime(status.last_updated):l.source?'本地内容':'迁移快照'}</small>${remote?`<small>${l.source.auto_update?(l.source.schedule_mode==='daily'?'每天 '+esc(l.source.daily_time):'每 '+l.source.interval_minutes+' 分钟'):'手动更新'}</small>`:''}${status.error?`<small class="error-message">${esc(status.error)}</small>`:''}</td><td><div class="filter-row"><button class="text-button" data-library-use="${esc(l.id)}">用于策略</button><button class="text-button" data-library-view="${esc(l.id)}">内容</button>${remote?`<button class="text-button" data-library-refresh="${esc(l.id)}" ${status.refresh_state==='running'?'disabled':''}>更新</button>`:''}<button class="text-button" data-library-edit="${esc(l.id)}">修改</button><button class="text-button danger-action" data-library-delete="${esc(l.id)}">删除</button></div></td></tr>`;
    }).join('')}</tbody></table>${items.length?'':'<div class="empty"><strong>没有符合条件的规则集</strong>调整搜索条件，或添加规则并选择匹配器或订阅。</div>'}</div>`;
  $$('[data-library-filter]').forEach(b=>b.onclick=()=>{libraryFilter=b.dataset.libraryFilter;renderLibrary()});
  $('#library-usage').onchange=e=>{libraryUsageFilter=e.target.value;renderLibrary()};
  $('#library-search').oninput=e=>{query=e.target.value;const position=e.target.selectionStart;renderLibrary();$('#library-search').focus();$('#library-search').setSelectionRange(position,position)};
  $$('[data-library-view]').forEach(b=>b.onclick=()=>viewLibrary(b.dataset.libraryView));
  $$('[data-library-edit]').forEach(b=>b.onclick=()=>editLibrary(b.dataset.libraryEdit));
  $$('[data-library-use]').forEach(b=>b.onclick=()=>editPolicy(null,b.dataset.libraryUse));
  $$('[data-library-usage]').forEach(b=>b.onclick=()=>showLibraryUsage(b.dataset.libraryUsage));
  $$('[data-library-refresh]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{adoptWorkspace(await api(`/api/library/${b.dataset.libraryRefresh}/refresh`,{},undefined,60000));toast('正在更新规则订阅。')}catch(e){toast(e.message);b.disabled=false}});
  $$('[data-library-delete]').forEach(b=>b.onclick=()=>{const l=find('library',b.dataset.libraryDelete);confirmRemoval('删除规则集',`删除「${l.name}」？仍有策略引用时会阻止删除。`,()=>{});$('#remove-confirm').onclick=async e=>{e.target.disabled=true;try{adoptWorkspace(await api(`/api/library/${l.id}/delete`,{revision:liveWorkspace.revision}));$('#dialog').close()}catch(error){$('#remove-error').textContent=error.message;e.target.disabled=false}}});
};

function showLibraryUsage(ident) {
  const refs=usageView().library[ident]?.references||[];
  showDialog('规则集使用情况',`<p><strong>${esc(find('library',ident).name)}</strong></p><div class="table-wrap"><table><thead><tr><th>方案</th><th>策略</th><th>状态</th></tr></thead><tbody>${refs.map(r=>`<tr><td>${esc(r.plan_name)}${r.current?' · 当前':''}</td><td>${esc(r.name)}</td><td>${!r.enabled?'策略已停用':r.runtime?'参与导出':'方案未参与导出'}</td></tr>`).join('')}</tbody></table></div>${refs.length?'':'<p>尚未被任何策略引用，不参与路由。</p>'}`);
}

const originalPlanToolbar=planToolbar;
planToolbar=function(){return originalPlanToolbar().replace('规则方案<select','当前编辑方案<select').replace('</div></div>',`<button class="button ${policyView==='dns'?'active':''}" id="view-dns" aria-pressed="${policyView==='dns'}">DNS / MosDNS</button></div></div>`)};
const originalBindPlanToolbar=bindPlanToolbar;
bindPlanToolbar=function(){originalBindPlanToolbar();$('#view-dns').onclick=()=>{policyView='dns';renderPolicies()};if($('#active-plan'))$('#active-plan').disabled=policyBusy||policyView==='dns'};

renderPolicies=function() {
  $('#create').hidden=policyView==='dns';$('#create').innerHTML=icon('plus')+(policyView==='inbounds'?'添加入站':'新建策略');
  if(policyView==='dns'){renderDns();return}if(policyView==='inbounds'){renderInbounds();return}
  const views=new Map(currentPolicyViews().map(p=>[p.id,p]));
  const items=data.policies.filter(p=>[p.name,views.get(p.id)?.summary||''].join(' ').toLowerCase().includes(query.toLowerCase()));
  const settings=usageView().settings;
  $('#content').innerHTML=planToolbar()+`<div class="dns-overview"><span>DNS：本地 ${esc(settings.local_upstream)} · 远程 ${esc(settings.remote_upstream)} · 未命中使用${settings.dns_default==='local'?'本地':'远程'}</span><button class="text-button" id="open-dns">设置 MosDNS</button></div>`+searchToolbar('搜索规则名称或匹配值')+`<div class="order-feedback" role="status" aria-live="polite">${policyBusy?'正在保存顺序…':query?'清空搜索后可拖动完整列表。':'从手柄拖动；键盘按空格抓取、方向键移动、回车放下、Esc 取消。'}${policyUndo&&policyUndo.plan===data.active_plan?'<button class="text-button" id="undo-order">撤销上次排序</button>':''}</div><div class="panel table-wrap policy-table"><table><thead><tr><th>顺序</th><th>匹配内容</th><th>流量出口</th><th>DNS</th><th>启用</th><th>操作</th></tr></thead><tbody id="policy-rows">${items.map(p=>{
    const index=data.policies.indexOf(p),v=views.get(p.id),refs=v?.sources||{};
    return `<tr data-policy-row="${esc(p.id)}" class="${p.enabled===false?'rule-disabled':''}"><td><div class="rule-order"><button class="icon-button drag-handle" data-drag-policy="${esc(p.id)}" aria-label="拖动 ${esc(p.name)}" aria-pressed="false" ${query||policyBusy?'disabled':''}>${actionIcon('grip')}</button><span class="order-number">${index+1}</span></div></td><td><strong>${esc(p.name)}</strong><small class="policy-expression">${esc(v?.summary||'')}</small><small>${refs.local?`本地 ${refs.local} 项 `:''}${refs.subscription?`订阅 ${refs.subscription} 项`:''}</small></td><td><select aria-label="${esc(p.name)} 出口" data-target="${esc(p.id)}" ${policyBusy?'disabled':''}>${targetOptions(p.target)}</select></td><td><select aria-label="${esc(p.name)} DNS" data-policy-dns="${esc(p.id)}" ${policyBusy?'disabled':''}>${[['auto','自动'],['local','本地'],['remote','远程']].map(([key,label])=>`<option value="${key}" ${(p.dns||'auto')===key?'selected':''}>${label}</option>`).join('')}</select></td><td>${toggleButton('policies',p)}</td><td><div class="filter-row"><button class="text-button" data-edit-rule="${esc(p.id)}">修改</button><button class="text-button" data-policy-position="${esc(p.id)}">移动</button><button class="text-button danger-action" data-delete-rule="${esc(p.id)}">删除</button></div></td></tr>`;
  }).join('')}<tr class="fallback"><td>${badge('固定最后')}</td><td><strong>兜底规则</strong><small>未命中的流量；DNS 使用上方全局设置</small></td><td><select id="fallback-target" aria-label="兜底出口">${targetOptions(data.fallback).replace(/<option value="REJECT"[^>]*>.*?<\/option>/,'')}</select></td><td>全局默认</td><td>${badge('始终启用','green')}</td><td></td></tr></tbody></table></div><p class="preview-note">从上到下，首条命中生效。共 ${data.policies.length} 条普通规则；兜底固定最后。</p>`;
  bindPlanToolbar();bindSearch(renderPolicies);$('#open-dns').onclick=()=>{location.hash='dns'};
  $$('[data-toggle]').forEach(b=>{b.disabled=policyBusy;b.onclick=()=>saveChange(()=>{const p=find('policies',b.dataset.id);p.enabled=!p.enabled}).catch(e=>toast(e.message))});
  $$('[data-target]').forEach(el=>el.onchange=()=>saveChange(()=>{find('policies',el.dataset.target).target=el.value}).catch(e=>toast(e.message)));
  $$('[data-policy-dns]').forEach(el=>el.onchange=()=>saveChange(()=>{find('policies',el.dataset.policyDns).dns=el.value}).catch(e=>toast(e.message)));
  $('#fallback-target').onchange=e=>{const target=e.target.value;saveChange(()=>{data.fallback=target}).catch(error=>toast(error.message))};
  $$('[data-edit-rule]').forEach(b=>b.onclick=()=>editPolicy(b.dataset.editRule));
  $$('[data-delete-rule]').forEach(b=>b.onclick=()=>{const p=find('policies',b.dataset.deleteRule);confirmRemoval('删除规则',`删除「${p.name}」？规则集内容保留。`,()=>{data.policies=data.policies.filter(v=>v.id!==p.id)})});
  $$('[data-policy-position]').forEach(b=>b.onclick=()=>showPolicyPosition(b.dataset.policyPosition));
  if($('#undo-order'))$('#undo-order').onclick=()=>{const undo=policyUndo;savePolicyOrder(undo.ids,null,undo.revision).catch(e=>toast(e.message))};
  bindPolicyDrag();
};

async function savePolicyOrder(ids, moved=null, revision=liveWorkspace.revision) {
  if(policyBusy)throw Error('正在保存顺序，请稍候。');const before=data.policies.map(p=>p.id),plan=data.active_plan;
  if(ids.every((v,i)=>v===before[i]))return;
  policyBusy=true;liveWorkspace.saving=true;$('#content').inert=true;
  try{const state=await api('/api/workspace/reorder-policies',{ids,plan,revision});policyUndo=moved?{ids:before,plan,revision:state.revision}:null;adoptWorkspace(state);toast(moved?`已移至第 ${ids.indexOf(moved)+1} 位。`:'已撤销排序。')}
  catch(error){policyUndo=null;await reloadWorkspace();throw error}
  finally{policyBusy=false;liveWorkspace.saving=false;$('#content').inert=false;if(page==='policies')renderPolicies();if(page==='library')renderLibrary();if(moved)$$('[data-drag-policy]').find(el=>el.dataset.dragPolicy===moved)?.focus()}
}
function showPolicyPosition(ident) {
  const index=data.policies.findIndex(p=>p.id===ident);
  showDialog('移动规则',`<form id="position-form"><p>${esc(find('policies',ident).name)}</p><label class="field">移到第几位<input id="policy-position" type="number" min="1" max="${data.policies.length}" value="${index+1}" required></label><div class="filter-row"><button type="button" class="button" id="position-up" ${index===0?'disabled':''}>上移</button><button type="button" class="button" id="position-down" ${index===data.policies.length-1?'disabled':''}>下移</button></div><p class="error-message" id="position-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="position-cancel">取消</button><button class="button primary" type="submit">移动</button></div></form>`);
  const move=async to=>{const ids=data.policies.map(p=>p.id);ids.splice(index,1);ids.splice(to,0,ident);try{await savePolicyOrder(ids,ident);$('#dialog').close()}catch(e){$('#position-error').textContent=e.message}};
  $('#position-up').onclick=()=>move(index-1);$('#position-down').onclick=()=>move(index+1);$('#position-cancel').onclick=()=>$('#dialog').close();$('#position-form').onsubmit=e=>{e.preventDefault();move(Number($('#policy-position').value)-1)};
}
function bindPolicyDrag() {
  $$('[data-drag-policy]').forEach(handle=>{
    const ident=handle.dataset.dragPolicy;
    let moving=false,keyboardMode=false,position=0,pointerY=0,animation=0;
    const rows=()=>$$('[data-policy-row]');
    const mark=()=>{rows().forEach(row=>row.classList.remove('drop-before','drop-after'));const list=rows(),from=data.policies.findIndex(p=>p.id===ident),slot=keyboardMode&&position>=from?position+1:position;if(list.length){const row=list[Math.min(slot,list.length-1)];row.classList.add(slot>=list.length?'drop-after':'drop-before')}};
    const stop=()=>{moving=false;cancelAnimationFrame(animation);handle.setAttribute('aria-pressed','false');rows().forEach(r=>r.classList.remove('drop-before','drop-after','dragging'));liveWorkspace.dragging=false};
    const finish=()=>{const ids=data.policies.map(p=>p.id),from=ids.indexOf(ident),to=keyboardMode?position:position>from?position-1:position;ids.splice(from,1);ids.splice(to,0,ident);stop();savePolicyOrder(ids,ident).catch(e=>toast(e.message))};
    const locate=()=>{const list=rows();position=list.findIndex(row=>pointerY<row.getBoundingClientRect().top+row.getBoundingClientRect().height/2);if(position<0)position=list.length;mark()};
    const tick=()=>{if(!moving)return;if(pointerY<110)window.scrollBy(0,-14);else if(pointerY>window.innerHeight-80)window.scrollBy(0,14);locate();animation=requestAnimationFrame(tick)};
    handle.onpointerdown=e=>{if(e.button!==0||query||policyBusy)return;e.preventDefault();moving=true;keyboardMode=false;liveWorkspace.dragging=true;pointerY=e.clientY;position=data.policies.findIndex(p=>p.id===ident);handle.setPointerCapture(e.pointerId);handle.setAttribute('aria-pressed','true');handle.closest('tr').classList.add('dragging');animation=requestAnimationFrame(tick)};
    handle.onpointermove=e=>{if(moving)pointerY=e.clientY};handle.onpointerup=()=>{if(moving)finish()};handle.onpointercancel=stop;
    handle.onkeydown=e=>{if(query||policyBusy)return;if(e.key===' '||e.key==='Enter'){e.preventDefault();if(moving){finish();return}moving=true;keyboardMode=true;liveWorkspace.dragging=true;position=data.policies.findIndex(p=>p.id===ident);handle.setAttribute('aria-pressed','true');mark()}else if(moving&&['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();position=Math.max(0,Math.min(data.policies.length-1,position+(e.key==='ArrowUp'?-1:1)));mark();$('.order-feedback').textContent=`放置到第 ${position+1} 位；回车保存，Esc 取消。`}else if(moving&&e.key==='Escape'){e.preventDefault();stop()}};
  });
}

function renderDns() {
  const s=dnsFormDraft||usageView().settings;dnsEdited=!!dnsFormDraft;
  $('#content').innerHTML=planToolbar()+`<div class="dns-layout"><form class="panel dns-form" id="dns-form"><div class="panel-header"><div><h2>MosDNS</h2><small>当前配置空间 · 独立配置与导出</small></div>${badge('配置管理')}</div><div class="editor-body"><div class="fields"><label class="field">本地 DNS 上游<input name="local_upstream" value="${esc(s.local_upstream)}" required><small>纯 IP，直连解析。</small></label><label class="field">远程 DNS 上游<input name="remote_upstream" value="${esc(s.remote_upstream)}" required><small>TCP / DoT / DoH，经下方 SOCKS 地址。</small></label></div><div class="fields"><label class="field">引导 DNS<input name="bootstrap" value="${esc(s.bootstrap)}" required><small>纯 IP，独立解析节点域名，避免启动循环。</small></label><label class="field">未命中域名使用<select name="dns_default"><option value="remote" ${s.dns_default==='remote'?'selected':''}>远程 DNS</option><option value="local" ${s.dns_default==='local'?'selected':''}>本地 DNS</option></select><small>独立于流量兜底出口。</small></label></div><label class="field">SOCKS 代理地址<input name="dns_socks5" value="${esc(s.dns_socks5||'127.0.0.1:'+s.mixed_port)}" required placeholder="127.0.0.1:17890"><small>填写 MosDNS 所在设备可访问的 IP:端口，支持其他主机。</small></label><label class="check-row"><input name="dns_follow_rules" type="checkbox" ${s.dns_follow_rules!==false?'checked':''}>使用当前代理方案的域名规则与顺序</label><details class="advanced-options"><summary>监听与兼容设置</summary><div class="fields"><label class="field">MosDNS 端口<input type="number" name="dns_port" min="1024" max="65535" value="${s.dns_port}" required><small>127.0.0.1 · UDP + TCP</small></label><label class="field">原代理入口端口<input type="number" name="mixed_port" min="1024" max="65535" value="${s.mixed_port}" required><small>旧配置兼容值；DNS 优先使用上方 SOCKS 地址</small></label></div><p class="preview-note">DNS 缓存固定 1024 条。当前监听为回环地址。</p></details><p id="dns-status" class="preview-note" role="status">已保存的工作区设置；校验通过不代表已部署运行。</p><p class="error-message" id="dns-error" role="alert"></p><div class="filter-row"><button type="submit" class="button primary">保存设置</button><button type="button" class="button" id="dns-preview">预览配置</button><button type="button" class="button" id="dns-check">校验已保存配置</button></div></div></form><aside class="panel dns-explanation"><h2>规则如何影响解析</h2><dl><dt>自动 + 直连</dt><dd>使用本地 DNS</dd><dt>自动 + 代理</dt><dd>使用远程 DNS</dd><dt>拒绝</dt><dd>域名返回 NXDOMAIN</dd><dt>未命中</dt><dd>使用左侧全局默认</dd></dl><p>单条策略可覆盖本地 / 远程选择。远程 DNS 经统一代理入口，不保证跟随该策略指定的节点组。</p><p class="preview-note">${esc(usageView().dns_scope)}</p><p class="preview-note">保存与导出不会启动解析服务；跨容器或跨主机运行需要另行配置互通。</p><button class="text-button" id="dns-download">导出 MosDNS</button></aside></div><section class="panel dns-output" id="dns-output" hidden><h2>MosDNS 配置预览</h2><p class="preview-note" id="dns-preview-note"></p><pre class="code-preview" id="dns-config"></pre></section>`;
  bindPlanToolbar();
  if(dnsEdited)$('#dns-status').textContent='有未保存的设置；切换页面后仍保留草稿';
  const values=()=>{const result=Object.fromEntries(new FormData($('#dns-form')));result.dns_follow_rules=$('#dns-form input[name=dns_follow_rules]').checked;for(const key of ['dns_port','mixed_port'])result[key]=Number(result[key]);return result};
  const busy=on=>{$$('#dns-form button,#dns-form input,#dns-form select').forEach(b=>b.disabled=on);liveWorkspace.saving=on};
  $('#dns-form').oninput=e=>{dnsEdited=true;dnsFormDraft=values();liveWorkspace.dnsEdited=true;$('#dns-status').textContent='有未保存的设置；切换页面后仍保留草稿';$('#dns-output').hidden=true};
  $('#dns-form').onsubmit=async e=>{e.preventDefault();const settings=values();busy(true);try{const state=await api('/api/workspace/settings',{settings,revision:liveWorkspace.revision});dnsEdited=false;dnsFormDraft=null;liveWorkspace.dnsEdited=false;adoptWorkspace(state);$('#dns-status').textContent='设置已保存；尚未执行本次原生校验。'}catch(error){$('#dns-error').textContent=error.message}finally{busy(false)}};
  $('#dns-preview').onclick=async()=>{const settings=values();busy(true);$('#dns-error').textContent='';try{const result=await api('/api/workspace/preview-dns',{settings});$('#dns-config').textContent=JSON.stringify(result.config,null,2);$('#dns-preview-note').textContent=(dnsEdited?'未保存表单的预览。':'已保存设置的预览。')+' '+result.scope;$('#dns-output').hidden=false}catch(error){$('#dns-error').textContent=error.message}finally{busy(false)}};
  $('#dns-check').onclick=async()=>{if(dnsEdited){$('#dns-error').textContent='请先保存设置，再校验。';return}busy(true);$('#dns-error').textContent='';$('#dns-status').textContent='正在检查 MosDNS…';try{await api('/api/workspace/check',{component:'dns'},undefined,120000);$('#dns-status').textContent='MosDNS 原生校验通过；未验证远程上游或 SOCKS 连通性。'}catch(error){$('#dns-status').textContent='校验未通过';$('#dns-error').textContent=error.message}finally{busy(false)}};
  $('#dns-download').onclick=()=>openConfigurationExport('dns');
}

// Node discovery and explicit bulk operations.
let nodeProtocol='all',nodeState='all',nodeSort='original';
renderNodes=function() {
  const active=n=>n.enabled!==false&&(n.source==='manual'||find('sources',n.source)?.enabled!==false);
  const items=data.nodes.filter(n=>(nodeOrigin==='all'||(nodeOrigin==='manual'?n.source==='manual':n.source!=='manual'))&&(filter==='all'||n.source===filter)&&(nodeProtocol==='all'||n.protocol===nodeProtocol)&&(nodeState==='all'||nodeState==='enabled'&&active(n)||nodeState==='disabled'&&!active(n)||nodeState==='failed'&&nodeStatus(n.id).state==='failed'||nodeState==='untested'&&!nodeStatus(n.id).state)&&`${n.name} ${n.protocol} ${n.region||''}`.toLowerCase().includes(query.toLowerCase()));
  const rank=n=>nodeStatus(n.id).state==='failed'?0:nodeStatus(n.id).state==='success'?2:1;
  if(nodeSort==='name')items.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'));if(nodeSort==='latency')items.sort((a,b)=>(nodeStatus(a.id).latency_ms??Infinity)-(nodeStatus(b.id).latency_ms??Infinity));if(nodeSort==='issues')items.sort((a,b)=>rank(a)-rank(b));
  const options=(entries,value)=>entries.map(([key,label])=>`<option value="${esc(key)}" ${key===value?'selected':''}>${esc(label)}</option>`).join('');
  const selection=[...selectedNodes].filter(ident=>find('nodes',ident));
  const probe=liveWorkspace.state?.group_status?.node_selection||{};
  $('#content').innerHTML=nodeOriginTabs(data.nodes.filter(n=>n.source==='manual').length,data.nodes.length)+searchToolbar('搜索名称、地区或协议',`<select id="source-filter" aria-label="按来源筛选">${options([['all','全部来源'],...data.sources.map(s=>[s.id,s.name]),['manual','手动添加']],filter)}</select><select id="node-protocol" aria-label="按协议筛选">${options([['all','全部协议'],...[...new Set(data.nodes.map(n=>n.protocol))].map(p=>[p,p])],nodeProtocol)}</select><select id="node-state" aria-label="按状态筛选">${options([['all','全部状态'],['enabled','已启用'],['disabled','已停用'],['failed','测速失败'],['untested','未测速']],nodeState)}</select><select id="node-sort" aria-label="节点排序">${options([['original','原始顺序'],['issues','异常优先'],['latency','延迟从低到高'],['name','名称']],nodeSort)}</select>`)+`<div class="panel table-wrap"><div class="bulkbar"><span>已选 ${selection.length} 个节点${probe.test_state==='running'?` · 测速 ${probe.test_finished||0}/${probe.test_total}`:''}</span><button class="button" id="nodes-test" ${!selection.length||probe.test_state==='running'?'disabled':''}>测速所选</button><button class="button" id="nodes-join" ${!selection.length?'disabled':''}>加入节点组</button><button class="text-button" id="nodes-clear" ${!selection.length?'disabled':''}>清空选择</button>${selection.length?'<button class="text-button" id="bulk-enable">启用所选</button><button class="text-button" id="bulk-disable">停用所选</button>':''}</div><table><thead><tr><th><input id="select-all" type="checkbox" aria-label="选择当前筛选结果" ${items.length&&items.every(n=>selectedNodes.has(n.id))?'checked':''}></th><th>节点名称</th><th>协议</th><th>来源</th><th>HTTP 延迟</th><th>启用</th><th>操作</th></tr></thead><tbody>${items.map(n=>{const status=nodeStatus(n.id);return `<tr><td><input type="checkbox" aria-label="选择 ${esc(n.name)}" data-check-node="${esc(n.id)}" ${selectedNodes.has(n.id)?'checked':''}></td><td><strong>${esc(n.name)}</strong><small>${esc(n.server)}:${n.port}</small></td><td>${badge(n.protocol)}</td><td>${esc(sourceName(n.source))}</td><td>${status.state==='success'?badge(status.latency_ms+' ms','green'):status.state==='failed'?badge('失败','error'):badge('未测速')}${status.tested_at?`<small>${serverTime(status.tested_at)}</small>`:''}</td><td>${toggleButton('nodes',n)}</td><td><div class="filter-row"><button class="text-button" data-test-node="${esc(n.id)}" ${!active(n)||probe.test_state==='running'?'disabled':''}>测速</button><button class="text-button" data-detail-node="${esc(n.id)}">修改</button></div></td></tr>`}).join('')}</tbody></table>${!items.length?'<div class="empty">没有符合筛选的节点。</div>':''}</div><p class="preview-note">显示 ${items.length} / ${data.nodes.length} 个节点。测速为 HTTP 延迟，结果带有时间。选择筛选结果不会清空此前已选节点。</p>${probe.error?`<p class="error-message">${esc(probe.error)}</p>`:''}`;
  bindNodeOrigin(()=>{filter='all';renderNodes()});bindSearch(renderNodes);bindToggles(renderNodes);
  for(const [selector,set] of [['#source-filter',v=>filter=v],['#node-protocol',v=>nodeProtocol=v],['#node-state',v=>nodeState=v],['#node-sort',v=>nodeSort=v]])$(selector).onchange=e=>{set(e.target.value);renderNodes()};
  $('#select-all').onchange=e=>{items.forEach(n=>e.target.checked?selectedNodes.add(n.id):selectedNodes.delete(n.id));renderNodes()};
  $$('[data-check-node]').forEach(el=>el.onchange=()=>{el.checked?selectedNodes.add(el.dataset.checkNode):selectedNodes.delete(el.dataset.checkNode);renderNodes()});
  $('#nodes-clear').onclick=()=>{selectedNodes.clear();renderNodes()};$('#nodes-join').onclick=()=>joinNodes(selection);
  const test=async ids=>{try{adoptWorkspace(await api('/api/nodes/test',{ids}));toast('已开始实际 HTTP 延迟测试。')}catch(e){toast(e.message)}};
  $('#nodes-test').onclick=()=>test(selection);$$('[data-test-node]').forEach(b=>b.onclick=()=>test([b.dataset.testNode]));
  $$('[data-detail-node]').forEach(b=>b.onclick=()=>editRealNode(b.dataset.detailNode));
  for(const on of [true,false]){const b=$(on?'#bulk-enable':'#bulk-disable');if(b)b.onclick=()=>saveChange(()=>{for(const n of data.nodes)if(selectedNodes.has(n.id))n.enabled=on}).catch(e=>toast(e.message))}
};

function joinNodes(ids) {
  showDialog('加入节点组',`<form id="join-form"><p>将明确选择的 ${ids.length} 个节点加入组；相同节点不会重复加入。</p><label class="field">节点组<select id="join-group"><option value="">创建新节点组</option>${data.groups.map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('')}</select></label><label class="field" id="join-name-field">新组名称<input id="join-name" value="所选节点" maxlength="80" required></label><p class="error-message" id="join-error" role="alert"></p><div class="dialog-actions"><button class="button" type="button" id="join-cancel">取消</button><button class="button primary" type="submit">加入并保存</button></div></form>`);
  $('#join-group').onchange=e=>{$('#join-name-field').hidden=!!e.target.value;$('#join-name').required=!e.target.value};$('#join-cancel').onclick=()=>$('#dialog').close();
  $('#join-form').onsubmit=async e=>{e.preventDefault();try{await saveChange(()=>{let group=find('groups',$('#join-group').value);if(!group){group={id:id('g'),name:$('#join-name').value.trim(),mode:'select',enabled:true,sources:[],nodes:[],groups:[],include:'',exclude:'',interval:300};data.groups.push(group)}group.nodes=[...new Set([...group.nodes,...ids])]});$('#dialog').close();toast('所选节点已加入节点组。')}catch(error){$('#join-error').textContent=error.message}};
}

const sourceListWithShortcuts=renderSources;
renderSources=function(){sourceListWithShortcuts();$$('[data-view-nodes]').forEach(b=>{const source=find('sources',b.dataset.viewNodes);const button=document.createElement('button');button.className='text-button';button.textContent='创建筛选组';button.onclick=()=>createSourceGroup(source.id);b.closest('tr').querySelector('td:last-child').append(button)})};
function createSourceGroup(sourceId) {
  const source=find('sources',sourceId);
  showDialog('从订阅创建筛选组',`<form id="source-group-form"><p>来源：${esc(source.name)}。只引用这个订阅，后续更新按筛选条件跟随。</p><label class="field">节点组名称<input id="source-group-name" value="${esc(source.name)} 筛选" required maxlength="80"></label><div class="fields"><label class="field">包含正则<input id="source-group-include" placeholder="如：香港|HK"></label><label class="field">排除正则<input id="source-group-exclude" placeholder="如：试用|流量"></label></div><label class="field">选择方式<select id="source-group-mode"><option value="select">手动选择</option><option value="url-test">自动测速</option></select></label><p class="error-message" id="source-group-error" role="alert"></p><div class="dialog-actions"><button class="button" type="button" id="source-group-cancel">取消</button><button class="button primary" type="submit">创建并保存</button></div></form>`);
  $('#source-group-cancel').onclick=()=>$('#dialog').close();$('#source-group-form').onsubmit=async e=>{e.preventDefault();const group={id:id('g'),name:$('#source-group-name').value.trim(),mode:$('#source-group-mode').value,enabled:true,sources:[sourceId],nodes:[],groups:[],include:$('#source-group-include').value,exclude:$('#source-group-exclude').value,interval:300};try{await saveChange(()=>data.groups.push(group));$('#dialog').close();selectedGroup=group.id;location.hash='groups'}catch(error){$('#source-group-error').textContent=error.message}};
}

const exportWithNativeValidation=$('#export').onclick;
$('#export').onclick=()=>{
  exportWithNativeValidation();
  const usage=usageView(),section=document.createElement('details');section.id='export-usage';section.open=false;
  section.innerHTML=`<summary>本次参与的规则内容</summary><p class="preview-note">${usage.plans.filter(p=>p.runtime).map(p=>esc(p.name)+(p.current?'（默认入口）':`（入站：${p.inbounds.map(esc).join('、')}）`)).join('；')}</p><p>使用 ${usage.included.length} 个规则集，另有 ${usage.excluded.length} 个不参与。</p><p class="preview-note">${usage.included.map(i=>esc(find('library',i)?.name||i)).join('、')||'当前方案仅使用直接填写的条件。'}</p><p class="preview-note">${esc(usage.dns_scope)}</p>`;
  $('#export-hint').after(section);
  $('#export-source').addEventListener('change',()=>{section.hidden=$('#export-source').value!=='current'});
};
