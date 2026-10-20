'use strict';
pages.groups[1]='按地区、线路归类节点；应用规则与出口在规则组里一起配置。';
pages.library[1]='一条里选择规则来源、节点出口、选择方式与启停。';
// The server supplies rule-group boundaries and their linked outlet document.
const nativeUnitRows=()=>nativePolicyView().rule_groups||nativePolicyView().policies||[];
nativePolicyRow=ident=>nativeUnitRows().find(p=>p.id===ident);
nativePolicyTargets=selected=>{
  const options=nativePolicyView().outlet_choices||nativePolicyView().targets||[];
  return (selected&&!options.some(t=>t.value===selected)?`<option selected value="${esc(selected)}">当前自定义出口</option>`:'')+options.map(t=>`<option value="${esc(t.value)}" ${t.value===selected?'selected':''} ${!t.available&&t.value!==selected?'disabled':''}>${esc(t.label)}${!t.available?'（未选用）':''}</option>`).join('');
};
function nativeLibrarySubTabs(){
  const ruleCount=nativeUnitRows().filter(p=>!p.fallback).length,sourceCount=Object.keys(nativeMap('rule-providers')).length,unbound=nativePolicyView().unbound_outlets?.length||0;
  return `<div class="group-category-toolbar"><div class="group-category-tabs" role="tablist" aria-label="分流规则与来源切换"><button type="button" class="button ${!nativeRuleSourceMode?'active':''}" role="tab" aria-selected="${!nativeRuleSourceMode}" data-library-subtab="rules">分流规则<span>${ruleCount}</span></button><button type="button" class="button ${nativeRuleSourceMode?'active':''}" role="tab" aria-selected="${nativeRuleSourceMode}" data-library-subtab="sources">规则来源库<span>${sourceCount}</span></button></div>${!nativeRuleSourceMode&&unbound?`<button type="button" class="text-button" id="rg-unbound">未关联出口（${unbound}）</button>`:''}${nativeRuleSourceMode?`<button type="button" class="text-button" id="it-rule-reuse-tab">复用其他空间来源</button>`:''}</div>`;
}
function bindNativeLibrarySubTabs(){
  $$('[data-library-subtab]').forEach(b=>b.onclick=()=>{const next=b.dataset.librarySubtab==='sources';if(nativeRuleSourceMode===next)return;nativeRuleSourceMode=next;query='';render()});
  if($('#rg-unbound'))$('#rg-unbound').onclick=()=>showNativeOutletList('unbound_outlets','未关联出口');
  if($('#it-rule-reuse-tab')&&typeof openResourceReuse==='function')$('#it-rule-reuse-tab').onclick=()=>openResourceReuse('rule-providers');
}
nativePolicySourceLibrary=function(){
  ruleOrderCleanup();nativeRenderProviders('rule-providers');
  $('#content').insertAdjacentHTML('afterbegin',nativeLibrarySubTabs());
  bindNativeLibrarySubTabs();
};
nativeRuleSettings=function(){
  const complete=nativeUnitRows(),all=complete.filter(p=>!p.fallback),fallback=complete.find(p=>p.fallback);
  const categories={all:'全部',direct:'直连',proxy:'代理',reject:'拦截'},canOrder=!query&&nativeRuleCategory==='all';
  const rows=all.filter(p=>(nativeRuleCategory==='all'||p.kind===nativeRuleCategory)&&(p.name+' '+p.summary).toLowerCase().includes(query.toLowerCase()));
  $('#content').innerHTML=`${nativeLibrarySubTabs()}<div class="toolbar np-rule-toolbar"><div class="np-category" aria-label="筛选规则">${Object.entries(categories).map(([key,label])=>`<button type="button" class="button ${nativeRuleCategory===key?'active':''}" data-np-category="${key}" aria-pressed="${nativeRuleCategory===key}">${label}<span>${key==='all'?all.length:all.filter(p=>p.kind===key).length}</span></button>`).join('')}</div><label class="search">${icon('search')}<input id="native-search" aria-label="搜索规则" placeholder="搜索规则或来源" value="${esc(query)}"></label></div><p class="np-guide" role="status">${canOrder?'从上到下匹配；拖动左侧手柄排序，方向键也可调整。':'筛选中暂停排序；清空搜索并选择“全部”后可拖动。'}</p><div class="np-settings-list">${rows.map(p=>`<article class="panel np-setting-row rule-with-order ${!p.enabled?'np-disabled':''}" data-rule-row="${p.id}">${ruleOrderControl(p.id,p.name,all.indexOf(p),canOrder)}<div class="np-rule-info"><strong>${esc(p.name)}</strong><small>${esc(p.summary)}${p.mixed_enabled?' · 部分来源已停用':''}</small>${p.unavailable_rules.length?'<small class="error-message">部分规则来源已停用或缺失，请检查来源。</small>':''}</div><button class="rg-outlet-summary" data-np-edit="${p.id}" aria-label="设置${esc(p.name)}的节点出口"><span>节点出口</span><strong>${esc(p.outlet_summary)}</strong></button><div class="np-rule-actions"><button class="switch" data-np-toggle="${p.id}" role="switch" aria-label="启用${esc(p.name)}" aria-checked="${p.enabled}"></button><button class="button" data-np-edit="${p.id}" aria-label="配置${esc(p.name)}规则">配置</button>${resourceMenu(p.name,`<button data-np-copy="${p.id}">复制规则</button><button data-rule-position="${p.id}" ${canOrder?'':'disabled'}>调整位置</button><button class="danger-action" data-np-delete="${p.id}">删除规则</button>`)}</div></article>`).join('')||'<div class="empty">没有符合条件的规则，可调整筛选或添加规则。</div>'}${fallback?`<article class="panel np-setting-row rule-with-order np-fallback" data-rule-row="${fallback.id}" data-rule-fallback><span class="np-fixed" aria-label="兜底固定最后">${icon('lock')}</span><div class="np-rule-info"><strong>兜底规则</strong><small>未命中前面规则时使用；固定在最后</small></div><button class="rg-outlet-summary" data-np-edit="${fallback.id}" aria-label="设置兜底出口"><span>节点出口</span><strong>${esc(fallback.outlet_summary)}</strong></button><button class="button" data-np-edit="${fallback.id}">配置兜底</button></article>`:''}</div>`;
  bindNativeSearch(nativeRuleSettings);bindNativeLibrarySubTabs();$$('[data-np-category]').forEach(b=>b.onclick=()=>{nativeRuleCategory=b.dataset.npCategory;nativeRuleSettings()});
  bindNativePolicyActions();
  bindRuleOrder({ids:()=>nativeUnitRows().map(p=>p.id),save:next=>nativePolicyOrder(next),enabled:canOrder});
  $$('[data-rule-position]').forEach(button=>button.onclick=()=>showRulePosition(button.dataset.rulePosition));
};
function showNativeOutletList(key,title){
  const names=nativePolicyView()[key]||[];showDialog(title,`<p>这些出口保留原有引用，可供多条规则使用。</p><div class="native-resource-list">${names.map(name=>`<div class="native-resource-row"><strong>${esc(name)}</strong><button class="button" data-rg-outlet="${esc(name)}">编辑</button></div>`).join('')}</div>`);$$('[data-rg-outlet]').forEach(b=>b.onclick=()=>nativeGroupEditor(b.dataset.rgOutlet,key==='shared_outlets'?'shared':'rules'));
}
bindNativePolicyActions=function(){
  $$('[data-np-toggle]').forEach(b=>b.onclick=()=>nativePolicyMutation({action:'unit-toggle',id:b.dataset.npToggle,enabled:b.getAttribute('aria-checked')!=='true'},'规则组启用状态已保存。').catch(error=>toast(error.message)));
  $$('[data-np-edit]').forEach(b=>b.onclick=()=>nativePolicyEditor(b.dataset.npEdit));
  $$('[data-np-copy]').forEach(b=>b.onclick=()=>nativePolicyMutation({action:'unit-copy',id:b.dataset.npCopy},'已复制规则组及其独立出口设置。').catch(error=>toast(error.message)));
  $$('[data-np-delete]').forEach(b=>b.onclick=()=>{confirmRemoval('删除规则组','删除这组分流条件；规则来源和节点保留。',()=>{});$('#remove-confirm').onclick=async e=>{e.target.disabled=true;try{await nativePolicyMutation({action:'unit-delete',id:b.dataset.npDelete},'规则组已删除。');$('#dialog').close()}catch(error){$('#remove-error').textContent=error.message;e.target.disabled=false}}});bindResourceMenus();
};
nativeRenderPolicies=function(){
  const rows=nativeUnitRows();
  $('#content').innerHTML=`<div class="np-order-heading"><p>按规则组从上到下匹配，拖动调整优先级。</p><button class="button" id="np-edit-rules">配置规则组</button></div><div class="np-order-list">${rows.map((p,i)=>`<article class="panel np-order-row ${p.fallback?'np-fallback':''} ${!p.enabled?'np-disabled':''}" data-np-row="${p.id}">${p.fallback?`<span class="np-fixed" aria-label="兜底固定最后">${icon('lock')}</span>`:`<button class="drag-handle np-handle" draggable="true" data-np-handle="${p.id}" aria-label="移动${esc(p.name)}，也可按上下方向键">${actionIcon('grip')}</button>`}<span class="np-order-number">${i+1}</span><div class="np-rule-info"><strong>${esc(p.name)}</strong><small>${esc(p.summary)}${!p.enabled?' · 已停用':''}</small></div><span class="np-outlet">${esc(p.outlet_summary)}</span>${p.fallback?'<span class="np-fixed-label">固定最后</span>':`<div class="np-order-buttons"><button class="icon-button" data-np-step="${p.id}" data-step="-1" aria-label="上移${esc(p.name)}" ${i===0?'disabled':''}>${icon('arrow')}</button><button class="icon-button" data-np-step="${p.id}" data-step="1" aria-label="下移${esc(p.name)}" ${i>=rows.length-2?'disabled':''}>${icon('arrow')}</button></div>`}</article>`).join('')}</div><p class="np-order-feedback" id="np-order-feedback" role="status">${rows.length-1} 条规则组 · 兜底固定最后</p>`;
  $('#np-edit-rules').onclick=()=>location.hash='library';$$('[data-np-step]').forEach(b=>b.onclick=()=>nativeMovePolicy(b.dataset.npStep,Number(b.dataset.step)));bindNativePolicyDrag();
};
nativePolicyOrder=async function(ids){
  const before=nativeValue().policies.map(p=>p.id),unitIds=nativeUnitRows().map(p=>p.id),scope=spaceId;if(ids.every((id,i)=>id===unitIds[i]))return;
  try{const revision=await nativePolicyMutation({action:'unit-reorder',ids},'规则组顺序已保存。');const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent='撤销';$('#toast').append(button);clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,10000);button.onclick=()=>{if(spaceId!==scope||liveWorkspace.revision!==revision){toast('配置已有后续修改，请手动调整顺序。');return}nativePolicyMutation({action:'reorder',ids:before},'已撤销规则组顺序调整。').catch(error=>toast(error.message))}}catch(error){toast(error.message)}
};

