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
const MIHOMO_TEMPLATES={
  standard:{
    name:'Mihomo 标准分流方案',
    desc:'包含港/台/日/新/美分组与常用分流规则模板，导入后可在界面自由配置订阅与规则',
    yaml:`mixed-port: 17890
allow-lan: true
mode: rule
log-level: info
ipv6: true

proxies:
  - name: "示例节点-导入后请在订阅来源添加真实订阅"
    type: trojan
    server: example.com
    port: 443
    password: "password"
    sni: example.com
    udp: true

proxy-groups:
  - name: "🚀 默认代理"
    type: select
    proxies:
      - "HK 香港节点"
      - "TW 台湾节点"
      - "JP 日本节点"
      - "SG 新加坡节点"
      - "US 美国节点"
      - "DIRECT"
  - name: "HK 香港节点"
    type: url-test
    proxies: ["示例节点-导入后请在订阅来源添加真实订阅"]
    filter: "(?i)(港|HK|Hong\\\\s*Kong)"
    interval: 300
  - name: "TW 台湾节点"
    type: url-test
    proxies: ["示例节点-导入后请在订阅来源添加真实订阅"]
    filter: "(?i)(台|TW|Taiwan)"
    interval: 300
  - name: "JP 日本节点"
    type: url-test
    proxies: ["示例节点-导入后请在订阅来源添加真实订阅"]
    filter: "(?i)(日|JP|Japan)"
    interval: 300
  - name: "SG 新加坡节点"
    type: url-test
    proxies: ["示例节点-导入后请在订阅来源添加真实订阅"]
    filter: "(?i)(新|SG|Singapore|狮城)"
    interval: 300
  - name: "US 美国节点"
    type: url-test
    proxies: ["示例节点-导入后请在订阅来源添加真实订阅"]
    filter: "(?i)(美|US|United\\\\s*States|America)"
    interval: 300

rules:
  - DOMAIN-SUFFIX,openai.com,US 美国节点
  - DOMAIN-SUFFIX,anthropic.com,US 美国节点
  - DOMAIN-SUFFIX,claude.ai,US 美国节点
  - DOMAIN-SUFFIX,youtube.com,HK 香港节点
  - DOMAIN-SUFFIX,googlevideo.com,HK 香港节点
  - DOMAIN-SUFFIX,netflix.com,SG 新加坡节点
  - DOMAIN-SUFFIX,t.me,SG 新加坡节点
  - DOMAIN-SUFFIX,telegram.org,SG 新加坡节点
  - DOMAIN-SUFFIX,github.com,🚀 默认代理
  - DOMAIN-SUFFIX,githubusercontent.com,🚀 默认代理
  - DOMAIN-SUFFIX,apple.com,DIRECT
  - DOMAIN-SUFFIX,icloud.com,DIRECT
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,qq.com,DIRECT
  - DOMAIN-SUFFIX,bilibili.com,DIRECT
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - MATCH,🚀 默认代理`
  },
  minimal:{
    name:'Mihomo 极简分流方案',
    desc:'包含默认代理组与基础直连分流模板，适合轻量自定义',
    yaml:`mixed-port: 17890
allow-lan: true
mode: rule
log-level: info

proxies:
  - name: "示例节点-导入后请在订阅来源添加真实订阅"
    type: trojan
    server: example.com
    port: 443
    password: "password"
    sni: example.com
    udp: true

proxy-groups:
  - name: "🚀 默认代理"
    type: select
    proxies:
      - "示例节点-导入后请在订阅来源添加真实订阅"
      - "DIRECT"

rules:
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,qq.com,DIRECT
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - MATCH,🚀 默认代理`
  }
};
async function openConfigImport(initialTemplate=''){
  try{await saveSpaceDrafts();await refreshSpaceCatalog()}catch(error){toast(error.message);return}
  showDialog(initialTemplate?'从 Mihomo 模板创建方案':'导入 Mihomo 配置 / 选择模板',`<form id="config-import-form">
    <p class="scope-editor-note">${badge('独立新方案','accent')} 支持直接上传或粘贴 Mihomo (<code>.yaml</code>) 配置、配置包 (<code>.zip</code>)，也可点击下方模板填入后自行调整再导入。</p>
    <div class="preset-pill-box">
      <div class="preset-pill-header"><span>📦 载入 Mihomo 配置模板（点击填入下方编辑框，可自由修改后再导入）：</span></div>
      <div class="preset-pill-list">
        <button type="button" class="preset-pill" data-ci-tpl="standard">📄 标准地区分流模板（港/台/日/新/美 + 常用规则）</button>
        <button type="button" class="preset-pill" data-ci-tpl="minimal">🪶 极简轻量模板（默认代理 + 国内直连）</button>
      </div>
    </div>
    <div class="fields">
      <label class="field">方案名称<input id="ci-name" required maxlength="80" placeholder="例如：主力 Mihomo 方案" value="导入 Mihomo 配置"></label>
      <label class="field">说明（可选）<input id="ci-description" maxlength="500" placeholder="例如：从 Mihomo YAML 导入或基于模板创建"></label>
    </div>
    <label class="field">选择 Mihomo 配置文件（.yaml / .yml / .zip / .json）<input id="ci-file" type="file" accept=".yaml,.yml,.json,.zip"></label>
    <label class="field">或者直接粘贴 / 编辑 Mihomo 配置内容（YAML）<textarea id="ci-text" class="mono" rows="8" spellcheck="false" placeholder="粘贴 Mihomo config.yaml 内容，或点击上方「📦 载入 Mihomo 配置模板」…"></textarea></label>
    <details class="advanced-options">
      <summary>高级导入选项（模式与 LocalDNS 附加）</summary>
      <label class="field" style="margin-top:10px">空间类型<select id="ci-mode">
        <option value="auto">自动识别（推荐：标准 Mihomo 配置可自由编辑节点组与规则）</option>
        <option value="native">Mihomo 原生空间（100% 原样保留 proxy-providers / rule-providers）</option>
        <option value="portable">通用标准空间（支持同时导出 Mihomo YAML 与 sing-box JSON）</option>
      </select></label>
      <label class="field">附加 LocalDNS 配置（可选，支持 localdns.json 或 .zip）<input id="ci-localdns-file" type="file" accept=".json,.zip"></label>
      <label class="check-row"><input type="checkbox" id="ci-draft" checked>允许先导入为草稿（后续可在界面继续补充订阅和节点）</label>
    </details>
    <p id="ci-error" class="error-message" role="alert"></p>
    <div class="dialog-actions">
      <button type="button" class="button" id="ci-cancel">取消</button>
      <button type="submit" class="button primary" id="ci-submit">导入并创建方案</button>
    </div>
  </form>`);
  const applyTpl=key=>{
    const tpl=MIHOMO_TEMPLATES[key];
    if(!tpl)return;
    $('#ci-name').value=tpl.name;
    $('#ci-description').value=tpl.desc;
    $('#ci-text').value=tpl.yaml;
    $('#ci-file').value='';
  };
  $$('[data-ci-tpl]').forEach(btn=>btn.onclick=()=>{
    applyTpl(btn.dataset.ciTpl);
    toast('已填入 Mihomo 模板，可直接点击右下角「导入并创建方案」或先修改内容。');
  });
  if(initialTemplate)applyTpl(initialTemplate);
  $('#ci-file').onchange=e=>{
    const f=e.target.files?.[0];
    if(f&&($('#ci-name').value==='导入 Mihomo 配置'||!$('#ci-name').value.trim())){
      $('#ci-name').value=f.name.replace(/\.(yaml|yml|json|zip)$/i,'')||'导入 Mihomo 配置';
    }
  };
  $('#ci-cancel').onclick=()=>$('#dialog').close();
  $('#config-import-form').onsubmit=async e=>{
    e.preventDefault();
    const btn=$('#ci-submit');btn.disabled=true;$('#ci-error').textContent='';
    try{
      const file=$('#ci-file').files?.[0],text=$('#ci-text').value.trim(),dnsFile=$('#ci-localdns-file').files?.[0];
      if(!file&&!text)throw Error('请选择 Mihomo 配置文件、粘贴 YAML 内容，或点击上方模板按钮载入模板。');
      if(file&&file.size>20*1024*1024)throw Error('配置文件不能超过 20 MiB。');
      if(dnsFile&&dnsFile.size>20*1024*1024)throw Error('LocalDNS 配置文件不能超过 20 MiB。');
      let chosenName=$('#ci-name').value.trim()||'导入 Mihomo 配置';
      const existingNames=new Set((spaceCatalog?.spaces||[]).map(s=>s.name));
      if(existingNames.has(chosenName)){
        let n=2;
        while(existingNames.has(`${chosenName} (${n})`))n++;
        chosenName=`${chosenName} (${n})`;
      }
      const payload={
        name:chosenName,
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
        payload.filename='pasted-config.yaml';
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
const DEFAULT_PRESET_REGIONS=[
  {key:'hk',name:'HK 香港节点',badge:'🇭🇰 香港节点 (自动测速)',filter:'(?i)(港|HK|Hong\\s*Kong)'},
  {key:'tw',name:'TW 台湾节点',badge:'🇹🇼 台湾节点 (自动测速)',filter:'(?i)(台|TW|Taiwan|Taipei)'},
  {key:'jp',name:'JP 日本节点',badge:'🇯🇵 日本节点 (自动测速)',filter:'(?i)(日|JP|Japan|Tokyo|Osaka)'},
  {key:'sg',name:'SG 新加坡节点',badge:'🇸🇬 新加坡节点 (自动测速)',filter:'(?i)(新|SG|Singapore|狮城)'},
  {key:'us',name:'US 美国节点',badge:'🇺🇸 美国节点 (自动测速)',filter:'(?i)(美|US|United\\s*States|America|Los\\s*Angeles|San\\s*Jose|Seattle)'}
];
const DEFAULT_PRESET_RULES=[
  {key:'ai',name:'AI 人工智能',pill:'🤖 AI (OpenAI/Claude)',desc:'🤖 AI 全家桶 (OpenAI/Claude) → 🇺🇸 美国节点',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ai-!cn.yaml',ip_url:'',default_region:'us'},
  {key:'youtube',name:'YouTube 视频',pill:'🎬 YouTube',desc:'🎬 YouTube 视频 → 🇭🇰 香港节点',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/youtube.yaml',ip_url:'',default_region:'hk'},
  {key:'netflix',name:'Netflix 奈飞',pill:'🍿 Netflix',desc:'🍿 Netflix 奈飞 → 🇸🇬 新加坡节点',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/netflix.yaml',ip_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/netflix.yaml',default_region:'sg'},
  {key:'telegram',name:'Telegram 电报',pill:'💬 Telegram',desc:'💬 Telegram 电报 → 🇸🇬 新加坡节点',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/telegram.yaml',ip_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/telegram.yaml',default_region:'sg'},
  {key:'github',name:'GitHub 开发者',pill:'🐙 GitHub',desc:'🐙 GitHub 开发者 → 🚀 默认代理',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/github.yaml',ip_url:'',default_region:'proxy'},
  {key:'apple',name:'Apple 苹果服务',pill:'🍎 Apple 服务',desc:'🍎 Apple / 微软服务 → DIRECT 直连',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/apple.yaml',ip_url:'',default_region:'direct'},
  {key:'cn',name:'国内域名与局域网直连',pill:'🏠 国内域名+IP直连',desc:'🏠 国内域名 & 局域网 IP → DIRECT 直连',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/cn.yaml',ip_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/cn.yaml',default_region:'direct'},
  {key:'adblock',name:'广告与追踪拦截',pill:'🛡️ 广告拦截',desc:'🛡️ 广告拦截 → REJECT',domain_url:'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ads-all.yaml',ip_url:'',default_region:'reject'}
];
let spacePresetsCache={regions:DEFAULT_PRESET_REGIONS,rules:DEFAULT_PRESET_RULES,controllers:{},links_by_space:{}};
async function refreshSpacePresets(){
  try{
    const res=await api('/api/spaces/presets');
    if(res&&typeof res==='object'){
      spacePresetsCache={
        regions:res.regions?.length?res.regions:DEFAULT_PRESET_REGIONS,
        rules:res.rules?.length?res.rules:DEFAULT_PRESET_RULES,
        controllers:res.controllers||{},
        links_by_space:res.links_by_space||{}
      };
    }
  }catch{}
  return spacePresetsCache;
}
async function copyTextSafe(text,okMsg='已复制到剪贴板。'){
  try{
    if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);
    else{
      const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';
      document.body.append(area);area.focus();area.select();document.execCommand('copy');area.remove();
    }
    toast(okMsg);
  }catch{
    toast('复制失败，请手动复制：'+text);
  }
}
async function copySpaceSubscriptionDirect(targetSpaceId){
  const s=(spaceCatalog?.spaces||[]).find(item=>item.id===targetSpaceId)||activeSpace();
  if(!s)return;
  const existing=(spacePresetsCache.links_by_space?.[s.id]||[]).at(-1);
  if(existing?.url){
    await copyTextSafe(existing.url,`已复制「${s.name}」的持久订阅链接！`);
    return;
  }
  try{
    toast(`正在为「${s.name}」生成持久订阅链接…`);
    const snap=s.id===spaceId?liveWorkspace:await api('/api/workspace',undefined,s.id);
    const created=await api('/api/workspace/subscriptions',{
      proxy:s.native?'mihomo':(s.proxy||'mihomo'),
      base_url:location.origin,
      rule_address:'original',
      revision:snap.revision
    },s.id);
    spacePresetsCache.links_by_space??={};
    spacePresetsCache.links_by_space[s.id]=[created];
    if(page==='spaces')paintSpaceCards();
    await copyTextSafe(created.url,`已生成并复制「${s.name}」的持久订阅链接！`);
  }catch(error){
    toast(error.message);
  }
}
function openClientControllerModal(targetSpaceId){
  const s=(spaceCatalog?.spaces||[]).find(item=>item.id===targetSpaceId)||activeSpace();
  if(!s)return;
  const ctrl=spacePresetsCache.controllers?.[s.id]||{enabled:false,url:'',secret:''};
  showDialog(`客户端自动热重载 · ${s.name}`,`<form id="client-ctrl-form">
    <p class="scope-editor-note">${badge('零 Agent 实时推送','accent')} 绑定局域网内客户端（如 OpenWrt / Clash Party / 软路由 Mihomo）的 External Controller 地址后，在网页改规则或用 Hermes 一句话加规则时，YoMesh 会自动调用 <code>PUT /configs?force=true</code> 触发客户端 0 秒热重载。</p>
    <label class="check-row" style="margin:10px 0"><input type="checkbox" id="cc-enabled" ${ctrl.enabled?'checked':''}>启用保存后自动热重载推送</label>
    <div class="fields">
      <label class="field">Mihomo External Controller 地址<input id="cc-url" type="url" placeholder="例如：http://192.168.8.2:9090" value="${esc(ctrl.url||'')}"><small>对应 Mihomo 配置中的 <code>external-controller</code> 地址。</small></label>
      <label class="field">Controller Secret 密钥（可选）<input id="cc-secret" type="password" placeholder="若未设置 secret 可留空" value="${esc(ctrl.secret||'')}"><small>对应 Mihomo 配置中的 <code>secret</code> 字段。</small></label>
    </div>
    ${ctrl.last_status?`<p class="preview-note">上次连接状态：<strong>${esc(ctrl.last_status)}</strong>${ctrl.last_error?' · '+esc(ctrl.last_error):''}</p>`:''}
    <p id="cc-error" class="error-message" role="alert"></p>
    <div class="dialog-actions">
      <button type="button" class="button" id="cc-cancel">取消</button>
      <button type="submit" class="button primary" id="cc-submit">保存并检测连通性</button>
    </div>
  </form>`);
  $('#cc-cancel').onclick=()=>$('#dialog').close();
  $('#client-ctrl-form').onsubmit=async e=>{
    e.preventDefault();
    const btn=$('#cc-submit');btn.disabled=true;$('#cc-error').textContent='';
    try{
      const enabled=$('#cc-enabled').checked,url=$('#cc-url').value.trim(),secret=$('#cc-secret').value.trim();
      if(enabled&&!url)throw Error('启用自动热重载时，请填写客户端 Controller 地址（如 http://192.168.8.2:9090）。');
      const saved=await api('/api/spaces/client-controller',{space_id:s.id,enabled,url,secret});
      spacePresetsCache.controllers??={};
      spacePresetsCache.controllers[s.id]=saved;
      $('#dialog').close();
      if(page==='spaces')paintSpaceCards();
      toast(saved.enabled?(saved.last_status==='connected'?'已连接客户端控制器！今后改规则将自动热重载。':`控制器设置已保存（探活结果：${saved.last_error||saved.last_status}）`):'已关闭此方案的客户端自动重载。');
    }catch(error){
      $('#cc-error').textContent=error.message;
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
    const mcpEndpoint=`${baseUrl}/mcp?token=${tokenDisplay}`;
    const manualYaml=`mcp_servers:\n  yomesh:\n    command: python3\n    args:\n      - /root/.local/share/yomesh/yomesh_hermes_mcp.py\n      - --config\n      - /root/.config/yomesh/hermes.json\n    enabled: true`;
    $('#content').innerHTML=`<div id="hermes-integration-panel" style="max-width:860px">
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title">
          <h3>第 1 步：生成 Hermes / MCP 专属接入密钥</h3>
          ${badge(status.configured?'已启用':'未启用',status.configured?'accent':'')}
        </div>
        <p class="preview-note" style="margin-top:6px">连接后，你可以直接用自然语言查域名分流原因、一句话向 YoMesh 置顶添加规则（支持批量域名与模糊节点组名，如“美国”自动匹配“US 美国节点”）、测速节点或获取订阅链接；若绑定了客户端控制器还会 0 秒触发热重载。</p>
        <p class="preview-note" style="margin-top:6px">${status.configured?`当前生效密钥前缀：<code>${esc(status.prefix||'ymh_')}***</code>${status.last_used_at?' · 最近调用：'+esc(serverTime(status.last_used_at)):''}`:'尚未生成接入密钥，请点击下方按钮一键生成。'}</p>
        <div class="filter-row" style="margin-top:12px">
          <button type="button" class="button primary" id="hm-rotate">${status.configured?'重新生成一键接入命令（旧密钥立即失效）':'一键生成密钥 & 安装命令'}</button>
          ${status.configured?'<button type="button" class="button danger-button" id="hm-revoke">停用 Hermes 接入</button>':''}
        </div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title">
          <h3>第 2 步（方式 A）：在运行 Hermes 的机器终端执行（一行自动配好）</h3>
          ${hermesFreshToken?badge('已填入最新密钥 · 请复制执行','accent'):badge('等待生成新密钥')}
        </div>
        <p class="preview-note" style="margin-top:6px">${hermesFreshToken?'复制下方命令到运行 Hermes 的服务器终端粘贴执行，将自动下载 MCP 脚本、写入 ~/.hermes/config.yaml 并运行 hermes mcp test yomesh 连通性自检：':'请先点击第 1 步中的「一键生成密钥 & 安装命令」，下方命令会自动填入一次性完整密钥：'}</p>
        <pre class="code-preview mono" id="hm-cmd" style="white-space:pre-wrap;word-break:break-all;margin:10px 0">${esc(oneLineCmd)}</pre>
        <div class="filter-row">
          <button type="button" class="button ${hermesFreshToken?'primary':''}" id="hm-copy-cmd" ${hermesFreshToken?'':'disabled'}>复制一键安装命令</button>
        </div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title">
          <h3>第 2 步（方式 B）：Streamable HTTP 标准 MCP 地址（免装脚本直连）</h3>
          ${badge('POST /mcp · JSON-RPC 2.0','accent')}
        </div>
        <p class="preview-note" style="margin-top:6px">支持 Streamable HTTP 的 MCP 客户端（如 Claude Code、Cursor、Cherry Studio 等）无需安装本地脚本，直接填入以下 HTTP MCP 端点即可：</p>
        <pre class="code-preview mono" id="hm-mcp-url" style="white-space:pre-wrap;word-break:break-all;margin:10px 0">${esc(mcpEndpoint)}</pre>
        <div class="filter-row">
          <button type="button" class="button" id="hm-copy-mcp" ${hermesFreshToken?'':'disabled'}>复制 HTTP MCP 地址</button>
        </div>
      </div>
      <div class="panel">
        <div class="section-title">
          <h3>支持的自然语言指令与 MCP 工具</h3>
          ${badge('5 个内置工具 · 规则自动置顶')}
        </div>
        <ul style="margin:8px 0 12px 18px;font-size:13px;line-height:1.8">
          <li><strong>查分流原因</strong>（<code>yomesh_diagnose_domain</code>）：“帮我查一下 <code>api.anthropic.com</code> 在 YoMesh 里命中了哪条规则、走哪个出口？”</li>
          <li><strong>一句话置顶加规则</strong>（<code>yomesh_add_rule</code>）：“把 <code>claude.ai, cursor.sh</code> 加到美国节点”、“把 <code>192.168.8.0/24</code> 设为直连 DIRECT”（新规则自动置顶到第 1 条，并支持模糊匹配节点组）</li>
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
        toast('已生成新的 Hermes 一键安装命令与 HTTP MCP 地址。');
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
    if($('#hm-copy-cmd'))$('#hm-copy-cmd').onclick=()=>copyTextSafe(oneLineCmd,'一键安装命令已复制，去 Hermes 终端粘贴执行即可。');
    if($('#hm-copy-mcp'))$('#hm-copy-mcp').onclick=()=>copyTextSafe(mcpEndpoint,'Streamable HTTP MCP 地址已复制。');
  };
  paint();
}
function openHermesIntegration(){
  if(location.hash==='#hermes')renderHermesPage();
  else location.hash='hermes';
}
let spacePresetsLoadedOnce=false;
function paintSpaceCards(){
  if(!spacePresetsLoadedOnce){
    spacePresetsLoadedOnce=true;
    refreshSpacePresets().then(()=>{if(page==='spaces')paintSpaceCards()});
  }
  const entries=spaceCatalog?.spaces||[];
  $('#content').innerHTML=`<div class="configuration-overview"><div><h2>Mihomo 配置导入、模板与导出</h2><p>支持直接导入现有 Mihomo YAML 配置，或选用内置 Mihomo 模板新建配置空间后按需配置订阅、分组与规则；随时一键导出 <code>mihomo.yaml</code> 或复制持久订阅链接。</p><div class="filter-row" style="margin-top:10px"><button type="button" class="button primary" id="space-import-config">📥 导入 Mihomo 配置</button><button type="button" class="button" id="space-template-create">📦 从 Mihomo 模板新建</button><button type="button" class="button" id="space-export-current">📤 导出当前 Mihomo 配置</button><button type="button" class="button" id="space-open-subscription">高级订阅链接设置</button></div></div><div><h2>Hermes 智能助手 & HTTP MCP</h2><p>一行命令配好 Hermes 或直连 <code>/mcp</code> 接口，用自然语言查分流、一句话置顶加规则并自动热重载客户端。</p><div class="filter-row" style="margin-top:10px"><button type="button" class="button" id="space-open-hermes">配置 Hermes / MCP 接入</button></div></div></div><div id="space-list-status" class="error-message" role="status"></div><div class="space-grid">${entries.map(s=>{
    const current=s.id===spaceId,counts=s.counts||{},g=s.generation||{},issues=g.excluded_policies?.length||0;
    const savedLink=(spacePresetsCache.links_by_space?.[s.id]||[]).at(-1);
    const linkPreview=savedLink?.url||`${location.origin}/s/<点击右侧按钮一键生成并复制>/${s.proxy==='sing-box'&&!s.native?'sing-box.json':'mihomo.yaml'}`;
    const ctrl=spacePresetsCache.controllers?.[s.id];
    const ctrlConnected=ctrl?.enabled&&ctrl?.url;
    const actions=current
      ?`<button class="button primary" data-space-configure="${esc(s.id)}">去配置规则</button><button class="button" data-space-export="${esc(s.id)}">${actionIcon('download')}导出 Mihomo 配置</button><button class="button" data-space-subscription="${esc(s.id)}">链接高级设置</button>${resourceMenu(s.name,`<button data-space-edit="${esc(s.id)}">${s.native?'修改名称与说明':'编辑方案选用'}</button><button data-space-copy="${esc(s.id)}">复制为新方案</button><button class="danger-action" data-space-delete="${esc(s.id)}" disabled>${s.id==='default'?'默认方案保留':'切换后可删除'}</button>`)}`
      :`<button class="button primary" data-space-use="${esc(s.id)}">切换到此方案</button><button class="button" data-space-edit="${esc(s.id)}">${s.native?'改名 / 说明':'编辑选用'}</button><button class="button" data-space-copy="${esc(s.id)}">复制为新方案</button>${resourceMenu(s.name,`<button data-space-subscription="${esc(s.id)}">切换并查看订阅链接</button><button data-space-export="${esc(s.id)}">切换并导出 Mihomo 配置</button><button class="danger-action" data-space-delete="${esc(s.id)}" ${s.id==='default'?'disabled':''}>${s.id==='default'?'默认方案保留':'删除此方案'}</button>`)}`;
    return `<article class="panel space-card ${current?'selected':''}" aria-label="${esc(s.name)}配置空间"><div class="space-card-heading"><div><h2>${esc(s.name)}</h2><p>${s.native?'Mihomo YAML':s.proxy==='sing-box'?'sing-box JSON':'Mihomo YAML'} · ${s.native?'原生引用与缓存':{inline:'内联规则',local:'本地文件引用',remote:'远程订阅引用'}[s.rule_output]||'内联规则'}</p></div>${current?badge('当前使用中','accent'):badge('未激活')}</div><p class="space-description">${esc(s.description||'节点组 + 规则 + 分流策略的生成方案')}</p><div class="space-counts"><span><b>${counts.groups||0}</b> 节点组</span><span><b>${counts.library||0}</b> 规则</span><span><b>${counts.policies||0}</b> 策略</span></div><div class="space-inline-sub-box"><div class="space-inline-sub-label"><span>🔗 专属持久订阅链接（填入客户端 / LocalDNS 自动拉取更新）</span></div><div class="space-inline-sub-row"><input type="text" class="mono" readonly value="${esc(linkPreview)}" aria-label="${esc(s.name)}订阅链接"><button type="button" class="button primary" data-space-copy-sub="${esc(s.id)}">📋 直接复制</button></div></div><div class="space-inline-ctrl-bar"><div class="space-inline-ctrl-info"><span>🔄 客户端自动重载：</span>${ctrlConnected?`<strong style="color:#34d399">● 已绑定 (${esc(ctrl.url)}) · 改规则 0 秒热重载</strong>`:`<span>未绑定（可选填 Mihomo Controller，改规则自动热重载）</span>`}</div><div class="filter-row" style="gap:8px">${ctrlConnected?`<button type="button" class="text-button" data-space-reload-now="${esc(s.id)}">立即重载</button>`:''}<button type="button" class="text-button" data-space-controller="${esc(s.id)}">${ctrlConnected?'修改控制器':'绑定客户端控制器'}</button></div></div><p class="space-readiness ${g.error?'error-message':'generation-ready'}">${g.error?esc(g.error):g.valid?'代理选用检查通过':'等待代理检查'}${issues?` · 跳过 ${issues} 条策略`:''}</p><div class="space-card-actions">${actions}</div></article>`;
  }).join('')}</div><p class="preview-note">“当前使用中”表示左侧导航正在编辑该方案。切换方案或导出前都会自动执行原生内核校验。</p>`;
  $('#space-import-config').onclick=()=>openConfigImport();
  $('#space-template-create').onclick=()=>openConfigImport('standard');
  $('#space-export-current').onclick=()=>openConfigurationExport('proxy');
  $('#space-open-subscription').onclick=openSubscriptionLink;
  $('#space-open-hermes').onclick=openHermesIntegration;
  $$('[data-space-copy-sub]').forEach(b=>b.onclick=()=>copySpaceSubscriptionDirect(b.dataset.spaceCopySub));
  $$('[data-space-controller]').forEach(b=>b.onclick=()=>openClientControllerModal(b.dataset.spaceController));
  $$('[data-space-reload-now]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{
      const res=await api('/api/spaces/client-controller',{action:'reload',space_id:b.dataset.spaceReloadNow});
      toast(res.reloaded?'已触发客户端 0 秒热重载！':`热重载未成功：${res.error||res.reason||'请检查客户端 Controller 地址'}`);
    }catch(err){toast(err.message)}
    finally{b.disabled=false}
  });
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
