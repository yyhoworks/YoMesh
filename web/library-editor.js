'use strict';
const importRuleFile = editLibrary;
let libraryEditorBusy=false;

addLibrary=()=>composeLibrary();
editLibrary=ident=>composeLibrary(ident);
async function composeLibrary(ident=null) {
  const original=ident?find('library',ident):null;
  let kind=original?.source?.type==='url'?'subscription':'matcher';
  let subscriptions=[{id:original?.id||id('l'),name:original?.name||'',url:original?.source?.url||'',format:original?.source?.format||'auto',auto_update:original?.source?.auto_update!==false}];
  let matchers=[{type:'DOMAIN-SUFFIX',values:[]}],name=original?.name||'';
  if(original){
    try{const result=await api('/api/library/prepare-editor',{id:ident});const groups=new Map();for(const [type,value] of result.criteria){if(!groups.has(type))groups.set(type,[]);groups.get(type).push(value)}matchers=[...groups].map(([type,values])=>({type,values}))}
    catch(error){toast(error.message);return}
  }
  showDialog(original?'修改规则':'添加规则',`<form id="library-composer"><p class="scope-editor-note">${badge('共享内容')} 修改会同步到引用此规则的配置空间；启停分别保存。</p><fieldset class="rule-output-choices library-kind"><legend>规则类型</legend>${[['subscription','订阅规则','填写订阅地址，可一次添加多条。'],['matcher','规则匹配器','选择匹配类型，填写域名或 IP。']].map(([value,title,hint])=>`<label class="rule-output-choice"><input type="radio" name="library_kind" value="${value}" ${kind===value?'checked':''} ><span><strong>${title}</strong><small>${hint}</small></span></label>`).join('')}</fieldset><div id="library-compose-body"></div><p class="error-message" id="library-compose-error" role="alert"></p><div class="dialog-actions"><button class="button" type="button" id="library-import">导入文件 / 原始文本</button><button class="button" type="button" id="library-compose-cancel">取消</button><button class="button primary" type="submit" id="library-compose-save">保存规则</button></div></form>`);
  const dialog=$('#dialog');dialog.classList.add('library-composer-dialog');
  const preventClose=e=>{if(libraryEditorBusy)e.preventDefault()};dialog.addEventListener('cancel',preventClose);
  dialog.addEventListener('close',()=>{dialog.classList.remove('library-composer-dialog');dialog.removeEventListener('cancel',preventClose)},{once:true});
  const paint=()=>{
    $('#library-compose-body').innerHTML=kind==='subscription'?`<p class="preview-note">每条地址独立保存、更新和选择；保存后在分流策略中引用。</p><div class="subscription-rows">${subscriptions.map((item,index)=>`<section class="subscription-row"><div class="section-title"><h3>订阅 ${index+1}</h3><button class="icon-button danger-action" type="button" data-remove-sub="${index}" aria-label="移除订阅 ${index+1}" ${subscriptions.length===1?'disabled':''}>${actionIcon('trash')}</button></div><label class="field">名称<input data-sub-index="${index}" data-field="name" value="${esc(item.name)}" maxlength="100" required></label><label class="field">订阅地址<input data-sub-index="${index}" data-field="url" value="${esc(item.url)}" type="url" placeholder="https://…/rules.mrs" required></label><details class="advanced-options"><summary>高级选项（默认自动识别格式并自动更新）</summary><label class="field">格式<select data-sub-index="${index}" data-field="format">${Object.entries(ruleFormats).map(([value,label])=>`<option value="${value}" ${item.format===value?'selected':''}>${label}</option>`).join('')}</select></label><label class="check-row"><input data-sub-index="${index}" data-field="auto_update" type="checkbox" ${item.auto_update?'checked':''}>自动更新</label></details></section>`).join('')}</div><button class="text-button" type="button" id="add-library-sub" ${subscriptions.length>=16?'disabled':''}>${icon('plus')}添加一条订阅</button>`:`<label class="field">规则名称<input id="matcher-library-name" value="${esc(name)}" maxlength="100" required placeholder="例如：家庭服务"></label><p class="preview-note">各匹配器和每行内容均为“任意命中”；AND / OR 组合在分流策略中配置。</p>${matchers.map((item,index)=>`<section class="matcher-row"><div class="condition-type-line"><label class="field">匹配类型<select data-matcher-type="${index}">${Object.entries(matchLabels).map(([value,label])=>`<option value="${value}" ${item.type===value?'selected':''}>${label}</option>`).join('')}</select></label><button class="icon-button danger-action" type="button" data-remove-matcher="${index}" aria-label="移除匹配器 ${index+1}" ${matchers.length===1?'disabled':''}>${actionIcon('trash')}</button></div><label class="field">匹配内容<textarea data-matcher-values="${index}" rows="5" spellcheck="false" placeholder="每行一个值" required>${esc(item.values.join('\n'))}</textarea></label></section>`).join('')}<button class="text-button" type="button" id="add-library-matcher">${icon('plus')}添加匹配器</button>`;
    $$('[data-sub-index]').forEach(el=>{const update=()=>subscriptions[Number(el.dataset.subIndex)][el.dataset.field]=el.type==='checkbox'?el.checked:el.value;el.oninput=update;el.onchange=update});
    $$('[data-remove-sub]').forEach(el=>el.onclick=()=>{subscriptions.splice(Number(el.dataset.removeSub),1);paint()});
    if($('#add-library-sub'))$('#add-library-sub').onclick=()=>{subscriptions.push({id:id('l'),name:'',url:'',format:'auto',auto_update:true});paint()};
    if($('#matcher-library-name'))$('#matcher-library-name').oninput=e=>name=e.target.value;
    $$('[data-matcher-type]').forEach(el=>el.onchange=()=>matchers[Number(el.dataset.matcherType)].type=el.value);
    $$('[data-matcher-values]').forEach(el=>el.oninput=()=>matchers[Number(el.dataset.matcherValues)].values=el.value.split('\n').map(v=>v.trim()).filter(Boolean));
    $$('[data-remove-matcher]').forEach(el=>el.onclick=()=>{matchers.splice(Number(el.dataset.removeMatcher),1);paint()});
    if($('#add-library-matcher'))$('#add-library-matcher').onclick=()=>{matchers.push({type:'DOMAIN-SUFFIX',values:[]});paint()};
  };
  $$('input[name=library_kind]').forEach(el=>el.onchange=()=>{kind=el.value;paint()});
  $('#library-compose-cancel').onclick=()=>dialog.close();
  $('#library-import').onclick=()=>{dialog.close();importRuleFile(ident)};
  $('#library-composer').onsubmit=async e=>{
    e.preventDefault();if(libraryEditorBusy)return;
    const items=kind==='subscription'?subscriptions.map(item=>({id:item.id,name:item.name.trim(),note:original?.note||'',source:{...(original?.source||{}),type:'url',url:item.url.trim(),format:item.format,auto_update:item.auto_update,interval_minutes:original?.source?.interval_minutes||1440}})):[{id:original?.id||id('l'),name:name.trim(),note:original?.note||'',source:{type:'manual',format:'classical'},matchers}];
    libraryEditorBusy=true;$('#library-compose-error').textContent='';$('#library-compose-save').textContent=kind==='subscription'?'正在拉取并检查…':'正在保存…';
    $$('#library-composer button,#library-composer input,#library-composer select,#library-composer textarea').forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;
    try{const state=await api('/api/library/save-batch',{items,revision:liveWorkspace.revision});adoptWorkspace(state,{keepDraft:true});dialog.close();toast(`已保存 ${items.length} 条规则，可在分流策略中选用。`)}
    catch(error){$('#library-compose-error').textContent=error.message}
    finally{libraryEditorBusy=false;$('#close-dialog').disabled=false;$$('#library-composer button,#library-composer input,#library-composer select,#library-composer textarea').forEach(el=>el.disabled=false);$$('input[name=library_kind]').forEach(el=>el.disabled=false);$('#library-compose-save').textContent='保存规则';if(dialog.open)paint()}
  };
  paint();
}
