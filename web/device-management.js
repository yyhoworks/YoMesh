'use strict';
// A device is an explicit target. Reading never publishes; publishing requires
// a prepared release and a second, digest-bound confirmation.
let deviceCatalog=null,deviceSerial=0,deviceBusy=false;
const deviceSpaceName=ident=>spaceCatalog?.spaces?.find(item=>item.id===ident)?.name||'未绑定空间';
const deviceDate=value=>value?new Date(value).toLocaleString('zh-CN'):'尚未读取';
const deviceError=(element,error)=>{element.textContent=error.message;element.classList.add('is-error')};

const paintSpaceCardsWithoutDevices=paintSpaceCards;
paintSpaceCards=function(){
  paintSpaceCardsWithoutDevices();
  $('#content').insertAdjacentHTML('beforeend',`<section class="panel device-panel" id="device-panel" aria-labelledby="device-heading"><div class="section-title"><div><h2 id="device-heading">设备同步</h2><p>读取指定服务器的 Mihomo / LocalDNS；已保存配置先预检，再确认发布。</p></div><button type="button" class="button" id="device-add" disabled>连接服务器</button></div><p class="device-status" id="device-list-status" role="status">正在读取设备列表…</p><div class="device-list" id="device-list"></div></section>`);
  const panel=$('#device-panel'),serial=++deviceSerial;
  $('#device-add',panel).insertAdjacentHTML('beforebegin','<button type="button" class="button" id="device-hermes" disabled>Hermes 接入</button>');
  api('/api/devices').then(result=>{
    if(!panel.isConnected||serial!==deviceSerial)return;
    deviceCatalog=result;paintDeviceList(panel);
  }).catch(error=>{
    if(!panel.isConnected||serial!==deviceSerial)return;
    const status=$('#device-list-status',panel);deviceError(status,error);
    if(error.message.includes('密码保护'))status.insertAdjacentHTML('afterend','<button type="button" class="button" id="device-open-account">打开账号设置</button>');
    $('#device-open-account',panel)?.addEventListener('click',()=>$('#account-btn').click());
  });
};

function paintDeviceList(panel=$('#device-panel')){
  if(!panel)return;
  const rows=deviceCatalog?.devices||[];
  $('#device-list-status',panel).textContent=rows.length?`${rows.length} 台服务器 · 仅显示已登记目标`:'尚无服务器。先核对 SSH 主机指纹，再安装受限执行器。';
  $('#device-list-status',panel).classList.remove('is-error');
  $('#device-add',panel).disabled=false;
  $('#device-add',panel).onclick=openDeviceSetup;
  $('#device-hermes',panel).disabled=false;
  $('#device-hermes',panel).onclick=openHermesAccess;
  $('#device-list',panel).innerHTML=rows.map(row=>`<div class="device-row"><div class="device-row-main"><strong>${esc(row.name)}</strong><small>${esc(row.host)}:${row.port} · ${esc(row.manager)} · ${esc(deviceSpaceName(row.space_id))}</small><small>最近读取：${esc(deviceDate(row.last_read))} · ${row.baseline_digest?'已有比对基准':'尚无比对基准'}</small></div><div class="device-row-actions"><button type="button" class="button" data-device-probe="${esc(row.id)}">检查连接</button><button type="button" class="button" data-device-read="${esc(row.id)}">读取并比对</button><button type="button" class="button primary" data-device-plan="${esc(row.id)}" ${row.space_id&&row.baseline_digest?'':'disabled'}>发布预检</button><button type="button" class="text-button" data-device-history="${esc(row.id)}">发布记录</button></div></div>`).join('');
  rows.forEach((row,index)=>{const actions=$$('.device-row-actions',panel)[index];if(row.space_id)actions.insertAdjacentHTML('beforeend',`<button type="button" class="text-button" data-device-unbind="${esc(row.id)}">解除绑定</button>`);else actions.insertAdjacentHTML('beforeend',`<button type="button" class="text-button" data-device-bind="${esc(row.id)}">绑定现有空间</button>`)});
  $$('[data-device-probe]',panel).forEach(button=>button.onclick=()=>deviceProbe(button.dataset.deviceProbe,button));
  $$('[data-device-read]',panel).forEach(button=>button.onclick=()=>deviceRead(button.dataset.deviceRead,button));
  $$('[data-device-plan]',panel).forEach(button=>button.onclick=()=>devicePlan(button.dataset.devicePlan,button));
  $$('[data-device-history]',panel).forEach(button=>button.onclick=()=>deviceHistory(button.dataset.deviceHistory));
  $$('[data-device-unbind]',panel).forEach(button=>button.onclick=()=>deviceUnbind(button.dataset.deviceUnbind));
  $$('[data-device-bind]',panel).forEach(button=>button.onclick=()=>deviceBind(button.dataset.deviceBind));
}

