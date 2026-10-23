'use strict';
// Each condition explicitly chooses a subscription, a stored ruleset or a matcher.
const subscriptionForms = new WeakMap();
let ruleSubscriptionPending = false;

function updateRuleAction() {
  $('#compound-target-field').hidden = ['DIRECT','REJECT'].includes(ruleDraft.target);
}

editPolicy = function(ident, seedLibrary=null) {
  const original=ident?find('policies',ident):null, plan=data.active_plan;
  const initial=seedLibrary?{type:'RULE-SET',value:seedLibrary}:defaultCondition('ruleset');
  ruleDraft=original?.type==='LOGICAL'?clone(original):{id:original?.id||id('p'),name:original?.name||'',type:'LOGICAL',group_operator:'or',condition_groups:[{operator:'or',conditions:original?[original.type==='RULE-SET'?{type:'RULE-SET',value:original.value}:{type:original.type,values:[original.value]}]:[initial]}],target:original?.target||'DIRECT',enabled:original?.enabled!==false,dns:original?.dns||'auto'};
  const presetBar=original?'':`<div class="preset-pill-box"><div class="preset-pill-title">📦 一键填入常用规则集（点击自动填好名称、官方规则集 URL 与推荐出口）</div><div class="preset-pill-list">${(typeof spacePresetsCache==='object'&&spacePresetsCache?.rules||typeof DEFAULT_PRESET_RULES!=='undefined'&&DEFAULT_PRESET_RULES||[]).map(r=>`<button type="button" class="preset-pill" data-cp-preset="${esc(r.key)}">${esc(r.pill||r.name)}</button>`).join('')}</div></div>`;
  showDialog(original?'编辑规则':'新建规则',`<form id="compound-rule-form"><div class="rule-composer"><div class="rule-composer-fields">${presetBar}<section class="rule-section"><h3>基本信息</h3><label class="field">规则名称<input id="compound-name" required maxlength="100" value="${esc(ruleDraft.name)}" placeholder="例如：家庭服务 / 海外影音"></label></section><section class="rule-section"><div class="section-title"><h3>条件组</h3><button class="button" type="button" id="add-condition-group">${icon('plus')}添加条件组</button></div><p class="preview-note">每个条件选择已存规则或直接填写匹配器；组内、组间独立选择 AND / OR。</p><div id="condition-groups"></div><label class="field group-operator-field" id="between-groups">条件组之间<select id="group-operator"><option value="or" ${ruleDraft.group_operator==='or'?'selected':''}>或（OR）· 任一组成立</option><option value="and" ${ruleDraft.group_operator==='and'?'selected':''}>且（AND）· 所有组成立</option></select></label></section><section class="rule-section"><h3>动作与 DNS</h3><label class="field">出站动作<select id="rule-action"><option value="direct" ${ruleDraft.target==='DIRECT'?'selected':''}>直连</option><option value="proxy" ${!['DIRECT','REJECT'].includes(ruleDraft.target)?'selected':''}>代理</option><option value="reject" ${ruleDraft.target==='REJECT'?'selected':''}>拒绝</option></select></label><label class="field" id="compound-target-field">节点 / 节点组<select id="compound-target">${targetOptions(ruleDraft.target).replace(/<option value="(?:DIRECT|REJECT)"[^>]*>.*?<\/option>/g,'')}</select></label><label class="field">DNS 上游<select id="compound-dns"><option value="auto">自动跟随动作</option><option value="local">本地上游</option><option value="remote">远程上游</option></select><small>远程 DNS 经统一代理入口；拒绝返回 NXDOMAIN。</small></label><label class="check-row"><input id="compound-enabled" type="checkbox" ${ruleDraft.enabled?'checked':''}>启用规则</label></section></div><aside class="rule-summary" aria-label="规则摘要"><h3>规则摘要</h3><p class="summary-plan">${esc(planLabel(plans().find(p=>p.id===plan)||{name:'当前工作区'}))}</p><p id="condition-totals"></p><span class="tiny-label">逻辑预览</span><p id="rule-logic-preview" class="logic-preview">请选择匹配内容</p><span class="tiny-label">流量出口</span><p id="rule-target-preview"></p><p class="preview-note" id="rule-dns-preview"></p><p class="error-message" id="compound-error" role="alert"></p><div class="composer-actions"><button class="button" type="button" id="compound-cancel">取消</button><button class="button primary" type="submit" id="compound-save" disabled>保存规则</button></div></aside></div></form>`);
  const dialog=$('#dialog');dialog.classList.add('rule-composer-dialog');
  const preventPendingClose=e=>{if(ruleSubscriptionPending)e.preventDefault()};dialog.addEventListener('cancel',preventPendingClose);
  dialog.addEventListener('close',()=>{dialog.classList.remove('rule-composer-dialog');dialog.removeEventListener('cancel',preventPendingClose);ruleDraft=null;clearTimeout(rulePreviewTimer);rulePreviewSerial++},{once:true});
  $$('[data-cp-preset]').forEach(btn=>btn.onclick=()=>{
    const list=typeof spacePresetsCache==='object'&&spacePresetsCache?.rules||typeof DEFAULT_PRESET_RULES!=='undefined'&&DEFAULT_PRESET_RULES||[];
    const preset=list.find(r=>r.key===btn.dataset.cpPreset);
    if(!preset)return;
    ruleDraft.name=preset.name;$('#compound-name').value=preset.name;
    const existingLib=(data.library||[]).find(l=>l.source?.url===preset.domain_url||l.name.toLowerCase().includes(preset.key));
    const cond=ruleDraft.condition_groups[0].conditions[0];
    if(existingLib){
      cond.type='RULE-SET';cond.value=existingLib.id;subscriptionForms.delete(cond);
    }else{
      cond.type='RULE-SET';cond.value='';
      subscriptionForms.set(cond,{name:preset.name,url:preset.domain_url,format:'auto',auto_update:true});
    }
    if(preset.default_region==='direct'){$('#rule-action').value='direct';ruleDraft.target='DIRECT'}
    else if(preset.default_region==='reject'){$('#rule-action').value='reject';ruleDraft.target='REJECT'}
    else{
      $('#rule-action').value='proxy';
      const opts=$$('#compound-target option');
      const regionRx={us:/(美|US|United|AI)/i,hk:/(港|HK|Hong)/i,sg:/(新|SG|Singapore|狮)/i,jp:/(日|JP|Japan)/i,tw:/(台|TW|Taiwan)/i,proxy:/(默认|Proxy|代理)/i}[preset.default_region];
      const matched=opts.find(o=>regionRx&&regionRx.test(o.textContent))||opts[0];
      if(matched){$('#compound-target').value=matched.value;ruleDraft.target=matched.value}
    }
    updateRuleAction();paintConditionGroups();previewCompoundRule();
    toast(existingLib?`已填入「${preset.name}」并匹配已存规则集！`:`已填入「${preset.name}」，请点下方“保存订阅并选用”自动拉取！`);
  });
  $('#compound-name').oninput=e=>{ruleDraft.name=e.target.value;previewCompoundRule()};
  $('#group-operator').onchange=e=>{ruleDraft.group_operator=e.target.value;previewCompoundRule()};
  $('#compound-dns').value=ruleDraft.dns||'auto';$('#compound-dns').onchange=e=>{ruleDraft.dns=e.target.value;previewCompoundRule()};
  $('#compound-enabled').onchange=e=>ruleDraft.enabled=e.target.checked;
  $('#compound-target').onchange=e=>{ruleDraft.target=e.target.value;previewCompoundRule()};
  $('#rule-action').onchange=()=>{const action=$('#rule-action').value;ruleDraft.target=action==='direct'?'DIRECT':action==='reject'?'REJECT':$('#compound-target').value;updateRuleAction();previewCompoundRule()};
  $('#add-condition-group').onclick=()=>{ruleDraft.condition_groups.push({operator:'or',conditions:[defaultCondition('ruleset')]});paintConditionGroups();previewCompoundRule()};
  $('#compound-cancel').onclick=()=>dialog.close();
  $('#compound-rule-form').onsubmit=async e=>{
    e.preventDefault();if(ruleSubscriptionPending)return;
    if(data.active_plan!==plan){$('#compound-error').textContent='当前方案已变化，请关闭表单后在目标方案重新编辑。';return}
    const value=clone(ruleDraft);$('#compound-save').disabled=true;
    try{await saveChange(()=>{if(original)data.policies=data.policies.map(p=>p.id===value.id?value:p);else data.policies.push(value)});dialog.close()}
    catch(error){$('#compound-error').textContent=error.message;previewCompoundRule()}
  };
  updateRuleAction();paintConditionGroups();previewCompoundRule();
};