function paintRuleOutlet(e){
  const mode=$('#rg-outlet-mode').value,direct=mode==='direct',preserve=mode==='preserve';
  $('#rg-fixed-outlet').hidden=!direct;$('#rg-members-section').hidden=direct||preserve;$('#rg-auto-fields').hidden=direct||preserve||mode==='select';$('#rg-default-field').hidden=mode!=='select';
  if(direct||preserve)return;
  const choices=nativePolicyView().outlet_choices||[],known=new Set(choices.map(c=>c.value)),extra=e.outletMembers.filter(m=>!known.has(m)).map(value=>({value,label:value}));
  const all=[...choices,...extra],selected=new Set(e.outletMembers);
  const ordered=all.filter(c=>selected.has(c.value)).concat(all.filter(c=>!selected.has(c.value)));
  const memberRow=c=>`<label class="check-row ${selected.has(c.value)?'member-chosen':''}" data-rg-member-label="${esc(c.label)}"><input type="checkbox" data-rg-member="${esc(c.value)}" ${selected.has(c.value)?'checked':''}>${esc(e.conditionList?c.value:c.label)}</label>`;
  if(e.conditionList){
    const names=liveWorkspace.state.group_category_names||{lines:'线路组',home:'回家组',shared:'共用出口组'},key=c=>c.category|| (c.resource==='builtin'?'builtin':c.resource==='node'?'manual':'existing');
    $('#rg-member-options').innerHTML=Object.entries({...names,manual:'手动节点',builtin:'内置出口',existing:'已有其他引用'}).map(([category,label])=>{const rows=ordered.filter(c=>key(c)===category);return rows.length?`<section class="rc-outlet-category"><h4>${esc(label)}</h4><div>${rows.map(memberRow).join('')}</div></section>`:''}).join('');
  }else $('#rg-member-options').innerHTML=ordered.map(memberRow).join('');
  let summary=$('#rg-selection-summary');if(!summary){summary=document.createElement('p');summary.id='rg-selection-summary';summary.className='member-selection-summary';summary.setAttribute('role','status');$('#rg-member-options').before(summary)}
  const labels=value=>e.conditionList?value:all.find(c=>c.value===value)?.label||value;
  const field=$('#rg-default-field');field.childNodes[0].textContent=e.conditionList?'默认出口':'启动默认出口';
  const fillDefault=()=>{
    if(!e.outletMembers.includes(e.outletDefault))e.outletDefault=e.outletMembers[0]||'';
    $('#rg-default').innerHTML=(e.outletMembers.length?e.outletMembers:['']).map(m=>`<option value="${esc(m)}" ${m===e.outletDefault?'selected':''}>${esc(m?labels(m):'请先勾选候选出口')}</option>`).join('');
    $('#rg-default').disabled=e.outletMembers.length<2;
    summary.textContent=e.conditionList?`候选出口 · 已选 ${e.outletMembers.length} 个`:e.outletMembers.length?`已选 ${e.outletMembers.length} 个候选出口${e.outletMembers.length===1?'：'+labels(e.outletMembers[0]):''}`:'尚未选择候选出口，请至少勾选一项。';
    $('small',field).textContent=e.outletMembers.length===1?'只有一个候选，默认使用此出口。':e.outletMembers.length?'仅从已选候选中选择；客户端保存的选择可能优先。':'先选择候选出口。';
  };fillDefault();
  $$('[data-rg-member]').forEach(input=>input.onchange=()=>{if(input.checked)e.outletMembers.push(input.dataset.rgMember);else e.outletMembers=e.outletMembers.filter(m=>m!==input.dataset.rgMember);if(!e.outletMembers.includes(e.outletDefault))e.outletDefault=e.outletMembers[0]||'';fillDefault();nativePolicyPreview(e)});
  $('#rg-default').onchange=ev=>{e.outletDefault=ev.target.value;nativePolicyPreview(e)};
  $('#rg-member-search').oninput=ev=>{const term=ev.target.value.toLowerCase();$$('[data-rg-member-label]').forEach(label=>label.hidden=!label.dataset.rgMemberLabel.toLowerCase().includes(term));$$('.rc-outlet-category').forEach(section=>section.hidden=!$$('[data-rg-member-label]',section).some(label=>!label.hidden))};
}
nativePolicyEditor=function(ident=null,done=null){
  const row=ident?nativePolicyRow(ident):null,spec=row?.editor||{mode:'rules',rules:[],operator:'or',no_resolve:false,target:'DIRECT'},outlet=row?.outlet,e={ident,mode:spec.mode,targetDirty:false,enabledDirty:false,previewSerial:0,timer:null,saving:false,spec:clone(spec),rules:new Set(spec.rules||[]),sources:ident?[]:[{url:'',name:''}],boundSources:clone(row?.source_settings||[]),outletMembers:clone(outlet?.members||[]),outletDefault:outlet?.default||'',row};nativePolicyForm=e;
  const outletMode=outlet?(modes[outlet.type]?outlet.type:'preserve'):'direct';
  showDialog(ident?'配置规则组':'添加规则组',`<form id="np-rule-form"><label class="field">规则组名称<input id="np-name" maxlength="80" value="${esc(row?.name||'')}" placeholder="例如：人工智能 / Netflix"></label>${row?.fallback?'<p class="np-guide">兜底接收未命中前面规则组的流量。</p>':`<section class="rg-editor-section"><h3>匹配规则</h3><div class="np-input-tabs" aria-label="匹配内容">${[['rules','规则订阅'],['matcher','规则匹配器'],['advanced','高级条件']].map(([key,label])=>`<button type="button" class="button ${e.mode===key?'active':''}" data-np-mode="${key}" aria-pressed="${e.mode===key}">${label}</button>`).join('')}</div><section id="np-match-input"></section>${row?.policy_ids.length>1?'<p class="preview-note">域名和 IP 来源已合在此条配置，各自的原生解析参数继续保留。</p>':''}</section>`}<section class="rg-editor-section"><h3>节点出口</h3><label class="field">出口方式<select id="rg-outlet-mode"><option value="direct">固定出口</option>${Object.entries(modes).map(([key,label])=>`<option value="${key}" ${key===outletMode?'selected':''}>${label}</option>`).join('')}${outletMode==='preserve'?`<option value="preserve" selected>保留原生 ${esc(outlet.type)}</option>`:''}</select></label><label class="field" id="rg-fixed-outlet">出口节点组<select id="np-target">${nativePolicyTargets(spec.target)}</select></label><section id="rg-members-section" hidden><label class="search">${icon('search')}<input id="rg-member-search" aria-label="搜索出口节点组" placeholder="搜索香港、日本或其他节点组"></label><div class="rg-member-options" id="rg-member-options"></div><label class="field" id="rg-default-field">默认选择<select id="rg-default"></select><small>内核运行时仍可切换；设备已保存的选择可能优先。</small></label><div class="fields" id="rg-auto-fields" hidden><label class="field">测速间隔（秒）<input id="rg-interval" type="number" min="1" max="86400" value="${outlet?.interval??300}"></label><label class="field">延迟容差（毫秒）<input id="rg-tolerance" type="number" min="0" max="86400" value="${outlet?.tolerance??50}"></label><label class="field">测速地址<input id="rg-test-url" type="url" value="${esc(outlet?.test_url||'https://www.gstatic.com/generate_204')}"></label></div></section>${outlet?.shared_units>1?'<p class="preview-note">此出口还被其他规则组引用，修改出口成员会同步影响这些引用。</p>':''}</section><details class="advanced-options np-extra"><summary>更多选项</summary>${row?.fallback?'':`<label class="check-row"><input id="np-enabled" type="checkbox" ${row?.enabled!==false?'checked':''}>启用规则组</label><label class="field">多条匹配内容<select id="np-operator"><option value="or" ${spec.operator!=='and'?'selected':''}>任意命中一条（OR）</option><option value="and" ${spec.operator==='and'?'selected':''}>同时命中（AND）</option></select></label>${row?.policy_ids.length>1?'':`<label class="check-row"><input id="np-no-resolve" type="checkbox" ${spec.no_resolve?'checked':''}>IP 匹配不额外解析域名</label>`}` }<div class="np-native-preview"><span>生成的原生条件</span><code id="np-native-text">等待配置</code></div></details><p id="np-rule-error" class="error-message" role="alert"></p><div class="np-editor-footer"><button type="button" class="button" id="np-rule-cancel">取消</button><button type="submit" class="button primary" id="np-rule-save">保存规则组</button></div></form>`);
  const dialog=$('#dialog');dialog.classList.add('np-rule-dialog');const prevent=ev=>{if(e.saving)ev.preventDefault()};dialog.addEventListener('cancel',prevent);dialog.addEventListener('close',()=>{dialog.classList.remove('np-rule-dialog');dialog.removeEventListener('cancel',prevent);clearTimeout(e.timer);if(nativePolicyForm===e)nativePolicyForm=null},{once:true});setNativeEditorReturn(done);$('#np-rule-cancel').textContent=done?'返回节点组':'取消';$('#np-rule-cancel').onclick=()=>dialog.close();
  if(!row?.fallback)nativePolicyMatchPanel(e);$$('[data-np-mode]').forEach(b=>b.onclick=()=>{nativePolicyReadMatch(e);e.mode=b.dataset.npMode;$$('[data-np-mode]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',x===b)});nativePolicyMatchPanel(e);nativePolicyPreview(e)});
  $('#rg-outlet-mode').onchange=()=>{if($('#rg-outlet-mode').value==='direct'&&outlet?.default)$('#np-target').value=outlet.default;paintRuleOutlet(e);nativePolicyPreview(e)};paintRuleOutlet(e);
  $('#np-target').onchange=()=>{e.targetDirty=true;nativePolicyPreview(e)};$('#np-name').oninput=()=>nativePolicyPreview(e);if($('#np-enabled'))$('#np-enabled').onchange=()=>{e.enabledDirty=true;nativePolicyPreview(e)};for(const id of ['np-operator','np-no-resolve','rg-interval','rg-tolerance','rg-test-url'])if($('#'+id))$('#'+id).onchange=()=>nativePolicyPreview(e);
  $('#np-rule-form').onsubmit=async ev=>{ev.preventDefault();if(e.saving)return;e.saving=true;e.previewSerial++;clearTimeout(e.timer);$('#np-rule-save').textContent=e.sources.some(s=>s.url.trim())?'正在读取订阅…':'正在保存…';$$('button,input,select,textarea',ev.target).forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;try{await nativePolicyMutation({action:'unit-save',id:ident,...nativePolicyFormValue(e)},'规则来源与节点出口已一起保存。');dialog.close()}catch(error){$('#np-rule-error').textContent=error.message}finally{e.saving=false;if($('#np-rule-save'))$('#np-rule-save').textContent='保存规则组';if($('#np-rule-form'))$$('button,input,select,textarea',$('#np-rule-form')).forEach(el=>el.disabled=false);$('#close-dialog').disabled=false}};nativePolicyPreview(e);
};
const readNativeMatches=nativePolicyReadMatch;
nativePolicyReadMatch=function(e){
  readNativeMatches(e);
  $$('[data-rg-new-format]').forEach(input=>e.sources[Number(input.dataset.rgNewFormat)].source_format=input.value);
  $$('[data-rg-source-url]').forEach(input=>e.boundSources[Number(input.dataset.rgSourceUrl)].url=input.value);
  $$('[data-rg-source-body]').forEach(input=>e.boundSources[Number(input.dataset.rgSourceBody)].body=input.value);
};
const paintNativeMatches=nativePolicyMatchPanel;
nativePolicyMatchPanel=function(e){
  if(e.mode!=='rules'){
    paintNativeMatches(e);
    if(e.mode==='matcher'){
      const type=$('#np-matcher-type'),values=$('#np-values'),hint=values.parentElement.querySelector('small');
      const explain=()=>{const port=['DST-PORT','SRC-PORT'].includes(type.value);values.placeholder=port?'每行一个端口，例如 19302、5349、3478；支持 1000-2000 范围':'每行一个匹配值，例如 example.com 或 192.168.0.0/16';hint.textContent=port?'可填单个端口、范围，或用 / 分隔；出口选择“拦截”即可阻止匹配流量。':'支持多行；默认任意命中一行即可。'};
      type.onchange=()=>{explain();nativePolicyPreview(e)};explain();
    }
    return;
  }
  const attached=e.boundSources.map((source,i)=>{
    if(!e.rules.has(source.ref))return '';
    const field=source.kind==='subscription'?`<label class="field">订阅地址<input data-rg-source-url="${i}" type="url" aria-label="${esc(source.ref)} 订阅地址" value="${esc(source.url)}" required></label>`:source.editable_text?`<label class="field">规则内容（YAML）<textarea data-rg-source-body="${i}" rows="5" spellcheck="false" aria-label="${esc(source.ref)} 规则内容">${esc(source.body)}</textarea></label>`:`<p class="preview-note">${source.kind==='missing'?'此来源缺失，请移除后填写正确的订阅地址。':'二进制本地规则 · 保留原格式，可在原生文件入口替换。'}</p>`;
    return `<section class="np-subscription-row"><div class="rg-source-fields">${field}<small class="preview-note">${esc(source.ref)}${source.kind==='local'&&source.path?' · '+esc(source.path):''}</small>${source.source_format==='clash-rules'?'<small class="preview-note">Clash 完整规则列表 · 保留全部匹配条件，导出本地快照；在 YoMesh 刷新后重新导出。</small>':''}</div><button class="icon-button" type="button" data-rg-source-remove="${i}" aria-label="移除${esc(source.ref)}">${actionIcon('trash')}</button></section>`;
  }).join('');
  const added=e.sources.map((source,i)=>`<section class="np-subscription-row"><div class="rg-source-fields"><label class="field">订阅地址<input data-np-source-url="${i}" type="url" placeholder="https://…/rules.yaml" value="${esc(source.url)}"></label><details class="advanced-options"><summary>来源格式</summary><label class="field">规则文件格式<select data-rg-new-format="${i}"><option value="payload" ${source.source_format!=='clash-rules'?'selected':''}>Mihomo 规则集（payload）</option><option value="clash-rules" ${source.source_format==='clash-rules'?'selected':''}>Clash 完整规则列表（rules）</option></select><small>完整列表需统一来源出口；匹配条件保留，出口由当前组指定，导出为本地快照。</small></label></details></div><button class="icon-button" type="button" data-np-source-remove="${i}" aria-label="移除新订阅地址${i+1}">${actionIcon('trash')}</button></section>`).join('');
  $('#np-match-input').innerHTML=`<div class="np-subscription-rows">${attached}${added}</div><div class="np-rule-source-tools"><button class="text-button" id="np-add-subscription" type="button">${icon('plus')}添加订阅地址</button></div>`;
  $$('[data-rg-source-url],[data-rg-source-body],[data-np-source-url]').forEach(input=>input.oninput=()=>nativePolicyPreview(e));
  $$('[data-rg-new-format]').forEach(input=>input.onchange=()=>nativePolicyPreview(e));
  $$('[data-rg-source-remove]').forEach(button=>button.onclick=()=>{nativePolicyReadMatch(e);e.rules.delete(e.boundSources[Number(button.dataset.rgSourceRemove)].ref);nativePolicyMatchPanel(e);nativePolicyPreview(e)});
  $$('[data-np-source-remove]').forEach(button=>button.onclick=()=>{nativePolicyReadMatch(e);e.sources.splice(Number(button.dataset.npSourceRemove),1);nativePolicyMatchPanel(e);nativePolicyPreview(e)});
  $('#np-add-subscription').onclick=()=>{nativePolicyReadMatch(e);if(e.sources.length>=16){$('#np-rule-error').textContent='一次最多添加 16 个订阅。';return}e.sources.push({url:'',name:''});nativePolicyMatchPanel(e);$$('[data-np-source-url]').at(-1).focus()};
};
const readNativeRuleForm=nativePolicyFormValue;
nativePolicyFormValue=function(e){
  const result=readNativeRuleForm(e),type=$('#rg-outlet-mode')?.value||'direct';
  result.unit=true;result.enabled_dirty=e.enabledDirty;
  result.sources=e.mode==='rules'?e.sources.filter(s=>s.url.trim()).map(s=>({name:s.name.trim(),url:s.url.trim(),source_format:s.source_format||'payload'})):[];
  result.source_edits=e.mode==='rules'?e.boundSources.filter(source=>{const original=e.row.source_settings.find(s=>s.ref===source.ref);return e.rules.has(source.ref)&&(source.kind==='subscription'?source.url!==original.url:source.editable_text&&source.body!==original.body)}).map(source=>source.kind==='subscription'?{ref:source.ref,url:source.url.trim()}:{ref:source.ref,body:source.body}):[];
  result.outlet=type==='preserve'?null:type==='direct'?{type}:{type,members:clone(e.outletMembers),default:e.outletDefault,interval:Number($('#rg-interval').value),tolerance:Number($('#rg-tolerance').value),test_url:$('#rg-test-url').value};return result;
};
nativePolicyPreview=function(e){
  clearTimeout(e.timer);const serial=++e.previewSerial;e.timer=setTimeout(async()=>{if(nativePolicyForm!==e||e.saving)return;try{const result=await api('/api/workspace/native-policy-preview',{id:e.ident,...nativePolicyFormValue(e),revision:liveWorkspace.revision});if(nativePolicyForm!==e||serial!==e.previewSerial||e.saving)return;$('#np-native-text').textContent=result.text;$('#np-rule-error').textContent=''}catch(error){if(nativePolicyForm===e&&serial===e.previewSerial&&!e.saving){$('#np-native-text').textContent='完成配置后显示预览';if(e.mode!=='rules'||e.rules.size||e.sources.some(s=>s.url.trim()))$('#np-rule-error').textContent=error.message}}},300);
};

