'use strict';
// A file:// copy cannot access the persistent service. Never show demo records as live data.
const renderFromServer = render;
render = function() {
  const ready = !!liveWorkspace.state;
  for(const selector of ['#create','#space-select','#export','#base-config','#edit-current-space']) $(selector).disabled = !ready;
  if(ready){renderFromServer();return}
  nav();$$('.nav-count').forEach(el=>el.textContent='—');
  $('#breadcrumb').textContent=pages[page][0];$('#page-title').textContent=pages[page][0];$('#page-description').textContent=pages[page][1];
  $('#create').hidden=false;$('#create').innerHTML=icon('plus')+pages[page][2];
  const local=location.protocol==='file:',failed=!!liveWorkspace.connectionError;
  $('.demo-badge').textContent=local?'本地文件 · 未连接':failed?'连接失败':'正在连接服务端…';
  $('.local-label').textContent='服务端工作区';
  $('.sidebar-bottom p').textContent='连接服务端后管理真实订阅、规则和入站。';
  const address='http://127.0.0.1:8767/#'+page;
  $('#content').innerHTML=`<section class="panel connection-state" role="status"><h2>${local?'请从服务端打开 YoMesh':failed?'暂时无法连接工作区':'正在读取配置工作区'}</h2><p>${local?'直接打开 HTML 文件无法保存订阅、规则或入站。请进入服务端工作台，或先启动本机服务。':failed?'检查服务与网络后重新连接。连接成功后即可添加、删除和修改配置。':'订阅、节点与规则从服务端加载。'}</p>${local||failed?`<div class="filter-row"><a class="button primary" href="${address}">打开服务端工作台</a>${local?'':'<button class="button" id="retry-workspace">重新连接</button>'}</div><p class="preview-note">本机服务：python scripts/yomesh.py serve --port 8767，然后访问 http://127.0.0.1:8767。</p>`:''}</section>`;
  if($('#retry-workspace'))$('#retry-workspace').onclick=()=>initializeWorkspace();
};
render();
