'use strict';

function subscriptionDeliverySummary(link){
  return link?.rule_address==='site'?`规则地址已转换为 ${link.base_url}；YoMesh 从上游更新规则。`:'规则地址保留上游来源；客户端直接更新远程规则。';
}

async function openSubscriptionLink(){
  try{await saveSpaceDrafts()}catch(error){toast(error.message);return}
  let listing;
  try{listing=await api('/api/workspace/subscriptions')}catch(error){toast(error.message);return}
  const native=nativeMode(),savedLink=listing.links.at(-1);
  showDialog('配置订阅链接',`<form id="subscription-form">
    <p class="export-scope">${esc(activeSpace()?.name||'当前配置空间')} · 在 LocalDNS 的 Mihomo / sing-box 管理器或客户端中填入此链接即可自动拉取配置</p>
    <section id="subscription-result" ${savedLink?'':'hidden'} aria-live="polite"><label class="field">配置订阅链接<input id="subscription-url" type="text" readonly spellcheck="false" value="${esc(savedLink?.url||'')}"></label><p class="preview-note" id="subscription-status">${esc(subscriptionDeliverySummary(savedLink))} 链接持有者可读取该配置，请妥善保存。</p><div class="dialog-actions"><button class="button" id="subscription-revoke" type="button">停用此链接</button><button class="button primary" id="subscription-copy" type="button">复制链接</button></div></section>
    <details class="it-subscription-settings" id="subscription-settings" ${savedLink?'':'open'}><summary>修改访问地址与规则地址</summary>
    ${native?'':`<label class="field">代理内核<select id="subscription-proxy"><option value="mihomo">Mihomo · YAML</option><option value="sing-box">sing-box · JSON</option></select></label>`}
    <label class="field">对外访问地址<input id="subscription-base" type="url" required value="${esc(savedLink?.base_url||location.origin)}" placeholder="http://192.0.2.10:8767"><small>先使用当前 IP；配置反向代理后可在这里改为域名。</small></label>
    <fieldset class="rule-output-choices"><legend>客户端使用的规则地址</legend>
      <label class="rule-output-choice"><input type="radio" name="subscription_rules" value="original" checked><span><strong>保留原始地址</strong><small>远程规则从原订阅更新，本地文件由本站提供。</small></span></label>
      <label class="rule-output-choice"><input type="radio" name="subscription_rules" value="site"><span><strong>使用本站地址</strong><small>${native?'规则订阅统一使用上面的 IP 或域名，上游不可用时使用已验证缓存。':'规则订阅统一使用上面的 IP 或域名，提供工作台已保存的规则快照。'}</small></span></label>
    </fieldset>
    <p class="preview-note">转换只作用于此配置订阅；规则详情始终保留上游源地址。链接跟随当前空间的已保存配置。</p>
    <p class="error-message" id="subscription-error" role="alert"></p>
    <div class="dialog-actions"><button class="button primary" type="submit" id="subscription-create">校验并生成链接</button></div>

    </details>
  </form>`);
  let current=savedLink,pending=false;
  if($('#subscription-proxy'))$('#subscription-proxy').value=savedLink?.proxy||activeSpace()?.proxy||'mihomo';
  $(`#subscription-form input[name=subscription_rules][value=${savedLink?.rule_address||'original'}]`).checked=true;
  const dialog=$('#dialog'),prevent=e=>{if(pending)e.preventDefault()};
  dialog.addEventListener('cancel',prevent);dialog.addEventListener('close',()=>dialog.removeEventListener('cancel',prevent),{once:true});
  const busy=value=>{pending=value;$('#close-dialog').disabled=value;$$('#subscription-form button,#subscription-form select,#subscription-form input:not([readonly])').forEach(el=>el.disabled=value)};
  $('#subscription-form').onsubmit=async event=>{
    event.preventDefault();if(pending)return;busy(true);$('#subscription-create').textContent='正在生成并校验…';$('#subscription-error').textContent='';
    try{current=await api('/api/workspace/subscriptions',{proxy:$('#subscription-proxy')?.value||'mihomo',base_url:$('#subscription-base').value.trim(),rule_address:$('#subscription-form input[name=subscription_rules]:checked').value,revision:liveWorkspace.revision});$('#subscription-url').value=current.url;$('#subscription-result').hidden=false;$('#subscription-settings').open=false;$('#subscription-status').textContent=`原生校验通过 · ${current.rule_count} 个规则来源。${subscriptionDeliverySummary(current)} 链接持有者可读取配置。`;if(typeof spacePresetsCache==='object'&&spacePresetsCache){spacePresetsCache.links_by_space??={};spacePresetsCache.links_by_space[spaceId]=[current];if(page==='spaces'&&typeof paintSpaceCards==='function')paintSpaceCards()}}
    catch(error){$('#subscription-error').textContent=error.message}
    finally{busy(false);$('#subscription-create').textContent='校验并生成链接'}
  };
  $('#subscription-form').addEventListener('input',event=>{if(!pending&&event.target.id!=='subscription-url')$('#subscription-result').hidden=true});
  $('#subscription-copy').onclick=async()=>{try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText($('#subscription-url').value);else{$('#subscription-url').focus();$('#subscription-url').select();if(!document.execCommand('copy'))throw Error('copy unavailable')}toast('配置订阅链接已复制。')}catch(error){$('#subscription-url').focus();$('#subscription-url').select();toast('请复制已选中的链接。')}};
  $('#subscription-revoke').onclick=async()=>{if(!current||pending)return;busy(true);try{await api('/api/workspace/subscriptions/revoke',{id:current.id});current=null;$('#subscription-settings').open=true;$('#subscription-result').hidden=true;$('#subscription-url').value='';if(typeof spacePresetsCache==='object'&&spacePresetsCache?.links_by_space){spacePresetsCache.links_by_space[spaceId]=[];if(page==='spaces'&&typeof paintSpaceCards==='function')paintSpaceCards()}toast('此订阅链接已停用。')}catch(error){$('#subscription-error').textContent=error.message}finally{busy(false)}};
}

const quickCopySubBtn=document.createElement('button');
quickCopySubBtn.id='quick-copy-subscription';quickCopySubBtn.type='button';quickCopySubBtn.className='button';quickCopySubBtn.textContent='🔗 一键复制订阅';
quickCopySubBtn.title='一键复制当前方案的持久订阅链接（如未生成则自动创建并复制）';
$('#export').before(quickCopySubBtn);
quickCopySubBtn.onclick=()=>{if(typeof copySpaceSubscriptionDirect==='function')copySpaceSubscriptionDirect(spaceId);else openSubscriptionLink()};
const subscriptionButton=document.createElement('button');
subscriptionButton.id='subscription-link';subscriptionButton.type='button';subscriptionButton.className='button';subscriptionButton.textContent='订阅链接';
subscriptionButton.disabled=true;
$('#export').before(subscriptionButton);subscriptionButton.onclick=openSubscriptionLink;
const mobileSubscription=document.createElement('button');mobileSubscription.type='button';mobileSubscription.className='button';mobileSubscription.textContent='订阅链接';
$('.mobile-sidebar-actions').append(mobileSubscription);mobileSubscription.onclick=()=>{setNavigation(false);openSubscriptionLink()};
