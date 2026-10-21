'use strict';
// MR-style forms share the persisted Profile and the server's member resolver.
let policyView = 'rules';
const planLabel = plan => ({default:'默认分流',direct:'直连',proxy:'应用分流'}[plan.name] ? {default:'默认分流',direct:'直连',proxy:'应用分流'}[plan.name] + ` · ${plan.name}` : plan.name);
const plans = () => data.routing_plans || [];
const ruleKind = kind => ({domain:'域名规则',ipcidr:'IP 规则',classical:'混合规则'}[kind] || kind);

async function saveChange(change) {
  const previous = clone(data);
  try { change(); await persist(); }
  catch (error) { data = previous; render(); throw error; }
}

function confirmRemoval(title, description, change) {
  showDialog(title, `<p>${esc(description)}</p><p class="error-message" id="remove-error" role="alert"></p><div class="dialog-actions"><button class="button" id="remove-cancel">取消</button><button class="button danger-button" id="remove-confirm">确认删除</button></div>`);
  $('#remove-cancel').onclick = () => $('#dialog').close();
  $('#remove-confirm').onclick = async e => {
    e.target.disabled = true;
    try { await saveChange(change); $('#dialog').close(); }
    catch (error) { $('#remove-error').textContent = error.message; e.target.disabled = false; }
  };
}

// Keep source options available without requiring them for the common path.
const detailedSourceForm = editSource;
editSource = function(sourceId) {
  detailedSourceForm(sourceId);
  const form = $('#source-form');
  for (const section of $$('.form-section', form)) {
    const title = $('h3', section), details = document.createElement('details');
    details.className = 'advanced-options';
    const summary = document.createElement('summary');
    summary.textContent = title.textContent;
    details.append(summary);
    title.remove();
    while (section.firstChild) details.append(section.firstChild);
    section.append(details);
  }
  const formatField = $('[name=format]', form)?.closest('.field');
  const noteField = $('[name=note]', form)?.closest('.field');
  const requestFields = $('[name=user_agent]', form).closest('.fields');
  const details = document.createElement('details');
  details.className = 'advanced-options request-options';
  details.innerHTML = '<summary>高级选项（格式识别、备注与请求 UA）</summary>';
  requestFields.before(details);
  if (formatField) details.append(formatField);
  if (noteField) details.append(noteField);
  details.append(requestFields);
};

const liveGroupEditor = renderGroupEditor;
renderGroupEditor = function() {
  liveGroupEditor();
  if (!draft) return;
  const advanced = document.createElement('details');
  advanced.className = 'advanced-options group-settings';
  advanced.innerHTML = `<summary>选择设置</summary><div class="fields"><label class="field" id="manual-member-field">默认选中<select id="group-selected"><option value="">按成员顺序</option></select></label><label class="field" id="interval-setting">检查间隔（秒）<input id="group-interval" type="number" min="1" value="${draft.interval || 300}"></label><label class="field" id="tolerance-setting">切换容差（毫秒）<input id="group-tolerance" type="number" min="0" value="${draft.tolerance ?? 50}"><small>延迟差超过这个值才切换节点</small></label></div><p id="mode-compatibility" class="preview-note"></p>`;
  $('.editor-body', $('#group-editor')).append(advanced);
  const modeFields = () => {
    $('#manual-member-field').hidden = draft.mode !== 'select';
    $('#interval-setting').hidden = draft.mode === 'select';
    $('#tolerance-setting').hidden = draft.mode !== 'url-test';
    $('#mode-compatibility').textContent = ['fallback','load-balance'].includes(draft.mode)
      ? '此选择方式可原样用于 Mihomo；sing-box 没有等价方式，导出时会阻止并提示。'
      : '支持 Mihomo 和 sing-box；已迁入的成员顺序会保留。';
  };
  $('#group-mode').onchange = e => {
    draft.mode = e.target.value;
    if (draft.mode !== 'select') delete draft.selected;
    modeFields(); updatePreview();
  };
  $('#group-interval').oninput = e => { draft.interval = Number(e.target.value); updatePreview(); };
  $('#group-tolerance').oninput = e => { draft.tolerance = Number(e.target.value); updatePreview(); };
  $('#group-selected').onchange = e => {
    if (e.target.value) draft.selected = e.target.value; else delete draft.selected;
    updatePreview();
  };
  modeFields();
};

