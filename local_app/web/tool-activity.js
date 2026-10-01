"use strict";

// A bounded view of CLI evidence, never an inferred plan or a tool runner.
globalThis.WorkspaceToolActivity = (() => {
  const LIMIT=80, records=new Map(), groups=new Map();
  const states={requested:"요청됨",running:"실행 중",completed:"결과 수신",error:"오류",interrupted:"결과 미확인"};
  const pending=record=>record.state==="requested"||record.state==="running";
  const text=(value,limit)=>typeof value==="string"?value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,"").slice(0,limit):"";
  let sessionId=null,liveRun=null,previousState="idle",baseLabel="",reportedLabel="",ignoredRuns=new Set();
  const current=()=>sessionId&&sessionId===active?.id;
  const runKey=record=>record.runId||"legacy";
  const key=record=>`${runKey(record)}:${record.id}`;
  const forRun=run=>[...records.values()].filter(record=>runKey(record)===run);
  function reset(id=null){
    sessionId=id;liveRun=null;previousState="idle";baseLabel="";reportedLabel="";ignoredRuns=new Set();records.clear();groups.clear();
    for(const id of ["tool-activity-badge","tool-activity-open"]){const node=$(id);if(node){node.hidden=true;node.textContent="";}}
    if($("status-text"))$("status-text").title="";
  }
  function row(record){
    const item=el("li",null,"tool-activity-row");item.dataset.state=record.state;
    const marker=el("span",record.state==="completed"?"✓":record.state==="error"?"!":pending(record)?"·":"–","tool-activity-marker");marker.setAttribute("aria-hidden","true");
    const copy=el("div",null,"tool-activity-copy"),heading=el("div",null,"tool-activity-action");
    heading.append(el("strong",record.action||"도구 사용"),el("span",record.tool,"tool-activity-tool"));copy.append(heading);
    if(record.target)copy.append(el("span",record.target,"tool-activity-target"));
    if(record.parentToolUseId)copy.append(el("small","추가 작업자의 활동","tool-activity-child"));
    item.append(marker,copy,el("span",states[record.state],"tool-activity-state"));return item;
  }
  function populate(group){
    if(!group.detail.open){group.list.replaceChildren();return;}
    group.list.replaceChildren(...forRun(group.run).map(row));
  }
  function place(card,run){
    // Keep one activity group alongside its request when restoring history.
    const conversation=$("conversation"),children=[...conversation.children],reply=children.find(node=>node.dataset.runId===run&&node.classList.contains("assistant"));
    if(reply){conversation.insertBefore(card,reply);return;}
    // A stopped request may have no assistant text. Keep its tools before the
    // next request rather than attaching them to the end of restored history.
    const last=children.map(node=>node.dataset.runId===run).lastIndexOf(true);
    if(last>=0&&children[last+1])conversation.insertBefore(card,children[last+1]);else conversation.append(card);
  }
  function refreshGroup(run){
    const rows=forRun(run);let group=groups.get(run);
    if(!rows.length){group?.card.remove();groups.delete(run);return;}
    if(!group){
      const card=el("article",null,"message tool-activity-card"),detail=el("details",null,"tool-activity-detail"),summary=el("summary",null,"tool-activity-summary");
      const icon=el("span","≋","tool-activity-symbol"),title=el("span","도구·스킬 활동","tool-activity-title"),count=el("span",null,"tool-activity-count"),status=el("span",null,"tool-activity-group-state"),list=el("ol",null,"tool-activity-list");
      icon.setAttribute("aria-hidden","true");card.dataset.runId=run==="legacy"?"":run;summary.append(icon,title,count,status);detail.append(summary,list);card.append(detail);
      group={run,card,detail,summary,count,status,list};groups.set(run,group);detail.ontoggle=()=>populate(group);place(card,run);
    }
    group.count.textContent=String(rows.length);
    const open=rows.filter(pending),errors=rows.filter(record=>record.state==="error"),unknown=rows.filter(record=>record.state==="interrupted");
    group.status.textContent=open.length?`${open.length}개 ${open.some(record=>record.state==="running")?"진행·대기":"요청"}`:errors.length?`${errors.length}개 오류`:unknown.length?"일부 결과 미확인":"결과 수신";
    group.card.dataset.activityState=open.length?"pending":errors.length?"error":unknown.length?"interrupted":"completed";
    group.card.dataset.messageSize=String(rows.reduce((sum,record)=>sum+record.tool.length+record.action.length+record.target.length,0));
    populate(group);if(typeof boundConversation==="function")boundConversation(group.card);
  }
  function sync(){
    const badge=$("tool-activity-badge"),button=$("tool-activity-open"),status=$("status-text");
    if(!badge||!button||!status)return;
    badge.hidden=true;button.hidden=true;status.title="";
    if(!current())return;
    const run=liveRun||(!["starting","running","approval","question"].includes(active.state)?active.lastRunId:null),rows=run?forRun(run):[];
    if(rows.length){button.hidden=false;button.textContent=`활동 ${rows.length}`;button.setAttribute("aria-label",`이번 요청의 도구·스킬 활동 ${rows.length}개 보기`);button.onclick=()=>{
      if(!current())return;const group=groups.get(run);if(!group)return;
      if(!group.card.parentElement&&!group.card.parent)place(group.card,run);
      group.detail.open=true;populate(group);group.summary.focus();group.card.scrollIntoView({block:"nearest",inline:"nearest"});
    };}
    // Waiting for a person, a terminal state, and setting changes always win.
    if(active.choice){status.textContent="다음 단계 선택을 기다려요";return;}
    if(active.state!=="running"||!liveRun||$("requests").children.length){if(baseLabel)status.textContent=baseLabel;return;}
    if(reportedLabel){status.textContent=reportedLabel;return;}
    const live=forRun(liveRun),waiting=live.filter(pending),record=waiting.filter(item=>item.state==="running").pop()||waiting[waiting.length-1]||live[live.length-1];
    if(!record){if(baseLabel)status.textContent=baseLabel;return;}
    const phase=record.state==="requested"?"요청":record.state==="running"?"실행 중":record.state==="completed"?"최근 응답":record.state==="error"?"오류 확인":"결과 미확인";
    const action=record.action||"도구 사용",target=record.target?` · ${record.target}`:"",more=waiting.length>1?` · 다른 도구 ${waiting.length-1}개`:"";
    const ongoing=record.state==="running"?`${action} 중`:action.endsWith("요청")?action:`${action} 요청`;
    status.textContent=pending(record)?`${ongoing}${target}${more}`:`${phase}: ${action}${target}`;
    status.title=`${record.tool} · ${status.textContent}`;badge.textContent=record.tool;badge.title=record.tool;badge.hidden=false;
  }
  function statusChanged(state,label,runId){
    if(!current())return;
    if(label)baseLabel=label;
    if((state==="starting"||state==="running")&&!["starting","running","approval","question"].includes(previousState)){
      liveRun=null;reportedLabel="";ignoredRuns=new Set([...records.values()].map(runKey));
    }
    if(runId&&!ignoredRuns.has(runId))liveRun=runId;
    if(state!=="running")reportedLabel="";
    if(state==="running"&&label&&![statusLabels.running,"요청을 처리하고 있어요"].includes(label))reportedLabel=label;
    previousState=state;
    if(["done","stopped","error","idle"].includes(state)){
      reportedLabel="";
      const changed=new Set();for(const [id,record]of records)if(pending(record)){records.set(id,{...record,state:"interrupted"});changed.add(runKey(record));}
      for(const run of changed)refreshGroup(run);
      if(active)active.toolActivity=[...records.values()];
    }
    sync();
  }
  function render(value,restoring=false){
    if(!current()||!value||!states[value.state]||typeof value.id!=="string"||!value.id||typeof value.tool!=="string"||!value.tool)return;
    const record={id:text(value.id,160),tool:text(value.tool,160),state:value.state,action:text(value.action,100),target:text(value.target,240),runId:text(value.runId,64),parentToolUseId:text(value.parentToolUseId,160),startedAt:value.startedAt,finishedAt:value.finishedAt};
    const id=key(record),old=records.get(id);if(old&&!pending(old))return;
    if(old?.state==="running"&&record.state==="requested")return;
    records.set(id,record);
    if(!restoring&&active.state==="running"&&!ignoredRuns.has(runKey(record))&&(!liveRun||liveRun===runKey(record)))liveRun=runKey(record);
    if(!restoring&&liveRun===runKey(record))reportedLabel="";
    const changed=new Set([runKey(record)]);
    while(records.size>LIMIT){const oldest=records.keys().next().value;changed.add(runKey(records.get(oldest)));records.delete(oldest);}
    for(const run of changed)refreshGroup(run);
    active.toolActivity=[...records.values()];sync();
  }
  function restore(values){
    previousState=active?.state||"idle";liveRun=active?.lastRunId||null;
    for(const record of(Array.isArray(values)?values:[]).slice(-LIMIT))render(record,true);
    if(!["starting","running","approval","question"].includes(previousState))statusChanged(previousState);
  }
  return {reset,render,restore,statusChanged,sync};
})();
