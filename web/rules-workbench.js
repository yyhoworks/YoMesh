'use strict';
// Rule data is only assembled here; validation and logic preview come from Profile.
const matchLabels = {'DOMAIN-SUFFIX':'域名后缀','DOMAIN':'完整域名','DOMAIN-KEYWORD':'域名关键词','DOMAIN-REGEX':'域名正则','IP-CIDR':'IPv4 CIDR','IP-CIDR6':'IPv6 CIDR'};
const ruleFormats = {auto:'自动识别',classical:'Surge / 混合文本',clash:'Clash YAML',domain:'域名列表',ipcidr:'IP 网段列表',adguard:'AdGuard 域名规则',mrs:'Mihomo MRS',srs:'sing-box SRS','sing-box':'sing-box JSON'};
let libraryFilter = 'all', ruleDraft = null, rulePreviewTimer, rulePreviewSerial = 0;
const conditionSourceKinds = new WeakMap();
actionIconPaths.up='M12 19V5M5 12l7-7 7 7';actionIconPaths.down='M12 5v14M5 12l7 7 7-7';
const policyRefs = p => p.type === 'RULE-SET' ? [p.value] : (p.condition_groups || []).flatMap(g => g.conditions.filter(c=>c.type==='RULE-SET').map(c=>c.value));
libraryReferences = ident => (plans().length ? plans().flatMap(p=>p.id===data.active_plan?data.policies:p.policies) : data.policies).filter(p=>policyRefs(p).includes(ident)).length;

const basicPolicyList = renderPolicies;
renderPolicies = function() {
  basicPolicyList();
  if (policyView !== 'rules') return;
  $$('[data-edit-rule]').forEach(button => {
    const p = find('policies',button.dataset.editRule);
    if (p.type !== 'LOGICAL') return;
    const cell = button.closest('tr').children[1];
    $('small',cell).textContent = `${p.condition_groups.length} 个条件组 · ${p.condition_groups.reduce((n,g)=>n+g.conditions.length,0)} 个条件`;
    if (policyRefs(p).length) {
      const contentButton = document.createElement('button');
      contentButton.className = 'text-button'; contentButton.textContent = '规则集';
      contentButton.onclick = () => viewLibrary(policyRefs(p)[0]);
      button.after(contentButton);
    }
  });
};

