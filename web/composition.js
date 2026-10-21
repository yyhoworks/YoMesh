'use strict';
// Shared resources are edited here; generation selections belong to one space.
pages.spaces[1]='为手机、电脑和网关，组合各自需要的配置。';
pages.groups[1]='编辑会同步到引用它的配置空间。';
pages.library[1]='每条规则选择匹配器或订阅。';
let groupSelectionFilter='all',ruleSelectionFilter='all',ruleChecks=new Set(),selectionBusy=false;
const generationView=()=>liveWorkspace.state?.generation||{};
const selectedForGeneration=(kind,ident)=>data.composition?.[kind]==null||data.composition[kind].includes(ident);
const recipeItems=kind=>kind==='rules'?data.library:kind==='policies'&&data.composition?.plan?data.routing_plans.find(p=>p.id===data.composition.plan)?.policies||[]:data[kind];
const moreIcon=()=>'<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg>';
function scopeCaption(){return `<span class="badge">共享内容</span><span>选用仅影响 <strong>「${esc(activeSpace()?.name||'当前方案')}」</strong></span>`}
function compactChip(label,kind,value,selected){return `<button class="scope-chip" data-${kind}-filter="${value}" aria-pressed="${selected===value}">${label}</button>`}
function resourceMenu(label,body){return `<details class="resource-more"><summary aria-label="${esc(label)} 更多操作">${moreIcon()}</summary><div class="resource-menu">${body}</div></details>`}
function bindResourceMenus(root=$('#content')){
  $$('details.resource-more',root).forEach(d=>d.addEventListener('toggle',()=>{if(!d.open)return;$$('details.resource-more[open]',root).forEach(other=>{if(other!==d)other.open=false});const menu=$('.resource-menu',d);menu.classList.toggle('above',menu.getBoundingClientRect().bottom>innerHeight-12)}));
}
document.addEventListener('click',e=>{if(!e.target.closest('details.resource-more'))$$('details.resource-more[open]').forEach(d=>d.open=false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape')$$('details.resource-more[open]').forEach(d=>d.open=false)});
async function saveGenerationSelection(selection,message){
  if(selectionBusy||liveWorkspace.saving)throw Error('正在保存，请稍后。');
  const scope=spaceId,before=clone(data.composition||{});
  selectionBusy=true;liveWorkspace.saving=true;
  for(const selector of ['#content','#navigation','.topbar'])$(selector).inert=true;
  try{const result=await api('/api/workspace/selection',{selection,revision:liveWorkspace.revision},scope);adoptWorkspace(result);const excluded=result.generation?.excluded_policies||[];selectionUndoToast(message+(excluded.length?` 此方案将跳过 ${excluded.length} 条策略。`:''),before,scope,result.revision);return result}
  catch(error){await reloadWorkspace().catch(()=>{});throw error}
  finally{selectionBusy=false;liveWorkspace.saving=false;for(const selector of ['#content','#navigation','.topbar'])$(selector).inert=false}
}
function selectionUndoToast(message,before,scope,revision){
  toast(message);const button=document.createElement('button');button.className='text-button';button.textContent='撤销';$('#toast').append(button);clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,10000);
  button.onclick=async()=>{if(scope!==spaceId||revision!==liveWorkspace.revision){toast('方案已更新，请在当前内容上调整选用关系。');return}button.disabled=true;try{await saveGenerationSelection(before,'已撤销选用调整。');toast('已撤销「'+(activeSpace()?.name||'当前方案')+'」的操作。')}catch(error){toast(error.message)}};
}
async function toggleGeneration(kind,ident,on){
  const selection=clone(data.composition||{}),rows=recipeItems(kind),chosen=new Set(selection[kind]??rows.map(r=>r.id));
  if(on)chosen.add(ident);else chosen.delete(ident);
  selection[kind]=rows.filter(r=>chosen.has(r.id)).map(r=>r.id);
  return saveGenerationSelection(selection,on?'已选用，仅影响当前方案。':'已取消选用，仅影响当前方案。');
}
renderGroups=function(){
  const chosen=data.groups.filter(g=>selectedForGeneration('groups',g.id)).length;
  const rows=data.groups.filter(g=>g.name.toLowerCase().includes(query.toLowerCase())&&(groupSelectionFilter!=='selected'||selectedForGeneration('groups',g.id)));
  const compact=document.documentElement.dataset.density==='compact';
  $('#content').innerHTML=`<div class="toolbar resource-toolbar"><label class="search">${icon('search')}<input id="flat-group-search" placeholder="搜索节点组" aria-label="搜索节点组" value="${esc(query)}"></label><div class="scope-chips">${compactChip('全部 '+data.groups.length,'group','all',groupSelectionFilter)}${compactChip('仅看已选 '+chosen,'group','selected',groupSelectionFilter)}</div><div class="density-choices" aria-label="节点组显示密度"><span>显示密度</span><button class="scope-chip" data-group-density="comfortable" aria-pressed="${!compact}">舒适</button><button class="scope-chip" data-group-density="compact" aria-pressed="${compact}">紧凑</button></div></div><div class="flat-group-grid ${compact?'group-compact':''}">${rows.map(g=>{
    const summary=liveWorkspace.state?.group_summaries?.[g.id],on=selectedForGeneration('groups',g.id);
    return `<article data-group-card="${esc(g.id)}" class="panel flat-group-card ${on?'chosen':''}"><div class="section-title"><h2>${esc(g.name)}</h2>${badge(modes[g.mode])}</div><div class="group-node-count"><strong>${summary?summary.count:'—'}</strong><span>个节点</span></div><div class="group-condition">${g.include?`匹配 <span>${esc(g.include)}</span>`:'全部节点'}${g.exclude?` · 排除 ${esc(g.exclude)}`:''}</div>${summary?.error?`<p class="error-message">${esc(summary.error)}</p>`:''}<div class="flat-group-actions"><label class="scope-switch"><input type="checkbox" data-generation-group="${esc(g.id)}" ${on?'checked':''}><span class="switch-visual" aria-hidden="true"></span><span>启用此分组</span></label><button class="button" data-flat-edit="${esc(g.id)}">编辑</button>${resourceMenu(g.name,`<button data-flat-usage="${esc(g.id)}">查看选用情况</button><button data-flat-copy="${esc(g.id)}">复制节点组</button><button class="danger-action" data-flat-delete="${esc(g.id)}">删除共享节点组</button>`)}</div></article>`;
  }).join('')||'<div class="empty">没有符合条件的节点组。</div>'}</div>`;
  $('#flat-group-search').oninput=e=>{query=e.target.value;const pos=e.target.selectionStart;renderGroups();$('#flat-group-search').focus();$('#flat-group-search').setSelectionRange(pos,pos)};
  $$('[data-group-filter]').forEach(el=>el.onclick=()=>{groupSelectionFilter=el.dataset.groupFilter;renderGroups()});
  $$('[data-group-density]').forEach(el=>el.onclick=()=>{$('#density').value=el.dataset.groupDensity;$('#density').dispatchEvent(new Event('change'));renderGroups()});
  $$('[data-generation-group]').forEach(el=>el.onchange=()=>toggleGeneration('groups',el.dataset.generationGroup,el.checked).catch(error=>toast(error.message)));
  $$('[data-flat-edit]').forEach(b=>b.onclick=()=>editFlatGroup(b.dataset.flatEdit));
  $$('[data-flat-copy]').forEach(b=>b.onclick=async()=>{const group=clone(find('groups',b.dataset.flatCopy));group.id=id('g');let name=group.name+' 副本',n=2;while(data.groups.some(g=>g.name===name))name=group.name+' 副本 '+n++;group.name=name;try{await saveChange(()=>data.groups.push(group));editFlatGroup(group.id)}catch(error){toast(error.message)}});
  $$('[data-flat-delete]').forEach(b=>b.onclick=()=>{const ident=b.dataset.flatDelete,g=find('groups',ident);confirmRemoval('删除共享节点组',`删除「${g.name}」？被策略或子组引用时会阻止删除。`,()=>{data.groups=data.groups.filter(item=>item.id!==ident)})});
  $$('[data-flat-usage]').forEach(b=>b.onclick=()=>showGroupSelection(b.dataset.flatUsage));bindResourceMenus();
};
async function showGroupSelection(ident){
  try{await refreshSpaceCatalog();const items=spaceCatalog.spaces.filter(s=>(s.source_space||s.id)===(activeSpace()?.source_space||spaceId)&&(!s.composition?.groups||s.composition.groups.includes(ident)));showDialog('选用情况 · '+find('groups',ident).name,`<p>共享内容；以下空间直接选用此节点组。</p><ul>${items.map(s=>`<li>${esc(s.name)}</li>`).join('')||'<li>尚无空间直接选用；也可能作为子组被包含。</li>'}</ul><p class="preview-note">最终组依赖以生成检查结果为准。</p>`)}catch(error){toast(error.message)}
}
function editFlatGroup(ident){
  selectedGroup=ident;draft=clone(find('groups',ident));showDialog('编辑共享节点组',`<p class="scope-editor-note">${badge('共享内容')} 内容修改会同步到引用它的配置空间。</p><section id="group-editor" aria-label="节点组编辑器"></section>`);
  const dialog=$('#dialog');dialog.classList.add('flat-group-dialog');
  const paint=()=>{renderGroupEditor();$('#save-group').onclick=async()=>{try{await persist();draft=null;dialog.close()}catch(error){toast(error.message)}};$('#reset-group').onclick=()=>{draft=clone(find('groups',ident));paint()};$('#delete-group').hidden=true};
  dialog.addEventListener('close',()=>{dialog.classList.remove('flat-group-dialog');draft=null;render()},{once:true});paint();
}
const groupEditorWithMembers=renderGroupEditor;
renderGroupEditor=function(){
  groupEditorWithMembers();if(!draft||!$('#dialog').classList.contains('flat-group-dialog'))return;
  const ident=draft.id;$('#delete-group').hidden=true;
  $('#save-group').onclick=async()=>{try{await persist();draft=null;$('#dialog').close()}catch(error){toast(error.message)}};
  $('#reset-group').onclick=()=>{draft=clone(find('groups',ident));renderGroupEditor()};
  $('#test-group').onclick=async()=>{try{if(dirty())await persist();draft=clone(find('groups',ident));adoptWorkspace(await api(`/api/groups/${encodeURIComponent(ident)}/test`,{}),{keepDraft:true});renderGroupEditor();toast('已开始通过节点测试 HTTP 延迟。')}catch(error){toast(error.message)}};
};
const createdGroup=addGroup;
addGroup=function(){
  createdGroup();
  let pendingInclude='';
  if(typeof nodeGroupTemplateBar==='function'&&$('#resource-form')){
    $('#resource-form').insertAdjacentHTML('afterbegin',nodeGroupTemplateBar('data-ag-template'));
    $$('[data-ag-template]').forEach(btn=>btn.onclick=()=>{
      const tpl=NODE_GROUP_TEMPLATES[Number(btn.dataset.agTemplate)];if(!tpl)return;
      const nameInput=$('[name="name"]',$('#resource-form')),modeSelect=$('[name="mode"]',$('#resource-form'));
      if(nameInput)nameInput.value=tpl.name;
      if(modeSelect)modeSelect.value=tpl.mode;
      pendingInclude=tpl.include||'';
      toast(`已填入「${tpl.label}」模板，保存后可勾选订阅来源。`);
    });
  }
  const handler=$('#resource-form').onsubmit;
  $('#resource-form').onsubmit=async e=>{
    const previous=new Set(data.groups.map(g=>g.id));
    await handler(e);
    const added=data.groups.find(g=>!previous.has(g.id));
    if(added&&!$('#dialog').open){
      if(pendingInclude){added.include=pendingInclude;try{await persist()}catch{}}
      editFlatGroup(added.id);
    }
  };
};
function currentRuleImpacts(ident){return generationView().rule_impacts?.[ident]||[]}
function shownRules(){return data.library.filter(r=>r.name.toLowerCase().includes(query.toLowerCase())&&(ruleSelectionFilter==='all'||ruleSelectionFilter==='on'&&selectedForGeneration('rules',r.id)||ruleSelectionFilter==='off'&&!selectedForGeneration('rules',r.id)||ruleSelectionFilter==='used'&&currentRuleImpacts(r.id).length))}
renderLibrary=function(){
  ruleChecks=new Set([...ruleChecks].filter(ident=>data.library.some(r=>r.id===ident)));
  const rows=shownRules(),enabled=data.library.filter(r=>selectedForGeneration('rules',r.id)).length;
  $('#content').innerHTML=`<div class="toolbar resource-toolbar"><label class="search">${icon('search')}<input id="library-search" type="search" aria-label="搜索规则" placeholder="搜索规则" value="${esc(query)}"></label><div class="scope-chips">${compactChip('全部 '+data.library.length,'rule','all',ruleSelectionFilter)}${compactChip('已启用 '+enabled,'rule','on',ruleSelectionFilter)}${compactChip('已停用 '+(data.library.length-enabled),'rule','off',ruleSelectionFilter)}${compactChip('已引用','rule','used',ruleSelectionFilter)}</div></div>${ruleChecks.size?`<div class="rule-batch"><span>已选 ${ruleChecks.size} 条</span><button class="button" data-rule-batch="on">批量启用</button><button class="button" data-rule-batch="off">批量停用</button><button class="text-button" id="rule-check-clear">取消选择</button></div>`:''}<section class="panel rule-list" aria-label="规则列表"><table class="rule-table"><thead><tr><th><label class="check-hit"><input type="checkbox" id="rules-select-all" aria-label="选择当前列表全部规则" ${rows.length&&rows.every(r=>ruleChecks.has(r.id))?'checked':''}></label></th><th>规则名称</th><th>规则数量</th><th>此方案引用</th><th>此方案启用</th><th>操作</th></tr></thead><tbody>${rows.map(r=>{
    const on=selectedForGeneration('rules',r.id),impacts=currentRuleImpacts(r.id),status=liveWorkspace.state.library_status?.[r.id]||{},remote=r.source?.type==='url';
    const detail=status.refresh_state==='running'?'订阅更新中…':status.refresh_state==='error'?'更新失败 · 可查看详情':remote?'订阅'+(r.source.auto_update?' · 自动更新':' · 手动更新'):r.source?.type==='file'?'导入文件':'匹配器';
    return `<tr class="scope-rule-row ${on?'':'rule-off'}"><td class="rule-check-cell"><label class="check-hit"><input type="checkbox" data-rule-check="${esc(r.id)}" aria-label="选择 ${esc(r.name)}" ${ruleChecks.has(r.id)?'checked':''}></label></td><td class="rule-name-cell"><strong>${esc(r.name)}</strong><small class="${!on&&impacts.length?'error-message':''}">${!on&&impacts.length?`将跳过 ${impacts.length} 条策略`:esc(detail)}</small></td><td class="rule-count-cell">${r.entries.length.toLocaleString()} 条</td><td class="rule-refs-cell"><button class="text-button" data-rule-impact="${esc(r.id)}">${impacts.length?impacts.length+' 条策略':'未引用'}</button></td><td class="rule-switch-cell"><button class="switch" role="switch" aria-checked="${on}" data-rule-toggle="${esc(r.id)}" aria-label="${esc(r.name)} 在当前方案启用"></button><span>${on?'启用':'停用'}</span></td><td class="rule-action-cell"><div class="filter-row"><button class="button" data-rule-edit="${esc(r.id)}">编辑</button>${resourceMenu(r.name,`<button data-rule-view="${esc(r.id)}">查看内容</button><button data-rule-detail="${esc(r.id)}">来源与更新详情</button>${remote?`<button data-rule-refresh="${esc(r.id)}" ${status.refresh_state==='running'?'disabled':''}>更新订阅</button>`:''}<button data-rule-impact="${esc(r.id)}">查看引用策略</button><button data-rule-use="${esc(r.id)}">用于新策略</button><button class="danger-action" data-rule-delete="${esc(r.id)}">删除共享规则</button>`)}</div></td></tr>`;
  }).join('')}</tbody></table>${rows.length?'':'<div class="empty">没有符合条件的规则。</div>'}</section><div class="resource-list-footer"><span>${rows.length} 条规则 · 开关仅影响「${esc(activeSpace()?.name||'当前方案')}」</span><span>内容编辑会同步至其他方案</span></div>`;
  $('#library-search').oninput=e=>{query=e.target.value;const pos=e.target.selectionStart;renderLibrary();$('#library-search').focus();$('#library-search').setSelectionRange(pos,pos)};
  $$('[data-rule-filter]').forEach(b=>b.onclick=()=>{ruleSelectionFilter=b.dataset.ruleFilter;ruleChecks.clear();renderLibrary()});
  $$('[data-rule-check]').forEach(el=>el.onchange=()=>{if(el.checked)ruleChecks.add(el.dataset.ruleCheck);else ruleChecks.delete(el.dataset.ruleCheck);renderLibrary();$$('[data-rule-check]').find(x=>x.dataset.ruleCheck===el.dataset.ruleCheck)?.focus()});
  $('#rules-select-all').onchange=e=>{rows.forEach(r=>e.target.checked?ruleChecks.add(r.id):ruleChecks.delete(r.id));renderLibrary()};
  if($('#rule-check-clear'))$('#rule-check-clear').onclick=()=>{ruleChecks.clear();renderLibrary()};
  $$('[data-rule-batch]').forEach(b=>b.onclick=async()=>{const chosen=new Set(data.composition?.rules??data.library.map(r=>r.id));ruleChecks.forEach(ident=>b.dataset.ruleBatch==='on'?chosen.add(ident):chosen.delete(ident));const selection={...clone(data.composition||{}),rules:data.library.filter(r=>chosen.has(r.id)).map(r=>r.id)};try{await saveGenerationSelection(selection,'批量选用已保存。');ruleChecks.clear();renderLibrary()}catch(error){toast(error.message)}});
  $$('[data-rule-toggle]').forEach(b=>b.onclick=()=>toggleGeneration('rules',b.dataset.ruleToggle,b.getAttribute('aria-checked')!=='true').catch(error=>toast(error.message)));
  $$('[data-rule-edit]').forEach(b=>b.onclick=()=>editLibrary(b.dataset.ruleEdit));
  $$('[data-rule-view]').forEach(b=>b.onclick=()=>viewLibrary(b.dataset.ruleView));
  $$('[data-rule-detail]').forEach(b=>b.onclick=()=>showRuleDetails(b.dataset.ruleDetail));
  $$('[data-rule-impact]').forEach(b=>b.onclick=()=>showRuleImpact(b.dataset.ruleImpact));
  $$('[data-rule-use]').forEach(b=>b.onclick=()=>editPolicy(null,b.dataset.ruleUse));
  $$('[data-rule-refresh]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{adoptWorkspace(await api(`/api/library/${encodeURIComponent(b.dataset.ruleRefresh)}/refresh`,{}));toast('正在更新规则订阅。')}catch(error){toast(error.message);b.disabled=false}});
  $$('[data-rule-delete]').forEach(b=>b.onclick=()=>deleteSharedRule(b.dataset.ruleDelete));bindResourceMenus();
};
function deleteSharedRule(ident){const r=find('library',ident);confirmRemoval('删除共享规则',`删除「${r.name}」？仍被任一分流策略引用时会阻止删除。`,()=>{});$('#remove-confirm').onclick=async e=>{e.target.disabled=true;try{adoptWorkspace(await api(`/api/library/${encodeURIComponent(ident)}/delete`,{revision:liveWorkspace.revision}));$('#dialog').close()}catch(error){$('#remove-error').textContent=error.message;e.target.disabled=false}}}
function showRuleImpact(ident){const r=find('library',ident),refs=currentRuleImpacts(ident);showDialog('引用策略 · '+r.name,`<p>当前生成方案：<strong>${esc(activeSpace()?.name)}</strong>。关闭后跳过引用它的整条策略。</p><div class="impact-list">${refs.map(ref=>`<div><strong>${esc(ref.name)}</strong><small>${esc(ref.plan_name)}${ref.current?' · 默认入口':' · 入站绑定'}</small></div>`).join('')||'<p>当前方案没有启用的策略引用它。</p>'}</div><p class="preview-note">AND / OR 组合条件完整保留，不删除子条件。</p>`)}
function showRuleDetails(ident){const r=find('library',ident),s=liveWorkspace.state.library_status?.[ident]||{};let origin=r.source?.type==='file'?'文件导入':'规则匹配器';if(r.source?.type==='url'){try{origin=new URL(r.source.url).host}catch{origin='规则订阅'}}showDialog('规则详情 · '+r.name,`<dl class="resource-details"><dt>来源</dt><dd>${esc(origin)}</dd><dt>格式</dt><dd>${esc(ruleFormats[s.detected_format||r.source?.format]||ruleKind(r.kind))}</dd><dt>内容数量</dt><dd>${r.entries.length.toLocaleString()} 条</dd><dt>最近更新</dt><dd>${s.last_updated?serverTime(s.last_updated):'已保存内容'}</dd><dt>更新计划</dt><dd>${r.source?.type==='url'&&r.source.auto_update?(r.source.schedule_mode==='daily'?'每天 '+esc(r.source.daily_time):'每 '+r.source.interval_minutes+' 分钟'):'手动更新'}</dd></dl>${s.error?`<p class="error-message">${esc(s.error)}</p>`:''}<p class="preview-note">${(usageView().library[ident]?.references||[]).length} 处共享策略引用。</p>`)}
const renderWithComposition=render;
render=function(){renderWithComposition();if(!liveWorkspace.state)return;$('#export').textContent='生成配置';if(['groups','library','policies'].includes(page)){$('#page-description').className='resource-scope';$('#page-description').innerHTML=scopeCaption()+`<span class="scope-extra">· ${esc(pages[page][1])}</span>`}else{$('#page-description').className=''}bindResourceMenus()};
// Preserve existing drag, keyboard, outbound and native export implementations.
const policyListWithActions=renderPolicies;
renderPolicies=function(){
  policyListWithActions();if(page!=='policies'||policyView!=='rules')return;
  $('.dns-overview')?.remove();$('#save-state')?.remove();$('.policy-table')?.classList.add('policy-responsive');
  const header=$$('.policy-table th');if(header[4])header[4].textContent='此方案启用';
  const matches=(data.composition?.plan||data.active_plan)===data.active_plan;
  if(!matches){const button=document.createElement('button');button.className='button';button.textContent='此方案选用此模板';button.onclick=()=>saveGenerationSelection({...clone(data.composition||{}),plan:data.active_plan,policies:null,fallback:plans().find(p=>p.id===data.active_plan)?.fallback||data.fallback},'已选用分流模板。').catch(error=>toast(error.message));$('.plan-toolbar .filter-row').append(button)}
  $$('[data-policy-row]').forEach(row=>{
    const ident=row.dataset.policyRow,p=find('policies',ident),toggle=$('[data-toggle]',row);
    if(toggle){const on=selectedForGeneration('policies',ident)&&p.enabled!==false;toggle.setAttribute('aria-checked',on);toggle.disabled=!matches||p.enabled===false||policyBusy;toggle.title=!matches?'先在配置空间选用此分流模板':p.enabled===false?'此共享策略已停用，请编辑后启用':'仅影响当前生成方案';toggle.onclick=()=>toggleGeneration('policies',ident,!selectedForGeneration('policies',ident)).catch(error=>toast(error.message));row.classList.toggle('rule-disabled',!on)}
    const cells=[...row.children];cells.forEach((cell,i)=>cell.dataset.label=['顺序','匹配条件','共享出口','DNS','此方案启用','操作'][i]);
    const actions=$('.filter-row',cells[5]);if(actions){const edit=$('[data-edit-rule]',actions);if(edit){edit.textContent='编辑';const others=[...actions.children].filter(x=>x!==edit);const details=document.createElement('details');details.className='resource-more';details.innerHTML=`<summary aria-label="${esc(p.name)} 更多操作">${moreIcon()}</summary><div class="resource-menu"></div>`;others.forEach(x=>$('.resource-menu',details).append(x));actions.append(details)}}
  });bindResourceMenus();
};
const policyEditorWithConditions=editPolicy;
editPolicy=function(ident,seedLibrary=null){
  policyEditorWithConditions(ident,seedLibrary);const sections=$$('.rule-composer-fields > .rule-section'),fields=$('.rule-composer-fields');if(!fields)return;
  $('#dialog-title').textContent=ident?'编辑分流策略':'新建分流策略';$('#compound-save').textContent='保存策略';$('#compound-enabled').closest('label').lastChild.textContent='启用共享策略';
  const form=$('#compound-rule-form'),footer=document.createElement('footer');footer.className='policy-editor-footer';footer.append($('#compound-error'),$('.composer-actions'));form.append(footer);$('#dialog').classList.add('policy-editor-dialog');
  const preventClose=e=>{if(liveWorkspace.saving)e.preventDefault()};$('#dialog').addEventListener('cancel',preventClose);$('#dialog').addEventListener('close',()=>{$('#dialog').classList.remove('policy-editor-dialog');$('#dialog').removeEventListener('cancel',preventClose)},{once:true});
  const submit=form.onsubmit;form.onsubmit=async e=>{$('#close-dialog').disabled=true;$('#compound-cancel').disabled=true;try{await submit(e)}finally{$('#close-dialog').disabled=false;if($('#compound-cancel'))$('#compound-cancel').disabled=false}};
  const note=document.createElement('p');note.className='scope-editor-note';note.innerHTML=badge('共享内容')+' 内容修改会同步到引用此模板的配置空间。';fields.prepend(note);
  const conditions=ruleDraft.condition_groups||[],condition=conditions[0]?.conditions[0],simple=conditions.length===1&&conditions[0].conditions.length===1&&condition?.type==='RULE-SET'&&(ruleDraft.dns||'auto')==='auto';
  if(!simple)return;
  const simpleFields=document.createElement('section');simpleFields.className='rule-section simple-policy-fields';simpleFields.innerHTML=`<label class="field">选择规则<select id="simple-rule"><option value="">请选择规则</option>${data.library.map(r=>`<option value="${esc(r.id)}" ${condition.value===r.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label><label class="field">选择出口<select id="simple-target">${targetOptions(ruleDraft.target)}</select></label>`;
  sections[0].after(simpleFields);const advanced=document.createElement('details');advanced.className='advanced-options';advanced.innerHTML='<summary>高级条件与 DNS</summary>';simpleFields.after(advanced);sections.slice(1).forEach(section=>advanced.append(section));
  $('#simple-rule').onchange=e=>{condition.value=e.target.value;paintConditionGroups();previewCompoundRule()};
  $('#simple-target').onchange=e=>{ruleDraft.target=e.target.value;$('#rule-action').value=e.target.value==='DIRECT'?'direct':e.target.value==='REJECT'?'reject':'proxy';if(!['DIRECT','REJECT'].includes(e.target.value))$('#compound-target').value=e.target.value;updateRuleAction();previewCompoundRule()};
  advanced.addEventListener('toggle',()=>{if(advanced.open)simpleFields.hidden=true});
};
const nativeExportWithUsage=$('#export').onclick;
$('#export').onclick=()=>{nativeExportWithUsage();const g=generationView(),section=document.createElement('section');section.className='generation-summary';section.innerHTML=`<h3>「${esc(activeSpace()?.name||'当前方案')}」生成内容</h3>${generationSummaryMarkup(g)}<p class="preview-note">选用检查与原生内核校验分别进行，下载前仍需校验。</p>`;$('#export-hint').after(section);$('#export-usage')?.remove();$('#export-source').addEventListener('change',()=>{section.hidden=$('#export-source').value!=='current'})};
function generationSummaryMarkup(g){return `<div class="generation-counts"><span><b>${g.counts?.groups??'—'}</b> 节点组</span><span><b>${g.counts?.library??'—'}</b> 规则</span><span><b>${g.counts?.policies??'—'}</b> 生效策略</span></div><p class="${g.error?'error-message':'generation-ready'}">${g.error?esc(g.error):g.valid?'选用检查通过':'正在检查选用关系…'}</p>${g.excluded_policies?.length?`<details><summary>跳过 ${g.excluded_policies.length} 条策略</summary>${g.excluded_policies.map(p=>`<p><strong>${esc(p.name)}</strong><small>${esc(p.reason)}</small></p>`).join('')}</details>`:''}`}
