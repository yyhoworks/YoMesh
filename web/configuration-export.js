'use strict';
$('#show-plan').onclick=()=>showDialog('配置与导出','<p>代理配置：先设置基础设置与接入设置，再配置节点分组和分流规则（含排序与兜底），完成后导出 Mihomo YAML 或 sing-box JSON。</p><p>DNS 配置：单独设置上游、监听和代理接线，再导出 MosDNS。已导入的设备使用 LocalDNS，保留其原生格式。</p><p>配置按方案保存，下载前检查对应内核；导出不会启动设备服务。</p>');

async function openConfigurationExport(component='proxy'){
  try{await saveSpaceDrafts()}catch(error){toast(error.message);return}
  if(component==='dns'&&nativeMode()&&!nativeDNSAvailable()&&!data.mosdns){toast('此空间只有 Mihomo 内置 DNS，请在基础设置中编辑。');return}
  const dns=component==='dns',native=nativeMode()&&(!dns||nativeDNSAvailable()),engine=nativeDNSAvailable()?'LocalDNS':'MosDNS';
  showDialog(dns?'导出 '+engine:'导出代理配置',`<form id="native-export-form">
    <p class="export-scope">${esc(activeSpace()?.name||'当前配置空间')} · ${dns?'独立 DNS 配置':'基础配置 + 入站 + 节点组 + 有序规则'}</p>
    ${dns?`<p>只生成 ${engine} 配置和所需规则文件。上游与代理接线保留当前设置。</p>`:`<label class="field">代理内核<select id="export-proxy"><option value="mihomo">Mihomo · YAML</option>${native?'<option disabled>sing-box · 当前原生空间不支持转换</option>':'<option value="sing-box">sing-box · JSON</option>'}</select></label>`}
    ${native?`<p class="preview-note">${dns?'LocalDNS FJGAC 保留独立的 DNS 文件与规则。':'保留原始订阅地址、缓存、证书及复杂规则；此原生空间支持 Mihomo。'}</p>`:`<fieldset class="rule-output-choices"><legend>${dns?'DNS 规则快照':'规则输出方式'}</legend>
      ${dns?'':`<label class="rule-output-choice"><input type="radio" name="rule_output" value="remote"><span><strong>远程订阅引用</strong><small>代理内核按原地址更新已选订阅。</small></span></label>`}
      <label class="rule-output-choice"><input type="radio" name="rule_output" value="local"><span><strong>本地文件引用</strong><small>规则保存在包内 rules/ 目录。</small></span></label>
      <label class="rule-output-choice"><input type="radio" name="rule_output" value="inline"><span><strong>内联规则</strong><small>将当前规则内容直接写入配置。</small></span></label>
    </fieldset><p class="preview-note" id="export-rule-note" role="status"></p>`}
    ${!dns&&!native?`<details class="advanced-options"><summary>其他配置来源</summary><label class="field">本次配置来源<select id="export-source"><option value="current">当前配置空间</option><option value="file">导入 YoMesh profile JSON</option><option value="example">内置兼容性样例</option></select></label><label class="field" id="export-file-field" hidden>配置文件<input id="export-file" type="file" accept=".json,application/json"></label><p class="preview-note" id="export-hint">使用当前方案的选用内容。</p></details>`:''}
    <div id="export-compatibility" hidden><p id="compatibility-description" class="notice"></p><label class="check-row"><input id="export-adjust-groups" type="checkbox">仅本次导出，将这些组改为自动测速</label><p class="preview-note">会改变选路行为；本次选择记录在配置包内。</p></div>
    <p class="preview-note">${dns?'下载前只检查 '+engine+'。规则使用导出时快照，未验证外部上游或代理连通性。':'下载前只检查所选代理内核。DNS 按基础配置保留；使用外部 DNS 服务时，请在 DNS 配置中单独导出。'}</p>
    <div id="export-preflight" class="export-preflight" role="status"><strong>正在检查配置组合…</strong><span>下载前仍需目标内核校验。</span></div>
    <p class="error-message" id="export-error" role="alert"></p><div class="dialog-actions"><button class="button" id="native-export-preview" type="button">生成预览</button>${dns?'':`<button class="button primary" id="native-export-single" type="button">📄 直接下载 mihomo.yaml</button>`}<button class="button ${dns?'primary':''}" id="native-export-submit" type="submit">${dns?'校验并下载':'下载完整配置包 (.zip)'}</button></div>
    <section id="export-preview" hidden><div class="section-title"><h3>包内文件预览</h3><button type="button" class="button" id="export-preview-copy">📋 复制当前文件内容</button></div><p class="preview-note">可直接复制上方 YAML 内容，或点击上方按钮下载单文件 / 完整配置包。</p><label class="field">文件<select id="export-preview-file"></select></label><pre class="code-preview" id="export-preview-code" tabindex="0"></pre></section>
  </form>`);
  let pending=false,preflightReady=false,preflightSerial=0,preflightTimer=null,preflightFingerprint='';
  const dialog=$('#dialog'),prevent=e=>{if(pending)e.preventDefault()};dialog.addEventListener('cancel',prevent);dialog.addEventListener('close',()=>{dialog.removeEventListener('cancel',prevent);clearTimeout(preflightTimer);preflightSerial++},{once:true});
  const target=()=>$('#export-proxy')?.value||'mihomo',source=()=>$('#export-source')?.value||'current';
  const mode=()=>native?'remote':$('#native-export-form input[name=rule_output]:checked').value;
  const incompatible=profile=>((profile?.composition&&source()==='current'?liveWorkspace.state?.generation?.groups:profile?.groups)||[]).filter(g=>['fallback','load-balance'].includes(g.mode));
  const invalidate=()=>{$('#export-preview').hidden=true;$('#export-error').textContent='';schedulePreflight()};
  const update=()=>{
    const groups=!native&&!dns&&target()==='sing-box'&&source()==='current'?incompatible(data):[];
    $('#export-compatibility').hidden=!groups.length;$('#compatibility-description').textContent=groups.length?groups.map(g=>g.name).join('、')+' 使用 sing-box 不支持的组类型；可选择 Mihomo 保留原行为。':'';
    if($('#native-export-single'))$('#native-export-single').textContent=target()==='sing-box'?'📄 直接下载 sing-box.json':'📄 直接下载 mihomo.yaml';
    if($('#export-rule-note'))$('#export-rule-note').textContent=dns?(data.mosdns?'使用本空间独立保存的 MosDNS 规则与上游，不自动转换代理规则。':data.settings?.dns_follow_rules===false?'未跟随代理规则，所有域名使用 DNS 默认上游。':'MosDNS 使用当前方案的域名规则快照；IP 条件保留在代理侧。'):{remote:target()==='mihomo'?'订阅须为 MRS / Clash payload YAML；不兼容格式可选本地文件或内联。':'订阅须为 SRS / rule-set JSON；不兼容格式可选本地文件或内联。',local:'解压后保留 rules/ 目录，并从配置包目录启动内核。',inline:'使用规则当前内容；订阅更新后需重新导出。'}[mode()];
  };
  if($('#export-proxy')){$('#export-proxy').value=native?'mihomo':activeSpace()?.proxy||'mihomo';$('#export-proxy').onchange=()=>{$('#export-adjust-groups').checked=false;invalidate();update()}}
  if(!native){let preferred=activeSpace()?.rule_output||'inline';if(dns&&preferred==='remote')preferred='local';$(`#native-export-form input[name=rule_output][value=${preferred}]`).checked=true;$$('#native-export-form input[name=rule_output]').forEach(el=>el.onchange=()=>{invalidate();update()})}
  if($('#export-source'))$('#export-source').onchange=()=>{$('#export-file-field').hidden=source()!=='file';$('#export-hint').textContent=source()==='example'?'样例仅用于配置格式验证，不能连接外网。':source()==='file'?'文件仅用于本次导出，不保存到工作区。':'使用当前方案的选用内容。';$('#export-adjust-groups').checked=false;invalidate();update()};
  if($('#export-file'))$('#export-file').onchange=invalidate;$('#export-adjust-groups').onchange=invalidate;
  const request=async()=>{
    const result={component,proxy:target(),rule_output:mode()};
    if(source()==='current')result.use_workspace=true;
    else if(source()==='example')result.profile=await api('/api/example');
    else{const file=$('#export-file').files[0];if(!file)throw Error('请选择 YoMesh profile JSON 文件。');if(file.size>24*1024*1024)throw Error('文件不能超过 24 MiB。');result.profile=JSON.parse(await file.text())}
    if(!dns&&result.proxy==='sing-box'&&source()==='current'){
      const groups=incompatible(data);if(groups.length&&!$('#export-adjust-groups').checked)throw Error('请选择 Mihomo，或明确勾选仅本次按自动测速导出。');if(groups.length)result.group_mode_overrides=Object.fromEntries(groups.map(g=>[g.id,'url-test']));
    }
    return result;
  };
  const schedulePreflight=()=>{
    clearTimeout(preflightTimer);const serial=++preflightSerial;preflightReady=false;
    $('#native-export-submit').disabled=true;$('#native-export-preview').disabled=true;if($('#native-export-single'))$('#native-export-single').disabled=true;
    $('#export-preflight').classList.remove('is-error');$('#export-preflight').innerHTML='<strong>正在检查配置组合…</strong><span>检查当前选用内容和引用，不启动设备服务。</span>';
    preflightTimer=setTimeout(async()=>{
      try{
        const body=await request(),result=await api('/api/workspace/export-check',{...body,revision:liveWorkspace.revision});
        if(serial!==preflightSerial||!$('#native-export-form'))return;
        preflightReady=result.ready;preflightFingerprint=JSON.stringify(body);
        $('#export-preflight').classList.toggle('is-error',!result.ready);
        $('#export-preflight').innerHTML=result.ready?`<strong>配置组合检查通过</strong><span>将生成 ${result.file_count} 个文件；可直接下载单文件或完整配置包。</span>`:`<strong>当前配置还不能生成</strong><span>${esc(result.error)}</span>`;
      }catch(error){if(serial!==preflightSerial||!$('#native-export-form'))return;$('#export-preflight').classList.add('is-error');$('#export-preflight').innerHTML=`<strong>请先完成配置</strong><span>${esc(error.message)}</span>`}
      if(serial===preflightSerial&&$('#native-export-form')){$('#native-export-submit').disabled=!preflightReady;$('#native-export-preview').disabled=!preflightReady;if($('#native-export-single'))$('#native-export-single').disabled=!preflightReady}
    },180);
  };
  const run=async modeAction=>{
    if(pending||!preflightReady)return;pending=true;$('#close-dialog').disabled=true;$('#export-error').textContent='';
    const controls=$$('#native-export-form input,#native-export-form select,#native-export-form button');controls.forEach(el=>el.disabled=true);
    if(modeAction==='zip')$('#native-export-submit').textContent='正在校验…';
    if(modeAction==='single'&&$('#native-export-single'))$('#native-export-single').textContent='正在生成…';
    try{
      const body=await request();if(JSON.stringify(body)!==preflightFingerprint)throw Error('导出选项已变化，请等待配置检查完成。');
      if(modeAction==='preview'||modeAction==='single'){
        const result=await api('/api/workspace/preview-export',body);
        result.files['manifest.json']=JSON.stringify(result.manifest,null,2);
        const names=Object.keys(result.files),main=dns?(engine==='LocalDNS'?'localdns/config/localdns.json':'mosdns.yaml'):body.proxy+(body.proxy==='mihomo'?'.yaml':'.json');
        names.sort((a,b)=>a===main?-1:b===main?1:0);
        $('#export-preview-file').innerHTML=names.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
        $('#export-preview-file').onchange=e=>$('#export-preview-code').textContent=result.files[e.target.value];
        $('#export-preview-code').textContent=result.files[names[0]];
        $('#export-preview').hidden=false;
        if(modeAction==='single'){
          const content=result.files[main]||result.files[names[0]]||'';
          const ext=body.proxy==='sing-box'?'sing-box.json':'mihomo.yaml';
          const blob=new Blob([content],{type:body.proxy==='sing-box'?'application/json;charset=utf-8':'text/yaml;charset=utf-8'});
          const url=URL.createObjectURL(blob),link=document.createElement('a');
          link.href=url;link.download=(activeSpace()?.name||'yomesh').replace(/[\\/:*?"<>|]/g,'_')+'-'+ext;
          link.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
          toast(`已直接下载 ${link.download}`);
        }
      }else{
        const response=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json',...workspaceHeaders()},body:JSON.stringify(body)});
        if(!response.ok)throw Error((await response.json()).error||'原生校验未通过。');
        const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
        link.href=url;link.download=(activeSpace()?.name||'yomesh').replace(/[\\/:*?"<>|]/g,'_')+'-'+(dns?engine.toLowerCase():body.proxy)+'.zip';
        link.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
        toast((dns?engine:body.proxy)+' 原生校验通过，已下载独立配置包。');
      }
    }catch(error){$('#export-error').textContent=error instanceof SyntaxError?'文件不是有效的 JSON。':error.message}
    finally{
      pending=false;$('#close-dialog').disabled=false;controls.forEach(el=>el.disabled=false);
      $('#native-export-submit').disabled=!preflightReady;$('#native-export-preview').disabled=!preflightReady;
      if($('#native-export-single')){$('#native-export-single').disabled=!preflightReady;$('#native-export-single').textContent=target()==='sing-box'?'📄 直接下载 sing-box.json':'📄 直接下载 mihomo.yaml'}
      $('#native-export-submit').textContent=dns?'校验并下载':'下载完整配置包 (.zip)';
    }
  };
  if($('#export-preview-copy'))$('#export-preview-copy').onclick=async()=>{
    const text=$('#export-preview-code')?.textContent||'';if(!text)return;
    try{await navigator.clipboard.writeText(text);toast('已复制当前预览文件内容。')}catch{toast('复制失败，请手动选中复制。')}
  };
  $('#native-export-preview').onclick=()=>run('preview');
  if($('#native-export-single'))$('#native-export-single').onclick=()=>run('single');
  $('#native-export-form').onsubmit=e=>{e.preventDefault();run('zip')};
  update();schedulePreflight();
}
$('#export').onclick=()=>openConfigurationExport(page==='dns'?'dns':'proxy');
const renderConfigurationFlow=render;
render=function(){renderConfigurationFlow();$('#export').textContent=page==='dns'?'导出 '+(nativeMode()?nativeDnsName():'MosDNS'):'导出 Mihomo 配置'};
