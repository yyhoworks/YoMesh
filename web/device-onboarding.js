'use strict';

// Setup material belongs next to the action that needs it. Downloads are
// explicit; generated credentials never enter URLs or browser storage.
function deviceServerGuide(){
  return `<details class="device-guide"><summary>新服务器如何接入？</summary>
    <ol class="device-steps">
      <li><strong>准备目标服务器</strong><p>需要 Linux、Python 3.11+、SSH，以及 OpenRC 或 systemd。先安装 Mihomo、LocalDNS，准备能正常启动的配置与服务。</p></li>
      <li><strong>填写名称与 IP 并核对指纹</strong><p>点击“连接服务器”，填写名称与 IP（常用路径与服务已预填标准值，安装时也会自动识别）。到目标服务器终端执行以下命令核对 SSH 主机指纹：</p><pre>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256</pre></li>
      <li><strong>复制一行命令完成安装</strong><p>生成设备密钥后，复制页面生成的自包含一键安装命令，在目标服务器的 root 终端粘贴回车即可自动完成自检与受限执行器安装（也支持备用下载四个文件上传安装）。</p></li>
      <li><strong>一键连接并同步</strong><p>点击弹窗底部“我已安装，立即连接并导入配置”，即可自动读取服务器配置并打开独立配置空间。后续修改配置时，直接点击工作台顶栏“同步到设备”即可一键预检发布。</p></li>
    </ol></details>`;
}

function setupDownload(filename,text){
  const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=filename;
  document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function bindSetupCopies(root=$('#dialog')){
  $$('[data-setup-copy]',root).forEach(button=>button.onclick=async()=>{
    const target=document.getElementById(button.dataset.setupCopy),text=target.value??target.textContent;
    try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);
      else{target.focus();target.select();if(!document.execCommand('copy'))throw Error()}
      toast('已复制。');
    }catch{target.focus();target.select();toast('请复制已选中的内容。')}
  });
}

function showDeviceInstall(result,settings,stateDir,validationEnv){
  const policy=result.target_policy||{format:'yomesh-executor-v1',manager:settings.manager,mihomo_home:settings.mihomo_home,mihomo_config:settings.mihomo_config,mihomo_binary:settings.mihomo_binary,localdns_root:settings.localdns_root,localdns_binary:settings.localdns_binary,state_dir:stateDir,services:settings.services,validation_env:validationEnv};
  const target=JSON.stringify(policy,null,2)+'\n';
  const manualCmd=`cd /tmp/yomesh-setup\npython3 device_installer.py target.json device.pub --check\npython3 device_installer.py target.json device.pub`;
  const oneLineCmd=result.install_command||manualCmd;
  showDialog('安装受限执行器 · '+result.device.name,`<div class="device-install"><p class="notice">已生成设备专用密钥。复制下面的一键安装命令到目标服务器终端执行，即可直接连接并导入配置。</p><ol class="device-steps"><li><strong>在目标服务器（root 终端）执行一键安装</strong><p>此命令已内联包含设备专用公钥、目标设置与受限执行器，无需手动上传文件，也不要求目标服务器反向连接 YoMesh：</p><label class="field">目标服务器一键安装命令<textarea id="device-install-command" rows="4" readonly spellcheck="false">${esc(oneLineCmd)}</textarea></label><div class="setup-downloads"><button type="button" class="button primary" data-setup-copy="device-install-command">复制一键安装命令</button></div><p>粘贴执行后，看到 <code>"installed": true</code> 即表示受限执行器已安装完成。</p><details class="advanced-options"><summary>备用方式：下载四个安装文件手动上传（SFTP / WinSCP）</summary><div class="setup-downloads"><button type="button" class="button" id="device-download-key">下载 device.pub</button><button type="button" class="button" id="device-download-target">下载 target.json</button><a class="button" href="/api/setup/device_installer.py" download>下载安装脚本</a><a class="button" href="/api/setup/device_executor.py" download>下载执行器脚本</a></div><label class="field">手动上传后执行命令<textarea id="device-manual-command" rows="3" readonly>${esc(manualCmd)}</textarea></label><button type="button" class="button" data-setup-copy="device-manual-command">复制手动安装命令</button><label class="field">设备专用公钥<textarea rows="3" readonly>${esc(result.public_key)}</textarea></label><label class="field">target.json<textarea rows="8" readonly>${esc(target)}</textarea></label></details></li><li><strong>立即连接并导入服务器配置</strong><p>在服务器执行完安装命令后，直接点击下方“我已安装，立即连接并导入配置”，系统会自动检查连接、拉取配置并创建独立工作台空间。</p></li></ol><p class="preview-note">安装会自动备份并更新 root 的 authorized_keys。设备密钥仅允许调用受限执行器；YoMesh 不保存 root 密码。</p><p class="device-status" id="device-install-status" role="status" hidden></p><p class="error-message" id="device-install-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-install-done">稍后手动读取</button><button type="button" class="button primary" id="device-install-connect">我已安装，立即连接并导入配置</button></div></div>`);
  $('#device-download-key').onclick=()=>setupDownload('device.pub',result.public_key+'\n');
  $('#device-download-target').onclick=()=>setupDownload('target.json',target);
  $('#device-install-done').onclick=()=>$('#dialog').close();
  $('#device-install-connect').onclick=async event=>{
    if(deviceBusy)return;
    const button=event.currentTarget,statusEl=$('#device-install-status'),errorEl=$('#device-install-error');
    deviceBusy=true;button.disabled=true;$('#device-install-done').disabled=true;errorEl.textContent='';
    statusEl.hidden=false;statusEl.textContent='正在检查执行器连接并读取服务器配置…';
    try{
      if(typeof saveSpaceDrafts==='function'&&(dirty()||dnsEdited))await saveSpaceDrafts();
      await api(`/api/devices/${result.device.id}/probe`);
      const preview=await api(`/api/devices/${result.device.id}/preview-pull`);
      const pulled=await api(`/api/devices/${result.device.id}/pull`,{remote_digest:preview.remote_digest});
      $('#dialog').close();
      await refreshDevices();
      if(pulled.space_id!==spaceId)await switchSpace(pulled.space_id);
      else await reloadWorkspace();
      if(page==='sync')location.hash='groups';
      toast(`已连接「${result.device.name}」并导入为独立配置空间。`);
    }catch(error){
      statusEl.hidden=true;
      errorEl.textContent=error.message;
      button.disabled=false;$('#device-install-done').disabled=false;
    }finally{deviceBusy=false}
  };
  bindSetupCopies();
}

