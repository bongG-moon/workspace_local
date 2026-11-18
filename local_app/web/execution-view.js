"use strict";

// Display the CLI's actual tool records. This module never executes code.
globalThis.WorkspaceExecutionView = (() => {
  let sessionId = null;
  const cards = new Map(), galleries = new Map();
  // A confirmation may finish after navigation. Keep its single-flight guard
  // until the server replies; never send the same decision twice from two views.
  const pendingReviews = new Set(), reviewButtons = new Map();
  const labels = {requested:"실행 요청",completed:"실행 완료",error:"실행 오류",interrupted:"결과 미확인"};
  function reset(id = null) { sessionId=id;cards.clear();galleries.clear(); }
  const current = () => sessionId && sessionId === active?.id;
  // Ordering is a display hint, not a claim that a file is final or verified.
  function fileRank(file){
      const name=String(file.name||file.path||'').replace(/\\/g,'/');
      if(/(?:^|\/)(?:scratch|tmp|temp|__pycache__)(?:\/|$)/i.test(name))return 3;
      if(/\.(?:html?|pdf|docx|pptx|xlsx?)$/i.test(name))return 0;
      if(/\.(?:md|csv|tsv|txt|png|jpe?g|webp)$/i.test(name))return 1;
      return 2;
  }
  function orderFiles(files) {
    return [...files].sort((a,b)=>fileRank(a)-fileRank(b)||(b.observedAt||0)-(a.observedAt||0));
  }
  function primaryFiles(files){
    const ordered=orderFiles(files),documents=ordered.filter(file=>fileRank(file)===0);
    return (documents.length?documents:ordered).slice(0,6);
  }
  const codeFile = file => /\.(?:md|js|mjs|cjs|jsx|ts|tsx|py|pyi|css|scss|sql|ps1|sh|bash|bat|cmd|yaml|yml|json|jsonc|toml|xml|ini|cfg|svg|cs|java|go|rs|c|h|cpp|hpp|r|rb|php|swift|kt)$/i.test(file.path);
  function reviewActions(card, runId, items) {
    const owner = sessionId, generation = selectionGeneration;
    const actions = el("div",null,"run-result-actions");
    function add(file,label,open) {
      if (!file) return;
      const button = el("button",label,"text-button");
      button.type = "button";
      button.title = (file.name || file.path) + " · " + label;
      button.onclick = () => {
        if (!currentFileLocation(owner,generation) || !card.isConnected) return;
        Promise.resolve(open(file,button)).catch(err => {if (currentFileLocation(owner,generation)) toast(err.message);});
      };
      actions.append(button);
    }
    add(items.find(file=>file.change!=="deleted"&&/\.(csv|tsv)$/i.test(file.path)), "원본 표 보기",
      (file,opener)=>preview(file.path,{view:"table",opener}));
    add(items.find(file=>file.change!=="deleted"&&/\.html?$/i.test(file.path)), "보고서 보기",
      (file,opener)=>preview(file.path,{opener}));
    const file=items[0], counts=items.length===1 && file?.lineCounts;
    const diffLabel=items.length>1 ? "전체 변경 내용 보기" :
      counts ? `${file.name||file.path.split(/[\\/]/).pop()}  +${counts.added} −${counts.removed} · 변경 내용 보기` : "변경 내용 보기";
    add(file, diffLabel,
      (file,opener)=>globalThis.WorkspaceProductivityActions?.openChanges({
        sessionId:owner,runId,path:items.length===1?file.path:undefined,opener}));
    if (actions.children.length) card.append(actions);
  }
  function observedCard(changes, {sessionId:owner=sessionId, current:valid=()=>current(), onConfirmed=()=>refreshResults(), previous} = {}) {
    for(const [key,groups] of reviewButtons)if(!pendingReviews.has(key)&&!groups.some(group=>group.some(button=>button.isConnected)))reviewButtons.delete(key);
    const rows=(changes||[]).filter(file=>file.attribution==="observed"&&file.id&&file.runId);
    if(!rows.length)return null;
    if(previous){previous.updateCandidates(rows);return previous;}
    const section=el("section",null,"observed-changes");
    const title=el("h3","명령 실행 중 확인된 폴더 변경");
    const note=el("p","다른 업무나 외부에서 바꾼 파일이 포함될 수 있어요. 확인한 파일만 이 업무 결과에 추가하세요.","observed-changes-note");
    function buildRow(file){
      const row=el("div",null,"observed-change"),heading=el("div",null,"observed-change-heading"),
        kind={created:"새 파일",modified:"수정",deleted:"삭제"}[file.change]||"변경",
        name=el("strong",file.name||file.path,"observed-change-name");
      const badge=el("span",kind,"observed-change-kind file-change-badge");badge.dataset.change=file.change;
      name.title=file.path;row.dataset.candidateId=file.id;heading.append(name,badge);row.append(heading);
      if(file.conflict)row.append(el("p","같은 폴더에서 다른 업무도 실행됐어요.","observed-change-conflict"));
      const actions=el("div",null,"observed-change-actions"),buttons=[],key=`${owner}:${file.runId}:${file.id}`;
      const activeRow=()=>valid()&&section.isConnected&&row.isConnected;
      const add=(label,action)=>{const button=el("button",label,"text-button");button.type="button";button.disabled=pendingReviews.has(key);button.onclick=action;actions.append(button);buttons.push(button);return button;};
      const compare=add("변경 내용 보기",()=>{
        if(!activeRow()||pendingReviews.has(key))return;
        return globalThis.WorkspaceProductivityActions?.openChanges({sessionId:owner,runId:file.runId,candidateId:file.id,path:file.path,opener:compare});
      });
      async function decide(action){
        if(!activeRow()||pendingReviews.has(key))return;
        pendingReviews.add(key);
        const update=busy=>{const kept=[];for(const group of reviewButtons.get(key)||[]){if(group.some(button=>button.isConnected)){for(const button of group)button.disabled=busy;kept.push(group);}}reviewButtons.set(key,kept);};
        update(true);
        try{
          const result=await api("/api/results/confirm",{id:owner,runId:file.runId,candidateId:file.id,action});
          // Folding the list may unmount this row while its decision is pending.
          // Its outcome still belongs to the view that submitted it.
          if(valid())await onConfirmed(result);
        }catch(err){if(valid())toast(err.message);}
        finally{pendingReviews.delete(key);update(false);}
      }
      add("이 업무 결과에 추가",()=>decide("include"));add("제외",()=>decide("dismiss"));
      const groups=(reviewButtons.get(key)||[]).filter(group=>group.some(button=>button.isConnected));groups.push(buttons);reviewButtons.set(key,groups);
      row.append(actions);return row;
    }
    let rendered=new Map(), currentFiles=[], more;
    section.updateCandidates=files=>{
      const next=new Map();
      const rowFor=file=>{
        const key=JSON.stringify([file.runId,file.id,file.path,file.name,file.change,!!file.conflict]);
        const row=rendered.get(key)||buildRow(file);
        next.set(key,row);return row;
      };
      const children=[title,note,...files.slice(0,5).map(rowFor)];
      currentFiles=files;
      if(files.length>5){
        if(!more){
          more=el("details",null,"run-results-more");more.append(el("summary"),el("div"));
          more.ontoggle=()=>section.updateCandidates(currentFiles);
        }
        const label=`나머지 변경 ${files.length-5}개 보기`;
        if(more.children[0].textContent!==label)more.children[0].textContent=label;
        reconcileRows(more.children[1],more.open?files.slice(5).map(rowFor):[]);
        children.push(more);
      }else if(more){more.ontoggle=null;more=null;}
      reconcileRows(section,children);rendered=next;
    };
    section.updateCandidates(rows);
    return section;
  }
  function appendResultFiles(parent, files, options){
    const present=files.filter(file=>file.change!=="deleted");
    if(present.length)globalThis.WorkspaceRichContent?.files(parent,present.map(file=>file.path),options);
    for(const file of files.filter(file=>file.change==="deleted")){
      const row=el("div",null,"run-result-deleted"),name=el("span",file.name||file.path);
      const badge=el("span","삭제","observed-change-kind file-change-badge");badge.dataset.change="deleted";
      name.title=file.path;row.append(badge,name);parent.append(row);
    }
  }
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
    const disclosure=el("span",wasOpen?"접기":"펼치기","disclosure-action");
    summary.append(marker,title,state,disclosure);detail.append(summary);
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
    detail.ontoggle=()=>{disclosure.textContent=detail.open?"접기":"펼치기";populate();};populate();
    detail.append(content);card.append(detail);
    while(cards.size>40){const first=cards.keys().next().value;cards.get(first).remove();cards.delete(first);}
    if(typeof boundConversation==="function")boundConversation(card);
  }
  function results(runId, files, restoring = false, observedChanges = []) {
    if(!current() || !runId)return;
    const items=orderFiles((files||[]).filter(file=>file.runId===runId&&file.attribution!=="observed")),
      candidates=(observedChanges||[]).filter(file=>file.runId===runId&&file.attribution==="observed"),
      signature=JSON.stringify([items.map(file=>[file.path,file.observedAt,file.change,file.attribution]),candidates]);
    const old=galleries.get(runId);if(old?.signature===signature)return;
    if(!items.length&&!candidates.length){if(old){old.card.remove();galleries.delete(runId);}return;}
    const card=old?.card||el("article",null,"message run-results"),wasOpen=card.querySelector("details")?.open===true;card.replaceChildren();card.dataset.runId=runId;
    const legacy = items.some(file => !["tool","confirmed"].includes(file.attribution));
    if(items.length)card.append(el("div",legacy ? "이전 버전의 폴더 변경 기록" : "이번 요청 중 바뀐 파일","run-results-label"));
    if(!legacy){
      const created=items.filter(file=>file.change==="created").length;
      const modified=items.filter(file=>file.change==="modified").length;
      const deleted=items.filter(file=>file.change==="deleted").length;
      const counts=[created && `새 파일 ${created}개`, modified && `수정한 파일 ${modified}개`,deleted&&`삭제된 파일 ${deleted}개`].filter(Boolean);
      if (counts.length) card.append(el("div",counts.join(" · "),"run-result-counts"));
    }
    const options={sessionId,workspace:active.workspace,onOpenFile:(path) => {
      if (!current()) return;
      return preview(path,{changeRunId:runId});
    }};
    const primary=primaryFiles(items);
    appendResultFiles(card,primary,options);
    if(!legacy&&items.length)reviewActions(card,runId,items);
    if(items.length>primary.length){
      const detail=el("details",null,"run-results-more"),content=el("div");
      detail.append(el("summary",`전체 변경 파일 ${items.length}개 보기`),content);
      let populated=false;
      const populate=()=>{if(detail.open&&!populated){populated=true;for(let start=0;start<items.length;start+=24)appendResultFiles(content,items.slice(start,start+24),options);}};
      detail.ontoggle=populate;detail.open=wasOpen;populate();card.append(detail);
    }
    if(candidates.length){const owner=sessionId,generation=selectionGeneration;card.append(observedCard(candidates,{sessionId:owner,current:()=>currentFileLocation(owner,generation)&&card.isConnected}));}
    card.dataset.messageSize=String([...items,...candidates].reduce((sum,file)=>sum+file.path.length,0));
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
  function restore(records, artifacts, observedChanges = []) {
    for(const record of (records||[]).slice(-40))render(record,true);
    for(const run of [...new Set([...(artifacts||[]),...observedChanges].map(file=>file.runId))].slice(-8))results(run,artifacts,true,observedChanges);
  }
  function syncResults(lastRunId, artifacts, observedChanges = []){
    const stamps=new Map();
    for(const file of [...(artifacts||[]),...observedChanges])if(file.runId){
      const stamp=Number(file.observedAt)||Number(file.finishedAt)||0;
      stamps.set(file.runId,Math.max(stamps.get(file.runId)||0,stamp));
    }
    if(lastRunId)stamps.set(lastRunId,Number.MAX_SAFE_INTEGER);
    const retained=[...stamps].sort((a,b)=>a[1]-b[1]).slice(-8).map(([run])=>run),keep=new Set(retained);
    for(const run of [...galleries.keys()])if(!keep.has(run))results(run,[],false,[]);
    // A saved review can recover an older confirmed result after a failed
    // history save. Place it beside its own response, even without an old card.
    for(const run of retained)results(run,artifacts,true,observedChanges);
  }
  return {reset,render,results,restore,syncResults,observedCard,orderFiles,primaryFiles,codeFile};
})();
