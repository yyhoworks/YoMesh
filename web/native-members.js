'use strict';

const memberArrow=step=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="${step<0?'m6 14 6-6 6 6':'m6 10 6 6 6-6'}"/></svg>`;
function markMemberDialog(){
  $('#dialog').classList.add('native-member-dialog');
  $('#dialog').addEventListener('close',()=>{if(!$('#dialog').open)$('#dialog').classList.remove('native-member-dialog')},{once:true});
}

function openNativeGroupLocation(editor,done=null){
  if(editor.type==='rule')nativePolicyEditor(editor.id,done);
  else nativeGroupEditor(editor.name,editor.role,null,done);
}
let nativeEditorReturn=null;
function setNativeEditorReturn(done){
  const dialog=$('#dialog');if(nativeEditorReturn)dialog.removeEventListener('close',nativeEditorReturn);
  nativeEditorReturn=null;if(!done)return;const scope=spaceId;
  nativeEditorReturn=()=>{nativeEditorReturn=null;queueMicrotask(()=>{if(spaceId===scope)done()})};
  dialog.addEventListener('close',nativeEditorReturn,{once:true});
}

const NODE_GROUP_TEMPLATES=[
  {label:'🇭🇰 香港节点',name:'🇭🇰 香港节点',mode:'url-test',region:'HK',include:'(?i)(港|HK|Hong\\s*Kong)'},
  {label:'🇹🇼 台湾节点',name:'🇹🇼 台湾节点',mode:'url-test',region:'TW',include:'(?i)(台|TW|Taiwan|Taipei)'},
  {label:'🇯🇵 日本节点',name:'🇯🇵 日本节点',mode:'url-test',region:'JP',include:'(?i)(日|JP|Japan|Tokyo|Osaka)'},
  {label:'🇸🇬 新加坡节点',name:'🇸🇬 新加坡节点',mode:'url-test',region:'SG',include:'(?i)(新|SG|Singapore|狮城)'},
  {label:'🇺🇸 美国节点',name:'🇺🇸 美国节点',mode:'url-test',region:'US',include:'(?i)(美|US|United\\s*States|America)'},
  {label:'🚀 默认代理',name:'🚀 默认代理',mode:'select',region:'',include:''}
];
function nodeGroupTemplateBar(attr='data-ng-template'){
  return `<div class="rule-preset-bar" style="margin-bottom:12px"><div class="rule-preset-header"><span>📦 常用节点组模板（点击填入名称、模式与地区筛选，再自行勾选订阅或节点保存）</span></div><div class="rule-preset-pills">${NODE_GROUP_TEMPLATES.map((t,idx)=>`<button type="button" class="rule-preset-pill" ${attr}="${idx}">${esc(t.label)}</button>`).join('')}</div></div>`;
}
nativeGroupEditor=function(name=null,role='nodes',restored=null,done=null){
  const groups=nativeValue().mihomo['proxy-groups']||[],original=groups.find(g=>g.name===name),base=original||{name:'新节点组',type:'select',proxies:['DIRECT']};
  const initial=restored||{name:base.name,type:base.type,filter:base.filter||'',providers:clone(base.use||[]),members:clone(base.proxies||[]),advanced:Object.fromEntries(Object.entries(base).filter(([key])=>!['name','type','filter','use','proxies'].includes(key)))};
  const e=clone(initial),providers=Object.keys(nativeMap('proxy-providers'));
  const choices=(nativePolicyView().group_member_choices||[]).filter(c=>c.value!==name&&!c.blocked_for?.includes(name));
  const known=new Set(choices.map(c=>c.value));e.members.filter(m=>!known.has(m)).forEach(value=>choices.push({value,label:'原成员 · '+value,resource:'existing'}));
  showDialog(role==='nodes'?(name?'编辑节点组':'添加节点组'):role==='rules'?'编辑未关联出口':'编辑共用出口',`<form id="native-group-form">${nodeGroupTemplateBar('data-ng-template')}<div class="fields"><label class="field">名称<input id="ng-name" value="${esc(e.name)}" ${name?'readonly':''} required></label><label class="field">选择方式<select id="ng-type">${!modes[e.type]?`<option value="${esc(e.type)}" selected>保留原生 ${esc(e.type)}</option>`:''}${Object.entries(modes).map(([key,label])=>`<option value="${key}" ${e.type===key?'selected':''}>${label}</option>`).join('')}</select></label></div><section class="native-member-section"><h3>订阅节点</h3><p class="preview-note">先在“订阅来源”添加订阅，再在这里勾选，用正则筛选香港、日本等线路。</p><div class="fields">${providers.map(p=>`<label class="check-row"><input type="checkbox" data-ng-source="${esc(p)}" ${e.providers.includes(p)?'checked':''}>${esc(p)}</label>`).join('')||'<p class="preview-note">还没有节点订阅，可先到左侧“订阅来源”添加。</p>'}</div><label class="field">包含正则<input id="ng-filter" value="${esc(e.filter)}" placeholder="例如：香港|HK"></label></section><section class="native-member-section"><div class="section-title"><h3>节点与子组成员</h3><button type="button" class="button" id="ng-add-node">添加手动节点</button></div><p class="preview-note">自建节点在节点库维护；应用出口在对应规则组内配置，可从成员旁的配置按钮打开。</p><label class="search">${icon('search')}<input id="ng-member-search" aria-label="搜索节点或子组" placeholder="搜索节点、子组或直连"></label><label class="check-row ng-advanced-references"><input type="checkbox" id="ng-show-rule-outlets">显示规则组出口（高级引用）</label><p id="ng-member-count" class="member-selection-summary" role="status"></p><div id="ng-member-choices" class="group-member-choices"></div><small class="preview-note">已选成员按上面的顺序导出，可用上下按钮调整。</small></section><details class="advanced-options native-member-section"><summary>测速、延迟容差及其他原生字段</summary><textarea id="ng-advanced" class="mono" rows="8" spellcheck="false">${esc(JSON.stringify(e.advanced,null,2))}</textarea></details><p id="ng-error" class="error-message" role="alert"></p><div class="native-member-footer"><button type="button" class="button" id="ng-cancel">${done?'返回上级节点组':'取消'}</button><button type="submit" class="button primary">保存节点组</button></div></form>`);
  markMemberDialog();
  setNativeEditorReturn(done);
  const paint=()=>{
    const term=$('#ng-member-search').value.toLowerCase(),picked=new Set(e.members),ordered=e.members.map(m=>choices.find(c=>c.value===m)).filter(Boolean).concat(choices.filter(c=>!picked.has(c.value)));
    $('#ng-member-count').textContent=e.members.length?`已选 ${e.members.length} 个显式成员；已选项排在前面。`:'尚未选择显式成员；也可以只使用上面的订阅筛选。';
    const visible=ordered.filter(c=>(onMember(c)||c.group_role!=='rules'||$('#ng-show-rule-outlets').checked)&&(!term||c.label.toLowerCase().includes(term)));
    function onMember(c){return picked.has(c.value)}
    $('#ng-member-choices').innerHTML=visible.map(c=>{const index=e.members.indexOf(c.value),on=index>=0;return `<div class="group-member-choice ${on?'member-chosen':''}"><label><input type="checkbox" data-ng-member="${esc(c.value)}" ${on?'checked':''}>${on?`<span class="member-position">${index+1}</span>`:''}<span>${esc(c.label)}</span></label><span class="member-order-actions">${c.editor?`<button type="button" class="text-button member-configure" data-ng-config="${esc(c.value)}" aria-label="配置${esc(c.label)}">${c.editor.type==='rule'?'配置规则组':'配置'}</button>`:''}${on?`<button type="button" class="icon-button" data-ng-move="${esc(c.value)}" data-step="-1" aria-label="上移${esc(c.label)}" ${index===0?'disabled':''}>${memberArrow(-1)}</button><button type="button" class="icon-button" data-ng-move="${esc(c.value)}" data-step="1" aria-label="下移${esc(c.label)}" ${index===e.members.length-1?'disabled':''}>${memberArrow(1)}</button>`:''}</span></div>`}).join('')||'<p class="empty">没有符合条件的成员。应用出口可勾选上方“显示规则组出口”。</p>';
    $$('[data-ng-member]').forEach(input=>input.onchange=()=>{if(input.checked)e.members.push(input.dataset.ngMember);else e.members=e.members.filter(m=>m!==input.dataset.ngMember);paint()});
    $$('[data-ng-move]').forEach(button=>button.onclick=()=>{const from=e.members.indexOf(button.dataset.ngMove),to=from+Number(button.dataset.step);if(to<0||to>=e.members.length)return;[e.members[from],e.members[to]]=[e.members[to],e.members[from]];paint()});
    $$('[data-ng-config]').forEach(button=>button.onclick=()=>{try{read();const scope=spaceId,editor=choices.find(c=>c.value===button.dataset.ngConfig).editor;openNativeGroupLocation(editor,()=>{if(spaceId===scope)nativeGroupEditor(name,role,e,done)})}catch(error){$('#ng-error').textContent=error.message}});
  };
  const read=()=>{e.name=$('#ng-name').value.trim();e.type=$('#ng-type').value;e.filter=$('#ng-filter').value;const chosen=$$('[data-ng-source]:checked').map(el=>el.dataset.ngSource);e.providers=e.providers.filter(p=>chosen.includes(p)).concat(chosen.filter(p=>!e.providers.includes(p)));e.advanced=JSON.parse($('#ng-advanced').value);if(!e.advanced||Array.isArray(e.advanced)||typeof e.advanced!=='object')throw Error('高级字段需要 JSON 对象。');if(typeof applyInteractiveGroupFields==='function')applyInteractiveGroupFields(e);return e};
  if(typeof enhanceInteractiveGroup==='function')enhanceInteractiveGroup(e,read);
  $$('[data-ng-template]').forEach(btn=>btn.onclick=()=>{
    const tpl=NODE_GROUP_TEMPLATES[Number(btn.dataset.ngTemplate)];if(!tpl)return;
    if(!name)$('#ng-name').value=tpl.name;
    $('#ng-type').value=tpl.mode;
    if($('#it-region')){$('#it-region').value=tpl.region;$('#ng-filter').value='';$('#it-region').dispatchEvent(new Event('change',{bubbles:true}))}
    else{$('#ng-filter').value=tpl.include;$('#ng-filter').dispatchEvent(new Event('input',{bubbles:true}))}
    toast(`已填入「${tpl.label}」模板，请勾选订阅或节点后保存。`);
  });
  $('#ng-member-search').oninput=paint;$('#ng-show-rule-outlets').onchange=paint;paint();$('#ng-cancel').onclick=()=>$('#dialog').close();
  $('#ng-add-node').onclick=()=>{try{read();nativeNodeEditor(null,created=>{if(created&&!e.members.includes(created))e.members.push(created);nativeGroupEditor(name,role,e,done)})}catch(error){$('#ng-error').textContent=error.message}};
  $('#native-group-form').onsubmit=async event=>{
    event.preventDefault();try{
      read();const updated={...e.advanced,name:e.name,type:e.type};
      for(const [key,value,empty] of [['filter',e.filter,''],['use',e.providers,[]],['proxies',e.members,[]]])if(Object.hasOwn(base,key)||JSON.stringify(value)!==JSON.stringify(empty))updated[key]=value;
      if(!original&&groups.some(g=>g.name===updated.name))throw Error('节点组名称已存在，请换一个名称。');
      const n=clone(nativeValue());n.mihomo['proxy-groups']??=[];if(name)n.mihomo['proxy-groups']=n.mihomo['proxy-groups'].map(g=>g.name===name?updated:g);else n.mihomo['proxy-groups'].push(updated);
      if(!original||n.group_roles?.[updated.name]){n.group_roles??={};n.group_roles[updated.name]=role}
      await saveNative(n);$('#dialog').close();
    }catch(error){$('#ng-error').textContent=error instanceof SyntaxError?'高级字段 JSON 格式无效。':error.message}
  };
};

let nodeOrigin='all',nativeNodeProvider='all';
function nodeOriginTabs(manual,total){
  return `<div class="node-origin-tabs" role="group" aria-label="节点类型">${[['all','全部节点',total],['manual','自建 / 回家节点',manual],['subscription','订阅节点',total-manual]].map(([key,label,count])=>`<button type="button" class="button ${nodeOrigin===key?'active':''}" data-node-origin="${key}" aria-pressed="${nodeOrigin===key}">${label}<span>${count}</span></button>`).join('')}</div>`;
}
function bindNodeOrigin(render){$$('[data-node-origin]').forEach(button=>button.onclick=()=>{nodeOrigin=button.dataset.nodeOrigin;render()})}
nativeRenderNodes=function(){
  const rows=liveWorkspace.state.native_view?.nodes||[],providers=Object.keys(nativeMap('proxy-providers'));
  if(!providers.includes(nativeNodeProvider))nativeNodeProvider='all';
  const manual=rows.filter(n=>n.origin==='manual'),subscriptions=rows.filter(n=>n.origin==='subscription'),term=query.toLowerCase();
  const matches=n=>(n.name+' '+(n.provider||'')+' '+n.type+' '+(n.server||'')).toLowerCase().includes(term);
  const row=n=>`<article class="panel native-resource-row"><div class="native-resource-name"><strong>${esc(n.name)}</strong><small>${esc(n.type)}${n.server?' · '+esc(n.server):''}${n.port?':'+esc(n.port):''}</small></div>${n.origin==='manual'?`<button class="button" data-native-node-edit="${esc(n.name)}">编辑</button>`:'<span class="preview-note">订阅同步</span>'}</article>`;
  const section=(title,items,note)=>`<section class="node-origin-section"><div class="section-title"><h2>${esc(title)} <span>${items.length}</span></h2></div>${note?`<p class="preview-note">${note}</p>`:''}<div class="native-resource-list">${items.map(row).join('')}</div></section>`;
  const own=manual.filter(matches);let content='';
  if(nodeOrigin!=='subscription'&&(own.length||nodeOrigin==='manual'))content+=section('自建 / 回家节点',own,'手动维护，订阅刷新不会覆盖。保存后到节点组勾选使用。')+(own.length?'':'<p class="empty">没有符合条件的自建节点，可用右上角“添加节点”创建。</p>');
  if(nodeOrigin!=='manual')for(const provider of providers){const items=subscriptions.filter(n=>n.provider===provider&&matches(n));if((nativeNodeProvider==='all'||provider===nativeNodeProvider)&&items.length)content+=section('订阅 · '+provider,items,'')}
  $('#content').innerHTML=nodeOriginTabs(manual.length,rows.length)+`<div class="toolbar"><label class="search">${icon('search')}<input id="native-search" aria-label="搜索节点" placeholder="搜索名称、协议或服务器" value="${esc(query)}"></label>${nodeOrigin!=='manual'?`<select id="native-node-provider" aria-label="按订阅来源筛选"><option value="all">全部订阅来源</option>${providers.map(p=>`<option value="${esc(p)}" ${p===nativeNodeProvider?'selected':''}>${esc(p)}</option>`).join('')}</select>`:''}</div>`+(content||'<p class="empty">没有符合条件的节点。可调整筛选，或到订阅来源刷新节点。</p>');
  bindNodeOrigin(nativeRenderNodes);bindNativeSearch(nativeRenderNodes);if($('#native-node-provider'))$('#native-node-provider').onchange=event=>{nativeNodeProvider=event.target.value;nativeRenderNodes()};$$('[data-native-node-edit]').forEach(button=>button.onclick=()=>nativeNodeEditor(button.dataset.nativeNodeEdit));
};

const renderNativeLineGroups=nativeRenderGroups;
nativeRenderGroups=function(){
  renderNativeLineGroups();
  const locations=nativePolicyView().group_management||[];
  $('#content').insertAdjacentHTML('afterbegin','<div class="node-group-help"><p>这里只维护线路和共用组合；Netflix、人工智能等应用出口在对应规则中编辑。</p><a class="button" href="#library">配置应用规则与出口</a></div>');
  const unused=locations.filter(m=>m.role==='rules'&&m.editor.type==='group');
  if(unused.length){$('#content').insertAdjacentHTML('beforeend',`<details class="workflow-unused"><summary>高级：未关联出口（${unused.length}）</summary><div class="native-resource-list">${unused.map(m=>`<div class="native-resource-row"><strong>${esc(m.name)}</strong><button class="button" data-group-location="${esc(m.name)}">配置</button></div>`).join('')}</div></details>`);$$('[data-group-location]').forEach(button=>button.onclick=()=>openNativeGroupLocation(locations.find(m=>m.name===button.dataset.groupLocation).editor))}
  $$('[data-native-group]').forEach(button=>button.onclick=()=>{const location=locations.find(m=>m.name===button.dataset.nativeGroup);nativeGroupEditor(button.dataset.nativeGroup,location?.role||'nodes')});
};

function nativeNodeEditor(name=null,done=null){
  const original=(nativeValue().mihomo.proxies||[]).find(n=>n.name===name),node=clone(original||{name:'',type:'ss',server:'',port:443,cipher:'aes-128-gcm',password:'',udp:true});
  const protocols={ss:'Shadowsocks',hysteria2:'Hysteria2',trojan:'Trojan',vmess:'VMess',vless:'VLESS',wireguard:'WireGuard'};
  const managed=['name','type','server','port','password','uuid','cipher'];
  showDialog(name?'编辑手动节点':'添加手动节点',`<form id="native-node-form"><p class="preview-note">保存到当前方案的节点库，可在节点组里勾选。现有节点名保持固定，连接扩展保留在高级参数中。</p><div class="fields"><label class="field">名称<input id="nn-name" value="${esc(node.name)}" ${name?'readonly':''} required></label><label class="field">协议<select id="nn-type">${!protocols[node.type]?`<option value="${esc(node.type)}" selected>保留原生 ${esc(node.type)}</option>`:''}${Object.entries(protocols).map(([key,label])=>`<option value="${key}" ${node.type===key?'selected':''}>${label}</option>`).join('')}</select></label><label class="field">服务器<input id="nn-server" value="${esc(node.server||'')}" placeholder="IP 或域名"></label><label class="field">端口<input id="nn-port" type="number" min="1" max="65535" value="${esc(node.port??'')}"></label></div><label class="field" id="nn-password-field">连接密码<input id="nn-password" type="password" autocomplete="new-password" value="${esc(node.password||'')}"></label><label class="field" id="nn-uuid-field">UUID<input id="nn-uuid" value="${esc(node.uuid||'')}"></label><label class="field" id="nn-cipher-field">加密方法<input id="nn-cipher" value="${esc(node.cipher||'')}" placeholder="例如 aes-128-gcm"></label><details class="advanced-options native-member-section"><summary>TLS、传输、WireGuard 及其他原生参数</summary><textarea id="nn-advanced" class="mono" rows="9" spellcheck="false">${esc(JSON.stringify(Object.fromEntries(Object.entries(node).filter(([key])=>!managed.includes(key))),null,2))}</textarea><small>保留完整原生字段；WireGuard 等协议的密钥与对端在这里设置。</small></details><p id="nn-error" class="error-message" role="alert"></p><div class="native-member-footer"><button type="button" class="button" id="nn-cancel">${done?'返回节点组':'取消'}</button><button type="submit" class="button primary">${done?'保存并加入当前节点组':'保存节点'}</button></div></form>`);
  markMemberDialog();
  let created=null;setNativeEditorReturn(done?()=>done(created):null);
  const fields=()=>{const type=$('#nn-type').value;$('#nn-password-field').hidden=!['ss','trojan','hysteria2'].includes(type);$('#nn-uuid-field').hidden=!['vmess','vless'].includes(type);$('#nn-cipher-field').hidden=type!=='ss';$('#nn-server').required=type!=='wireguard';$('#nn-port').required=type!=='wireguard';$('#nn-password').required=['ss','trojan','hysteria2'].includes(type);$('#nn-uuid').required=['vmess','vless'].includes(type);$('#nn-cipher').required=type==='ss'};$('#nn-type').onchange=fields;fields();
  $('#nn-cancel').onclick=()=>$('#dialog').close();
  $('#native-node-form').onsubmit=async event=>{
    event.preventDefault();try{
      const advanced=JSON.parse($('#nn-advanced').value);if(!advanced||Array.isArray(advanced)||typeof advanced!=='object')throw Error('高级参数需要 JSON 对象。');
      const updated={...advanced,name:$('#nn-name').value.trim(),type:$('#nn-type').value};
      for(const key of ['server','port','password','uuid','cipher']){const input=$('#nn-'+key),value=key==='port'?(input.value?Number(input.value):null):input.value;if(Object.hasOwn(node,key)||value!==''&&value!==null)updated[key]=value}
      const n=clone(nativeValue());n.mihomo.proxies??=[];if(!name&&n.mihomo.proxies.some(p=>p.name===updated.name))throw Error('节点名称已存在，请换一个名称。');if(name)n.mihomo.proxies=n.mihomo.proxies.map(p=>p.name===name?updated:p);else n.mihomo.proxies.push(updated);
      await saveNative(n,'手动节点已保存，可到节点组勾选。');created=updated.name;$('#dialog').close();
    }catch(error){$('#nn-error').textContent=error instanceof SyntaxError?'高级参数 JSON 格式无效。':error.message}
  };
}

const renderBeforeNativeMembers=render;
render=function(){renderBeforeNativeMembers();if(nativeMode()&&page==='nodes'&&liveWorkspace.state)$('#create').onclick=()=>nativeNodeEditor()};