renderLibrary = function() {
  const items = data.library.filter(l=>l.name.toLowerCase().includes(query.toLowerCase()) && (libraryFilter==='all' || (l.source?.type==='url')===(libraryFilter==='url')));
  $('#content').innerHTML = `<div class="toolbar"><div class="view-tabs" aria-label="规则来源类型">${[['all','全部规则集'],['manual','本地 / 文件'],['url','规则订阅']].map(([key,label])=>`<button class="button ${libraryFilter===key?'active':''}" aria-pressed="${libraryFilter===key}" data-library-filter="${key}">${label}</button>`).join('')}</div><label class="search">${icon('search')}<input id="library-search" aria-label="搜索规则集" placeholder="搜索规则集" value="${esc(query)}"></label></div><div class="panel table-wrap managed-library"><table><thead><tr><th>规则集 / 来源</th><th>规则数量</th><th>更新状态</th><th>操作</th></tr></thead><tbody>${items.map(l=>{
    const status=liveWorkspace.state?.library_status?.[l.id]||{}, remote=l.source?.type==='url';
    let origin=l.source?.type==='file'?'导入文件':'本地规则';
    if(remote){try{origin=new URL(l.source.url).host}catch{origin='规则订阅'}}
    return `<tr><td><strong>${esc(l.name)}</strong><small>${esc(origin)} · ${esc(ruleFormats[status.detected_format||l.source?.format]||ruleKind(l.kind))}</small></td><td>${l.entries.length.toLocaleString()} 条<small>${libraryReferences(l.id)} 处规则引用</small></td><td>${status.refresh_state==='running'?badge('更新中…','accent'):status.refresh_state==='error'?badge('更新失败','error'):badge(remote?'订阅已保存':'内容已保存','green')}<small>${status.last_updated?serverTime(status.last_updated):'MR 迁移快照'}</small>${remote?`<small>${l.source.auto_update?(l.source.schedule_mode==='daily'?'每天 '+esc(l.source.daily_time):'每 '+l.source.interval_minutes+' 分钟'):'手动更新'}</small>`:''}${status.error?`<small class="error-message">${esc(status.error)}</small>`:''}</td><td><div class="filter-row"><button class="text-button" data-library-view="${esc(l.id)}">内容</button>${remote?`<button class="text-button" data-library-refresh="${esc(l.id)}" ${status.refresh_state==='running'?'disabled':''}>刷新</button>`:''}<button class="text-button" data-library-edit="${esc(l.id)}">修改</button><button class="text-button danger-action" data-library-delete="${esc(l.id)}">删除</button></div></td></tr>`;
  }).join('')}</tbody></table>${items.length?'':'<div class="empty"><strong>这里还没有规则集</strong>添加远程地址、导入文件，或直接粘贴规则内容。</div>'}</div><p class="preview-note">先在这里添加一次，再在分流规则的条件组中引用。订阅更新后，引用它的所有规则自动使用最新内容；更新失败保留上一版。</p>`;
  $$('[data-library-filter]').forEach(b=>b.onclick=()=>{libraryFilter=b.dataset.libraryFilter;renderLibrary()});
  $('#library-search').oninput=e=>{query=e.target.value;const position=e.target.selectionStart;renderLibrary();$('#library-search').focus();$('#library-search').setSelectionRange(position,position)};
  $$('[data-library-view]').forEach(b=>b.onclick=()=>viewLibrary(b.dataset.libraryView));
  $$('[data-library-edit]').forEach(b=>b.onclick=()=>editLibrary(b.dataset.libraryEdit));
  $$('[data-library-refresh]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{adoptWorkspace(await api(`/api/library/${b.dataset.libraryRefresh}/refresh`,{}));toast('正在更新规则订阅。')}catch(e){toast(e.message);b.disabled=false}});
  $$('[data-library-delete]').forEach(b=>b.onclick=()=>{const l=find('library',b.dataset.libraryDelete);confirmRemoval('删除规则集',`删除「${l.name}」？正在被任一方案引用的规则集会阻止删除。`,async()=>{});$('#remove-confirm').onclick=async e=>{e.target.disabled=true;try{adoptWorkspace(await api(`/api/library/${l.id}/delete`,{revision:liveWorkspace.revision}));$('#dialog').close()}catch(error){$('#remove-error').textContent=error.message;e.target.disabled=false}}});
};

editLibrary = function(ident) {
  const library=ident?find('library',ident):null, source=library?.source||{}, remote=source.type==='url';
  showDialog(library?'修改规则集':'添加规则集',`<form id="library-form"><label class="field">名称<input name="name" required maxlength="100" value="${esc(library?.name||'')}" placeholder="例如：流媒体 / 自定义直连"></label><label class="field">来源方式<select name="source_type" id="rule-source-type"><option value="manual">手动粘贴</option><option value="url" ${remote?'selected':''}>订阅地址</option><option value="file">导入文件</option></select></label><label class="field" id="rule-url-field">订阅地址<input name="url" type="url" value="${esc(source.url)}" placeholder="https://…/rules.yaml"></label><label class="field">规则格式<select name="format" id="rule-source-format">${Object.entries(ruleFormats).map(([key,label])=>`<option value="${key}" ${(remote?source.format:library?.kind||'auto')===key?'selected':''}>${label}</option>`).join('')}</select></label><label class="field" id="rule-text-field">规则内容<textarea name="text" rows="10" spellcheck="false" placeholder="每行一条，例如 DOMAIN-SUFFIX,example.com 或 +.example.com">${esc(library?.entries.join('\n')||'')}</textarea></label><label class="field" id="rule-file-field">选择文件<input name="file" id="rule-import-file" type="file" accept=".yaml,.yml,.txt,.list,.json,.mrs,.srs"><small>支持 UTF-8 文本、YAML、JSON、MRS、SRS，最多 16 MiB</small></label><details class="advanced-options" id="rule-source-schedule"><summary>自动更新与请求设置</summary><label class="check-row"><input name="auto_update" type="checkbox" ${source.auto_update!==false?'checked':''}> 自动更新</label><div class="fields"><label class="field">更新方式<select name="schedule_mode" id="rule-schedule-mode"><option value="interval" ${source.schedule_mode!=='daily'?'selected':''}>按间隔</option><option value="daily" ${source.schedule_mode==='daily'?'selected':''}>每天固定时刻</option></select></label><label class="field" id="rule-interval-field">间隔（分钟）<input name="interval_minutes" type="number" min="1" max="43200" value="${source.interval_minutes||1440}"></label><label class="field" id="rule-daily-field">北京时间<input name="daily_time" type="time" value="${esc(source.daily_time||'04:00')}"></label></div><div class="fields"><label class="field">请求超时（秒）<input name="request_timeout" type="number" min="3" max="120" value="${source.request_timeout||20}"></label><label class="field">请求 UA<input name="user_agent" value="${esc(source.user_agent||'YoMesh/0.4')}"></label></div></details><p class="preview-note">规则集只保存匹配内容，流量出口在分流规则里选择。AdGuard 支持域名拦截；含例外、修饰符或不兼容逻辑时会明确报错。</p><p class="error-message" id="library-form-error" role="alert"></p><div class="dialog-actions"><button class="button" type="button" id="library-cancel">取消</button><button class="button primary" type="submit" id="library-submit">保存规则集</button></div></form>`);
  const fields=()=>{
    const type=$('#rule-source-type').value;
    const setSourceField=(selector,active)=>{
      const field=$(selector),control=$('input,textarea',field);
      field.hidden=!active;control.disabled=!active;control.required=active;
    };
    setSourceField('#rule-url-field',type==='url');
    setSourceField('#rule-text-field',type==='manual');
    setSourceField('#rule-file-field',type==='file');
    $('#rule-source-schedule').hidden=type!=='url';
    $$('#rule-source-schedule input,#rule-source-schedule select').forEach(control=>control.disabled=type!=='url');
    const daily=$('#rule-schedule-mode').value==='daily';
    $('#rule-interval-field').hidden=daily;$('#rule-interval-field input').disabled=type!=='url'||daily;
    $('#rule-daily-field').hidden=!daily;$('#rule-daily-field input').disabled=type!=='url'||!daily;
    $('#library-submit').textContent=type==='url'?'保存并更新':type==='file'?'导入规则集':'保存规则集';
  };
  $('#rule-source-type').onchange=()=>{if($('#rule-source-type').value==='manual')$('#rule-source-format').value=library?.kind||'auto';fields()};$('#rule-schedule-mode').onchange=fields;fields();
  $('#library-cancel').onclick=()=>$('#dialog').close();
  $('#library-form').onsubmit=async e=>{
    e.preventDefault();const form=e.target,values=new FormData(form),button=$('#library-submit');button.disabled=true;$('#library-cancel').disabled=true;$('#close-dialog').disabled=true;button.textContent='正在读取并检查规则…';$('#library-form-error').textContent='';
    try{
      const type=values.get('source_type'),item={name:values.get('name'),source:{type,format:values.get('format')},text:values.get('text')};
      if(library){item.id=library.id;item.note=library.note||''}
      if(type==='url')Object.assign(item.source,{url:values.get('url'),auto_update:values.has('auto_update'),interval_minutes:Number(values.get('interval_minutes')||1440),schedule_mode:values.get('schedule_mode'),daily_time:values.get('daily_time')||'04:00',request_timeout:Number(values.get('request_timeout')),user_agent:values.get('user_agent')});
      if(type==='file'){
        const file=$('#rule-import-file').files[0];if(!file)throw Error('请选择规则文件。');if(file.size>16*1024*1024)throw Error('规则文件不能超过 16 MiB。');
        const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        item.file_base64=btoa(binary);item.source.filename=file.name;delete item.text;
      }
      adoptWorkspace(await api('/api/library/save',{item,revision:liveWorkspace.revision}));$('#dialog').close();toast('规则集已保存，可在条件组中直接引用。');
    }catch(error){$('#library-form-error').textContent=error.message;if(error.message.includes('服务器数据已更新'))await reloadWorkspace({repaint:false,keepDraft:true})}
    finally{button.disabled=false;$('#library-cancel').disabled=false;$('#close-dialog').disabled=false;fields()}
  };
};
addLibrary=()=>editLibrary(null);

function defaultCondition(kind='matcher') {
  if(kind==='matcher')return {type:'DOMAIN-SUFFIX',values:[]};
  const condition={type:'RULE-SET',value:''};conditionSourceKinds.set(condition,kind);return condition;
}
function conditionSource(condition) { return condition.type==='RULE-SET'?'ruleset':'matcher'; }

function previewCompoundRule() {
  const serial=++rulePreviewSerial;clearTimeout(rulePreviewTimer);$('#compound-save').disabled=true;
  $('#condition-totals').textContent=`${ruleDraft.condition_groups.length} 个条件组 · ${ruleDraft.condition_groups.reduce((n,g)=>n+g.conditions.length,0)} 个条件`;
  $('#rule-target-preview').textContent=targetName(ruleDraft.target);$('#compound-error').textContent='';
  if(ruleDraft.condition_groups.some(g=>!g.conditions.length||g.conditions.some(c=>c.type==='RULE-SET'?!c.value:!(c.values?.length||c.value)))){$('#rule-logic-preview').textContent='请选择规则订阅、本地规则集，或填写匹配器内容。';return}
  rulePreviewTimer=setTimeout(async()=>{
    try{
      const policy={...clone(ruleDraft),name:ruleDraft.name.trim()||'未命名规则'},profile={library:data.library,nodes:data.nodes,sources:data.sources,groups:data.groups};
      const result=await api('/api/workspace/preview-rule',{profile,policy});
      if(serial!==rulePreviewSerial||!ruleDraft||!$('#rule-logic-preview'))return;
      $('#rule-logic-preview').textContent=result.summary;
      $('#rule-dns-preview').textContent=result.has_ip?'IP 条件在代理侧执行；DNS 不会把含 IP 的 AND 条件改成单独的域名匹配。':{auto:'DNS 自动跟随动作',local:'DNS 使用本地上游',remote:'DNS 使用代理上游'}[ruleDraft.dns||'auto'];
      $('#compound-save').disabled=!ruleDraft.name.trim();
    }catch(error){if(serial===rulePreviewSerial&&$('#compound-error')){$('#compound-error').textContent=error.message;$('#rule-logic-preview').textContent='请补全或修正条件';$('#compound-save').disabled=true}}
  },220);
}

let groupMemberKind='sources', groupMemberQuery='';
renderGroupEditor=function() {
  if(!draft)return;
  const tplBar=typeof nodeGroupTemplateBar==='function'?nodeGroupTemplateBar('data-pg-template'):'';
  $('#group-editor').innerHTML=`<div class="panel-header"><div><h2 id="editor-title">${esc(draft.name)}</h2><small>成员、选择方式与测速</small></div><label class="check-row"><input id="group-enabled" type="checkbox" ${draft.enabled!==false?'checked':''}>启用</label></div><div class="editor-body">${tplBar}<div class="fields"><label class="field">节点组名称<input id="group-name" value="${esc(draft.name)}" maxlength="80"></label><label class="field">选择方式<select id="group-mode">${Object.entries(modes).map(([key,label])=>`<option value="${key}" ${draft.mode===key?'selected':''}>${label}</option>`).join('')}</select></label></div><section class="form-section"><h3>成员来源</h3><p class="preview-note">订阅、节点和子组可以组合选择；正则仅筛选节点，不筛选子组。</p><div class="member-picker-tabs view-tabs">${[['sources','订阅'],['nodes','节点'],['groups','节点组']].map(([key,label])=>`<button type="button" class="button ${key===groupMemberKind?'active':''}" data-member-kind="${key}">${label} <span>${draft[key].length}</span></button>`).join('')}</div><label class="search"><input id="member-search" aria-label="搜索成员来源" placeholder="搜索成员来源" value="${esc(groupMemberQuery)}"></label><div class="member-picker-toolbar"><button class="text-button" id="member-select-all">选择搜索结果</button><button class="text-button" id="member-clear">清空本类选择</button></div><div id="member-picker-options" class="member-picker-options"></div></section><section class="form-section"><h3>正则筛选</h3><div class="fields"><label class="field">包含正则<input id="include" value="${esc(draft.include)}" placeholder="如：香港|HK"></label><label class="field">排除正则<input id="exclude" value="${esc(draft.exclude)}" placeholder="如：试用|流量"></label></div></section><section class="form-section"><div class="preview-header"><strong>成员预览 <span id="preview-count"></span></strong><button class="button" id="test-group">${actionIcon('test')}保存并测速</button></div><p class="preview-note" id="group-test-status"></p><div id="group-error" class="error-message" role="alert"></div><div id="member-preview"></div><button class="text-button" id="clear-group-selected" ${draft.mode==='select'?'':'hidden'}>清除默认成员，按成员顺序</button><p class="preview-note">手动组点选默认成员；上下移动调整成员顺序。这里配置下次导出的行为。</p></section><details class="advanced-options"><summary>测速与切换设置</summary><label class="field">测速 URL<input id="group-test-url" type="url" value="${esc(draft.test_url||'')}" placeholder="默认 https://www.gstatic.com/generate_204"></label><div class="fields"><label class="field">手动测速超时（毫秒）<input id="group-test-timeout" type="number" min="500" max="30000" value="${draft.test_timeout_ms||3000}"></label><label class="field" id="group-interval-field">自动检查间隔（秒）<input id="group-interval" type="number" min="1" value="${draft.interval||300}"></label><label class="field" id="group-tolerance-field">切换容差（毫秒）<input id="group-tolerance" type="number" min="0" value="${draft.tolerance??50}"></label></div><p class="preview-note" id="group-mode-note"></p></details></div><div class="editor-footer"><button class="text-button danger-action" id="delete-group">删除节点组</button><div class="filter-row"><button class="button" id="reset-group">还原</button><button class="button primary" id="save-group">保存更改</button></div></div>`;
  const modeFields=()=>{$('#group-interval-field').hidden=draft.mode==='select';$('#group-tolerance-field').hidden=draft.mode!=='url-test';$('#clear-group-selected').hidden=draft.mode!=='select';$('#group-mode-note').textContent=['fallback','load-balance'].includes(draft.mode)?'该方式原样支持 Mihomo；sing-box 导出时需明确选择兼容方式。':'支持 Mihomo 和 sing-box；手动测速超时仅控制工作台发起的测试。'};
  $$('[data-pg-template]').forEach(btn=>btn.onclick=()=>{
    const tpl=NODE_GROUP_TEMPLATES[Number(btn.dataset.pgTemplate)];if(!tpl)return;
    draft.name=tpl.name;draft.mode=tpl.mode;draft.include=tpl.include;if(draft.mode!=='select')delete draft.selected;
    $('#group-name').value=tpl.name;$('#group-mode').value=tpl.mode;$('#include').value=tpl.include;
    modeFields();updatePreview();toast(`已填入「${tpl.label}」模板，请勾选订阅或节点来源后保存。`);
  });
  $('#group-name').oninput=e=>{draft.name=e.target.value;updatePreview()};
  $('#group-enabled').onchange=e=>{draft.enabled=e.target.checked;updatePreview()};
  $('#group-mode').onchange=e=>{draft.mode=e.target.value;if(draft.mode!=='select')delete draft.selected;modeFields();updatePreview()};
  $('#include').oninput=e=>{draft.include=e.target.value;updatePreview()};$('#exclude').oninput=e=>{draft.exclude=e.target.value;updatePreview()};
  $('#group-interval').oninput=e=>{draft.interval=Number(e.target.value);updatePreview()};$('#group-tolerance').oninput=e=>{draft.tolerance=Number(e.target.value);updatePreview()};
  $('#group-test-url').oninput=e=>{if(e.target.value.trim())draft.test_url=e.target.value.trim();else delete draft.test_url;updatePreview()};$('#group-test-timeout').oninput=e=>{draft.test_timeout_ms=Number(e.target.value);updatePreview()};
  $$('[data-member-kind]').forEach(b=>b.onclick=()=>{groupMemberKind=b.dataset.memberKind;groupMemberQuery='';renderGroupEditor()});
  $('#member-search').oninput=e=>{groupMemberQuery=e.target.value;paintMemberPicker()};
  $('#member-select-all').onclick=()=>{draft[groupMemberKind]=[...new Set([...draft[groupMemberKind],...memberCandidates().map(x=>x.id)])];paintMemberPicker();updatePreview()};
  $('#member-clear').onclick=()=>{draft[groupMemberKind]=[];delete draft.selected;paintMemberPicker();updatePreview()};
  $('#clear-group-selected').onclick=()=>{delete draft.selected;updatePreview()};
  $('#reset-group').onclick=()=>{draft=clone(saved.groups.find(g=>g.id===selectedGroup));renderGroups()};
  $('#save-group').onclick=async()=>{try{await persist()}catch(e){toast(e.message)}};
  $('#delete-group').onclick=()=>{const ident=draft.id;confirmRemoval('删除节点组',`删除「${draft.name}」？仍被规则或其他组引用时会阻止删除。`,()=>{data.groups=data.groups.filter(g=>g.id!==ident);draft=null;selectedGroup=data.groups[0]?.id})};
  $('#test-group').onclick=async()=>{
    const ident=draft.id;
    try{if(dirty())await persist();adoptWorkspace(await api(`/api/groups/${ident}/test`,{}),{keepDraft:true});toast('已开始通过节点测试 HTTP 延迟。')}
    catch(e){toast(e.message)}
  };
  modeFields();paintMemberPicker();updateGroupProbeState();updatePreview();
};
function memberCandidates(){return data[groupMemberKind].filter(item=>item.id!==draft.id&&`${item.name} ${item.protocol||''}`.toLowerCase().includes(groupMemberQuery.toLowerCase()))}
function paintMemberPicker(){
  const items=memberCandidates();
  $('#member-picker-options').innerHTML=items.length?items.map(item=>`<label class="member-picker-option"><input type="checkbox" value="${esc(item.id)}" data-pick-member ${draft[groupMemberKind].includes(item.id)?'checked':''}><span>${esc(item.name)}<small>${groupMemberKind==='sources'?data.nodes.filter(n=>n.source===item.id).length+' 个节点':groupMemberKind==='groups'?'子组 · '+modes[item.mode]:esc(item.protocol)}${item.enabled===false?' · 已停用':''}</small></span></label>`).join(''):'<p class="preview-note">没有匹配的成员</p>';
  $$('[data-pick-member]').forEach(input=>input.onchange=()=>{if(input.checked)draft[groupMemberKind].push(input.value);else{draft[groupMemberKind]=draft[groupMemberKind].filter(i=>i!==input.value);if(draft.selected===input.value)delete draft.selected}updatePreview()});
}
function updateGroupProbeState(){
  if(!draft||!$('#group-test-status'))return;
  const status=liveWorkspace.state?.group_status?.[draft.id]||{},running=status.test_state==='running';
  $('#test-group').disabled=running||draft.enabled===false;
  $('#group-test-status').textContent=running?`正在测速 ${status.test_finished||0} / ${status.test_total||0}…`:status.test_state==='success'?`${status.test_success} / ${status.test_total} 个测试成功 · ${serverTime(status.tested_at)}`:status.test_state==='error'?status.error:'测速通过真实节点访问测试地址，显示 HTTP 延迟。';
}
updatePreview=function(){
  if(!draft||!$('#group-error'))return;
  $$('[data-member-kind]').forEach(b=>{$('span',b).textContent=draft[b.dataset.memberKind].length});
  clearTimeout(liveWorkspace.previewTimer);const serial=++liveWorkspace.previewSerial,group=clone(draft),error=groupError(group);
  $('#editor-title').textContent=group.name||'未命名节点组';$('#group-error').textContent=error;$('#save-group').disabled=!!error;updateGroupProbeState();
  if(error){$('#member-preview').textContent='修正错误后查看成员';return}
  liveWorkspace.previewTimer=setTimeout(async()=>{
    try{
      const profile={sources:data.sources,nodes:data.nodes,groups:data.groups.map(g=>g.id===group.id?group:g)};
      const result=await api('/api/workspace/preview-group',{profile,group_id:group.id});
      if(serial!==liveWorkspace.previewSerial||page!=='groups'||!$('#member-preview'))return;
      $('#preview-count').textContent=`(${result.members.length})`;
      $('#member-preview').innerHTML=result.members.length?`<div class="ordered-members">${result.members.map((member,index)=>{
        const status=nodeStatus(member.id),latency=status.state==='success'?badge(status.latency_ms+' ms','green'):status.state==='failed'?badge('超时 / 失败','error'):badge(member.protocol==='节点组'?'子组':'未测速');
        return `<div class="ordered-member"><label>${group.mode==='select'?`<input type="radio" name="group-default-member" value="${esc(member.id)}" data-default-member ${group.selected===member.id?'checked':''}>`:''}<span>${esc(member.name)}<small>${esc(member.protocol)}</small></span></label>${latency}<div class="filter-row"><button class="icon-button" data-member-up="${index}" aria-label="上移成员 ${esc(member.name)}" ${index===0?'disabled':''}>${actionIcon('up')}</button><button class="icon-button" data-member-down="${index}" aria-label="下移成员 ${esc(member.name)}" ${index===result.members.length-1?'disabled':''}>${actionIcon('down')}</button></div></div>`;
      }).join('')}</div>`:`<div class="empty">${group.enabled===false?'节点组已停用，不参与导出。':'没有匹配成员，可先保存草稿；导出前需要有效成员。'}</div>`;
      $$('[data-default-member]').forEach(input=>input.onchange=()=>{draft.selected=input.value;updatePreview()});
      const move=(index,step)=>{const ids=result.members.map(m=>m.id),next=index+step;if(next<0||next>=ids.length)return;[ids[index],ids[next]]=[ids[next],ids[index]];draft.order=ids;updatePreview()};
      $$('[data-member-up]').forEach(b=>b.onclick=()=>move(Number(b.dataset.memberUp),-1));$$('[data-member-down]').forEach(b=>b.onclick=()=>move(Number(b.dataset.memberDown),1));
    }catch(error){if(serial===liveWorkspace.previewSerial&&$('#group-error')){$('#group-error').textContent=error.message;$('#save-group').disabled=true}}
  },220);
};
const adoptWithRuleUpdates=adoptWorkspace;
adoptWorkspace=function(value,options={}){adoptWithRuleUpdates(value,options);if(options.repaint===false&&page==='groups'&&!$('#dialog').open)updatePreview()};
