'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=name=>fs.readFileSync(path.join(root,'web',name),'utf8');
const esc=source('app.js').split(/\r?\n/).find(line=>line.startsWith('const esc ='));
const editor=source('space-editor.js');
const modalSource=editor.slice(editor.indexOf('async function openClientControllerModal('),editor.indexOf('let hermesFreshToken='));

test('native port values render as text in node and inbound lists',()=>{
  const value='<img src=x onerror="synthetic()">';
  const content={innerHTML:''};
  const context={$:()=>content,$$:()=>[],query:'',nativeValue:()=>({mihomo:{listeners:[{name:'fixture',type:'mixed',port:value}]}}),liveWorkspace:{state:{native_view:{nodes:[{name:'fixture',type:'trojan',port:value}]}}}};
  vm.createContext(context);
  vm.runInContext(esc,context);
  for(const name of ['nativeRenderNodes','nativeRenderListeners']){
    vm.runInContext(source('native-workbench.js').split(/\r?\n/).find(line=>line.startsWith('function '+name+'(')),context);
    context[name]();
    assert(!content.innerHTML.includes('<img'));
    assert(content.innerHTML.includes('&lt;img'));
  }
});

test('the final native node renderer also escapes subscribed and manual ports',()=>{
  const content={innerHTML:''};
  const nodes=[{name:'fixture',type:'trojan',origin:'manual',server:'node.example.test',port:'<img src=x onerror="synthetic()">'}];
  const context={$:()=>content,$$:()=>[],query:'',nativeMap:()=>({}),icon:()=>'',bindNativeSearch(){},liveWorkspace:{state:{native_view:{nodes}}}};
  vm.createContext(context);
  const members=source('native-members.js');
  vm.runInContext(esc+'\n'+members.slice(members.indexOf("let nodeOrigin='all'"),members.indexOf('const renderNativeLineGroups=')),context);
  context.nativeRenderNodes();
  assert(!content.innerHTML.includes('<img'));
  assert(content.innerHTML.includes('&lt;img'));
});

test('controller load failure does not open a blank editable form',async()=>{
  let opened=false,message='';
  const context={refreshSpacePresets:async()=>{throw Error('synthetic offline')},showDialog:()=>{opened=true},toast:text=>{message=text}};
  vm.createContext(context);
  vm.runInContext(modalSource,context);
  await context.openClientControllerModal('s');
  assert.equal(opened,false);
  assert(message.includes('未能读取'));
});

test('controller form submits blank secret as preserve and explicit clear separately',async()=>{
  const elements={};
  let submitted,markup;
  const context={
    refreshSpacePresets:async()=>{},spaceCatalog:{spaces:[{id:'s',name:'fixture'}]},
    spacePresetsCache:{controllers:{s:{url:'http://127.0.0.1:9090',enabled:true,has_secret:true}}},
    $:selector=>elements[selector]??={},showDialog:(_,body)=>{markup=body},badge:()=>'',
    toast(){},page:'other',api:async(_,body)=>{submitted=body;return {enabled:false}},
  };
  vm.createContext(context);
  vm.runInContext(esc+'\n'+modalSource,context);
  await context.openClientControllerModal('s');
  assert(markup.includes('已配置；留空保持原值'));
  assert(!markup.includes('value="synthetic-secret"'));
  elements['#cc-enabled']={checked:true};
  elements['#cc-url']={value:'http://127.0.0.1:9090'};
  elements['#cc-secret']={value:''};
  elements['#cc-clear-secret']={checked:false};
  elements['#dialog']={close(){}};
  await elements['#client-ctrl-form'].onsubmit({preventDefault(){}});
  assert.equal(submitted.secret,'');
  assert.equal(submitted.clear_secret,false);
  elements['#cc-clear-secret'].checked=true;
  await elements['#client-ctrl-form'].onsubmit({preventDefault(){}});
  assert.equal(submitted.clear_secret,true);
});

test('copy failure never includes the copied secret in a toast',async()=>{
  let message='';
  const context={navigator:{clipboard:{writeText:async()=>{throw Error('synthetic denied')}}},toast:text=>{message=text}};
  vm.createContext(context);
  const copySource=editor.slice(editor.indexOf('async function copyTextSafe('),editor.indexOf('async function copySpaceSubscriptionDirect('));
  vm.runInContext(copySource,context);
  await context.copyTextSafe('ymh_synthetic_secret');
  assert(!message.includes('ymh_synthetic_secret'));
  assert(message.includes('复制失败'));
});

test('Hermes uses Authorization and clears transient secrets when leaving the page',async()=>{
  const elements={'#content':{}};
  const context={
    page:'hermes',location:{origin:'http://127.0.0.1:8767'},
    window:{addEventListener:(_,listener)=>{context.hashChanged=listener}},
    $:selector=>elements[selector]??={},badge:()=>'',toast(){},serverTime:()=>'',
    api:async(url)=>url.endsWith('/rotate')?{configured:true,token:'ymh_synthetic_secret',installation_command:'synthetic pinned command'}:{configured:false},
  };
  vm.createContext(context);
  const hermesSource=editor.slice(editor.indexOf('let hermesFreshToken='),editor.indexOf('function openHermesIntegration('));
  vm.runInContext(esc+'\n'+hermesSource,context);
  await context.renderHermesPage();
  await elements['#hm-rotate'].onclick({target:{}});
  assert(elements['#content'].innerHTML.includes('Authorization 请求头'));
  assert(!elements['#content'].innerHTML.includes('/mcp?token='));
  assert.equal(vm.runInContext('hermesFreshToken',context),'ymh_synthetic_secret');
  context.page='spaces';context.hashChanged();
  assert.equal(vm.runInContext('hermesFreshToken',context),null);
  assert.equal(vm.runInContext('hermesFreshCommand',context),null);
});

