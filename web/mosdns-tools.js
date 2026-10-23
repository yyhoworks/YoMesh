/* MosDNS form import, migration reports and bounded snapshot editing. */
const mosdnsPageSize=250;
function mosdnsPagedInput(path,label,note,values){
 const e=mosdnsDrafts.get(spaceId);e.editors??=new Map();
 const state=e.editors.get(path)||{start:0};
 state.start=Math.max(0,Math.min(state.start,Math.max(0,values.length-1)));
 state.length=Math.min(mosdnsPageSize,values.length-state.start);e.editors.set(path,state);
 return `<div class="field mosdns-paged"><span>${label}</span><p data-md-count="${path}" role="status">共 ${values.length} 条，正在编辑第 ${state.start+1}–${state.start+state.length} 条。其余条件完整保留。</p><textarea aria-label="${label} 当前分段" data-md-page="${path}" rows="10" spellcheck="false">${esc(values.slice(state.start,state.start+state.length).join('\n'))}</textarea><div class="filter-row"><button type="button" class="button" data-md-page-move="${path}" data-step="-1" ${state.start===0?'disabled':''}>上一段</button><button type="button" class="button" data-md-page-move="${path}" data-step="1" ${state.start+state.length>=values.length?'disabled':''}>下一段</button><label class="field">查找条件<input type="search" data-md-page-query="${path}" placeholder="输入域名或 CIDR"></label><button type="button" class="button" data-md-page-find="${path}">查找并定位</button></div><small>${note} 每段最多 ${mosdnsPageSize} 条，可直接增删当前段。</small></div>`;
}
function mosdnsMigrationReport(config){
 const report=config.migration;if(!report)return '';
 const labels={mapped:'已读取',review:'待核对',unsupported:'未等价迁移'};
 return `<section class="panel mosdns-section mosdns-migration" aria-label="LocalDNS 迁移报告"><h2>LocalDNS 迁移报告</h2><p>已读取原件并生成可编辑的 MosDNS 草稿。导入的解析规则与响应 IP 来源暂时停用，请核对动作、顺序和未迁移项后启用。</p><details><summary>查看 ${report.items.length} 项转换说明</summary><dl>${report.items.map(item=>`<div><dt>${esc(item.name)} · ${labels[item.status]}</dt><dd>${esc(item.detail)}</dd></div>`).join('')}</dl></details>${mosdnsInput('migration.reviewed','我已核对停用规则和未迁移项，允许校验与导出','确认不会自动启用规则，也不表示 LocalDNS 全部功能已经等价迁移。','checkbox')}</section>`;
}
function mosdnsImportPanel(e){
 return mosdnsSection('读入已有配置',`${e.legacy?'<p>从当前方案保存的 LocalDNS 原件生成表单，不需要重新上传文件。</p><button type="button" class="button" id="md-import-localdns">从 LocalDNS 原件生成表单</button><hr>':''}<label class="field">导入 YoMesh MosDNS 设置 JSON<input id="md-import" type="file" accept=".json,application/json"><small>读入后替换当前草稿；保存后才写入当前方案。</small></label><details><summary>粘贴设置 JSON</summary><label class="field">设置 JSON<textarea id="md-paste" class="mosdns-code" rows="8" spellcheck="false" placeholder="粘贴含 groups / rules 的 YoMesh MosDNS 设置">${esc(e.importText||'')}</textarea></label><button type="button" class="button" id="md-import-paste">读入表单</button></details><button type="button" class="text-button" id="md-export-model">下载设置 JSON</button>`);
}
function mosdnsViewKey(detail){
 const rule=detail.closest('.mosdns-rule'),prefix=rule?.querySelector('[data-md]')?.dataset.md||'';
 return prefix+':'+(detail.dataset.mdKey||detail.querySelector('[data-md]')?.dataset.md||detail.querySelector('summary')?.textContent);
}
function mosdnsRememberView(){
 const form=$('#mosdns-form');if(!form)return;
 const e=mosdnsDrafts.get(form.dataset.space);if(!e)return;
 e.views??=new Map();e.views.set(form.dataset.tab,new Map([...form.querySelectorAll('details')].map(d=>[mosdnsViewKey(d),d.open])));
}
function mosdnsRestoreView(e){
 const form=$('#mosdns-form');form.dataset.space=spaceId;form.dataset.tab=mosdnsTab;
 const state=e.views?.get(mosdnsTab);if(state)[...form.querySelectorAll('details')].forEach(d=>{const key=mosdnsViewKey(d);if(state.has(key))d.open=state.get(key)});
}
function mosdnsBindTools(e,busy,fail,change){
 const id=spaceId;
 const receive=result=>{if(spaceId!==id)throw Error('当前方案已经切换，请回到原方案重新读入。');e.config=clone(result.settings);e.rawText=undefined;e.importText='';e.editors=new Map();e.views=new Map();mosdnsTab='listen';mosdnsMark();renderMosdns();toast('已读入 MosDNS 表单；请核对后保存。')};
 const run=async action=>{const status=$('#md-status'),previous=status.textContent;busy(true);$('#md-error').textContent='';status.textContent='正在读取并验证配置，请稍候…';try{await action()}catch(error){if(status.isConnected)status.textContent=previous;fail(error)}finally{busy(false)}};
 const readText=async text=>{
   if(new Blob([text]).size>16*1024*1024)throw Error('设置 JSON 不能超过 16 MiB。');
   let config;try{config=JSON.parse(text)}catch{throw Error('设置 JSON 格式无效，请修正后重新读入；当前草稿已保留。')}
   receive(await api('/api/workspace/mosdns/import',{config,revision:e.revision},id,30000));
 };
 if($('#md-import-localdns'))$('#md-import-localdns').onclick=()=>run(async()=>receive(await api('/api/workspace/mosdns/import-localdns',{revision:e.revision},id,30000)));
 if($('#md-paste'))$('#md-paste').oninput=event=>{e.importText=event.target.value};
 if($('#md-import-paste'))$('#md-import-paste').onclick=()=>run(()=>readText(e.importText||''));
 if($('#md-import'))$('#md-import').onchange=event=>{const file=event.target.files[0];if(file)run(async()=>{if(file.size>16*1024*1024)throw Error('设置 JSON 不能超过 16 MiB。');await readText(await file.text())})};
 $('#mosdns-form').addEventListener('input',event=>{
   const path=event.target.dataset.mdPage;if(!path)return;
   const state=e.editors.get(path),values=mosdnsGet(e.config,path),replacement=mosdnsLines(event.target.value);
   const updated=values.slice(0,state.start).concat(replacement,values.slice(state.start+state.length));
   mosdnsSet(e.config,path,updated);state.length=replacement.length;mosdnsMark();
   $(`[data-md-count="${path}"]`).textContent=`共 ${updated.length} 条，正在编辑第 ${state.start+1}–${state.start+state.length} 条。其余条件完整保留。`;
 });
 $$('[data-md-page-move]').forEach(button=>button.onclick=()=>{
   const path=button.dataset.mdPageMove,state=e.editors.get(path);state.history??=[];
   if(button.dataset.step==='1'){state.history.push(state.start);state.start+=state.length}
   else state.start=state.history.pop()??Math.max(0,state.start-mosdnsPageSize);
   renderMosdns();
 });
 $$('[data-md-page-find]').forEach(button=>button.onclick=()=>{
   const path=button.dataset.mdPageFind,query=$(`[data-md-page-query="${path}"]`).value.trim();
   if(!query){fail(Error('请先输入需要查找的条件。'));return}
   const index=mosdnsGet(e.config,path).findIndex(value=>value.includes(query));
   if(index<0){fail(Error('未找到匹配条件；已保留当前编辑。'));return}
   Object.assign(e.editors.get(path),{start:index,history:[]});renderMosdns();$(`[data-md-page="${path}"]`).focus();
 });
}
