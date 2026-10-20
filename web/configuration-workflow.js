'use strict';

for(const [key,label] of Object.entries({spaces:'配置方案',sources:'节点订阅',nodes:'全部节点',groups:'节点分组',library:'分流规则',inbounds:'接入设置'}))pages[key][0]=label;
pages.spaces[2]='新建配置方案';pages.sources[2]='添加节点订阅';pages.groups[2]='新建节点分组';pages.inbounds[2]='添加接入';
pages.groups[1]='管理线路组、回家组和共用出口组；规则专用出口在“分流规则”中配置。';
pages.library[1]='配置匹配内容与节点出口，按从上到下的顺序生效。';
const spaceSwitcherLabel=$('.space-switcher').firstChild;if(spaceSwitcherLabel?.nodeType===Node.TEXT_NODE)spaceSwitcherLabel.textContent='当前配置方案';
$('#space-select').setAttribute('aria-label','当前配置方案');$('#base-config').textContent='基础设置';
const mobileBase=$('[data-mobile-control="base-config"]');if(mobileBase)mobileBase.textContent='基础设置';

const renderBeforeWorkflow=render;
render=function(){
  if(page==='policies'){page='library';nativeRuleSourceMode=false;portableRuleSources=false;query='';history.replaceState(null,'','#library')}
  if(page!=='library')ruleOrderCleanup();
  renderBeforeWorkflow();
  const dnsLabel=(nativeMode()?nativeDnsName():'MosDNS')+' 配置';
  const dnsNav=$('#navigation a[href="#dns"] span');if(dnsNav)dnsNav.textContent=dnsLabel;
  if(page==='dns'){$('#page-title').textContent=dnsLabel;$('#breadcrumb').textContent=dnsLabel}
  $('#export').disabled=page==='dns'&&nativeMode()&&!nativeDNSAvailable();
  if($('#export').disabled)$('#export').textContent='无独立 DNS 配置';
  if(nativeMode()){
    const count=$('#navigation a[href="#groups"] .nav-count');if(count)count.textContent=liveWorkspace.state?.native_view?.node_groups?.length??0;
  }
  if(liveWorkspace.state&&page==='library')$('#create').innerHTML=icon('plus')+(nativeMode()?nativeRuleSourceMode?'添加规则来源':'添加规则':portableRuleSources?'添加规则来源':'添加规则');
  if(liveWorkspace.state&&!nativeMode()){
    const count=$('#navigation a[href="#library"] .nav-count');if(count)count.textContent=data.policies.length;
  }
};
