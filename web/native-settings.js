'use strict';
$('#base-config').onclick=()=>editBaseConfig();
async function editBaseConfig(initial='mihomo') {
  let documents;
  try{documents=await api('/api/workspace/base-configs')}catch(error){toast(error.message);return}
  let target=initial,busy=false;const native=documents.native===true;
  const texts={'mihomo':documents.stored.mihomo||documents.defaults.mihomo, 'sing-box':documents.stored['sing-box']||documents.defaults['sing-box']};
  const persisted={...texts};
  showDialog('基础设置',`<form id="base-config-form"><div class="view-tabs base-tabs" role="group" aria-label="基础配置内核"><button type="button" class="button" data-base-target="mihomo">Mihomo · YAML</button><button type="button" class="button" data-base-target="sing-box">sing-box · JSON</button></div><p id="base-description"></p><div class="filter-row base-code-tools"><button type="button" class="button" id="base-inbounds">接入设置</button><span class="preview-note">监听与连接入口在这里配置</span></div><p class="preview-note" id="base-merge-note">基础字段覆盖默认值；节点、节点组、分流规则和绑定入站统一合并。留空并保存可恢复默认。</p><label class="field base-code-label" for="base-config-text">基础配置内容</label><div class="native-code-editor"><pre id="base-line-numbers" aria-hidden="true"></pre><textarea id="base-config-text" wrap="off" spellcheck="false" autocomplete="off" aria-describedby="base-description"></textarea></div><div class="filter-row base-code-tools"><button type="button" class="text-button" id="base-fill-default">填入默认配置</button><button type="button" class="text-button" id="base-clear">恢复默认</button><span id="base-save-state" role="status">保存草稿后，下载时仍需原生校验。</span></div><label class="check-row" id="base-compatibility" hidden><input type="checkbox" id="base-adjust-groups">仅本次校验，将不兼容节点组按自动测速检查</label><p class="error-message" id="base-error" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="base-cancel">关闭</button><button type="button" class="button" id="base-preview">预览合并结果</button><button type="button" class="button" id="base-check">原生校验</button><button type="submit" class="button primary" id="base-save">保存基础配置</button></div><section id="base-preview-output" hidden><h3>合并后的配置草稿</h3><p class="preview-note">预览包含节点凭据，请按自己的配置文件保管。</p><pre class="code-preview" id="base-preview-code" tabindex="0"></pre></section></form>`);
  const dialog=$('#dialog');dialog.classList.add('base-config-dialog');
  const preventClose=e=>{if(busy)e.preventDefault()};dialog.addEventListener('cancel',preventClose);
  dialog.addEventListener('close',()=>{dialog.classList.remove('base-config-dialog');dialog.removeEventListener('cancel',preventClose);render()},{once:true});
  const lineNumbers=()=>{$('#base-line-numbers').textContent=Array.from({length:$('#base-config-text').value.split('\n').length},(_,i)=>i+1).join('\n')};
  const invalidate=()=>{$('#base-preview-output').hidden=true;$('#base-error').textContent='';$('#base-save-state').textContent='有未保存的修改。'};
  const paint=()=>{
    $$('[data-base-target]').forEach(button=>{button.classList.toggle('active',button.dataset.baseTarget===target);button.setAttribute('aria-pressed',button.dataset.baseTarget===target)});
    $('#base-config-text').value=texts[target];$('#base-config-text').placeholder=documents.defaults[target];
    $('#base-description').textContent=target==='mihomo'?'Mihomo 1.19.32：可设置端口、DNS、TUN、嗅探、控制接口等原生基础字段。':'sing-box 1.14.3：可设置 log、dns、experimental、route 基础选项及原生入站；入站由基础模板与绑定入站合并；DNS 在独立入口配置。';
    const unsupported=native&&target==='sing-box';
    $('#base-merge-note').textContent=native&&target==='mihomo'?'编辑当前原生基础字段；节点、节点组、规则和入站保持独立管理。':'基础字段覆盖默认值；节点、节点组、规则和入站统一合并。留空并保存可恢复默认。';
    if(native)$('#base-description').textContent=unsupported?'sing-box 1.14.3 基础模板：可以编辑和保存。当前空间保留 Mihomo 原生节点与规则，尚不能直接导出 sing-box；完整生成请使用双内核配置空间。':'Mihomo 1.19.32：当前导入空间的实际基础字段。节点组、规则组和入站独立编辑，完整保留原生字段。';
    $('#base-preview').disabled=unsupported;$('#base-check').disabled=unsupported;$('#base-fill-default').hidden=native&&target==='mihomo';$('#base-clear').hidden=native&&target==='mihomo';
    $('#base-compatibility').hidden=target!=='sing-box'||!data.groups.some(g=>['fallback','load-balance'].includes(g.mode));
    $('#base-adjust-groups').checked=false;lineNumbers();invalidate();$('#base-save-state').textContent=unsupported?'这里只保存 sing-box 基础模板，不转换当前 Mihomo 原生方案。':'当前内核的基础配置独立保存。';
  };
  $$('[data-base-target]').forEach(button=>button.onclick=()=>{texts[target]=$('#base-config-text').value;target=button.dataset.baseTarget;paint()});
  $('#base-config-text').oninput=()=>{texts[target]=$('#base-config-text').value;lineNumbers();invalidate()};
  $('#base-config-text').onscroll=e=>$('#base-line-numbers').scrollTop=e.target.scrollTop;
  $('#base-config-text').onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();e.target.setRangeText('  ',e.target.selectionStart,e.target.selectionEnd,'end');e.target.dispatchEvent(new Event('input'))}if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();$('#base-config-form').requestSubmit()}};
  $('#base-fill-default').onclick=()=>{texts[target]=documents.defaults[target];paint();$('#base-save-state').textContent='已填入默认配置，保存后生效。'};
  $('#base-clear').onclick=()=>{texts[target]='';paint();$('#base-save-state').textContent='已清空，保存后恢复默认基础配置。'};
  $('#base-cancel').onclick=()=>dialog.close();$('#base-inbounds').onclick=()=>{texts[target]=$('#base-config-text').value;if(Object.keys(texts).some(key=>texts[key]!==persisted[key])){$('#base-error').textContent='请先保存已修改的基础配置，再进入入站设置。';return}dialog.close();location.hash='inbounds'};
  const run=async operation=>{
    if(busy)return;busy=true;$('#base-error').textContent='';
    const active=target,text=$('#base-config-text').value;
    $$('#base-config-form button,#base-config-form textarea,#base-config-form input').forEach(el=>el.disabled=true);$('#close-dialog').disabled=true;
    $('#base-save-state').textContent={save:'正在保存…',preview:'正在合并…',check:'正在运行目标代理内核校验…'}[operation];
    try{
      if(operation==='save'){
        adoptWorkspace(await api('/api/workspace/base-config',{target:active,text,revision:liveWorkspace.revision}),{repaint:false,keepDraft:true});documents.stored[active]=text;persisted[active]=text;
        $('#base-save-state').textContent=native&&active==='sing-box'?'sing-box 基础模板已保存；当前 Mihomo 原生方案未转换。':'已保存。生成配置时会合并；下载前执行原生校验。';
      }else{
        const request={target:active,text,revision:liveWorkspace.revision};
        if(active==='sing-box'&&$('#base-adjust-groups').checked)request.group_mode_overrides=Object.fromEntries(data.groups.filter(g=>['fallback','load-balance'].includes(g.mode)).map(g=>[g.id,'url-test']));
        const result=await api(operation==='check'?'/api/workspace/check-base-config':'/api/workspace/preview-base-config',request,spaceId,operation==='check'?60000:30000);
        if(operation==='preview'){$('#base-preview-code').textContent=result.files[active+(active==='mihomo'?'.yaml':'.json')];$('#base-preview-output').hidden=false;$('#base-save-state').textContent='合并结果为草稿，尚未原生校验。'}
        else $('#base-save-state').textContent='目标代理内核原生校验通过；草稿尚未保存。';
      }
    }catch(error){$('#base-error').textContent=error.message;$('#base-save-state').textContent='未完成，请修正后重试。'}
    finally{busy=false;$$('#base-config-form button,#base-config-form textarea,#base-config-form input').forEach(el=>el.disabled=false);$('#close-dialog').disabled=false;if(native&&target==='sing-box'){$('#base-preview').disabled=true;$('#base-check').disabled=true}}
  };
  $('#base-config-form').onsubmit=e=>{e.preventDefault();run('save')};$('#base-preview').onclick=()=>run('preview');$('#base-check').onclick=()=>run('check');paint();
}