test('Hermes response after a route change cannot overwrite another page',async()=>{
  let resolve;
  const content={innerHTML:''};
  const context={page:'hermes',window:{addEventListener(){}},$:()=>content,api:()=>new Promise(done=>{resolve=done})};
  vm.createContext(context);
  vm.runInContext(editor.slice(editor.indexOf('let hermesFreshToken='),editor.indexOf('function openHermesIntegration(')),context);
  const pending=context.renderHermesPage();
  context.page='spaces';content.innerHTML='space contents';resolve({configured:false});
  await pending;
  assert.equal(content.innerHTML,'space contents');
});

test('queued persist calls serialize revisions and do not overwrite newer in-memory edits until drained',async()=>{
  const calls=[];
  const resolvers=[];
  const context={
    spaceId:'default',
    draft:null,
    selectedGroup:'g1',
    data:{groups:[{id:'g1'}],fallback:'DIRECT'},
    saved:{groups:[{id:'g1'}],fallback:'DIRECT'},
    liveWorkspace:{state:null,revision:1,saving:false},
    clone:value=>JSON.parse(JSON.stringify(value)),
    render(){context.renderedFallback=context.data.fallback},
    nav(){},
    toast(){},
    api:(_path,body)=>new Promise(resolve=>{
      calls.push(body);
      resolvers.push(resolve);
    }),
  };
  vm.createContext(context);
  const ws=source('workspace.js');
  const adoptLine=ws.split(/\r?\n/).find(line=>line.startsWith('function adoptWorkspace('));
  const commitLine=ws.split(/\r?\n/).find(line=>line.startsWith('function commitDraft('));
  const persistLine=ws.split(/\r?\n/).find(line=>line.startsWith('persist=function('));
  vm.runInContext([adoptLine,commitLine,persistLine].join('\n'),context);

  context.data.fallback='PROXY-1';
  const p1=context.persist();
  context.data.fallback='PROXY-2';
  const p2=context.persist();
  context.data.fallback='PROXY-3';
  await Promise.resolve();

  assert.equal(calls.length,1);
  assert.equal(calls[0].revision,1);
  assert.equal(calls[0].profile.fallback,'PROXY-1');

  resolvers[0]({revision:2,profile:{groups:[{id:'g1'}],fallback:'PROXY-1'}});
  await p1;
  assert.equal(context.data.fallback,'PROXY-3');
  assert.equal(context.liveWorkspace.revision,2);
  assert.equal(calls.length,2);
  assert.equal(calls[1].revision,2);
  assert.equal(calls[1].profile.fallback,'PROXY-2');

  resolvers[1]({revision:3,profile:{groups:[{id:'g1'}],fallback:'PROXY-2'}});
  await p2;
  assert.equal(context.liveWorkspace.revision,3);
  assert.equal(context.data.fallback,'PROXY-2');
  assert.equal(context.liveWorkspace.saving,false);
});

test('paintSpaceCards preserves presets load error and retry button across subsequent repaints',async()=>{
  const elements={'#content':{innerHTML:''},'#space-list-status':{innerHTML:''}};
  const context={
    page:'spaces',
    spaceId:'default',
    spaceCatalog:{spaces:[]},
    location:{origin:'http://127.0.0.1:8767'},
    $:selector=>elements[selector]??={},
    $$:()=>[],
    badge:()=>'',
    actionIcon:()=>'',
    resourceMenu:()=>'',
    bindResourceMenus(){},
    openConfigImport(){},
    openConfigurationExport(){},
    openSubscriptionLink(){},
    copySpaceSubscriptionDirect(){},
    openClientControllerModal(){},
    switchSpace:async()=>{},
    editSpace(){},
    deleteSpace(){},
    toast(){},
    api:async()=>{throw Error('presets timeout')},
  };
  vm.createContext(context);
  const presetsSlice=editor.slice(editor.indexOf('const DEFAULT_PRESET_REGIONS='),editor.indexOf('async function copyTextSafe('));
  const paintSlice=editor.slice(editor.indexOf('function paintSpacePresetsStatus('),editor.indexOf('async function editSpace('));
  vm.runInContext([esc,presetsSlice,paintSlice].join('\n'),context);

  await context.refreshSpacePresets(false);
  context.paintSpaceCards();
  assert(elements['#space-list-status'].innerHTML.includes('presets timeout'));
  assert(elements['#space-list-status'].innerHTML.includes('space-presets-retry'));
});

test('quick copy subscription button aborts with toast instead of prompting to create link when presets fail',async()=>{
  let dialogOpened=false,toastMsg='';
  const elements={'#export':{before(){}},'.mobile-sidebar-actions':{append(){}}};
  const context={
    spaceId:'default',
    spacePresetsCache:{links_by_space:{}},
    document:{createElement:()=>({})},
    $:selector=>elements[selector]??={},
    copySpaceSubscriptionDirect(){},
    openSubscriptionLink(){},
    setNavigation(){},
    activeSpace:()=>({id:'default',name:'Default'}),
    refreshSpacePresets:async()=>{throw Error('network failure')},
    showDialog:()=>{dialogOpened=true},
    toast:msg=>{toastMsg=msg},
  };
  vm.createContext(context);
  const subSource=source('subscription-links.js');
  vm.runInContext(esc+'\n'+subSource.slice(subSource.indexOf('const quickCopySubBtn=')),context);
  const btn=vm.runInContext('quickCopySubBtn',context);
  await btn.onclick();
  assert.equal(dialogOpened,false);
  assert(toastMsg.includes('未能读取订阅链接状态'));
});