function paintConditionGroups() {
  const total=ruleDraft.condition_groups.reduce((n,g)=>n+g.conditions.length,0);
  $('#between-groups').hidden=ruleDraft.condition_groups.length<2;$('#add-condition-group').disabled=ruleDraft.condition_groups.length>=16||total>=64;
  $('#condition-groups').innerHTML=ruleDraft.condition_groups.map((group,gi)=>`<section class="condition-group" data-condition-group="${gi}"><div class="condition-group-heading"><strong>条件组 ${gi+1}</strong><div class="condition-logic" role="group" aria-label="条件组 ${gi+1} 关系">${[['or','或（OR）'],['and','且（AND）']].map(([value,label])=>`<button class="button ${group.operator===value?'active':''}" type="button" data-condition-logic="${gi}" data-value="${value}" aria-pressed="${group.operator===value}">${label}</button>`).join('')}</div><button class="icon-button danger-action" type="button" data-remove-condition-group="${gi}" aria-label="删除条件组 ${gi+1}" ${ruleDraft.condition_groups.length===1?'disabled':''}>${actionIcon('trash')}</button></div>${group.conditions.map((condition,ci)=>{
    const source=conditionSource(condition),key=`${gi}:${ci}`,selected=find('library',condition.value),draft=subscriptionForms.get(condition);
    return `<div class="rule-condition" data-condition="${key}"><div class="condition-type-line"><select data-condition-source="${key}" aria-label="条件 ${gi+1}-${ci+1} 来源">${[['ruleset','已存规则'],['matcher','匹配器']].map(([value,label])=>`<option value="${value}" ${source===value?'selected':''}>${label}</option>`).join('')}</select>${source==='matcher'?`<select data-condition-type="${key}" aria-label="条件 ${gi+1}-${ci+1} 匹配类型">${Object.entries(matchLabels).map(([value,label])=>`<option value="${value}" ${condition.type===value?'selected':''}>${label}</option>`).join('')}</select>`:''}<button class="icon-button danger-action" type="button" data-remove-condition="${key}" aria-label="删除条件 ${gi+1}-${ci+1}" ${group.conditions.length===1?'disabled':''}>${actionIcon('trash')}</button></div>${source==='matcher'?`<label class="field condition-values">匹配内容<textarea data-condition-values="${key}" rows="5" spellcheck="false" placeholder="每行一个值">${esc((condition.values||[condition.value]).filter(Boolean).join('\n'))}</textarea><small>每行一个值，命中任意一个即可；本条件与其他条件按组内关系组合。</small></label>`:`<div class="condition-library-line"><div class="condition-picker" data-condition-picker="${key}"><input role="combobox" aria-label="条件 ${gi+1}-${ci+1} ${source==='subscription'?'规则订阅':'已存规则'}" aria-expanded="false" aria-autocomplete="list" aria-controls="rule-options-${gi}-${ci}" autocomplete="off" placeholder="${source==='subscription'?'搜索或选择已保存的规则':'搜索或选择已有规则'}" value="${esc(selected?.name||'')}"><div class="condition-picker-popup" hidden><div role="listbox" id="rule-options-${gi}-${ci}" aria-label="${source==='subscription'?'规则订阅':'已存规则'}候选"></div></div></div>${source!=='matcher'?`<button class="button" type="button" data-new-subscription="${key}">${icon('plus')}新建订阅</button>`:''}</div><p class="preview-note">${selected?`${selected.entries.length.toLocaleString()} 条 · ${source==='subscription'?'订阅更新后使用新内容，失败保留上一版。':'引用已存规则内容。'}`:source==='subscription'?'选择规则订阅，或直接新建一个订阅地址。':'请选择具体规则集，不会自动加入全部已存规则。'}</p>${draft?`<div class="inline-subscription"><h4>新建规则订阅</h4><div class="fields"><label class="field">订阅名称<input data-sub-field="${key}" data-field="name" value="${esc(draft.name)}" maxlength="100" placeholder="例如：流媒体规则"></label><label class="field">规则格式<select data-sub-field="${key}" data-field="format">${Object.entries(ruleFormats).map(([value,label])=>`<option value="${value}" ${draft.format===value?'selected':''}>${label}</option>`).join('')}</select></label></div><label class="field">订阅地址<input data-sub-field="${key}" data-field="url" type="url" value="${esc(draft.url)}" placeholder="https://…/rules.mrs"></label><label class="check-row"><input type="checkbox" data-sub-field="${key}" data-field="auto_update" ${draft.auto_update?'checked':''}>每天自动更新</label><p class="preview-note">订阅保存到规则并选入当前条件；当前策略仍需点“保存规则”。</p><p class="error-message" role="alert">${esc(draft.error||'')}</p><div class="filter-row"><button class="button" type="button" data-cancel-subscription="${key}">取消新增</button><button class="button primary" type="button" data-save-subscription="${key}">保存订阅并选用</button></div></div>`:''}`}</div>`;
  }).join('')}<button class="text-button add-condition" type="button" data-add-condition="${gi}" ${total>=64?'disabled':''}>${icon('plus')}添加条件</button></section>`).join('');
  const at=key=>{const [g,c]=key.split(':').map(Number);return ruleDraft.condition_groups[g].conditions[c]};
  $$('[data-condition-logic]').forEach(el=>el.onclick=()=>{ruleDraft.condition_groups[Number(el.dataset.conditionLogic)].operator=el.dataset.value;paintConditionGroups();previewCompoundRule()});
  $$('[data-condition-source]').forEach(el=>el.onchange=()=>{const [g,c]=el.dataset.conditionSource.split(':').map(Number);ruleDraft.condition_groups[g].conditions[c]=defaultCondition(el.value);paintConditionGroups();previewCompoundRule()});
  $$('[data-condition-type]').forEach(el=>el.onchange=()=>{const c=at(el.dataset.conditionType);c.type=el.value;paintConditionGroups();previewCompoundRule()});
  $$('[data-condition-values]').forEach(el=>el.oninput=()=>{at(el.dataset.conditionValues).values=el.value.split('\n').map(v=>v.trim()).filter(Boolean);previewCompoundRule()});
  $$('[data-add-condition]').forEach(el=>el.onclick=()=>{ruleDraft.condition_groups[Number(el.dataset.addCondition)].conditions.push(defaultCondition('ruleset'));paintConditionGroups();previewCompoundRule()});
  $$('[data-remove-condition]').forEach(el=>el.onclick=()=>{const [g,c]=el.dataset.removeCondition.split(':').map(Number);ruleDraft.condition_groups[g].conditions.splice(c,1);paintConditionGroups();previewCompoundRule()});
  $$('[data-remove-condition-group]').forEach(el=>el.onclick=()=>{ruleDraft.condition_groups.splice(Number(el.dataset.removeConditionGroup),1);paintConditionGroups();previewCompoundRule()});
  $$('[data-new-subscription]').forEach(el=>el.onclick=()=>{const c=at(el.dataset.newSubscription);subscriptionForms.set(c,{name:'',url:'',format:'auto',auto_update:true});paintConditionGroups()});
  $$('[data-cancel-subscription]').forEach(el=>el.onclick=()=>{subscriptionForms.delete(at(el.dataset.cancelSubscription));paintConditionGroups()});
  $$('[data-sub-field]').forEach(el=>{const update=()=>{subscriptionForms.get(at(el.dataset.subField))[el.dataset.field]=el.type==='checkbox'?el.checked:el.value};el.oninput=update;el.onchange=update});
  $$('[data-save-subscription]').forEach(el=>el.onclick=()=>saveConditionSubscription(at(el.dataset.saveSubscription)));
  $$('[data-condition-picker]').forEach(root=>bindConditionPicker(root,at(root.dataset.conditionPicker)));
};