updatePreview = function() {
  if (!draft) return;
  const serial = ++liveWorkspace.previewSerial;
  clearTimeout(liveWorkspace.previewTimer);
  const error = groupError(draft);
  $('#group-error').textContent = error;
  $('#save-group').disabled = !!error;
  $('#editor-title').textContent = draft.name || '未命名节点组';
  if (error) { $('#member-preview').textContent = '修正错误后查看成员'; return; }
  liveWorkspace.previewTimer = setTimeout(async () => {
    try {
      const profile = {sources:data.sources,nodes:data.nodes,groups:data.groups.map(g => g.id === draft.id ? clone(draft) : g)};
      const result = await api('/api/workspace/preview-group', {profile,group_id:draft.id});
      if (serial !== liveWorkspace.previewSerial || !$('#member-preview') || page !== 'groups') return;
      $('#preview-count').textContent = `(${result.members.length})`;
      $('#member-preview').innerHTML = result.members.length
        ? `<div class="preview-list">${result.members.map(n => `<div class="preview-node"><span>${esc(n.name)}</span><small>${esc(n.protocol)}</small></div>`).join('')}</div>`
        : '<div class="empty">没有匹配成员。可保存草稿；导出前需要有效成员。</div>';
      if ($('#group-selected')) $('#group-selected').innerHTML = '<option value="">按成员顺序</option>' + result.members.map(n => `<option value="${esc(n.id)}" ${draft.selected === n.id ? 'selected' : ''}>${esc(n.name)}</option>`).join('');
    } catch (error) {
      if (serial === liveWorkspace.previewSerial && page === 'groups' && $('#group-error')) {
        $('#group-error').textContent = error.message;
        $('#save-group').disabled = true;
      }
    }
  }, 250);
};

function planToolbar() {
  return `<div class="plan-toolbar"><div class="filter-row">${plans().length ? `<label class="plan-select">规则方案<select id="active-plan">${plans().map(p => `<option value="${esc(p.id)}" ${p.id === data.active_plan ? 'selected' : ''}>${esc(planLabel(p))}</option>`).join('')}</select></label>` : '<span class="summary-line">当前工作区</span>'}<button class="text-button" id="migration-report">迁移核对</button></div><div class="view-tabs" aria-label="分流设置"><button class="button ${policyView === 'rules' ? 'active' : ''}" id="view-rules" aria-pressed="${policyView === 'rules'}">分流规则</button><button class="button ${policyView === 'inbounds' ? 'active' : ''}" id="view-inbounds" aria-pressed="${policyView === 'inbounds'}">入站绑定 <span>${(data.inbounds || []).length}</span></button></div></div>`;
}

function bindPlanToolbar() {
  if ($('#active-plan')) $('#active-plan').onchange = async e => {
    const ident = e.target.value;
    e.target.disabled = true;
    try {
      if (dirty()) await persist();
      adoptWorkspace(await api('/api/workspace/select-plan',{id:ident,revision:liveWorkspace.revision}));
      toast('已切换规则方案，入站仍使用各自绑定的方案。');
    } catch (error) { renderPolicies(); toast(error.message); }
  };
  $('#view-rules').onclick = () => { policyView = 'rules'; renderPolicies(); };
  $('#view-inbounds').onclick = () => { policyView = 'inbounds'; renderPolicies(); };
  $('#migration-report').onclick = showMigrationReport;
}

function showMigrationReport() {
  const report = liveWorkspace.state?.migration_report;
  if (!report?.plans) { showDialog('迁移核对','<p>当前工作区尚无 MR 迁移记录。</p>'); return; }
  showDialog('MR 迁移核对', `<p>来源 ${esc(report.source)} · 迁入 ${report.nodes} 个节点、${report.node_groups.length} 个节点组。</p><div class="table-wrap"><table><thead><tr><th>规则方案</th><th>普通规则</th><th>兜底出口</th></tr></thead><tbody>${report.plans.map(p => `<tr><td>${esc(p.name)}</td><td>${p.policies}</td><td>${esc(targetName(p.fallback))}</td></tr>`).join('')}</tbody></table></div><p>${report.library} 个规则集，共 ${report.rule_entries.toLocaleString()} 条内容（规则集之间可有重复）。条件类型、规则顺序、启停状态与出口引用随迁移保留。</p><p>${report.inbounds.filter(i => i.home).length} 个回家入站和 ${report.inbounds.filter(i => !i.home).length} 个普通入站已纳入配置。</p>${report.notes.map(n => `<p class="preview-note">${esc(n)}</p>`).join('')}<p class="preview-note">此处为导入时的核对记录；规则库是内容快照。页面编辑后以当前工作区为准。</p>`);
}