const hermesMcpYaml=`mcp_servers:
  yomesh:
    command: python3
    args:
      - /root/.local/share/yomesh/yomesh_hermes_mcp.py
      - --config
      - /root/.config/yomesh/hermes.json
    enabled: true`;

function hermesSetupAddress(value){
  try{
    const url=new URL(value);
    const local=['127.0.0.1','localhost'].includes(url.hostname);
    if((url.protocol!=='https:'&&!(url.protocol==='http:'&&local))||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw Error();
    return url.origin;
  }catch{return null}
}

function paintHermesAccess(state,token=null,address=null){
  const defaultAddress=hermesSetupAddress(location.origin)||'';
  const base=address??defaultAddress;
  showDialog('Hermes 接入',`<div class="device-install hermes-setup"><p>把当前 YoMesh 接入 Hermes，即可用对话查看设备、比对 Mihomo / LocalDNS 和准备发布预检。这里展示的是在 <strong>Hermes 主机</strong>上的配置步骤。</p><p class="device-status">${state.enabled?'已启用 · '+deviceDate(new Date(state.created*1000).toISOString()):'尚未启用'}</p><ol class="device-steps"><li><strong>准备 Hermes 主机并下载桥接脚本</strong><p>先安装好 Hermes 和 Python 3.11+。以下示例以 root 运行 Hermes；其他运行用户请将全部路径改到该用户的目录。</p><a class="button" href="/api/setup/yomesh_hermes_mcp.py" download>下载 yomesh_hermes_mcp.py</a><p>在 Hermes 主机执行，然后将脚本上传到第一个目录：</p><pre>mkdir -p /root/.local/share/yomesh /root/.config/yomesh</pre></li><li><strong>保存 YoMesh 地址与接入令牌</strong><label class="field">Hermes 访问 YoMesh 的地址<input id="hermes-setup-address" type="url" value="${esc(base)}" placeholder="https://你的YoMesh域名:端口" autocomplete="off"><small>填写 Hermes 主机能够访问的 HTTPS 根地址。${defaultAddress?'可按实际访问地址修改。':'当前局域网 HTTP 地址不能用于远程 Hermes 接入。'}仅本机 127.0.0.1 / localhost 测试可用 HTTP。</small></label><p class="error-message" id="hermes-address-error" role="alert"></p>${token?'<p class="notice">新令牌只在此窗口展示一次。请下载或复制配置并妥善保存，关闭后不再回显。</p>':`<p>${state.enabled?'已有接入可继续使用 Hermes 主机上保存的令牌；下面提供配置模板。遗失令牌时可在底部轮换，旧令牌会失效。':'先按底部“生成令牌”，再下载包含令牌的配置文件。当前显示的是待填写模板。'}</p>`}<label class="field">${token?'hermes.json（含本次接入令牌）':'hermes.json 模板（需填写接入令牌）'}<textarea id="hermes-setup-json" rows="5" readonly spellcheck="false"></textarea></label><div class="setup-downloads"><button type="button" class="button" id="hermes-download-config">${token?'下载 hermes.json':'下载配置模板'}</button><button type="button" class="button" data-setup-copy="hermes-setup-json" id="hermes-copy-config">复制配置</button></div><p>将文件保存到 Hermes 主机的 <code>/root/.config/yomesh/hermes.json</code>。接入令牌只用于这份配置，无需填 SSH 密码。</p></li><li><strong>在 Hermes 中登记 MCP 服务</strong><p>编辑 Hermes 主机的 <code>~/.hermes/config.yaml</code>，把以下 <code>yomesh</code> 项合并到 <code>mcp_servers</code> 下。保留已有服务，避免重复写两个同名键。</p><label class="field">Hermes MCP 配置<textarea id="hermes-setup-yaml" rows="10" readonly spellcheck="false">${esc(hermesMcpYaml)}</textarea></label><button type="button" class="button" data-setup-copy="hermes-setup-yaml">复制 MCP 配置</button></li><li><strong>检查连接，然后开始使用</strong><label class="field">在 Hermes 主机执行<textarea id="hermes-setup-test" rows="2" readonly>chmod 600 /root/.config/yomesh/hermes.json
hermes mcp test yomesh</textarea></label><button type="button" class="button" data-setup-copy="hermes-setup-test">复制检查命令</button><p>看到 <code>Connected</code> 和 <code>Tools discovered: 5</code> 表示桥接脚本已连接。接着在 Hermes 对话中要求“列出 YoMesh 的服务器”，确认能读取实际设备。</p><p>例如：“检查家庭网关的配置是否与 YoMesh 一致”。之后在 YoMesh 新登记的服务器会出现在同一设备列表中，无需逐台配置 Hermes。</p></li></ol><p class="preview-note">Hermes 可读取摘要、比对与准备发布；实际发布和回滚在 YoMesh 页面确认。令牌请勿放入聊天或公开仓库。</p><p class="error-message" id="device-hermes-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-hermes-close">关闭</button>${state.enabled?'<button type="button" class="button danger-button" id="device-hermes-revoke">撤销接入</button>':''}<button type="button" class="button primary" id="device-hermes-rotate">${state.enabled?'轮换令牌':'生成令牌'}</button></div></div>`);
  const config=$('#hermes-setup-json'),addressInput=$('#hermes-setup-address');
  if(token)$('#dialog').addEventListener('close',()=>config.closest('.hermes-setup')?.remove(),{once:true});
  if(!state.enabled)config.closest('label').before($('#device-hermes-rotate'));
  const updateConfig=()=>{
    const valid=hermesSetupAddress(addressInput.value.trim());
    $('#hermes-address-error').textContent=valid?'':'请填写 Hermes 能访问的 HTTPS 根地址，例如 https://mesh.example.com:6888。';
    config.value=JSON.stringify({base_url:valid||'https://你的YoMesh地址',token:token||'<请填入YoMesh生成的接入令牌>'},null,2);
    $('#hermes-download-config').disabled=!valid;$('#hermes-copy-config').disabled=!valid;
    $('#device-hermes-rotate').disabled=!valid;
  };
  addressInput.oninput=updateConfig;updateConfig();
  $('#hermes-download-config').onclick=()=>setupDownload('hermes.json',config.value+'\n');
  $('#device-hermes-close').onclick=()=>$('#dialog').close();
  $('#device-hermes-rotate').onclick=async event=>{
    if(!hermesSetupAddress(addressInput.value.trim())){addressInput.focus();return}
    event.target.disabled=true;
    try{const result=await api('/api/integrations/hermes/rotate',{});paintHermesAccess(result,result.token,addressInput.value.trim())}
    catch(error){$('#device-hermes-error').textContent=error.message;event.target.disabled=false}
  };
  if(state.enabled)$('#device-hermes-revoke').onclick=event=>{
    const button=event.target;button.textContent='确认撤销接入';
    $('#device-hermes-error').textContent='撤销后 Hermes 将立即无法访问；再次点击确认。';
    button.onclick=async()=>{button.disabled=true;try{paintHermesAccess(await api('/api/integrations/hermes/revoke',{}),null,addressInput.value.trim())}catch(error){$('#device-hermes-error').textContent=error.message;button.disabled=false}};
  };
  bindSetupCopies();
}
