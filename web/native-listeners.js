'use strict';

const listenerFields=new Set(['name','type','listen','port','cipher','password','users','certificate','private-key','alpn','udp','proxy','rule','routing-mark']);
const listenerProtocols={mixed:'HTTP / SOCKS',http:'HTTP',socks:'SOCKS',shadowsocks:'Shadowsocks',trojan:'Trojan',hysteria2:'Hysteria2'};
const fullNativeListenerEditor=nativeListenerEditor;
nativeListenerEditor=function(index=null){
  const original=index===null?null:nativeValue().mihomo.listeners[index];
  const base=clone(original||{name:'',type:'mixed',listen:'127.0.0.1',port:17891});
  const validUsers=!base.users||(base.type==='hysteria2'?typeof base.users==='object'&&!Array.isArray(base.users)&&Object.values(base.users).every(p=>typeof p==='string'):Array.isArray(base.users)&&base.users.every(u=>u&&typeof u==='object'&&typeof u.username==='string'&&typeof u.password==='string'));
  if(!listenerProtocols[base.type]||!validUsers||base.alpn&&!Array.isArray(base.alpn))return fullNativeListenerEditor(index);
  let users=base.type==='hysteria2'?Object.entries(base.users||{}).map(([username,password])=>({username,password})):clone(base.users||[]),saving=false;
  const text=(id,label,value='',extra='')=>`<label class="field">${label}<input id="li-${id}" value="${esc(value)}" ${extra}></label>`;
  showDialog(original?'编辑入站':'添加入站',`<form id="listener-form"><div class="fields">${text('name','入站名称',base.name,original?'readonly':'required')}
    <label class="field">协议<select id="li-type" ${original?'disabled':''}>${Object.entries(listenerProtocols).map(([key,label])=>`<option value="${key}" ${base.type===key?'selected':''}>${label}</option>`).join('')}</select></label>
    ${text('listen','监听地址',base.listen||'0.0.0.0','required')}${text('port','监听端口',base.port??'','inputmode="numeric" required')}
    </div><p class="preview-note">监听地址决定哪些设备能接入；连接流量默认使用当前规则列表。</p>
    <section id="li-ss" class="listener-section"><h3>连接凭据</h3><div class="fields">${text('cipher','加密方式',base.cipher||'aes-128-gcm')}${text('password','连接密码',base.password||'','type="password" autocomplete="new-password"')}</div></section>
    <section id="li-user-section" class="listener-section"><div class="section-title"><h3>连接用户</h3><button type="button" class="button" id="li-add-user">添加用户</button></div><div id="li-users"></div><p id="li-user-hint" class="preview-note"></p></section>
    <section id="li-tls" class="listener-section"><h3>证书与私钥</h3><p class="preview-note">可使用配置包内的原有路径，也可粘贴 PEM 内容。</p><label class="field">证书<textarea id="li-certificate" rows="3" spellcheck="false">${esc(base.certificate||'')}</textarea></label><label class="field">私钥<textarea id="li-private-key" rows="3" spellcheck="false">${esc(base['private-key']||'')}</textarea></label>${text('alpn','ALPN（逗号或换行分隔）',(base.alpn||[]).join(', '))}</section>
    <details class="advanced-options listener-section" ${base.proxy||base.rule?'open':''}><summary>独立出口与其他选项</summary><p class="preview-note">通常保持“跟随当前规则”。指定出口会让此入站直接使用该出口；原有子规则引用完整保留。</p><label class="field">固定出口<select id="li-proxy"><option value="">跟随当前规则</option>${nativePolicyTargets(base.proxy||'')}</select></label>${text('rule','子规则名称（可选）',base.rule||'')}
    <div class="fields">${text('routing-mark','路由标记（Linux，可选）',base['routing-mark']??'','type="number" min="0"')}<label class="field">UDP<select id="li-udp"><option value="">使用内核默认</option><option value="true" ${base.udp===true?'selected':''}>启用</option><option value="false" ${base.udp===false?'selected':''}>关闭</option></select></label></div></details>
    <details class="advanced-options listener-section"><summary>其他原生参数（JSON）</summary><p class="preview-note">未在表单中展示的字段保留在这里，按原生格式导出。</p><textarea id="li-advanced" class="mono" rows="7" spellcheck="false">${esc(JSON.stringify(Object.fromEntries(Object.entries(base).filter(([key])=>!listenerFields.has(key))),null,2))}</textarea></details>
    <p class="error-message" id="li-error" role="alert"></p><div class="native-member-footer"><button type="button" class="button" id="li-cancel">取消</button><button type="submit" class="button primary" id="li-save">保存入站</button></div></form>`);
  markMemberDialog();setNativeEditorReturn(null);if(!base.proxy)$('#li-proxy').value='';
  const seed=Object.fromEntries(['listen','cipher','password','certificate','private-key','alpn','proxy','rule','routing-mark','udp'].map(key=>[key,$('#li-'+key).value]));
  const readUsers=()=>{$$('[data-li-user-name]').forEach(input=>users[Number(input.dataset.liUserName)].username=input.value);$$('[data-li-user-password]').forEach(input=>users[Number(input.dataset.liUserPassword)].password=input.value)};
  const paintUsers=()=>{
    $('#li-users').innerHTML=users.map((u,i)=>`<div class="listener-user"><label class="field">用户名<input data-li-user-name="${i}" value="${esc(u.username)}" autocomplete="off"></label><label class="field">密码<input data-li-user-password="${i}" type="password" value="${esc(u.password)}" autocomplete="new-password"></label><button type="button" class="icon-button" data-li-user-remove="${i}" aria-label="移除第 ${i+1} 个连接用户">${actionIcon('trash')}</button></div>`).join('')||'<p class="preview-note">尚未添加连接用户。</p>';
    $$('[data-li-user-remove]').forEach(button=>button.onclick=()=>{readUsers();users.splice(Number(button.dataset.liUserRemove),1);paintUsers()});
  };
  const paintType=()=>{
    const type=$('#li-type').value,ss=type==='shadowsocks',tls=['trojan','hysteria2'].includes(type);
    $('#li-ss').hidden=!ss;$('#li-tls').hidden=!tls;$('#li-user-section').hidden=ss;
    for(const key of ['cipher','password'])$('#li-'+key).required=ss;
    for(const key of ['certificate','private-key'])$('#li-'+key).required=tls;
    $('#li-user-hint').textContent=tls?'请至少添加一位用户，并填写证书与私钥。':'不设置用户时允许无认证连接，请根据监听范围决定。';
  };
  $('#li-type').onchange=paintType;paintUsers();paintType();
  $('#li-add-user').onclick=()=>{readUsers();users.push({username:'',password:''});paintUsers()};
  $('#li-cancel').onclick=()=>$('#dialog').close();
  const prevent=event=>{if(saving)event.preventDefault()};$('#dialog').addEventListener('cancel',prevent);$('#dialog').addEventListener('close',()=>$('#dialog').removeEventListener('cancel',prevent),{once:true});
  $('#listener-form').onsubmit=async event=>{
    event.preventDefault();if(saving)return;
    try{
      readUsers();const type=$('#li-type').value,changes={};
      const put=(key,value)=>{if(original&&Object.hasOwn(seed,key)&&$('#li-'+key).value===seed[key])return;if(JSON.stringify(value)!==JSON.stringify(original?.[key]))changes[key]=value===undefined?null:value};
      const read=key=>{const value=$('#li-'+key).value;if(original&&!Object.hasOwn(original,key)&&seed[key]===value)return undefined;return value||undefined};
      put('name',$('#li-name').value.trim());put('type',type);put('listen',read('listen'));
      const port=$('#li-port').value.trim();put('port',original&&port===String(original.port)?original.port:/^\d+$/.test(port)?Number(port):port);
      if(type==='shadowsocks'){put('cipher',read('cipher'));put('password',read('password'))}
      else{
        const names=users.map(u=>u.username);if(new Set(names).size!==names.length)throw Error('用户名不能重复。');
        const value=type==='hysteria2'?Object.fromEntries(users.map(u=>[u.username,u.password])):users;
        put('users',users.length||Object.hasOwn(original||{},'users')?value:undefined);
      }
      if(['trojan','hysteria2'].includes(type)){put('certificate',read('certificate'));put('private-key',read('private-key'));const alpn=read('alpn');put('alpn',alpn?alpn.split(/[,\n]+/).map(v=>v.trim()).filter(Boolean):undefined)}
      for(const key of ['proxy','rule'])put(key,read(key));
      const mark=read('routing-mark');put('routing-mark',mark===undefined?undefined:Number(mark));const udp=read('udp');put('udp',udp===undefined?undefined:udp==='true');
      const advanced=JSON.parse($('#li-advanced').value);if(!advanced||Array.isArray(advanced)||typeof advanced!=='object')throw Error('其他原生参数需要 JSON 对象。');
      saving=true;$('#li-save').disabled=true;$('#close-dialog').disabled=true;
      adoptWorkspace(await api('/api/workspace/native-listener',{name:original?.name??null,changes,advanced,revision:liveWorkspace.revision}));$('#dialog').close();toast('入站已保存，导出时与规则及节点一起校验。');
    }catch(error){$('#li-error').textContent=error instanceof SyntaxError?'其他原生参数 JSON 格式无效。':error.message}
    finally{saving=false;$('#close-dialog').disabled=false;if($('#li-save'))$('#li-save').disabled=false}
  };
};
