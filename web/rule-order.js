'use strict';

// The backend supplies the complete ordered IDs; this controller only moves IDs.
let ruleOrderCleanup=()=>{};
function ruleOrderControl(id,name,index,enabled=true){
  return `<div class="rule-order-control"><button type="button" class="drag-handle np-handle" data-rule-handle="${esc(id)}" draggable="false" aria-label="移动${esc(name)}，上下方向键调整顺序" ${enabled?'':'disabled'}>${actionIcon('grip')}</button><span>${index+1}</span></div>`;
}
function bindRuleOrder({ids,save,enabled=true}){
  ruleOrderCleanup();
  const root=$('#content'),controller=new AbortController(),signal=controller.signal;
  let drag=null,pointer=null,saving=false;
  const rows=()=>$$('[data-rule-row]',root);
  const reset=()=>{
    rows().forEach(r=>r.classList.remove('np-drop-before','np-drop-after','np-grabbed'));
    const previous=pointer;drag=null;pointer=null;liveWorkspace.dragging=false;
    if(previous?.handle.hasPointerCapture(previous.pointerId))previous.handle.releasePointerCapture(previous.pointerId);
  };
  ruleOrderCleanup=()=>{reset();controller.abort()};
  if(!enabled)return;
  const busy=()=>saving||liveWorkspace.saving;
  const rowAt=y=>rows().find(r=>y<r.getBoundingClientRect().bottom)||rows().at(-1);
  const place=(id,row,y)=>{
    const order=ids(),from=order.indexOf(id),target=row?.dataset.ruleRow;
    if(from<0||from===order.length-1||!target||id===target)return null;
    order.splice(from,1);let to=order.indexOf(target);if(to<0)return null;
    const box=row.getBoundingClientRect();
    if(!row.hasAttribute('data-rule-fallback')&&y>box.top+box.height/2)to++;
    order.splice(to,0,id);return order;
  };
  const mark=(row,y)=>{
    rows().forEach(r=>r.classList.remove('np-drop-before','np-drop-after'));
    const box=row.getBoundingClientRect();row.classList.add(!row.hasAttribute('data-rule-fallback')&&y>box.top+box.height/2?'np-drop-after':'np-drop-before');
  };
  const commit=async(order,id)=>{
    if(!order||busy())return;const before=ids();if(before.every((v,i)=>v===order[i]))return;
    saving=true;const scope=spaceId;
    try{await save(order,id)}catch(error){toast(error.message)}finally{
      saving=false;if(spaceId===scope)$$('[data-rule-handle]').find(h=>h.dataset.ruleHandle===id)?.focus();
    }
  };
  root.addEventListener('dragstart',event=>{
    const handle=event.target.closest('[data-rule-handle]');if(!handle)return;
    if(busy()){event.preventDefault();return}drag=handle.dataset.ruleHandle;liveWorkspace.dragging=true;
    event.dataTransfer.setData('text/plain',drag);event.dataTransfer.effectAllowed='move';handle.closest('[data-rule-row]').classList.add('np-grabbed');
  },{signal});
  root.addEventListener('dragover',event=>{if(!drag)return;const row=event.target.closest('[data-rule-row]');if(row){event.preventDefault();mark(row,event.clientY)}},{signal});
  root.addEventListener('drop',event=>{if(!drag)return;event.preventDefault();const id=drag,order=place(id,event.target.closest('[data-rule-row]'),event.clientY);reset();commit(order,id)},{signal});
  root.addEventListener('dragend',reset,{signal});
  root.addEventListener('keydown',event=>{
    const handle=event.target.closest('[data-rule-handle]');if(!handle||busy()||!['ArrowUp','ArrowDown'].includes(event.key))return;
    event.preventDefault();const order=ids(),from=order.indexOf(handle.dataset.ruleHandle),to=from+(event.key==='ArrowUp'?-1:1);
    if(from<0||to<0||to>=order.length-1)return;[order[from],order[to]]=[order[to],order[from]];commit(order,handle.dataset.ruleHandle);
  },{signal});
  root.addEventListener('pointerdown',event=>{
    const handle=event.target.closest('[data-rule-handle]');if(!handle||handle.disabled||pointer||busy())return;
    pointer={id:handle.dataset.ruleHandle,pointerId:event.pointerId,start:event.clientY,y:event.clientY,handle,moved:false};
    handle.setPointerCapture(event.pointerId);handle.focus();liveWorkspace.dragging=true;event.preventDefault();
  },{signal});
  root.addEventListener('pointermove',event=>{
    if(!pointer||pointer.pointerId!==event.pointerId)return;pointer.y=event.clientY;
    if(!pointer.moved&&Math.abs(pointer.y-pointer.start)<6)return;pointer.moved=true;
    const row=rowAt(pointer.y);if(row)mark(row,pointer.y);
    if(event.clientY<80)window.scrollBy(0,-12);else if(event.clientY>innerHeight-80)window.scrollBy(0,12);
  },{signal});
  root.addEventListener('pointerup',event=>{
    if(!pointer||pointer.pointerId!==event.pointerId)return;const state=pointer;
    const order=state.moved?place(state.id,rowAt(event.clientY),event.clientY):null;reset();commit(order,state.id);
  },{signal});
  root.addEventListener('pointercancel',event=>{if(pointer?.pointerId===event.pointerId)reset()},{signal});
  root.addEventListener('lostpointercapture',event=>{if(pointer?.pointerId===event.pointerId)reset()},{signal});
  window.addEventListener('blur',reset,{signal});
}

function showRulePosition(id){
  const native=nativeMode(),rows=native?nativeUnitRows().filter(r=>!r.fallback):data.policies;
  const index=rows.findIndex(r=>r.id===id);if(index<0)return;
  showDialog('调整规则位置',`<form id="rule-position-form"><p>${esc(rows[index].name)}</p><label class="field">移到第几位<input id="rule-position" type="number" min="1" max="${rows.length}" value="${index+1}" required></label><p class="preview-note">只调整优先级；兜底始终固定在最后。</p><p id="rule-position-error" class="error-message" role="alert"></p><div class="dialog-actions"><button class="button" type="button" id="rule-position-cancel">取消</button><button class="button primary" type="submit">保存位置</button></div></form>`);
  $('#rule-position-cancel').onclick=()=>$('#dialog').close();
  $('#rule-position-form').onsubmit=async event=>{
    event.preventDefault();const to=Number($('#rule-position').value)-1;if(to<0||to>=rows.length)return;
    const order=(native?nativeUnitRows():data.policies).map(p=>p.id);order.splice(index,1);order.splice(to,0,id);
    try{if(native)await nativePolicyOrder(order);else await savePolicyOrder(order,id);$('#dialog').close()}catch(error){$('#rule-position-error').textContent=error.message}
  };
}