function bindConditionPicker(root,condition) {
  const input=$('input',root),popup=$('.condition-picker-popup',root),list=$('[role=listbox]',root),source=conditionSource(condition);
  let candidates=[],cursor=-1;
  const close=()=>{popup.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');input.value=find('library',condition.value)?.name||''};
  const choose=ident=>{condition.value=ident;close();const key=root.dataset.conditionPicker;paintConditionGroups();$(`[data-condition-picker="${key}"] input`).focus();previewCompoundRule()};
  const paint=search=>{
    candidates=data.library.filter(l=>`${l.name} ${l.source?.url||''}`.toLowerCase().includes(search.toLowerCase())).slice(0,100);cursor=-1;
    list.innerHTML=candidates.map((l,i)=>`<button type="button" role="option" id="${list.id}-${i}" data-rule-option="${esc(l.id)}" aria-selected="${condition.value===l.id}"><span>${esc(l.name)}</span><small>${l.entries.length.toLocaleString()} 条 · ${esc(ruleKind(l.kind))}</small></button>`).join('')||'<p>没有匹配项。'+(source==='subscription'?'可点击旁边“新建订阅”。':'请先在规则添加已存规则。')+'</p>';
    $$('[data-rule-option]',list).forEach(el=>el.onclick=()=>choose(el.dataset.ruleOption));popup.hidden=false;input.setAttribute('aria-expanded','true');
  };
  input.onclick=()=>{if(popup.hidden)paint('');else close()};input.oninput=()=>paint(input.value);
  input.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close()}else if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if(popup.hidden)paint('');cursor=Math.max(0,Math.min(candidates.length-1,cursor+(e.key==='ArrowDown'?1:-1)));const options=$$('[role=option]',list);options.forEach((el,i)=>el.classList.toggle('option-focus',i===cursor));if(options[cursor]){input.setAttribute('aria-activedescendant',options[cursor].id);list.scrollTop=options[cursor].offsetTop-list.offsetTop}}else if(e.key==='Enter'){e.preventDefault();if(!popup.hidden&&candidates[cursor])choose(candidates[cursor].id)}else if(e.key==='Tab')close()};
  input.onblur=()=>setTimeout(()=>{if(!root.contains(document.activeElement))close()},0);
}

async function saveConditionSubscription(condition) {
  const values=subscriptionForms.get(condition);values.error='';
  if(!values.name.trim()||!values.url.trim()){values.error='请输入订阅名称和地址。';paintConditionGroups();return}
  const item={id:id('l'),name:values.name.trim(),source:{type:'url',format:values.format,url:values.url.trim(),auto_update:values.auto_update,interval_minutes:1440}};
  ruleSubscriptionPending=true;
  clearTimeout(rulePreviewTimer);rulePreviewSerial++;
  const busy=on=>{$$('#compound-rule-form button,#compound-rule-form input,#compound-rule-form select,#compound-rule-form textarea').forEach(el=>el.disabled=on);$('#close-dialog').disabled=on};
  busy(true);
  $('[data-save-subscription]').textContent='正在拉取并检查…';
  try{const state=await api('/api/library/save',{item,revision:liveWorkspace.revision},spaceId,60000);adoptWorkspace(state,{repaint:false,keepDraft:true});condition.value=item.id;conditionSourceKinds.set(condition,'subscription');subscriptionForms.delete(condition)}
  catch(error){values.error=error.message}
  finally{ruleSubscriptionPending=false;busy(false);paintConditionGroups();previewCompoundRule()}
}
