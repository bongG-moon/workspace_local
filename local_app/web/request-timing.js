"use strict";

// Completed request metadata only: no polling, clocks, or per-token rendering.
globalThis.WorkspaceRequestTiming = (() => {
  const LIMIT=150, records=new Map(), views=new Map();
  const outcomes={done:"",stopped:"답변 중지",error:"오류로 종료"};
  let sessionId=null;
  const current=()=>sessionId&&sessionId===active?.id;
  function normalize(value){
    if(!value||typeof value.runId!=="string"||!value.runId||value.runId.length>64||
      typeof value.status!=="string"||!Object.prototype.hasOwnProperty.call(outcomes,value.status)||
      !Number.isSafeInteger(value.durationMs)||value.durationMs<0||value.durationMs>=1e12||
      typeof value.startedAt!=="number"||!Number.isFinite(value.startedAt)||value.startedAt<=0||value.startedAt>=1e12||
      typeof value.finishedAt!=="number"||!Number.isFinite(value.finishedAt)||value.finishedAt<=0||value.finishedAt>=1e12)return null;
    // The saved duration uses a monotonic clock. A Windows clock correction
    // can move finishedAt before startedAt without invalidating that duration.
    return {runId:value.runId,startedAt:value.startedAt,finishedAt:value.finishedAt,durationMs:value.durationMs,status:value.status};
  }
  function format(ms){
    if(ms<1000)return "1초 미만";
    const seconds=Math.floor(ms/1000),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
    return [hours?`${hours}시간`:"",minutes?`${minutes}분`:"",rest?`${rest}초`:""].filter(Boolean).join(" ");
  }
  function reset(id=null){for(const node of views.values())node.remove();records.clear();views.clear();sessionId=id;}
  function forget(id){views.get(id)?.remove();views.delete(id);records.delete(id);}
  function sync(runId){
    if(!current()||!records.size||runId&&!records.has(runId))return;
    const conversation=$("conversation"),children=[...conversation.children];
    const anchors=new Map();
    for(const child of children)if(child.classList.contains("message")&&child.dataset.runId)anchors.set(child.dataset.runId,child);
    for(const [id,record]of records){
      if(runId&&id!==runId)continue;
      const anchor=anchors.get(id);if(!anchor){views.get(id)?.remove();views.delete(id);continue;}
      let footer=views.get(id);
      if(!footer){
        footer=el("div",null,"request-timing");footer.dataset.runId=id;
        const icon=document.createElementNS("http://www.w3.org/2000/svg","svg"),use=document.createElementNS("http://www.w3.org/2000/svg","use");
        icon.setAttribute("class","icon");icon.setAttribute("aria-hidden","true");use.setAttribute("href","#i-clock");icon.append(use);
        footer.append(icon,el("span",null,"request-timing-duration"),el("span",null,"request-timing-outcome"));views.set(id,footer);
      }
      const duration=`${format(record.durationMs)} 소요`,outcome=outcomes[record.status];
      footer.dataset.state=record.status;
      const label=footer.querySelector(".request-timing-duration"),status=footer.querySelector(".request-timing-outcome");
      if(label.textContent!==duration)label.textContent=duration;
      status.hidden=!outcome;if(status.textContent!==outcome)status.textContent=outcome;
      const explanation="요청 실행부터 종료까지의 시간입니다. 승인·답변 대기를 포함하며, 이어 할 일의 대기 시간은 제외합니다.";
      footer.title=explanation;footer.setAttribute("aria-label",`${duration}${outcome?` · ${outcome}`:""}. ${explanation}`);
      if(anchor.nextSibling!==footer)conversation.insertBefore(footer,anchor.nextSibling);
    }
  }
  function render(value){
    if(!current())return false;const record=normalize(value);if(!record)return false;
    // One terminal snapshot per request. Duplicate delivery must not move a
    // completed duration forward or let a delayed success replace a stop.
    if(!records.has(record.runId))records.set(record.runId,record);
    const message=(active.messages||[]).find(row=>row.runId===record.runId);
    if(message&&!message.runTiming)message.runTiming=records.get(record.runId);
    while(records.size>LIMIT)forget(records.keys().next().value);
    sync(record.runId);return true;
  }
  function restore(messages){
    if(!current())return;
    for(const message of(Array.isArray(messages)?messages:[]).slice(-LIMIT)){
      const record=normalize(message.runTiming);
      if(record&&record.runId===message.runId&&!records.has(record.runId))records.set(record.runId,record);
    }
    while(records.size>LIMIT)forget(records.keys().next().value);
    sync();
  }
  function prune(){
    if(!current())return;
    const retained=new Set([...$("conversation").children].filter(node=>node.classList.contains("message")).map(node=>node.dataset.runId));
    for(const id of views.keys())if(!retained.has(id))forget(id);
  }
  return {reset,render,restore,sync,prune};
})();
