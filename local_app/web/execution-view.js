"use strict";

// Display the CLI's actual tool records. This module never executes code.
globalThis.WorkspaceExecutionView = (() => {
  let sessionId = null;
  const cards = new Map(), galleries = new Map();
  const labels = {requested:"실행 요청",completed:"실행 완료",error:"실행 오류",interrupted:"결과 미확인"};
  function reset(id = null) { sessionId=id;cards.clear();galleries.clear(); }
  const current = () => sessionId && sessionId === active?.id;
  function place(node, runId, beforeReply = false) {
    const conversation = $("conversation");
    if (beforeReply && runId) {
      const next = [...conversation.children].find(child => child.dataset.runId === runId && child.classList.contains("assistant"));
      if (next) { conversation.insertBefore(node,next);return; }
    }
    conversation.append(node);
  }
  function render(record, restoring = false) {
    if (!current() || !record || !record.id || !labels[record.state]) return;
    const key = `${record.runId || ""}:${record.id}`;
    let card = cards.get(key), wasOpen = false;
    if (card) wasOpen=card.querySelector("details")?.open===true;
    else {
      card=el("article",null,"message execution-card");card.dataset.executionId=record.id;
      card.dataset.runId=record.runId||"";cards.set(key,card);place(card,record.runId,restoring);
    }
    card.replaceChildren();card.dataset.executionState=record.state;
    // Count output in the existing conversation memory/display budget.
    card.dataset.messageSize=String(String(record.command||"").length+String(record.output||"").length);
    const detail=el("details",null,"execution-detail"),summary=el("summary",null,"execution-summary");
    detail.open=wasOpen;
    const marker=el("span",record.state==="completed"?"✓":record.state==="error"?"!":record.state==="requested"?"◌":"–","execution-marker");
    marker.setAttribute("aria-hidden","true");
    const title=el("span",record.description||`${record.tool || "도구"} 명령`,"execution-title");
    const state=el("span",labels[record.state],"execution-state");
    summary.append(marker,title,state);detail.append(summary);
    const content=el("div",null,"execution-content");
    let populated=false;
    const populate=()=>{
      if(populated||!detail.open)return;populated=true;
      globalThis.WorkspaceRichContent?.code(content,record.command||"",record.tool==="PowerShell"?"powershell":"bash",{label:"실행 명령"});
      if(record.output){
        content.append(el("div","실행 결과","execution-output-label"));
        const output=el("pre",String(record.output),"execution-output");output.tabIndex=0;content.append(output);
      } else content.append(el("p",record.state==="requested"?"Claude의 실행 결과를 기다리고 있어요.":record.state==="completed"?"출력 없이 완료됐어요.":"CLI에서 완료 결과를 확인하지 못했어요.","execution-note"));
      if(record.truncated)content.append(el("p","긴 명령·결과의 일부만 표시합니다.","execution-note"));
    };
    detail.ontoggle=populate;populate();
    detail.append(content);card.append(detail);
    while(cards.size>40){const first=cards.keys().next().value;cards.get(first).remove();cards.delete(first);}
    if(typeof boundConversation==="function")boundConversation(card);
  }
  function results(runId, files, restoring = false) {
    if(!current() || !runId)return;
    const items=(files||[]).filter(file=>file.runId===runId).slice(-12),signature=JSON.stringify(items.map(file=>[file.path,file.observedAt]));
    const old=galleries.get(runId);if(old?.signature===signature)return;
    if(!items.length)return;
    const card=old?.card||el("article",null,"message run-results");card.replaceChildren();card.dataset.runId=runId;
    card.append(el("div","이번 요청 중 바뀐 파일","run-results-label"));
    globalThis.WorkspaceRichContent?.files(card,items.map(file=>file.path),{sessionId,workspace:active.workspace});
    card.dataset.messageSize=String(items.reduce((sum,file)=>sum+file.path.length,0));
    if(!old) {
      if(restoring){
        const siblings=[...$("conversation").children],last=siblings.filter(node=>node.dataset.runId===runId).pop();
        if(last?.nextSibling)$("conversation").insertBefore(card,last.nextSibling);else $("conversation").append(card);
      }else $("conversation").append(card);
    }
    galleries.set(runId,{card,signature});
    while(galleries.size>8){const first=galleries.keys().next().value;galleries.get(first).card.remove();galleries.delete(first);}
    if(typeof boundConversation==="function")boundConversation(card);
  }
  function restore(records, artifacts) {
    for(const record of (records||[]).slice(-40))render(record,true);
    for(const run of [...new Set((artifacts||[]).map(file=>file.runId))].slice(-8))results(run,artifacts,true);
  }
  return {reset,render,results,restore};
})();