renderPolicies = function() {
  $('#create').innerHTML = icon('plus') + (policyView === 'inbounds' ? '添加入站' : '新建策略');
  if (policyView === 'inbounds') { renderInbounds(); return; }
  const items = data.policies.filter(p => [p.name,p.value,...(p.condition_groups||[]).flatMap(g=>g.conditions.flatMap(c=>c.type==='RULE-SET'?[find('library',c.value)?.name||'']:c.values||[c.value]))].join(' ').toLowerCase().includes(query.toLowerCase()));
  $('#content').innerHTML = planToolbar() + searchToolbar('搜索规则名称或匹配值') + `<div class="panel table-wrap policy-table"><table><thead><tr><th>顺序</th><th>匹配规则</th><th>流量出口</th><th>启用</th><th>操作</th></tr></thead><tbody>${items.map(p => {
    const index = data.policies.indexOf(p), library = find('library',p.value);
    return `<tr class="${p.enabled ? '' : 'rule-disabled'}"><td><div class="rule-order"><span class="order-number">${index + 1}</span><button class="icon-button" data-rule-move="${esc(p.id)}" data-step="-1" aria-label="上移 ${esc(p.name)}" ${index === 0 ? 'disabled' : ''}>↑</button><button class="icon-button" data-rule-move="${esc(p.id)}" data-step="1" aria-label="下移 ${esc(p.name)}" ${index === data.policies.length - 1 ? 'disabled' : ''}>↓</button></div></td><td><strong>${esc(p.name)}</strong><small>${p.type === 'RULE-SET' ? `${esc(ruleKind(library?.kind))} · ${library?.entries.length.toLocaleString() || 0} 条` : `${esc(p.type)} · ${esc(p.value)}`}</small></td><td><select class="outbound" aria-label="${esc(p.name)} 出口" data-target="${esc(p.id)}">${targetOptions(p.target)}</select></td><td>${toggleButton('policies',p)}</td><td><div class="filter-row"><button class="text-button" data-edit-rule="${esc(p.id)}">修改</button>${library ? `<button class="text-button" data-rule-content="${esc(library.id)}">内容</button>` : ''}<button class="text-button danger-action" data-delete-rule="${esc(p.id)}">删除</button></div></td></tr>`;
  }).join('')}<tr class="fallback"><td>${badge('固定最后')}</td><td><strong>兜底规则</strong><small>其余流量 · MATCH</small></td><td><select class="outbound" id="fallback-target" aria-label="兜底出口">${targetOptions(data.fallback).replace(/<option value="REJECT"[^>]*>.*?<\/option>/,'')}</select></td><td>${badge('始终启用','green')}</td><td></td></tr></tbody></table></div><p class="preview-note">从上到下，首条命中生效。直接选择出口即可保存；兜底固定在最后。当前 ${data.policies.length} 条普通规则。</p>`;
  bindPlanToolbar(); bindSearch(renderPolicies);
  $$('[data-toggle]').forEach(b => b.onclick = () => saveChange(() => { const p = find('policies',b.dataset.id); p.enabled = !p.enabled; }).catch(e => toast(e.message)));
  $$('[data-target]').forEach(s => s.onchange = () => saveChange(() => { find('policies',s.dataset.target).target = s.value; }).catch(e => toast(e.message)));
  $('#fallback-target').onchange = e => { const target = e.target.value; saveChange(() => { data.fallback = target; }).catch(error => toast(error.message)); };
  $$('[data-edit-rule]').forEach(b => b.onclick = () => editPolicy(b.dataset.editRule));
  $$('[data-rule-content]').forEach(b => b.onclick = () => viewLibrary(b.dataset.ruleContent));
  $$('[data-delete-rule]').forEach(b => b.onclick = () => { const p = find('policies',b.dataset.deleteRule); confirmRemoval('删除规则',`删除「${p.name}」？规则集内容会保留。`,() => { data.policies = data.policies.filter(v => v.id !== p.id); }); });
  $$('[data-rule-move]').forEach(b => b.onclick = () => saveChange(() => {
    const i = data.policies.findIndex(p => p.id === b.dataset.ruleMove), j = i + Number(b.dataset.step);
    if (j >= 0 && j < data.policies.length) [data.policies[i],data.policies[j]] = [data.policies[j],data.policies[i]];
  }).catch(e => toast(e.message)));
};