function deviceBind(ident){
  const row=deviceCatalog.devices.find(item=>item.id===ident),spaces=(spaceCatalog?.spaces||[]).filter(item=>item.native);
  showDialog('绑定原生配置空间',`<form id="device-bind-form"><p>绑定只记录目标关系，不读取或修改设备。第一次读取如要覆盖此空间，会再次要求确认。</p><label class="field">原生配置空间<select id="device-bind-space" required>${spaces.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</select></label>${spaces.length?'':'<p class="error-message">暂无原生空间，请先读取设备并导入。</p>'}<p class="error-message" id="device-bind-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-bind-cancel">取消</button><button type="submit" class="button primary" ${spaces.length?'':'disabled'}>绑定到「${esc(row.name)}」</button></div></form>`);
  $('#device-bind-cancel').onclick=()=>$('#dialog').close();
  $('#device-bind-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api(`/api/devices/${ident}/bind`,{space_id:$('#device-bind-space').value,revision:deviceCatalog.revision});$('#dialog').close();await refreshDevices();toast('已绑定；请读取并比对设备配置。')}catch(error){$('#device-bind-error').textContent=error.message;button.disabled=false}};
}

function deviceUnbind(ident){
  const row=deviceCatalog.devices.find(item=>item.id===ident);
  showDialog('解除设备绑定',`<p>解除「${esc(row.name)}」与「${esc(deviceSpaceName(row.space_id))}」的关联？已导入的配置空间和目标设备文件都不会删除或修改。之后可重新读取或绑定其他原生空间。</p><p class="error-message" id="device-unbind-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-unbind-cancel">取消</button><button type="button" class="button danger-button" id="device-unbind-confirm">解除绑定</button></div>`);
  $('#device-unbind-cancel').onclick=()=>$('#dialog').close();
  $('#device-unbind-confirm').onclick=async event=>{event.target.disabled=true;try{await api(`/api/devices/${ident}/unbind`,{revision:deviceCatalog.revision});$('#dialog').close();await refreshDevices();toast('已解除绑定；设备配置未修改。')}catch(error){$('#device-unbind-error').textContent=error.message;event.target.disabled=false}};
}

async function refreshDevices(){deviceCatalog=await api('/api/devices');if(page==='spaces')paintDeviceList()}
async function deviceProbe(ident,button){
  if(deviceBusy)return;deviceBusy=true;button.disabled=true;
  try{const result=await api(`/api/devices/${ident}/probe`);toast(`执行器已连接 · Mihomo ${result.mihomo?'可用':'缺失'} · LocalDNS ${result.localdns?'可用':'缺失'}`)}
  catch(error){toast(error.message)}finally{deviceBusy=false;button.disabled=false}
}

function openDeviceSetup(){
  showDialog('连接指定服务器',`<form id="device-setup-form" class="device-setup-form"><p class="notice">先在目标设备安装受限执行器。YoMesh 只保存设备专用 SSH 密钥，不保存 root 密码；发布前会核对设备指纹、逐文件差异和内核校验。</p><div class="fields"><label class="field">服务器名称<input name="name" maxlength="80" required placeholder="例如：家庭网关"></label><label class="field">IP 或主机名<input name="host" required placeholder="192.168.8.7"></label><label class="field">SSH 端口<input name="port" type="number" min="1" max="65535" value="22" required></label><label class="field">SSH 用户<input name="username" value="root" readonly></label></div><div class="filter-row"><button type="button" class="button" id="device-get-fingerprint">读取 SSH 主机指纹</button><span id="device-fingerprint-status" role="status">请在目标设备上独立核对指纹。</span></div><label class="field">已核对的 SSH 主机指纹<input name="host_key" pattern="SHA256:[A-Za-z0-9+/]{43}" required placeholder="SHA256:…"></label><label class="check-row"><input name="confirmed_host_key" type="checkbox" required> 我已在目标设备核对该指纹</label><fieldset><legend>Mihomo 路径</legend><div class="fields"><label class="field">工作目录<input name="mihomo_home" required placeholder="/etc/mihomo"></label><label class="field">主配置文件<input name="mihomo_config" required placeholder="/etc/mihomo/config.yaml"></label><label class="field">内核程序<input name="mihomo_binary" required placeholder="/usr/local/bin/mihomo"></label></div></fieldset><fieldset><legend>LocalDNS 路径</legend><div class="fields"><label class="field">配置根目录<input name="localdns_root" required placeholder="/opt/localdns"></label><label class="field">内核程序<input name="localdns_binary" required placeholder="/opt/localdns/localdns"></label></div></fieldset><fieldset><legend>服务与执行器</legend><div class="fields"><label class="field">服务管理<select name="manager"><option value="openrc">OpenRC</option><option value="systemd">systemd</option></select></label><label class="field">Mihomo 服务<input name="mihomo_service" required placeholder="mihomo"></label><label class="field">LocalDNS 服务<input name="localdns_service" required placeholder="localdns"></label><label class="field">执行器状态目录<input name="state_dir" required value="/var/lib/yomesh-executor"></label></div></fieldset><p class="error-message" id="device-setup-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-setup-cancel">取消</button><button type="submit" class="button primary" id="device-setup-submit">生成设备密钥</button></div></form>`);
  $('#device-setup-form fieldset:last-of-type').insertAdjacentHTML('beforeend','<details class="advanced-options"><summary>校验程序所需环境变量</summary><label class="field">JSON 对象<textarea name="validation_env" rows="3" spellcheck="false">{}</textarea><small>例如 LocalDNS 依赖 LD_PRELOAD 时填写对应路径；只用于设备上的原生校验。</small></label></details>');
  $('#device-setup-cancel').onclick=()=>$('#dialog').close();
  $('#device-get-fingerprint').onclick=async()=>{
    const form=$('#device-setup-form'),host=form.elements.host.value.trim(),port=Number(form.elements.port.value),status=$('#device-fingerprint-status');
    if(!host||!port){status.textContent='先填写服务器地址和端口。';return}
    status.textContent='正在读取指纹…';
    try{const result=await api('/api/devices/host-key',{host,port});form.elements.host_key.value=result.fingerprint;form.elements.confirmed_host_key.checked=false;status.textContent='已读取；请与服务器本机指纹核对后勾选。'}
    catch(error){status.textContent=error.message}
  };
  $('#device-setup-form').onsubmit=async event=>{
    event.preventDefault();if(deviceBusy)return;
    const form=event.currentTarget,values=Object.fromEntries(new FormData(form)),error=$('#device-setup-error');
    const settings={name:values.name.trim(),host:values.host.trim(),port:Number(values.port),username:'root',host_key:values.host_key.trim(),mihomo_home:values.mihomo_home.trim(),mihomo_config:values.mihomo_config.trim(),mihomo_binary:values.mihomo_binary.trim(),localdns_root:values.localdns_root.trim(),localdns_binary:values.localdns_binary.trim(),manager:values.manager,services:{mihomo:values.mihomo_service.trim(),localdns:values.localdns_service.trim()}};
    let validationEnv;try{validationEnv=JSON.parse(values.validation_env||'{}');if(!validationEnv||Array.isArray(validationEnv)||typeof validationEnv!=='object')throw Error()}catch{error.textContent='校验环境变量需要 JSON 对象。';return}
    if(Object.entries(validationEnv).some(([key,value])=>!/^[A-Z][A-Z0-9_]*$/.test(key)||typeof value!=='string'||value.length>512)){error.textContent='校验环境变量只能使用大写名称与 512 字符以内的字符串值。';return}
    if(!values.state_dir.startsWith('/')||values.state_dir.split('/').includes('..')){error.textContent='执行器状态目录需要设备上的绝对路径。';return}
    deviceBusy=true;$('#device-setup-submit').disabled=true;error.textContent='';
    try{const result=await api('/api/devices',{device:settings,revision:deviceCatalog.revision});await refreshDevices();showDeviceInstall(result,settings,values.state_dir.trim(),validationEnv)}
    catch(reason){error.textContent=reason.message;$('#device-setup-submit').disabled=false}
    finally{deviceBusy=false}
  };
}

function showDeviceInstall(result,settings,stateDir,validationEnv){
  const policy={format:'yomesh-executor-v1',manager:settings.manager,mihomo_home:settings.mihomo_home,mihomo_config:settings.mihomo_config,mihomo_binary:settings.mihomo_binary,localdns_root:settings.localdns_root,localdns_binary:settings.localdns_binary,state_dir:stateDir,services:settings.services,validation_env:validationEnv};
  showDialog('安装受限执行器 · '+result.device.name,`<div class="device-install"><p>设备记录已保存，但尚未连接成功。将下列公钥保存为 <code>device.pub</code>、目标设置保存为 <code>target.json</code>，和项目提供的 <code>device_installer.py</code>、<code>device_executor.py</code> 一起放到目标服务器。先核对，再由设备管理员执行安装。</p><label class="field">设备专用公钥<textarea rows="4" readonly>${esc(result.public_key)}</textarea></label><label class="field">目标设置 JSON<textarea rows="12" readonly>${esc(JSON.stringify(policy,null,2))}</textarea></label><pre>python3 device_installer.py target.json device.pub --check\npython3 device_installer.py target.json device.pub</pre><p class="preview-note">安装会备份并更新 root 的 authorized_keys，密钥被强制限定为 YoMesh 执行器；不允许任意 shell。安装完成后点击“检查连接”。</p><div class="dialog-actions"><button type="button" class="button primary" id="device-install-done">完成</button></div></div>`);
  $('#device-install-done').onclick=()=>$('#dialog').close();
}

async function deviceRead(ident,button){
  if(deviceBusy)return;deviceBusy=true;button.disabled=true;
  try{
    if(dirty()||dnsEdited)throw Error('当前仍有未保存的配置草稿，请先保存或取消，再读取设备。');
    const result=await api(`/api/devices/${ident}/preview-pull`),row=deviceCatalog.devices.find(item=>item.id===ident);
    showDialog('读取并比对 · '+row.name,`<div class="device-preview"><p>${result.different?'设备配置与当前空间不同。':'设备配置与当前空间相同。'}读取只生成预览，不修改设备。</p><p class="device-status">设备文件 ${result.counts.files} 个 · Mihomo 规则 ${result.counts.policies} 条 · 节点组 ${result.counts.proxy_groups} 个</p>${result.local_changed_since_pull?'<p class="error-message">本地空间自上次读取后已有修改。覆盖会丢失这些草稿，请先核对。</p><label class="check-row"><input id="device-replace" type="checkbox"> 我确认用设备当前配置覆盖绑定空间</label>':''}<details><summary>本次读取的文件</summary><ul class="device-preview-list">${Object.keys(result.remote_manifest).map(name=>`<li>${esc(name)}</li>`).join('')}</ul></details><p class="error-message" id="device-read-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-read-cancel">取消</button><button type="button" class="button primary" id="device-read-confirm">${row.space_id?'更新绑定空间':'导入为独立空间'}</button></div></div>`);
    if(result.local_changed_since_pull&&!row.baseline_digest)$('.device-preview>.error-message').textContent='这是绑定后的首次读取。确认后会以设备配置建立比对基准，并替换当前空间内容。';
    $('#device-read-cancel').onclick=()=>$('#dialog').close();
    $('#device-read-confirm').onclick=async event=>{
      if(deviceBusy)return;
      const replace=!!$('#device-replace')?.checked;if(result.local_changed_since_pull&&!replace){$('#device-read-error').textContent='先确认是否覆盖本地修改。';return}
      deviceBusy=true;event.target.disabled=true;
      try{const pulled=await api(`/api/devices/${ident}/pull`,{remote_digest:result.remote_digest,workspace_revision:result.workspace_revision,replace});$('#dialog').close();await refreshDevices();if(pulled.space_id!==spaceId)await switchSpace(pulled.space_id);else await reloadWorkspace();toast('已读取到独立配置空间；设备尚未被修改。')}
      catch(error){$('#device-read-error').textContent=error.message;event.target.disabled=false}
      finally{deviceBusy=false}
    };
  }catch(error){toast(error.message)}finally{deviceBusy=false;button.disabled=false}
}

async function devicePlan(ident,button){
  if(deviceBusy)return;deviceBusy=true;button.disabled=true;
  try{
    if(dirty()||dnsEdited)throw Error('当前仍有未保存的配置草稿，请先保存或取消，再预检发布。');
    const row=deviceCatalog.devices.find(item=>item.id===ident),snapshot=await api('/api/workspace',undefined,row.space_id);
    const release=await api(`/api/devices/${ident}/plan`,{workspace_revision:snapshot.revision});
    showDeviceRelease(release);
  }catch(error){toast(error.message)}finally{deviceBusy=false;button.disabled=false}
}

function deviceValidationLabel(value){
  return value==='passed'?'通过':value==='failed'?'未通过':value||'未执行';
}

function showDeviceRelease(release){
  const prepared=release.status==='prepared',running=release.status==='running',success=release.status==='succeeded';
  const stateLabels={prepared:'预检通过，等待确认',running:'正在应用并检查服务',succeeded:'发布成功',rolled_back:'已回滚',conflict:'设备配置发生冲突',failed:'发布失败',needs_manual_recovery:'自动回滚未完成，需要人工恢复'};
  showDialog('设备发布 · '+release.device_name,`<div class="device-release"><p class="device-status" id="device-release-state" role="status">${esc(stateLabels[release.status]||release.status)}</p><p>目标服务器：${esc(release.device_name)} · 配置空间：${esc(deviceSpaceName(release.space_id))}</p><p class="preview-note">发布包 SHA-256：<code>${esc(release.bundle_sha256)}</code></p><p>设备原生校验：Mihomo ${esc(deviceValidationLabel(release.validation?.mihomo))} · LocalDNS ${esc(deviceValidationLabel(release.validation?.localdns||'不适用'))}</p><details open><summary>逐文件差异（${release.changes.length}）</summary><ul class="device-preview-list">${release.changes.map(item=>`<li>${esc(item.file)} · ${item.before?item.after?'修改':'删除':'新增'}</li>`).join('')}</ul></details><p class="error-message" id="device-release-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-release-close">关闭</button>${prepared?'<button type="button" class="button primary" id="device-release-approve">确认发布到这台服务器</button>':''}${success?'<button type="button" class="button danger-button" id="device-release-rollback">回滚此次发布</button>':''}${running?'<button type="button" class="button" id="device-release-refresh">刷新状态</button>':''}</div></div>`);
  $('#device-release-close').onclick=()=>$('#dialog').close();
  if(prepared)$('#device-release-approve').onclick=async event=>{
    event.target.disabled=true;
    try{const current=await api(`/api/devices/releases/${release.id}/approve`,{device_id:release.device_id,bundle_sha256:release.bundle_sha256,workspace_revision:release.revision});showDeviceRelease(current)}
    catch(error){$('#device-release-error').textContent=error.message;event.target.disabled=false}
  };
  if(running)$('#device-release-refresh').onclick=async event=>{
    event.target.disabled=true;
    try{const current=await api(`/api/devices/releases/${release.id}/status`,{});showDeviceRelease(current);if(current.status==='succeeded')await refreshDevices()}
    catch(error){$('#device-release-error').textContent=error.message;event.target.disabled=false}
  };
  if(success)$('#device-release-rollback').onclick=async event=>{
    event.target.disabled=true;
    try{const current=await api(`/api/devices/releases/${release.id}/rollback`,{});showDeviceRelease(current);await refreshDevices()}
    catch(error){$('#device-release-error').textContent=error.message;event.target.disabled=false}
  };
}

async function deviceHistory(ident){
  try{const rows=await api(`/api/devices/${ident}/releases`);showDialog('设备发布记录',`<div class="device-list">${rows.length?rows.slice().reverse().map(row=>`<button type="button" class="button" data-device-release="${esc(row.id)}">${esc(deviceDate(new Date(row.created*1000).toISOString()))} · ${esc(row.status)} · ${row.changes.length} 个文件</button>`).join(''):'<p>尚无发布记录。</p>'}</div>`);$$('[data-device-release]').forEach(button=>button.onclick=()=>showDeviceRelease(rows.find(row=>row.id===button.dataset.deviceRelease)))}catch(error){toast(error.message)}
}

async function openHermesAccess(){
  try{const state=await api('/api/integrations/hermes');paintHermesAccess(state)}catch(error){toast(error.message)}
}

function paintHermesAccess(state,token=null){
  const base=location.origin;
  showDialog('Hermes 接入',`<div class="device-install"><p>Hermes 可读取设备摘要、查看发布记录与准备原生校验；不能确认发布、回滚或取得 root 密码。所有实际发布仍在 YoMesh 工作台确认。</p><p class="device-status">${state.enabled?'已启用 · '+deviceDate(new Date(state.created*1000).toISOString()):'尚未启用'}</p>${token?`<p class="notice">此令牌只显示这一次。请保存到 Hermes 主机上仅 root 可读的配置文件，不要放入聊天、命令行、环境变量或仓库。</p><label class="field">一次性接入令牌<textarea rows="3" readonly>${esc(token)}</textarea></label><label class="field">Hermes bridge 配置示例<textarea rows="6" readonly>${esc(JSON.stringify({base_url:base,token},null,2))}</textarea></label><pre>chmod 600 /root/.config/yomesh/hermes.json\npython3 /root/.local/share/yomesh/yomesh_hermes_mcp.py --config /root/.config/yomesh/hermes.json</pre>`:''}<p class="preview-note">将项目提供的 <code>scripts/yomesh_hermes_mcp.py</code> 放到 Hermes 主机，并在 Hermes 的 <code>mcp_servers</code> 中配置上述 stdio 命令。连接使用 HTTPS；本机测试例外。</p><p class="error-message" id="device-hermes-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="device-hermes-close">关闭</button>${state.enabled?'<button type="button" class="button danger-button" id="device-hermes-revoke">撤销接入</button>':''}<button type="button" class="button primary" id="device-hermes-rotate">${state.enabled?'轮换令牌':'生成令牌'}</button></div></div>`);
  $('#device-hermes-close').onclick=()=>$('#dialog').close();
  $('#device-hermes-rotate').onclick=async event=>{event.target.disabled=true;try{const result=await api('/api/integrations/hermes/rotate',{});paintHermesAccess(result,result.token)}catch(error){$('#device-hermes-error').textContent=error.message;event.target.disabled=false}};
  if(state.enabled)$('#device-hermes-revoke').onclick=event=>{
    event.target.disabled=true;
    $('#device-hermes-error').textContent='撤销后 Hermes 将立即无法访问；再次点击确认。';
    event.target.textContent='确认撤销接入';
    event.target.onclick=async()=>{try{paintHermesAccess(await api('/api/integrations/hermes/revoke',{}))}catch(error){$('#device-hermes-error').textContent=error.message;event.target.disabled=false}};
    event.target.disabled=false;
  };
}
