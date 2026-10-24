'use strict';

// The server supplies editable leaves. This editor never parses native expressions.
const previousRuleFormValue=nativePolicyFormValue,previousRulePreview=nativePolicyPreview;
const ruleConditionTypes=()=>[{value:'RULE-SET',label:'规则订阅 / 本地规则集'},...(nativePolicyView().matchers||[])];
const emptyRuleCondition=type=>type==='RULE-SET'?{type,value:'',no_resolve:false,source:{url:'',source_format:'payload'}}:{type,value:type==='NETWORK'?'tcp':'',no_resolve:false};

function updateRuleConditionRelation(e){
  const list=e.mode==='conditions',multiple=e.conditions.length>1;
  $('#rg-condition-relation').hidden=!list||!multiple;
  $('#rg-condition-count').textContent=list?`${e.conditions.length} 个条件`:'完整原文';
  $('#rg-add-condition-tools').hidden=!list;
  $$('[data-rg-connector]').forEach(el=>el.textContent=e.operator==='and'?'并且':'或者');
  $$('[data-rg-editor-mode]').forEach(button=>{const selected=button.dataset.rgEditorMode===e.mode;button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected))});
}

function paintRuleConditionList(e){
  const panel=$('#np-match-input');
  if(e.mode==='advanced'){
    panel.innerHTML=`<label class="field">完整匹配表达式<textarea id="np-expression" class="mono" rows="5" spellcheck="false">${esc(e.text||'')}</textarea><small>嵌套逻辑和其他原生条件完整保留；更改出口不会重写条件。</small></label>`;
    $('#np-expression').oninput=event=>{e.text=event.target.value;nativePolicyPreview(e)};
  }else{
    panel.innerHTML=e.conditions.map((condition,index)=>{
      const source=condition.type==='RULE-SET',attached=source&&condition.value?e.boundSources.find(s=>s.ref===condition.value):null;
      let input='';
      const sourceEdit=attached&&(e.sourceEditing.has(index)||attached.kind==='missing'),sourceDetail=attached&&(e.sourceDetails.has(index)||attached.kind==='missing');
      if(source&&attached){
        input=attached.kind==='subscription'?`<label class="field">上游源地址<input type="url" data-rc-url="${index}" value="${esc(attached.url)}" ${attached.readonly?'readonly':''} required></label><small class="preview-note">${esc(attached.ref)} · YoMesh 从此地址更新。客户端使用的地址由“订阅链接 → 规则地址”设置决定。</small>`:attached.editable_text?`<label class="field">规则内容（YAML）<textarea data-rc-body="${index}" rows="4" spellcheck="false" ${attached.readonly?'readonly':''}>${esc(attached.body)}</textarea></label><small class="preview-note">本地规则集 · ${esc(attached.ref)}</small>`:`<p class="preview-note">${esc(attached.ref)} · ${attached.kind==='missing'?'来源缺失，请选择已有来源或填写新地址。':'保留原有原生来源，可在来源管理入口编辑。'}</p>`;
        input=`<div class="rc-source-content" data-rc-source-detail="${index}" ${sourceDetail?'':'hidden'}>${input}`;
        if(attached.readonly)input+='<p class="preview-note">正在复用已存来源；需要其他地址时，在上方选择“填写新地址”。</p>';
        if(attached.source_format==='clash-rules')input+='<p class="preview-note">Clash 完整规则列表 · 导出本地快照；在 YoMesh 刷新后重新导出。</p>';
        input+=`<details class="advanced-options rc-resolve"><summary>解析选项${condition.no_resolve?' · no-resolve':''}</summary><label class="check-row"><input type="checkbox" data-rc-no-resolve="${index}" ${condition.no_resolve?'checked':''}>不为此条件额外解析域名（no-resolve）</label><p class="preview-note">请求已有目标 IP 时仍可匹配；不会关闭客户端的 DNS。</p></details></div><label class="field rc-source-change" ${sourceEdit?'':'hidden'}>规则来源<select data-rc-existing="${index}"><option value="">填写新地址</option>${attached.kind==='missing'?`<option value="${esc(attached.ref)}" selected disabled>缺失 · ${esc(attached.ref)}</option>`:''}${Object.keys(nativeMap('rule-providers')).map(name=>`<option value="${esc(name)}" ${name===attached.ref?'selected':''}>${esc(name)}</option>`).join('')}</select></label>`;
      }else if(source){
        input=`<label class="field">使用已有规则来源<select data-rc-existing="${index}"><option value="">填写新地址</option>${Object.keys(nativeMap('rule-providers')).map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('')}</select></label><label class="field">上游源地址<input type="url" data-rc-new-url="${index}" value="${esc(condition.source?.url||'')}" placeholder="https://…/rules.yaml" required><small>填写原始规则地址；在“订阅链接”中可选择转换为本站 IP 或域名。</small></label><details class="advanced-options rc-source-format"><summary>来源格式</summary><label class="field">规则文件格式<select data-rc-format="${index}"><option value="payload" ${condition.source?.source_format!=='clash-rules'?'selected':''}>Mihomo 规则集（payload）</option><option value="clash-rules" ${condition.source?.source_format==='clash-rules'?'selected':''}>Clash 完整规则列表（rules）</option></select><small>完整列表需统一来源出口；由当前规则组指定实际出口。</small></label></details>`;
      }else if(condition.type==='NETWORK'){
        input=`<label class="field">网络协议<select data-rc-value="${index}"><option value="tcp" ${condition.value==='tcp'?'selected':''}>TCP</option><option value="udp" ${condition.value==='udp'?'selected':''}>UDP</option></select></label>`;
      }else{
        const port=['DST-PORT','SRC-PORT'].includes(condition.type),ip=condition.type.includes('CIDR');
        input=`<label class="field">匹配内容<textarea data-rc-value="${index}" rows="3" placeholder="${port?'例如 443 或 1000-2000':ip?'例如 '+(condition.type==='IP-CIDR6'?'2001:db8::/32':'192.168.0.0/16'):'每行一条，例如 example.com'}" required>${esc(condition.value)}</textarea><small>每行一条，命中其中任意一条即可；附加条件之间仍按上方 AND / OR 组合。</small>${port?'<small>支持单个端口、范围或 / 分隔；其他类型的内容可继续添加条件。</small>':''}</label>`;
      }
      const resolvable=!attached&&(source||condition.type==='IP-CIDR'||condition.type==='IP-CIDR6'||condition.no_resolve);
      const typeSelector=`<select data-rc-type="${index}" aria-label="条件 ${index+1} 类型">${ruleConditionTypes().map(option=>`<option value="${esc(option.value)}" ${option.value===condition.type?'selected':''}>${esc(option.value==='RULE-SET'&&attached?.kind==='local'?'本地规则集':option.label)}</option>`).join('')}</select>`;
      const heading=attached?`<div class="rc-source-heading"><span>${attached.kind==='local'?'本地规则集':'规则订阅'}</span><strong>${esc(attached.ref)}</strong>${condition.no_resolve?'<small>no-resolve</small>':''}<button type="button" class="text-button" data-rc-change="${index}" aria-expanded="${!!sourceEdit}">${sourceEdit?'取消更换':'更换'}</button><button type="button" class="text-button" data-rc-detail="${index}" aria-expanded="${!!sourceDetail}">${sourceDetail?'收起详情':'详情'}</button>${e.conditions.length>1?`<button type="button" class="text-button danger-action" data-rc-remove="${index}" aria-label="移除条件 ${index+1}">移除</button>`:''}</div><div class="rc-condition-top" ${sourceEdit?'':'hidden'}><span class="rc-number">${index+1}</span>${typeSelector}</div>`:`<div class="rc-condition-top"><span class="rc-number">${index+1}</span>${typeSelector}<button type="button" class="button rc-remove" data-rc-remove="${index}" aria-label="移除条件 ${index+1}" ${e.conditions.length===1?'disabled':''}>移除</button></div>`;
      return `<section class="rc-condition">${heading}${input}${resolvable?`<details class="advanced-options rc-resolve" ${condition.no_resolve?'open':''}><summary>解析选项${condition.no_resolve?' · no-resolve':''}</summary><label class="check-row"><input type="checkbox" data-rc-no-resolve="${index}" ${condition.no_resolve?'checked':''}>不为此条件额外解析域名（no-resolve）</label><p class="preview-note">请求已有目标 IP 时仍可匹配；不会关闭客户端的 DNS。</p></details>`:''}${index<e.conditions.length-1?'<div class="rc-connector" data-rg-connector></div>':''}</section>`;
    }).join('');
    $$('[data-rc-existing]').forEach(input=>input.onchange=()=>{const previousFlag=e.conditions[Number(input.dataset.rcExisting)].no_resolve;if(!input.value){e.conditions[Number(input.dataset.rcExisting)]={...emptyRuleCondition('RULE-SET'),no_resolve:previousFlag};paintRuleConditionList(e);nativePolicyPreview(e);return}const name=input.value,p=nativeMap('rule-providers')[name],body=nativeValue().documents?.[(p.path||'').replace(/^\.\//,'')];e.conditions[Number(input.dataset.rcExisting)]={type:'RULE-SET',value:name,no_resolve:previousFlag};if(!e.boundSources.some(s=>s.ref===name))e.boundSources.push({ref:name,readonly:true,kind:p.type==='http'?'subscription':'local',url:p.url||'',path:p.path||'',body,editable_text:typeof body==='string'&&(p.format||'yaml')==='yaml'});paintRuleConditionList(e);nativePolicyPreview(e)});
    $$('[data-rc-type]').forEach(input=>input.onchange=()=>{e.conditions[Number(input.dataset.rcType)]=emptyRuleCondition(input.value);paintRuleConditionList(e);nativePolicyPreview(e)});
    $$('[data-rc-change]').forEach(button=>button.onclick=()=>{const index=Number(button.dataset.rcChange);e.sourceEditing.has(index)?e.sourceEditing.delete(index):e.sourceEditing.add(index);const section=button.closest('.rc-condition');$$('.rc-condition-top,.rc-source-change',section).forEach(el=>el.hidden=!e.sourceEditing.has(index));button.textContent=e.sourceEditing.has(index)?'取消更换':'更换';button.setAttribute('aria-expanded',String(e.sourceEditing.has(index)))});
    $$('[data-rc-detail]').forEach(button=>button.onclick=()=>{const index=Number(button.dataset.rcDetail),panel=$(`[data-rc-source-detail="${index}"]`);if(!panel.hidden){const invalid=$$('input,textarea',panel).find(el=>!el.checkValidity());if(invalid){invalid.reportValidity();return}}e.sourceDetails.has(index)?e.sourceDetails.delete(index):e.sourceDetails.add(index);panel.hidden=!e.sourceDetails.has(index);button.textContent=panel.hidden?'详情':'收起详情';button.setAttribute('aria-expanded',String(!panel.hidden))});
    $$('[data-rc-remove]').forEach(button=>button.onclick=()=>{if(e.conditions.length===1)return;const index=Number(button.dataset.rcRemove);e.conditions.splice(index,1);e.sourceEditing=new Set([...e.sourceEditing].filter(i=>i!==index).map(i=>i>index?i-1:i));e.sourceDetails=new Set([...e.sourceDetails].filter(i=>i!==index).map(i=>i>index?i-1:i));paintRuleConditionList(e);$$('[data-rc-type]').find(el=>!el.closest('[hidden]'))?.focus();nativePolicyPreview(e)});
    $$('[data-rc-value]').forEach(input=>input.oninput=()=>{e.conditions[Number(input.dataset.rcValue)].value=input.value;nativePolicyPreview(e)});
    $$('[data-rc-new-url]').forEach(input=>input.oninput=()=>{e.conditions[Number(input.dataset.rcNewUrl)].source.url=input.value;nativePolicyPreview(e)});
    $$('[data-rc-format]').forEach(input=>input.onchange=()=>{e.conditions[Number(input.dataset.rcFormat)].source.source_format=input.value;nativePolicyPreview(e)});
    $$('[data-rc-url],[data-rc-body]').forEach(input=>input.oninput=()=>{const index=Number(input.dataset.rcUrl??input.dataset.rcBody),ref=e.conditions[index].value,source=e.boundSources.find(s=>s.ref===ref);source[input.dataset.rcUrl!==undefined?'url':'body']=input.value;nativePolicyPreview(e)});
    $$('[data-rc-no-resolve]').forEach(input=>input.onchange=()=>{e.conditions[Number(input.dataset.rcNoResolve)].no_resolve=input.checked;nativePolicyPreview(e)});
  }
  updateRuleConditionRelation(e);
}

function paintRuleCandidateSummary(e){
  $('#rg-picked-members').textContent=e.outletMembers.slice(0,3).join('、')+(e.outletMembers.length>3?`，以及其他 ${e.outletMembers.length-3} 个`:'')||'请至少选择一个候选出口。';
  $('#rg-open-selection-count').textContent=`候选出口 · 已选 ${e.outletMembers.length} 个`;
}

nativePolicyFormValue=function(e){
  if(!e.conditionList)return previousRuleFormValue(e);
  const draft={mode:e.mode,name:$('#np-name').value,target:$('#np-target').value,enabled:e.row?.enabled!==false};
  if(e.mode==='conditions')Object.assign(draft,{operator:e.operator,conditions:clone(e.conditions)});
  if(e.mode==='advanced')Object.assign(draft,{text:e.text,set_target:true});
  const active=new Set(e.mode==='conditions'?e.conditions.filter(c=>c.type==='RULE-SET').map(c=>c.value):[]);
  const source_edits=e.boundSources.filter(source=>{const original=e.row?.source_settings.find(s=>s.ref===source.ref);return original&&active.has(source.ref)&&(source.kind==='subscription'?source.url!==original.url:source.editable_text&&source.body!==original.body)}).map(source=>source.kind==='subscription'?{ref:source.ref,url:source.url.trim()}:{ref:source.ref,body:source.body});
  const type=$('#rg-outlet-mode').value;
  return {unit:true,draft,enabled_dirty:false,sources:[],source_edits,outlet:type==='preserve'?null:type==='direct'?{type}:{type,members:clone(e.outletMembers),default:e.outletDefault,interval:Number($('#rg-interval').value),tolerance:Number($('#rg-tolerance').value),test_url:$('#rg-test-url').value}};
};

nativePolicyPreview=function(e){
  if(!e.conditionList)return previousRulePreview(e);
  clearTimeout(e.timer);const serial=++e.previewSerial;
  e.timer=setTimeout(async()=>{if(nativePolicyForm!==e||e.saving)return;try{
    const result=await api('/api/workspace/native-policy-preview',{id:e.ident,...nativePolicyFormValue(e),revision:liveWorkspace.revision});
    if(nativePolicyForm!==e||serial!==e.previewSerial||e.saving)return;
    $('#np-native-text').textContent=result.text;$('#np-rule-error').textContent='';$('#rg-preview-status').textContent=result.effect||'条件预览已更新';$('#rg-preview-status').title=result.effect||result.text;
  }catch(error){if(nativePolicyForm!==e||serial!==e.previewSerial||e.saving)return;$('#np-native-text').textContent='完成配置后显示预览';$('#np-rule-error').textContent=error.message;$('#rg-preview-status').textContent='请补充或检查条件';}},300);
};

nativePolicyEditor=function(ident=null,done=null){
  const row=ident?nativePolicyRow(ident):null,spec=row?.condition_editor||{mode:'conditions',operator:'or',conditions:[emptyRuleCondition('RULE-SET')]};
  const outlet=row?.outlet,outletMode=outlet?(modes[outlet.type]?outlet.type:'preserve'):'direct';
  const e={conditionList:true,ident,row,mode:row?.fallback?'fallback':spec.mode,operator:spec.operator||'or',conditions:clone(spec.conditions||[]),text:spec.text||'',boundSources:clone(row?.source_settings||[]),outletMembers:clone(row?.outlet?.members||[]),outletDefault:row?.outlet?.default||'',lastMultiMode:outletMode!=='direct'?outletMode:'select',sourceEditing:new Set(),sourceDetails:new Set(),previewSerial:0,timer:null,saving:false};nativePolicyForm=e;
  const multiModeLabels={select:'在客户端手动切换 (select)','url-test':'自动测速选最快 (url-test)',fallback:'按顺序故障转移 (fallback)','load-balance':'负载均衡 (load-balance)'};
  const extraMatchers=ruleConditionTypes().filter(o=>!['RULE-SET','DOMAIN-SUFFIX'].includes(o.value));
  const presetBar=row?.fallback?'':`<div class="preset-pill-box"><div class="preset-pill-title">📦 一键填入常用规则集（点击自动填好名称、官方规则集 URL 与推荐出口）</div><div class="preset-pill-list">${(typeof spacePresetsCache==='object'&&spacePresetsCache?.rules||typeof DEFAULT_PRESET_RULES!=='undefined'&&DEFAULT_PRESET_RULES||[]).map(r=>`<button type="button" class="preset-pill" data-rc-preset="${esc(r.key)}">${esc(r.pill||r.name)}</button>`).join('')}</div></div>`;
  showDialog(ident?'配置规则':'添加规则',`<form id="np-rule-form"><div class="rc-editor-scroll">${presetBar}<p class="rc-editor-state">${row?.fallback?'兜底始终启用':row?.mixed_enabled?'部分来源已停用':row?row.enabled?'当前方案已启用':'当前方案已停用':'新建后在当前方案启用'}<span>启停在规则列表管理</span></p><label class="field">规则名称<input id="np-name" maxlength="80" value="${esc(row?.name||'')}" placeholder="例如：人工智能 / Netflix" required></label>${row?.fallback?'<p class="np-guide">兜底接收未命中前面规则的流量，只需选择节点出口。</p>':`<section class="rg-editor-section"><div class="rc-section-heading"><h3>1. 匹配什么流量</h3><span id="rg-condition-count" class="preview-note"></span></div><details class="rc-mode-options" id="rg-editor-mode-details"><summary>高级编辑方式</summary><div class="rc-editor-modes" aria-label="条件编辑方式"><button type="button" class="text-button" data-rg-editor-mode="conditions" aria-pressed="false">条件清单</button>${row?.policy_ids.length>1?'': '<button type="button" class="text-button" data-rg-editor-mode="advanced" aria-pressed="false">完整原文</button>'}</div></details><fieldset id="rg-condition-relation" class="rc-relation"><legend class="sr-only">条件之间的关系</legend><label class="check-row"><input type="radio" name="rc-operator" value="or" ${e.operator!=='and'?'checked':''}>满足任意条件（OR）</label><label class="check-row"><input type="radio" name="rc-operator" value="and" ${e.operator==='and'?'checked':''}>满足全部条件（AND）</label></fieldset><section id="np-match-input"></section><div id="rg-add-condition-tools" class="rc-add-bar"><button type="button" class="button" data-rg-add-type="RULE-SET">${icon('plus')}添加规则订阅 / 规则集</button><button type="button" class="button" data-rg-add-type="DOMAIN-SUFFIX">${icon('plus')}添加域名 / IP 条件</button><details class="rc-add-menu" id="rg-add-more-menu"><summary>更多条件类型</summary><div>${extraMatchers.map(option=>`<button type="button" class="button" data-rg-add-type="${esc(option.value)}">${esc(option.label)}</button>`).join('')}</div></details></div>${row?.policy_ids.length>1?'<p class="preview-note">各来源的解析参数和启用状态分别保留；改变组合关系前请确认匹配范围。</p>':''}</section>`}<section class="rg-editor-section"><h3>${row?.fallback?'兜底流量出口':'2. 走哪个出口'}</h3><div class="rc-outlet-cards" role="group" aria-label="出口模式"><button type="button" class="rc-outlet-card" data-rc-outlet-cat="single" aria-pressed="${outletMode==='direct'}"><strong>直接走单个出口</strong><small>直接指向某一个现成节点组、直连或拦截</small></button><button type="button" class="rc-outlet-card" data-rc-outlet-cat="multi" aria-pressed="${outletMode!=='direct'}"><strong>组合多个候选出口</strong><small>挑选多个候选线路，支持手动切换或自动测速</small></button></div><label class="field" id="rg-fixed-outlet">目标出口<select id="np-target">${nativePolicyTargets(row?.target||'DIRECT')}</select><small>所有命中此规则的流量直接走所选出口，无需配置额外候选列表。</small></label><div class="fields rc-outlet-fields" ${outletMode==='direct'?'hidden':''}><label class="field">多候选选路方式<select id="rg-outlet-mode"><option value="direct" ${outletMode==='direct'?'selected':''} hidden>直接走单个出口</option>${Object.entries(modes).map(([key,label])=>`<option value="${key}" ${key===outletMode?'selected':''}>${esc(multiModeLabels[key]||label)}</option>`).join('')}${outletMode==='preserve'?`<option value="preserve" selected>保留原生 ${esc(outlet.type)}</option>`:''}</select></label><label class="field" id="rg-default-field">默认出口<select id="rg-default"></select><small>仅从已选候选中选择。</small></label></div><section id="rg-members-section" hidden><details class="rc-candidate-picker" id="rg-candidate-picker" ${e.outletMembers.length?'':'open'}><summary><span class="rc-candidate-collapsed"><strong id="rg-selection-summary" class="member-selection-summary"></strong><small id="rg-picked-members"></small></span><span id="rg-picker-action">调整候选</span></summary><p id="rg-open-selection-count" role="status"></p><label class="search">${icon('search')}<input id="rg-member-search" aria-label="搜索候选出口" placeholder="搜索节点组或节点"></label><div class="rg-member-options" id="rg-member-options"></div></details><details class="rc-auto-options" id="rg-auto-options" hidden><summary>测速参数</summary><div class="fields" id="rg-auto-fields" hidden><label class="field">测速间隔（秒）<input id="rg-interval" type="number" min="1" max="86400" value="${outlet?.interval??300}"></label><label class="field">延迟容差（毫秒）<input id="rg-tolerance" type="number" min="0" max="86400" value="${outlet?.tolerance??50}"></label><label class="field">测速地址<input id="rg-test-url" type="url" value="${esc(outlet?.test_url||'https://www.gstatic.com/generate_204')}"></label></div></details></section>${outlet?.shared_units>1?'<p class="preview-note">此出口还被其他规则组引用，修改出口成员会同步影响这些引用。</p>':''}</section><details class="advanced-options rc-native-preview"><summary>原生条件预览</summary><p class="preview-note">预览由当前配置统一解析；下载前仍需目标内核校验。</p><div class="np-native-preview"><code id="np-native-text">等待配置</code></div></details></div><p id="np-rule-error" class="error-message" role="alert"></p><div class="np-editor-footer"><span id="rg-preview-status" role="status">正在准备条件预览</span><button type="button" class="button" id="np-rule-cancel">${done?'返回节点组':'取消'}</button><button type="submit" class="button primary" id="np-rule-save">保存规则</button></div></form>`);
  const dialog=$('#dialog');dialog.classList.add('np-rule-dialog','rc-editor-dialog');
  const state=$('.rc-editor-state'),nameField=$('#np-name').closest('label'),nameRow=document.createElement('div');nameRow.className='rc-name-row';nameField.before(nameRow);nameRow.append(nameField,state);
  const prevent=event=>{if(e.saving)event.preventDefault()};dialog.addEventListener('cancel',prevent);
  dialog.addEventListener('close',()=>{dialog.classList.remove('np-rule-dialog','rc-editor-dialog');dialog.removeEventListener('cancel',prevent);clearTimeout(e.timer);if(nativePolicyForm===e)nativePolicyForm=null},{once:true});setNativeEditorReturn(done);
  $('#np-rule-cancel').onclick=()=>dialog.close();
  const usableGroupOutlets=(nativePolicyView().outlet_choices||[]).filter(choice=>choice.available&&choice.resource!=='builtin');
  const outletCards=$('.rc-outlet-cards');
  if(outletCards&&!usableGroupOutlets.length)outletCards.insertAdjacentHTML('afterend',`<div class="notice rc-outlet-hint">还没有可用的节点组出口（当前只有 DIRECT / REJECT）。先到「节点分组」建一个地区组，再回来选出口。<button type="button" class="button" id="rg-outlet-hint-groups">去建组</button></div>`);
  if($('#rg-outlet-hint-groups'))$('#rg-outlet-hint-groups').onclick=()=>{dialog.close();location.hash='groups'};
  $$('[data-rc-preset]').forEach(btn=>btn.onclick=()=>{
    const list=typeof spacePresetsCache==='object'&&spacePresetsCache?.rules||typeof DEFAULT_PRESET_RULES!=='undefined'&&DEFAULT_PRESET_RULES||[];
    const preset=list.find(r=>r.key===btn.dataset.rcPreset);
    if(!preset)return;
    $('#np-name').value=preset.name;
    e.mode='conditions';e.operator='or';e.sourceEditing.clear();e.sourceDetails.clear();
    e.conditions=[{type:'RULE-SET',value:'',no_resolve:false,source:{url:preset.domain_url,source_format:'payload'}},...(preset.ip_url?[{type:'RULE-SET',value:'',no_resolve:true,source:{url:preset.ip_url,source_format:'payload'}}]:[])];
    const targetSel=$('#np-target');
    if(targetSel){
      const opts=$$('option:not([disabled])',targetSel);
      const regionRx={direct:/^DIRECT$/i,reject:/^REJECT$/i,us:/(美|US|United|AI)/i,hk:/(港|HK|Hong)/i,sg:/(新|SG|Singapore|狮)/i,jp:/(日|JP|Japan)/i,tw:/(台|TW|Taiwan)/i,proxy:/(默认|Proxy|代理|节点)/i}[preset.default_region];
      const matched=opts.find(o=>regionRx&&regionRx.test(o.value+' '+o.textContent))||opts.find(o=>!['DIRECT','REJECT'].includes(o.value));
      if(matched)targetSel.value=matched.value;
    }
    paintRuleConditionList(e);nativePolicyPreview(e);
    toast(`已一键填入「${preset.name}」规则集 URL 与推荐出口！`);
  });
  if(!row?.fallback){
    paintRuleConditionList(e);
    $$('input[name="rc-operator"]').forEach(input=>input.onchange=()=>{e.operator=input.value;updateRuleConditionRelation(e);nativePolicyPreview(e)});
    $$('[data-rg-add-type]').forEach(button=>button.onclick=()=>{if(e.conditions.length>=128){$('#np-rule-error').textContent='最多添加 128 个条件。';return}e.conditions.push(emptyRuleCondition(button.dataset.rgAddType));if($('#rg-add-more-menu'))$('#rg-add-more-menu').open=false;paintRuleConditionList(e);$$('[data-rc-type]').at(-1)?.focus();nativePolicyPreview(e)});
    $$('[data-rg-editor-mode]').forEach(button=>button.onclick=async()=>{
      if(e.saving||button.dataset.rgEditorMode===e.mode)return;clearTimeout(e.timer);const serial=++e.previewSerial;button.disabled=true;
      try{const value=nativePolicyFormValue(e);if(button.dataset.rgEditorMode==='advanced'&&(e.conditions.some(c=>c.source)||value.source_edits.length))throw Error('新增或修改的订阅请先保存规则组，再编辑完整原文。');const result=await api('/api/workspace/native-policy-preview',{id:e.ident,...value,revision:liveWorkspace.revision});if(nativePolicyForm!==e||e.saving||serial!==e.previewSerial)return;const next=result.policy.condition_editor;
        if(button.dataset.rgEditorMode==='conditions'&&next?.mode!=='conditions')throw Error('包含嵌套逻辑或暂不支持的条件，请继续编辑完整原文。');
        e.mode=button.dataset.rgEditorMode;if(e.mode==='advanced')e.text=result.text;else{e.conditions=clone(next.conditions);e.boundSources=clone(result.policy.source_settings||[]);e.operator=next.operator;$$('input[name="rc-operator"]').forEach(input=>input.checked=input.value===e.operator)}paintRuleConditionList(e);nativePolicyPreview(e);
      }catch(error){if(nativePolicyForm===e)$('#np-rule-error').textContent=error.message}finally{button.disabled=false}
    });
  }
  const members=$('#rg-members-section'),auto=$('#rg-auto-options');members.after(auto);const modeOptions=$('#rg-editor-mode-details');if(modeOptions){$('#rg-add-condition-tools').after(modeOptions);modeOptions.open=e.mode==='advanced';if(row?.policy_ids.length>1)modeOptions.hidden=true}const picker=$('#rg-candidate-picker');const updatePicker=()=>{$('#rg-picker-action').textContent=picker.open?'收起候选':'调整候选'};picker.ontoggle=updatePicker;updatePicker();
  const paintOutlet=()=>{const direct=$('#rg-outlet-mode').value==='direct';if(!direct)e.lastMultiMode=$('#rg-outlet-mode').value;$$('[data-rc-outlet-cat]').forEach(b=>b.setAttribute('aria-pressed',String((b.dataset.rcOutletCat==='single')===direct)));$('.rc-outlet-fields').hidden=direct;paintRuleOutlet(e);paintRuleCandidateSummary(e);$('#rg-auto-options').hidden=$('#rg-auto-fields').hidden};
  $$('[data-rc-outlet-cat]').forEach(btn=>btn.onclick=()=>{
    const wantSingle=btn.dataset.rcOutletCat==='single';
    if(wantSingle){$('#rg-outlet-mode').value='direct';if(outlet?.default)$('#np-target').value=outlet.default}
    else{$('#rg-outlet-mode').value=e.lastMultiMode||'select';if(!e.outletMembers.length&&$('#np-target').value){e.outletMembers=[$('#np-target').value];e.outletDefault=$('#np-target').value}$('#rg-candidate-picker').open=true}
    paintOutlet();nativePolicyPreview(e);
  });
  $('#rg-outlet-mode').onchange=()=>{if($('#rg-outlet-mode').value==='direct'&&outlet?.default)$('#np-target').value=outlet.default;paintOutlet();nativePolicyPreview(e)};
  $('#rg-member-options').addEventListener('change',()=>paintRuleCandidateSummary(e));paintOutlet();
  $('#np-target').onchange=()=>nativePolicyPreview(e);$('#np-name').oninput=()=>nativePolicyPreview(e);
  for(const id of ['rg-interval','rg-tolerance','rg-test-url'])$('#'+id).onchange=()=>nativePolicyPreview(e);
  $('#np-rule-form').onsubmit=async event=>{
    event.preventDefault();if(e.saving)return;$('#np-rule-error').textContent='';const value=nativePolicyFormValue(e);e.saving=true;e.previewSerial++;clearTimeout(e.timer);
    const controls=$$('button,input,select,textarea',event.target).map(el=>[el,el.disabled]);controls.forEach(([el])=>el.disabled=true);$('#close-dialog').disabled=true;
    $('#np-rule-save').textContent=e.conditions.some(c=>c.source?.url)?'正在读取订阅…':'正在保存…';
    try{await nativePolicyMutation({action:'unit-save',id:ident,...value},'规则条件与节点出口已一起保存。');dialog.close()}
    catch(error){$('#np-rule-error').textContent=error.message}
    finally{e.saving=false;controls.forEach(([el,disabled])=>el.disabled=disabled);$('#close-dialog').disabled=false;if(nativePolicyForm===e)$('#np-rule-save').textContent='保存规则'}
  };
  nativePolicyPreview(e);
};