editPolicy = function(policyId) {
  const p = find('policies',policyId);
  const types = {'RULE-SET':'规则集','DOMAIN-SUFFIX':'域名后缀','DOMAIN':'完整域名','DOMAIN-KEYWORD':'域名关键词','DOMAIN-REGEX':'域名正则','IP-CIDR':'IPv4 网段','IP-CIDR6':'IPv6 网段'};
  showDialog(p ? '修改规则' : '新建规则',`<form id="resource-form"><label class="field">名称<input name="name" required maxlength="80" value="${esc(p?.name || '')}"></label><label class="field">匹配方式<select id="policy-type" name="type">${Object.entries(types).map(([key,label]) => `<option value="${key}" ${p?.type === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label><div id="policy-value"></div><label class="field">流量出口<select name="target">${targetOptions(p?.target || 'DIRECT')}</select></label>${formButtons()}</form>`);
  const update = () => { $('#policy-value').innerHTML = $('#policy-type').value === 'RULE-SET'
    ? `<label class="field">规则集<select name="value" required>${data.library.map(l => `<option value="${esc(l.id)}" ${p?.value === l.id ? 'selected' : ''}>${esc(l.name)} · ${l.entries.length.toLocaleString()} 条</option>`).join('')}</select></label>`
    : `<label class="field">匹配内容<input name="value" required value="${p?.type === $('#policy-type').value ? esc(p.value) : ''}" placeholder="例如 example.com 或 198.51.100.0/24"></label>`; };
  $('#policy-type').onchange = update; update();
  wireForm(v => { if (!v.name.trim() || !v.value.trim()) throw Error('请输入名称与匹配内容。'); if (p) Object.assign(p,v); else data.policies.push({id:id('p'),...v,enabled:true}); });
};

renderLibrary = function() {
  const items = data.library.filter(l => l.name.toLowerCase().includes(query.toLowerCase()));
  $('#content').innerHTML = searchToolbar('搜索规则集') + `<div class="library-grid">${items.map(l => `<article class="panel library-item"><div class="section-title">${badge(ruleKind(l.kind))}${badge('本地快照')}</div><h3>${esc(l.name)}</h3><p>${esc(l.note || '')}</p><div class="library-foot"><span>${l.entries.length.toLocaleString()} 条 · ${libraryReferences(l.id)} 处规则引用</span><button class="text-button" data-library="${esc(l.id)}">查看内容 ${icon('arrow')}</button></div></article>`).join('')}</div>${items.length ? '' : '<div class="empty">没有规则集。可以添加域名、IP 网段或混合规则。</div>'}`;
  bindSearch(renderLibrary);
  $$('[data-library]').forEach(b => b.onclick = () => viewLibrary(b.dataset.library));
};

function libraryReferences(ident) {
  const policies = plans().length ? plans().flatMap(p => p.id === data.active_plan ? data.policies : p.policies) : data.policies;
  return policies.filter(p => p.type === 'RULE-SET' && p.value === ident).length;
}

function viewLibrary(ident) {
  const library = find('library',ident);
  let offset = 0, search = '';
  showDialog(library.name,`<div class="toolbar"><label class="search">${icon('search')}<input id="rule-content-search" aria-label="搜索规则内容" placeholder="搜索域名、IP 或匹配类型"></label><button class="button" id="edit-library">修改内容</button></div><pre class="code-preview" id="rule-content"></pre><div class="rule-pagination"><span id="rule-page-info" class="summary-line"></span><div class="filter-row"><button class="button" id="rule-prev">上一页</button><button class="button" id="rule-next">下一页</button></div></div><p class="preview-note">每页最多 200 条；全部 ${library.entries.length.toLocaleString()} 条内容保存在服务端，导出使用完整内容。</p>`);
  const paint = () => {
    const entries = search ? library.entries.filter(v => v.toLowerCase().includes(search)) : library.entries;
    $('#rule-content').textContent = entries.slice(offset,offset + 200).join('\n') || '没有匹配内容';
    $('#rule-page-info').textContent = `匹配 ${entries.length.toLocaleString()} 条 · 第 ${Math.floor(offset / 200) + 1} / ${Math.max(1,Math.ceil(entries.length / 200))} 页`;
    $('#rule-prev').disabled = !offset;
    $('#rule-next').disabled = offset + 200 >= entries.length;
  };
  $('#rule-content-search').oninput = e => { search = e.target.value.toLowerCase(); offset = 0; paint(); };
  $('#rule-prev').onclick = () => { offset = Math.max(0,offset - 200); paint(); };
  $('#rule-next').onclick = () => { offset += 200; paint(); };
  $('#edit-library').onclick = () => editLibrary(ident);
  paint();
}

function editLibrary(ident) {
  const library = ident ? find('library',ident) : null;
  showDialog(library ? '修改规则集' : '添加规则集',`<form id="resource-form"><label class="field">名称<input name="name" required maxlength="80" value="${esc(library?.name || '')}"></label><label class="field">内容类型<select name="kind">${['domain','ipcidr','classical'].map(kind => `<option value="${kind}" ${library?.kind === kind ? 'selected' : ''}>${ruleKind(kind)}</option>`).join('')}</select></label><label class="field">规则内容<textarea name="entries" rows="14" required spellcheck="false" placeholder="每行一条。混合规则例如 DOMAIN-SUFFIX,example.com">${esc(library?.entries.join('\n') || '')}</textarea></label><p class="preview-note">域名后缀使用 +.example.com；混合规则保留 DOMAIN、DOMAIN-SUFFIX、DOMAIN-KEYWORD、DOMAIN-REGEX、IP-CIDR 与 IP-CIDR6 类型。保存时检查全部内容。</p>${formButtons()}</form>`);
  wireForm(v => {
    const entries = [...new Set(v.entries.split('\n').map(x => x.trim()).filter(Boolean))];
    if (!v.name.trim() || !entries.length) throw Error('请输入名称和至少一条规则。');
    const updated = {name:v.name.trim(),kind:v.kind,entries};
    if (library) Object.assign(library,updated);
    else data.library.push({id:id('l'),...updated,note:'手动维护的规则集'});
  });
}
addLibrary = () => editLibrary(null);

function renderInbounds() {
  const inbounds = data.inbounds || [];
  $('#content').innerHTML = planToolbar() + `<div class="notice"><strong>入站接收连接，规则方案决定去向。</strong> 回家入站可继续绑定 default，访问内网设备走其 Local 直连规则。这里保存和导出配置，实际监听需在目标设备启动内核。</div><div class="panel table-wrap inbound-table"><table><thead><tr><th>入站</th><th>监听</th><th>绑定规则方案</th><th>启用</th><th>操作</th></tr></thead><tbody>${inbounds.map(i => `<tr><td><strong>${esc(i.name)}</strong><small>${i.home ? '回家访问' : '普通代理'} · ${esc(i.protocol)}</small></td><td class="mono">${esc(i.listen)}:${esc(i.port)}</td><td>${esc(planLabel(plans().find(p => p.id === i.plan) || {name:i.plan}))}</td><td>${toggleButton('inbounds',i)}</td><td><div class="filter-row"><button class="text-button" data-inbound-edit="${esc(i.id)}">修改</button><button class="text-button danger-action" data-inbound-delete="${esc(i.id)}">删除</button></div></td></tr>`).join('')}</tbody></table>${inbounds.length ? '' : '<div class="empty">尚无入站。添加一个回家入站，再选择它使用的规则方案。</div>'}</div><p class="preview-note">Mihomo 与 sing-box 均支持已列出的入站协议。切换当前编辑方案，不改变入站绑定。密码与证书仅在修改表单中查看。</p>`;
  bindPlanToolbar();
  $$('[data-toggle]').forEach(b => b.onclick = () => saveChange(() => { const inbound = data.inbounds.find(i => i.id === b.dataset.id); inbound.enabled = !inbound.enabled; }).catch(e => toast(e.message)));
  $$('[data-inbound-edit]').forEach(b => b.onclick = () => editInbound(b.dataset.inboundEdit));
  $$('[data-inbound-delete]').forEach(b => b.onclick = () => { const inbound = data.inbounds.find(i => i.id === b.dataset.inboundDelete); confirmRemoval('删除入站',`删除「${inbound.name}」的配置？其他入站与规则方案会保留。`,() => { data.inbounds = data.inbounds.filter(i => i.id !== inbound.id); }); });
}

async function editInbound(ident) {
  if(!plans().length){
    try{adoptWorkspace(await api('/api/workspace/ensure-plan',{revision:liveWorkspace.revision}),{repaint:false,keepDraft:true})}
    catch(error){toast(error.message);return}
  }
  const original = (data.inbounds || []).find(i => i.id === ident);
  const value = clone(original || {id:id('i'),name:'回家节点',protocol:'shadowsocks',listen:'0.0.0.0',port:33143,home:true,enabled:true,sniff:true,plan:plans().find(p => p.name === 'default')?.id || data.active_plan,method:'aes-128-gcm',password:''});
  showDialog(original ? '修改入站' : '添加回家入站',`<form id="resource-form"><div class="fields"><label class="field">名称<input name="name" required value="${esc(value.name)}"></label><label class="field">协议<select name="protocol" id="inbound-protocol">${['shadowsocks','hysteria2','trojan','mixed'].map(p => `<option value="${p}" ${p === value.protocol ? 'selected' : ''}>${{shadowsocks:'Shadowsocks',hysteria2:'Hysteria2',trojan:'Trojan',mixed:'HTTP / SOCKS'}[p]}</option>`).join('')}</select></label></div><label class="field">使用规则方案<select name="plan">${plans().map(p => `<option value="${esc(p.id)}" ${p.id === value.plan ? 'selected' : ''}>${esc(planLabel(p))}</option>`).join('')}</select><small>家中设备的网段应在所选方案内指向“直连”</small></label><div class="fields"><label class="field">监听地址<input name="listen" required value="${esc(value.listen)}"></label><label class="field">端口<input name="port" type="number" min="1024" max="65535" required value="${esc(value.port)}"></label></div><label class="field" id="inbound-password-field">连接密码<input type="password" name="password" autocomplete="new-password" value="${esc(value.password)}"></label><label class="field" id="inbound-method-field">加密方法<select name="method">${['aes-128-gcm','aes-256-gcm','chacha20-ietf-poly1305','2022-blake3-aes-128-gcm','2022-blake3-aes-256-gcm','2022-blake3-chacha20-poly1305'].map(m => `<option ${m === value.method ? 'selected' : ''}>${m}</option>`).join('')}</select></label><div id="inbound-tls-fields"><label class="field">证书（PEM）<textarea name="certificate" rows="4" spellcheck="false">${esc(value.certificate)}</textarea></label><label class="field">私钥（PEM）<textarea name="private_key" rows="4" spellcheck="false">${esc(value.private_key)}</textarea></label></div><details class="advanced-options"><summary>更多设置</summary><label class="check-row"><input name="home" type="checkbox" ${value.home ? 'checked' : ''}> 标记为回家入站</label><label class="check-row"><input name="sniff" type="checkbox" ${value.sniff ? 'checked' : ''}> 嗅探域名用于分流</label><div id="inbound-hy2-fields"><label class="field">ALPN<input name="alpn" value="${esc((value.alpn || []).join(','))}" placeholder="h3"></label><div class="fields"><label class="field">上传 Mbps<input name="up_mbps" type="number" min="0" value="${value.up_mbps || 0}"></label><label class="field">下载 Mbps<input name="down_mbps" type="number" min="0" value="${value.down_mbps || 0}"></label></div><label class="field">Salamander 混淆密码<input name="obfs_password" type="password" value="${esc(value.obfs?.password)}"></label></div></details><label class="check-row"><input name="enabled" type="checkbox" ${value.enabled ? 'checked' : ''}> 启用此入站</label><p class="preview-note">保存仅更新配置。沿用已有回家客户端时，保持协议、端口、密码和证书一致。</p>${formButtons()}</form>`);
  const certificateFields = $('#inbound-tls-fields');
  const certificates = document.createElement('details');
  certificates.className = 'advanced-options';
  certificates.innerHTML = '<summary>证书设置</summary>';
  certificateFields.before(certificates);
  certificates.append(certificateFields);
  const fields = () => {
    const protocol = $('#inbound-protocol').value;
    $('#inbound-password-field').hidden = protocol === 'mixed';
    $('#inbound-password-field input').required = protocol !== 'mixed';
    $('#inbound-method-field').hidden = protocol !== 'shadowsocks';
    $('#inbound-tls-fields').hidden = !['trojan','hysteria2'].includes(protocol);
    certificates.hidden = !['trojan','hysteria2'].includes(protocol);
    $('#inbound-hy2-fields').hidden = protocol !== 'hysteria2';
  };
  $('#inbound-protocol').onchange = fields; fields();
  wireForm(v => {
    const form = $('#resource-form'), checked = name => $(`[name=${name}]`,form).checked;
    const inbound = {id:value.id,name:v.name.trim(),protocol:v.protocol,listen:v.listen.trim(),port:Number(v.port),plan:v.plan,home:checked('home'),sniff:checked('sniff'),enabled:checked('enabled')};
    if (v.protocol !== 'mixed') inbound.password = v.password;
    if (v.protocol === 'shadowsocks') inbound.method = v.method;
    if (['trojan','hysteria2'].includes(v.protocol)) Object.assign(inbound,{certificate:v.certificate,private_key:v.private_key});
    if (v.protocol === 'hysteria2') {
      Object.assign(inbound,{alpn:v.alpn.split(',').map(s => s.trim()).filter(Boolean),up_mbps:Number(v.up_mbps),down_mbps:Number(v.down_mbps)});
      if (v.obfs_password) inbound.obfs = {type:'salamander',password:v.obfs_password};
    }
    if (v.protocol === 'mixed' && value.protocol === 'mixed' && value.users) inbound.users = value.users;
    data.inbounds = original ? data.inbounds.map(i => i.id === value.id ? inbound : i) : [...(data.inbounds || []),inbound];
  });
}

// Editing an existing node requires only familiar connection fields.
const importNodeDialog = editRealNode;
editRealNode = function(ident) {
  if (!ident) { importNodeDialog(null); return; }
  const node = find('nodes',ident), passwordProtocol = ['shadowsocks','trojan','hysteria2'].includes(node.protocol);
  showDialog('修改节点',`<form id="resource-form"><label class="field">节点名称<input name="name" required value="${esc(node.name)}"></label><p class="preview-note">${esc(node.protocol)} · ${esc(sourceName(node.source))}${node.source !== 'manual' ? '；订阅刷新会更新连接参数' : ''}</p><div class="fields"><label class="field">服务器<input name="server" required value="${esc(node.server)}"></label><label class="field">端口<input name="port" type="number" min="1" max="65535" required value="${esc(node.port)}"></label></div><label class="field">${passwordProtocol ? '连接密码' : 'UUID'}<input name="credential" type="${passwordProtocol ? 'password' : 'text'}" required value="${esc(passwordProtocol ? node.password : node.uuid)}"></label>${node.protocol === 'shadowsocks' ? `<label class="field">加密方法<select name="method">${['aes-128-gcm','aes-256-gcm','chacha20-ietf-poly1305','2022-blake3-aes-128-gcm','2022-blake3-aes-256-gcm','2022-blake3-chacha20-poly1305'].map(m => `<option ${node.method === m ? 'selected' : ''}>${m}</option>`).join('')}</select></label>` : ''}<details class="advanced-options"><summary>TLS 与传输设置</summary><p class="preview-note">已有 Reality、WebSocket、gRPC、ALPN 与混淆参数会完整保留；复杂参数可在下方修改。</p><label class="field">扩展参数（JSON）<textarea name="advanced" rows="10" spellcheck="false">${esc(JSON.stringify(Object.fromEntries(['tls','transport','flow','alter_id','security','udp','obfs'].filter(k => k in node).map(k => [k,node[k]])),null,2))}</textarea></label></details>${formButtons()}</form>`);
  wireForm(v => {
    const extension = JSON.parse(v.advanced);
    if (!extension || Array.isArray(extension) || typeof extension !== 'object' || Object.keys(extension).some(k => !['tls','transport','flow','alter_id','security','udp','obfs'].includes(k))) throw Error('扩展参数仅可包含 TLS、传输、流控与混淆设置。');
    const updated = clone(node);
    for (const k of ['tls','transport','flow','alter_id','security','udp','obfs']) delete updated[k];
    Object.assign(updated,extension,{name:v.name.trim(),server:v.server.trim(),port:Number(v.port)},passwordProtocol ? {password:v.credential} : {uuid:v.credential});
    if (node.protocol === 'shadowsocks') updated.method = v.method;
    data.nodes = data.nodes.map(n => n.id === node.id ? updated : n);
  });
};

$('#create').onclick = () => {
  if (page === 'policies' && policyView === 'inbounds') editInbound();
  else ({sources:editSource,nodes:addNode,groups:addGroup,library:addLibrary,policies:editPolicy})[page]();
};
