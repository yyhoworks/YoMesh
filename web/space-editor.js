'use strict';
// Selection forms render server summaries; no rule or group resolution in JS.
let spaceEditor=null,spaceEditorSerial=0;
const editorKinds={policies:'分流策略',groups:'节点组',rules:'规则',settings:'生成设置'};
function spaceEditorRows(kind){const e=spaceEditor,p=e.profile;return kind==='rules'?p.library:kind==='policies'&&e.selection.plan?p.routing_plans.find(plan=>plan.id===e.selection.plan)?.policies||[]:p[kind]||[]}
function editorSelected(kind,ident){return spaceEditor.selection[kind]==null||spaceEditor.selection[kind].includes(ident)}
function editorTargetOptions(selected){const p=spaceEditor.profile;return [['DIRECT','直连'],...p.groups.map(g=>[g.id,g.name]),...p.nodes.map(n=>[n.id,n.name])].map(([ident,name])=>`<option value="${esc(ident)}" ${ident===selected?'selected':''}>${esc(name)}</option>`).join('')}
function readFilePayload(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>{const res=String(reader.result||''),idx=res.indexOf(',');resolve(idx>=0?res.slice(idx+1):res)};
    reader.onerror=()=>reject(Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}
async function openConfigImport(){
  try{await saveSpaceDrafts();await refreshSpaceCatalog()}catch(error){toast(error.message);return}
  showDialog('导入现有配置',`<form id="config-import-form">
    <p class="scope-editor-note">${badge('独立新方案','accent')} 支持直接导入 Mihomo (.yaml)、sing-box (.json)、YoMesh (.json) 或 .zip 配置包，并可选附带 LocalDNS 配置自动对齐端口与上游接线。</p>
    <div class="fields">
      <label class="field">方案名称<input id="ci-name" required maxlength="80" placeholder="例如：家庭网关 / LocalDNS 代理" value="导入配置"></label>
      <label class="field">说明（可选）<input id="ci-description" maxlength="500" placeholder="例如：从现有 Mihomo / sing-box 导入"></label>
    </div>
    <label class="field">选择代理配置文件（.yaml / .yml / .json / .zip）<input id="ci-file" type="file" accept=".yaml,.yml,.json,.zip"></label>
    <label class="field">或者直接粘贴配置内容（YAML / JSON）<textarea id="ci-text" class="mono" rows="6" spellcheck="false" placeholder="粘贴 Mihomo config.yaml 或 sing-box config.json 内容…"></textarea></label>
    <details class="advanced-options" open>
      <summary>LocalDNS 联动与导入方式</summary>
      <label class="field" style="margin-top:10px">附加 LocalDNS 配置（可选，支持 localdns.json 或 LocalDNS .zip 配置包）<input id="ci-localdns-file" type="file" accept=".json,.zip"><small>上传后会自动提取 LocalDNS 监听端口与代理接线（mixed_port / dns_port / socks5）。</small></label>
      <label class="field">空间类型<select id="ci-mode">
        <option value="auto">自动识别（推荐：可同时输出 Mihomo 与 sing-box；含复杂 provider 时自动保留原生）</option>
        <option value="portable">通用配置空间（可一键切换导出 Mihomo YAML 与 sing-box JSON）</option>
        <option value="native">Mihomo 原生空间（100% 保留 proxy-providers / rule-providers 与 LocalDNS 目录）</option>
      </select></label>
      <label class="check-row"><input type="checkbox" id="ci-draft" checked>允许先导入为草稿（若原配置引用了本地缺失文件，可在导入后补充再校验）</label>
    </details>
    <p id="ci-error" class="error-message" role="alert"></p>
    <div class="dialog-actions">
      <button type="button" class="button" id="ci-cancel">取消</button>
      <button type="submit" class="button primary" id="ci-submit">读取并创建方案</button>
    </div>
  </form>`);
  $('#ci-file').onchange=e=>{
    const f=e.target.files?.[0];
    if(f&&($('#ci-name').value==='导入配置'||!$('#ci-name').value.trim())){
      $('#ci-name').value=f.name.replace(/\.(yaml|yml|json|zip)$/i,'')||'导入配置';
    }
  };
  $('#ci-cancel').onclick=()=>$('#dialog').close();
  $('#config-import-form').onsubmit=async e=>{
    e.preventDefault();
    const btn=$('#ci-submit');btn.disabled=true;$('#ci-error').textContent='';
    try{
      const file=$('#ci-file').files?.[0],text=$('#ci-text').value.trim(),dnsFile=$('#ci-localdns-file').files?.[0];
      if(!file&&!text)throw Error('请选择代理配置文件，或直接粘贴配置内容。');
      if(file&&file.size>20*1024*1024)throw Error('配置文件不能超过 20 MiB。');
      if(dnsFile&&dnsFile.size>20*1024*1024)throw Error('LocalDNS 配置文件不能超过 20 MiB。');
      const payload={
        name:$('#ci-name').value.trim()||'导入配置',
        description:$('#ci-description').value.trim(),
        mode:$('#ci-mode').value,
        draft:$('#ci-draft').checked,
        revision:spaceCatalog.revision
      };
      if(file){
        payload.filename=file.name;
        if(/\.zip$/i.test(file.name))payload.content_base64=await readFilePayload(file);
        else payload.content=await file.text();
      }else{
        payload.filename='pasted-config';
        payload.content=text;
      }
      if(dnsFile){
        payload.localdns_filename=dnsFile.name;
        if(/\.zip$/i.test(dnsFile.name))payload.localdns_base64=await readFilePayload(dnsFile);
        else payload.localdns_content=await dnsFile.text();
      }
      const result=await api('/api/spaces/import-config',payload);
      spaceCatalog=result;
      $('#dialog').close();
      await switchSpace(result.id);
      const summary=result.import_summary;
      toast(summary?`已导入「${summary.format}」配置（${summary.nodes||0} 节点 / ${summary.groups||0} 节点组）`:'配置已导入到新方案。');
    }catch(error){
      $('#ci-error').textContent=error.message;
      btn.disabled=false;
    }
  };
}
let hermesFreshToken=null;
async function renderHermesPage(){
  $('#content').innerHTML='<div class="panel"><p class="preview-note">正在读取 Hermes 接入状态…</p></div>';
  let status={configured:false,prefix:null,created_at:null,last_used_at:null},loadError='';
  try{status=await api('/api/integrations/hermes')}catch(error){loadError=error.message}
  const paint=()=>{
    const baseUrl=location.origin;
    const tokenDisplay=hermesFreshToken||'<点击上方按钮生成密钥后自动填入>';
    const oneLineCmd=`curl -fsSL "${baseUrl}/api/setup/hermes_installer.py" | YOMESH_URL="${baseUrl}" YOMESH_TOKEN="${tokenDisplay}" python3 -`;
    const manualYaml=`mcp_servers:\n  yomesh:\n    command: python3\n    args:\n      - /root/.local/share/yomesh/yomesh_hermes_mcp.py\n      - --config\n      - /root/.config/yomesh/hermes.json\n    enabled: true`;
    $('#content').innerHTML=`<div id="hermes-integration-panel" style="max-width:860px">
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title">
          <h3>第 1 步：生成 Hermes 专属接入密钥</h3>
          ${badge(status.configured?'已启用':'未启用',status.configured?'accent':'')}
        </div>
        <p class="preview-note" style="margin-top:6px">连接 Hermes 后，你可以直接用自然语言查域名分流原因、一句话向 YoMesh 添加规则、测速节点或获取订阅链接。无需配置 SSH 免密，密钥随时可一键吊销。</p>
        <p class="preview-note" style="margin-top:6px">${status.configured?`当前生效密钥前缀：<code>${esc(status.prefix||'ymh_')}***</code>${status.last_used_at?' · 最近调用：'+esc(serverTime(status.last_used_at)):''}`:'尚未生成接入密钥，请点击下方按钮一键生成。'}</p>
        <div class="filter-row" style="margin-top:12px">
          <button type="button" class="button primary" id="hm-rotate">${status.configured?'重新生成一键接入命令（旧密钥立即失效）':'一键生成密钥 & 安装命令'}</button>
          ${status.configured?'<button type="button" class="button danger-button" id="hm-revoke">停用 Hermes 接入</button>':''}
        </div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title">
          <h3>第 2 步：在运行 Hermes 的机器终端执行（一行自动配好）</h3>
          ${hermesFreshToken?badge('已填入最新密钥 · 请复制执行','accent'):badge('等待生成新密钥')}
        </div>
        <p class="preview-note" style="margin-top:6px">${hermesFreshToken?'复制下方命令到运行 Hermes 的服务器终端粘贴执行，将自动下载 MCP 脚本、写入 ~/.hermes/config.yaml 并运行 hermes mcp test yomesh 连通性自检：':'请先点击第 1 步中的「一键生成密钥 & 安装命令」，下方命令会自动填入一次性完整密钥：'}</p>
        <pre class="code-preview mono" id="hm-cmd" style="white-space:pre-wrap;word-break:break-all;margin:10px 0">${esc(oneLineCmd)}</pre>
        <div class="filter-row">
          <button type="button" class="button ${hermesFreshToken?'primary':''}" id="hm-copy-cmd" ${hermesFreshToken?'':'disabled'}>复制一键安装命令</button>
        </div>
      </div>
      <div class="panel">
        <div class="section-title">
          <h3>支持的自然语言指令与 MCP 工具</h3>
          ${badge('5 个内置工具')}
        </div>
        <ul style="margin:8px 0 12px 18px;font-size:13px;line-height:1.8">
          <li><strong>查分流原因</strong>（<code>yomesh_diagnose_domain</code>）：“帮我查一下 <code>api.anthropic.com</code> 在 YoMesh 里命中了哪条规则、走哪个出口？”</li>
          <li><strong>一句话加规则</strong>（<code>yomesh_add_rule</code>）：“把 <code>claude.ai</code> 加到 AI 节点组”、“把 <code>192.168.8.0/24</code> 设为直连 DIRECT”</li>
          <li><strong>节点延迟测速</strong>（<code>yomesh_test_nodes</code>）：“帮我测速一下当前配置空间的节点，看看哪个香港节点延迟最低”</li>
          <li><strong>获取订阅链接</strong>（<code>yomesh_subscription_link</code>）：“把当前空间的 Mihomo 订阅链接发给我，我要填到 LocalDNS”</li>
          <li><strong>查看配置空间</strong>（<code>yomesh_list_spaces</code>）：“列出我当前在 YoMesh 里的所有配置空间和节点统计”</li>
        </ul>
        <details class="advanced-options">
          <summary>查看 ~/.hermes/config.yaml 自动写入结构</summary>
          <pre class="code-preview mono" style="margin-top:8px">${esc(manualYaml)}</pre>
        </details>
      </div>
      <p id="hm-error" class="error-message" role="alert">${esc(loadError)}</p>
    </div>`;
    $('#hm-rotate').onclick=async e=>{
      e.target.disabled=true;
      try{
        const res=await api('/api/integrations/hermes/rotate',{});
        status=res;hermesFreshToken=res.token;loadError='';paint();
        toast('已生成新的 Hermes 一键安装命令。');
      }catch(err){$('#hm-error').textContent=err.message;e.target.disabled=false}
    };
    if($('#hm-revoke'))$('#hm-revoke').onclick=async e=>{
      e.target.disabled=true;
      try{
        status=await api('/api/integrations/hermes/revoke',{});
        hermesFreshToken=null;loadError='';paint();
        toast('已停用 Hermes 接入密钥。');
      }catch(err){$('#hm-error').textContent=err.message;e.target.disabled=false}
    };
    if($('#hm-copy-cmd'))$('#hm-copy-cmd').onclick=async()=>{
      try{
        await navigator.clipboard.writeText(oneLineCmd);
        toast('一键安装命令已复制，去 Hermes 终端粘贴执行即可。');
      }catch{
        toast('请手动选中上方命令复制。');
      }
    };
  };
  paint();
}
function openHermesIntegration(){
  if(location.hash==='#hermes')renderHermesPage();
  else location.hash='hermes';
}
function paintSpaceCards(){
  const entries=spaceCatalog?.spaces||[];
  $('#content').innerHTML=`<div class="configuration-overview"><div><h2>代理配置与 LocalDNS 联动</h2><p>导入现有 Mihomo YAML / sing-box JSON 或添加订阅节点；生成订阅链接填入 LocalDNS 的代理管理器即可自动拉取更新。</p><div class="filter-row" style="margin-top:10px"><button type="button" class="button primary" id="space-import-config">导入现有配置</button><button type="button" class="button" id="space-open-subscription">获取订阅链接（联动 LocalDNS）</button></div></div><div><h2>Hermes 智能助手</h2><p>一行命令自动配好 <code>~/.hermes/config.yaml</code>，直接用自然语言查域名分流、加规则、测速节点。</p><div class="filter-row" style="margin-top:10px"><button type="button" class="button" id="space-open-hermes">配置 Hermes 接入</button></div></div></div><div id="space-list-status" class="error-message" role="status"></div><div class="space-grid">${entries.map(s=>{
    const current=s.id===spaceId,counts=s.counts||{},g=s.generation||{},issues=g.excluded_policies?.length||0;
    const actions=current
      ?`<button class="button primary" data-space-export="${esc(s.id)}">${actionIcon('download')}导出代理</button><button class="button" data-space-subscription="${esc(s.id)}">订阅链接</button><button class="button" data-space-configure="${esc(s.id)}">去配置规则</button>${resourceMenu(s.name,`<button data-space-edit="${esc(s.id)}">${s.native?'修改名称与说明':'编辑方案选用'}</button><button data-space-copy="${esc(s.id)}">复制为新方案</button><button class="danger-action" data-space-delete="${esc(s.id)}" disabled>${s.id==='default'?'默认方案保留':'切换后可删除'}</button>`)}`
      :`<button class="button primary" data-space-use="${esc(s.id)}">切换到此方案</button><button class="button" data-space-edit="${esc(s.id)}">${s.native?'改名 / 说明':'编辑选用'}</button><button class="button" data-space-copy="${esc(s.id)}">复制为新方案</button>${resourceMenu(s.name,`<button data-space-subscription="${esc(s.id)}">切换并查看订阅链接</button><button data-space-export="${esc(s.id)}">切换并导出代理</button><button class="danger-action" data-space-delete="${esc(s.id)}" ${s.id==='default'?'disabled':''}>${s.id==='default'?'默认方案保留':'删除此方案'}</button>`)}`;
    return `<article class="panel space-card ${current?'selected':''}" aria-label="${esc(s.name)}配置空间"><div class="space-card-heading"><div><h2>${esc(s.name)}</h2><p>${s.native?'Mihomo YAML':s.proxy==='sing-box'?'sing-box JSON':'Mihomo YAML'} · ${s.native?'原生引用与缓存':{inline:'内联规则',local:'本地文件引用',remote:'远程订阅引用'}[s.rule_output]||'内联规则'}</p></div>${current?badge('当前使用中','accent'):badge('未激活')}</div><p class="space-description">${esc(s.description||'节点组 + 规则 + 分流策略的生成方案')}</p><div class="space-counts"><span><b>${counts.groups||0}</b> 节点组</span><span><b>${counts.library||0}</b> 规则</span><span><b>${counts.policies||0}</b> 策略</span></div><p class="space-readiness ${g.error?'error-message':'generation-ready'}">${g.error?esc(g.error):g.valid?'代理选用检查通过':'等待代理检查'}${issues?` · 跳过 ${issues} 条策略`:''}</p><div class="space-card-actions">${actions}</div></article>`;
  }).join('')}</div><p class="preview-note">“当前使用中”表示左侧导航正在编辑该方案。切换方案或导出前都会自动执行原生内核校验。</p>`;
  $('#space-import-config').onclick=openConfigImport;
  $('#space-open-subscription').onclick=openSubscriptionLink;
  $('#space-open-hermes').onclick=openHermesIntegration;
  $$('[data-space-configure]').forEach(b=>b.onclick=()=>{location.hash='library'});
  $$('[data-space-use]').forEach(b=>b.onclick=()=>switchSpace(b.dataset.spaceUse).catch(error=>toast(error.message)));
  $$('[data-space-edit]').forEach(b=>b.onclick=()=>editSpace(b.dataset.spaceEdit));
  $$('[data-space-copy]').forEach(b=>b.onclick=()=>editSpace(null,b.dataset.spaceCopy));
  $$('[data-space-export]').forEach(b=>b.onclick=async()=>{try{await switchSpace(b.dataset.spaceExport);openConfigurationExport('proxy')}catch(error){toast(error.message)}});
  $$('[data-space-subscription]').forEach(b=>b.onclick=async()=>{try{await switchSpace(b.dataset.spaceSubscription);await openSubscriptionLink()}catch(error){toast(error.message)}});
  $$('[data-space-delete]').forEach(b=>b.onclick=()=>deleteSpace(b.dataset.spaceDelete));bindResourceMenus();
}
async function editSpace(ident=null,copyFrom=null){
  try{
    await saveSpaceDrafts();await refreshSpaceCatalog();
    const original=spaceCatalog.spaces.find(s=>s.id===ident),sourceId=ident||copyFrom||'default';
    const source=spaceCatalog.spaces.find(s=>s.id===sourceId),snapshot=await api('/api/workspace',undefined,sourceId);
    const e={id:ident,copyFrom,sourceId,revision:snapshot.revision,catalogRevision:spaceCatalog.revision,profile:snapshot.profile,usage:snapshot.usage,selection:clone(snapshot.profile.composition||{}),tab:'policies',query:'',only:false,pending:false,saving:false,preview:null,timer:null,serial:0,fields:{name:original?.name||(copyFrom?source.name+' 副本':''),description:original?.description||'',proxy:source?.proxy||'mihomo',rule_output:source?.rule_output||'inline'}};
    if(!original&&!copyFrom)e.selection={groups:[],rules:[],policies:[],fallback:'DIRECT'};
    spaceEditor=e;
    showDialog(ident?'编辑方案 · '+original.name:copyFrom?'复制配置空间':'新建配置空间',`<form id="space-form" class="space-editor-form"><div class="space-editor-scope">${badge('仅此方案','accent')} 选用共享内容，组合此设备需要的配置。</div><div class="space-editor-tabs" role="tablist" aria-label="方案编辑分区"></div><div class="space-editor-body"><section class="space-editor-main" id="space-editor-panel" role="tabpanel"></section><aside class="space-editor-summary" aria-label="生成内容检查"></aside></div><p id="space-form-error" class="error-message" role="alert"></p><footer class="space-editor-footer"><span>选用关系只保存到此方案</span><button type="button" class="button" id="space-form-cancel">取消</button><button type="submit" class="button" value="save">${ident?'保存方案':'创建方案'}</button><button type="submit" class="button primary" id="space-save-generate" value="generate" disabled>保存并生成</button></footer></form>`);
    const dialog=$('#dialog');dialog.classList.add('space-editor-dialog');
    const prevent=e=>{if(spaceEditor?.saving)e.preventDefault()};dialog.addEventListener('cancel',prevent);
    dialog.addEventListener('close',()=>{dialog.classList.remove('space-editor-dialog');dialog.removeEventListener('cancel',prevent);clearTimeout(e.timer);e.serial++;if(spaceEditor===e)spaceEditor=null},{once:true});
    $('#space-form-cancel').onclick=()=>dialog.close();$('#space-form').onsubmit=saveSpaceForm;
    paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview();
  }catch(error){toast(error.message)}
}
function paintSpaceEditorTabs(){
  const e=spaceEditor;
  $('.space-editor-tabs').innerHTML=Object.entries(editorKinds).map(([kind,label])=>`<button type="button" id="space-tab-${kind}" role="tab" aria-controls="space-editor-panel" aria-selected="${e.tab===kind}" tabindex="${e.tab===kind?'0':'-1'}" data-space-tab="${kind}">${label}${kind==='settings'?'':`<span>${e.selection[kind]?.length??spaceEditorRows(kind).length}</span>`}</button>`).join('');
  $$('[data-space-tab]').forEach(b=>{b.onclick=()=>{e.tab=b.dataset.spaceTab;e.query='';e.only=false;paintSpaceEditorTabs();paintSpaceEditorPanel();$('#space-tab-'+e.tab).focus()};b.onkeydown=ev=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key))return;ev.preventDefault();const keys=Object.keys(editorKinds),index=keys.indexOf(e.tab),next=ev.key==='Home'?0:ev.key==='End'?keys.length-1:(index+(ev.key==='ArrowRight'?1:-1)+keys.length)%keys.length;$$('[data-space-tab]')[next].click()}});
}
function paintSpaceEditorPanel(){
  const e=spaceEditor,key=e.tab,panel=$('#space-editor-panel');panel.setAttribute('aria-labelledby','space-tab-'+key);
  if(key==='settings'){
    panel.innerHTML=`<section class="space-form-section"><h3>方案信息</h3><label class="field">名称<input id="space-name" required maxlength="80" placeholder="例如：手机 / 电脑 / 家庭网关" value="${esc(e.fields.name)}"></label><label class="field">说明<input id="space-description" maxlength="500" value="${esc(e.fields.description)}"></label></section><section class="space-form-section"><h3>默认输出</h3><div class="fields"><label class="field">配置内核<select id="space-proxy"><option value="mihomo" ${e.fields.proxy==='mihomo'?'selected':''}>Mihomo · YAML</option><option value="sing-box" ${e.fields.proxy==='sing-box'?'selected':''}>sing-box · JSON</option></select></label><label class="field">规则输出方式<select id="space-output">${[['remote','远程订阅引用'],['local','本地文件引用'],['inline','内联规则']].map(([k,label])=>`<option value="${k}" ${e.fields.rule_output===k?'selected':''}>${label}</option>`).join('')}</select></label></div><p class="preview-note">生成时仍可重新选择；不兼容的内核能力会明确阻止导出。</p></section><section class="space-form-section"><h3>更多配置</h3><p>基础配置和入站属于代理方案；MosDNS 在独立 DNS 入口编辑与导出。</p></section>`;
    $('#space-name').oninput=ev=>{e.fields.name=ev.target.value};$('#space-description').oninput=ev=>e.fields.description=ev.target.value;$('#space-proxy').onchange=ev=>e.fields.proxy=ev.target.value;$('#space-output').onchange=ev=>e.fields.rule_output=ev.target.value;return;
  }
  const descriptions={policies:'先选择策略，再检查所需的节点组和规则。',groups:'选用出口；所需子组由服务端统一包含。',rules:'停用规则时，引用它的整条策略将跳过。'};
  panel.innerHTML=`${key==='policies'?`<div class="fields space-plan-fields"><label class="field">共享分流模板<select id="space-plan"><option value="">跟随共享默认模板</option>${(e.profile.routing_plans||[]).map(p=>`<option value="${esc(p.id)}" ${e.selection.plan===p.id?'selected':''}>${esc(planLabel(p))}</option>`).join('')}</select></label><label class="field">兜底出口<select id="space-fallback">${editorTargetOptions(e.selection.fallback||e.profile.routing_plans?.find(p=>p.id===e.selection.plan)?.fallback||e.profile.fallback)}</select></label></div>`:''}<div class="space-editor-banner"><span>${descriptions[key]}</span><button type="button" class="button" id="space-select-dependencies" disabled>选用所需资源</button></div><div class="toolbar"><label class="search">${icon('search')}<input type="search" id="space-search" aria-label="搜索${editorKinds[key]}" placeholder="搜索${editorKinds[key]}" value="${esc(e.query)}"></label><button type="button" class="scope-chip" id="space-only" aria-pressed="${e.only}">仅看已选</button></div><div class="space-selection-tools"><label class="check-row"><input type="checkbox" id="space-follow-all" ${e.selection[key]==null?'checked':''}>全部（含以后新增）</label><button type="button" class="text-button" id="space-select-visible">选中此列表</button></div><div id="space-options"></div>`;
  if($('#space-plan'))$('#space-plan').onchange=ev=>{const plan=e.profile.routing_plans?.find(p=>p.id===ev.target.value);if(plan)e.selection.plan=plan.id;else delete e.selection.plan;e.selection.policies=null;e.selection.fallback=plan?.fallback||e.profile.fallback;paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview()};
  if($('#space-fallback'))$('#space-fallback').onchange=ev=>{e.selection.fallback=ev.target.value;requestSpacePreview()};
  $('#space-search').oninput=ev=>{e.query=ev.target.value;paintSpaceOptions()};
  $('#space-only').onclick=()=>{e.only=!e.only;$('#space-only').setAttribute('aria-pressed',e.only);paintSpaceOptions()};
  $('#space-follow-all').onchange=ev=>{e.selection[key]=ev.target.checked?null:spaceEditorRows(key).map(row=>row.id);paintSpaceEditorTabs();paintSpaceOptions();requestSpacePreview()};
  $('#space-select-visible').onclick=()=>{const chosen=new Set(e.selection[key]??spaceEditorRows(key).map(row=>row.id));filteredSpaceOptions().forEach(row=>chosen.add(row.id));e.selection[key]=spaceEditorRows(key).filter(row=>chosen.has(row.id)).map(row=>row.id);$('#space-follow-all').checked=false;paintSpaceEditorTabs();paintSpaceOptions();requestSpacePreview()};
  $('#space-select-dependencies').onclick=()=>{if(e.pending||!e.preview?.recommended_selection)return;e.selection=clone(e.preview.recommended_selection);paintSpaceEditorTabs();paintSpaceEditorPanel();requestSpacePreview()};
  paintSpaceOptions();paintSpacePreview();
}
function filteredSpaceOptions(){const e=spaceEditor;return spaceEditorRows(e.tab).filter(row=>row.name.toLowerCase().includes(e.query.toLowerCase())&&(!e.only||editorSelected(e.tab,row.id)))}
function paintSpaceOptions(){
  const e=spaceEditor,key=e.tab,plan=e.selection.plan||e.profile.active_plan,views=e.usage?.plans.find(p=>p.id===plan)?.policies||[];
  $('#space-options').innerHTML=filteredSpaceOptions().map(row=>{const summary=key==='policies'?views.find(v=>v.id===row.id)?.summary||'分流策略':key==='rules'?`${row.entries.length.toLocaleString()} 条 · ${row.source?.type==='url'?'订阅规则':'匹配器 / 已存内容'}`:modes[row.mode];return `<label class="space-selection-row"><input type="checkbox" data-space-option="${esc(row.id)}" ${editorSelected(key,row.id)?'checked':''}><span><strong>${esc(row.name)}</strong><small>${esc(summary)}</small></span>${badge(key==='groups'?modes[row.mode]:key==='rules'?(row.source?.type==='url'?'订阅':'匹配器'):'策略')}</label>`}).join('')||'<div class="empty">没有符合条件的内容。</div>';
  $$('[data-space-option]').forEach(el=>el.onchange=()=>{const chosen=new Set(e.selection[key]??spaceEditorRows(key).map(row=>row.id));if(el.checked)chosen.add(el.dataset.spaceOption);else chosen.delete(el.dataset.spaceOption);e.selection[key]=spaceEditorRows(key).filter(row=>chosen.has(row.id)).map(row=>row.id);$('#space-follow-all').checked=false;paintSpaceEditorTabs();if(e.only&&!el.checked)paintSpaceOptions();requestSpacePreview()});
}
function requestSpacePreview(){
  const e=spaceEditor;if(!e)return;clearTimeout(e.timer);e.pending=true;const serial=++e.serial;paintSpacePreview();
  e.timer=setTimeout(async()=>{try{const preview=await api('/api/workspace/preview-selection',{selection:clone(e.selection),revision:e.revision,blank:!e.id&&!e.copyFrom},e.sourceId);if(spaceEditor!==e||serial!==e.serial)return;e.preview=preview;$('#space-form-error').textContent=''}catch(error){if(spaceEditor!==e||serial!==e.serial)return;e.preview={valid:false,error:error.message,counts:{}};$('#space-form-error').textContent=error.message}finally{if(spaceEditor===e&&serial===e.serial){e.pending=false;paintSpacePreview()}}},160);
}
function paintSpacePreview(){
  const e=spaceEditor;if(!e)return;
  const summary=$('.space-editor-summary');if(summary)summary.innerHTML=`<h3>此方案的生成内容</h3>${e.preview?generationSummaryMarkup(e.preview):'<p>正在检查选用关系…</p>'}${e.pending?'<p class="preview-note" role="status">正在更新检查结果…</p>':''}<p class="preview-note">保存后，生成时读取共享内容的最新版本。</p>`;
  const save=$('#space-save-generate');if(save)save.disabled=e.pending||e.saving||!e.preview?.valid;
  const dependencies=$('#space-select-dependencies');if(dependencies)dependencies.disabled=e.pending||!e.preview?.recommended_selection;
}
async function saveSpaceForm(ev){
  ev.preventDefault();const e=spaceEditor;if(!e||e.saving)return;
  if(!e.fields.name.trim()){e.tab='settings';paintSpaceEditorTabs();paintSpaceEditorPanel();$('#space-name').reportValidity();$('#space-name').focus();return}
  const generate=ev.submitter?.value==='generate';if(generate&&(e.pending||!e.preview?.valid))return;
  e.saving=true;$$('#space-form button,#space-form input,#space-form select').forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;
  try{
    const request={space:{...e.fields,composition:clone(e.selection)},revision:e.catalogRevision,workspace_revision:e.revision};if(e.id)request.id=e.id;else if(e.copyFrom)request.copy_from=e.copyFrom;
    const result=await api('/api/spaces/save',request,e.sourceId);spaceCatalog=result;const ident=e.id||result.id;
    $('#dialog').close();if(ident===spaceId)await reloadWorkspace();else if(!e.id||generate)await switchSpace(ident);else render();
    if(generate)$('#export').click();else toast('生成方案已保存。');
  }catch(error){if($('#space-form-error'))$('#space-form-error').textContent=error.message;else toast(error.message)}
  finally{e.saving=false;$('#close-dialog').disabled=false;if(spaceEditor===e){$$('#space-form button,#space-form input,#space-form select').forEach(el=>el.disabled=false);paintSpacePreview()}}
}