// Portable spaces already bind a policy directly to a region group. Present that
// binding on the Rules page; source snapshots remain an explicit secondary view.
let portableRuleSources=false;
const paintPortableRuleSources=renderLibrary;
function portableLibrarySubTabs(){
  return `<div class="group-category-toolbar"><div class="group-category-tabs" role="tablist" aria-label="分流规则与来源切换"><button type="button" class="button ${!portableRuleSources?'active':''}" role="tab" aria-selected="${!portableRuleSources}" data-portable-subtab="rules">分流规则<span>${data.policies?.length||0}</span></button><button type="button" class="button ${portableRuleSources?'active':''}" role="tab" aria-selected="${portableRuleSources}" data-portable-subtab="sources">规则来源库<span>${data.library?.length||0}</span></button></div></div>`;
}
function bindPortableLibrarySubTabs(){
  $$('[data-portable-subtab]').forEach(b=>b.onclick=()=>{const next=b.dataset.portableSubtab==='sources';if(portableRuleSources===next)return;portableRuleSources=next;query='';render()});
}
renderLibrary=function(){
  if(portableRuleSources){ruleOrderCleanup();paintPortableRuleSources();$('#content').insertAdjacentHTML('afterbegin',portableLibrarySubTabs());bindPortableLibrarySubTabs();return}
  const views=usageView().plans.find(v=>v.id===data.active_plan)?.policies||[],canOrder=!query;
  const rows=data.policies.filter(p=>(p.name+' '+(views.find(v=>v.id===p.id)?.summary||'')).toLowerCase().includes(query.toLowerCase()));
  $('#content').innerHTML=`${portableLibrarySubTabs()}<div class="toolbar"><label class="search">${icon('search')}<input id="rg-search" aria-label="搜索规则" placeholder="搜索规则" value="${esc(query)}"></label></div><p class="np-guide" role="status">${canOrder?'匹配内容和出口在同一条设置；拖动手柄排序，方向键也可调整。':'搜索中暂停排序；清空搜索后可以调整完整顺序。'}${policyUndo&&policyUndo.plan===data.active_plan?'<button class="text-button" id="rg-undo-order">撤销上次排序</button>':''}</p><div class="np-settings-list">${rows.map(p=>`<article class="panel np-setting-row rule-with-order" data-rule-row="${p.id}">${ruleOrderControl(p.id,p.name,data.policies.indexOf(p),canOrder)}<div class="np-rule-info"><strong>${esc(p.name)}</strong><small>${esc(views.find(v=>v.id===p.id)?.summary||'')}</small>${p.enabled===false?'<small>共享模板已停用，可在配置中启用。</small>':''}</div><label class="field np-target">节点出口<select data-rg-portable-target="${p.id}">${targetOptions(p.target)}</select></label><div class="np-rule-actions"><button class="switch" role="switch" data-rg-portable-toggle="${p.id}" aria-label="选用${esc(p.name)}" aria-checked="${selectedForGeneration('policies',p.id)&&p.enabled!==false}" ${p.enabled===false?'disabled':''}></button><button class="button" data-rg-portable-edit="${p.id}">配置</button><button class="text-button" data-rule-position="${p.id}" ${canOrder?'':'disabled'}>移动</button></div></article>`).join('')||'<div class="empty">没有符合条件的规则，可调整搜索或添加规则。</div>'}<article class="panel np-setting-row rule-with-order np-fallback" data-rule-row="__fallback__" data-rule-fallback><span class="np-fixed" aria-label="兜底固定最后">${icon('lock')}</span><div class="np-rule-info"><strong>兜底规则</strong><small>未命中前面规则时使用；固定在最后</small></div><label class="field np-target">兜底出口<select id="rg-portable-fallback">${targetOptions(data.fallback)}</select></label><span class="np-fixed-label">始终生效</span></article></div>`;
  $('#rg-search').oninput=ev=>{query=ev.target.value;const pos=ev.target.selectionStart;renderLibrary();$('#rg-search').focus();$('#rg-search').setSelectionRange(pos,pos)};bindPortableLibrarySubTabs();bindResourceMenus();
  $$('[data-rg-portable-edit]').forEach(b=>b.onclick=()=>editPolicy(b.dataset.rgPortableEdit));$$('[data-rg-portable-toggle]').forEach(b=>b.onclick=()=>toggleGeneration('policies',b.dataset.rgPortableToggle,b.getAttribute('aria-checked')!=='true').catch(error=>toast(error.message)));$$('[data-rg-portable-target]').forEach(el=>el.onchange=()=>saveChange(()=>find('policies',el.dataset.rgPortableTarget).target=el.value).catch(error=>toast(error.message)));
  $('#rg-portable-fallback').onchange=event=>saveChange(()=>data.fallback=event.target.value).catch(error=>toast(error.message));
  bindRuleOrder({ids:()=>[...data.policies.map(p=>p.id),'__fallback__'],save:(next,moved)=>savePolicyOrder(next.filter(id=>id!=='__fallback__'),moved),enabled:canOrder});
  if($('#rg-undo-order'))$('#rg-undo-order').onclick=()=>{const undo=policyUndo;savePolicyOrder(undo.ids,null,undo.revision).catch(error=>toast(error.message))};
  $$('[data-rule-position]').forEach(button=>button.onclick=()=>showRulePosition(button.dataset.rulePosition));
};
const renderUnifiedRules=render;
render=function(){if(page!=='library')portableRuleSources=false;renderUnifiedRules();if(page==='library'&&!nativeMode()){$('#create').innerHTML=icon('plus')+(portableRuleSources?'添加规则来源':'添加规则');$('#create').onclick=()=>portableRuleSources?addLibrary():editPolicy()}};
