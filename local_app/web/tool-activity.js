"use strict";

// A bounded view of CLI evidence, never an inferred plan or a tool runner.
globalThis.WorkspaceToolActivity = (() => {
  const LIMIT=80, PREVIEW=6, records=new Map(), groups=new Map();
  const states={requested:"요청됨",running:"실행 중",completed:"결과 수신",error:"오류",interrupted:"결과 미확인"};
  const pending=record=>record.state==="requested"||record.state==="running";
  const text=(value,limit)=>typeof value==="string"?value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,"").slice(0,limit):"";
  const phases=new Set(["receiving","tool_preparing","tool_requested","tool_running","tool_result","answering","delegated","compacting"]);
  const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  let sessionId=null,liveRun=null,previousState="idle",baseLabel="",reportedLabel="",actualActivity=null,ignoredRuns=new Set();
  const current=()=>sessionId&&sessionId===active?.id;
  const runKey=record=>record.runId||"legacy";
  const key=record=>`${runKey(record)}:${record.id}`;
  const forRun=run=>[...records.values()].filter(record=>runKey(record)===run);
  function reset(id=null){
    sessionId=id;liveRun=null;previousState="idle";baseLabel="";reportedLabel="";actualActivity=null;ignoredRuns=new Set();records.clear();groups.clear();
    for(const id of ["tool-activity-badge","tool-activity-open"]){const node=$(id);if(node){node.hidden=true;node.textContent="";}}
    if($("status-text"))$("status-text").title="";
  }
  function row(record,view){
    if(!view){
      const item=el("li",null,"tool-activity-row"),marker=el("span",null,"tool-activity-marker"),copy=el("div",null,"tool-activity-copy"),heading=el("div",null,"tool-activity-action");
      const action=el("strong"),tool=el("span",null,"tool-activity-tool"),target=el("span",null,"tool-activity-target"),child=el("small","추가 작업자","tool-activity-child"),state=el("span",null,"tool-activity-state");
      marker.setAttribute("aria-hidden","true");heading.append(action,tool,child);copy.append(heading,target);item.append(marker,copy,state);item.dataset.activityId=record.id;
      view={item,marker,action,tool,target,child,state};
    }
    view.item.dataset.state=record.state;
    setText(view.marker,record.state==="completed"?"✓":record.state==="error"?"!":pending(record)?"·":"–");
    setText(view.action,record.action||"도구 사용");setText(view.tool,record.tool);setText(view.target,record.target);view.target.hidden=!record.target;view.target.title=record.target;
    view.child.hidden=!record.parentToolUseId;setText(view.state,states[record.state]);return view;
  }
  function preview(group,rows){
    // Retain pending work even when newer, completed calls fill the preview.
    const chosen=new Set(rows.filter(pending).slice(-PREVIEW).map(key));
    for(const record of [...rows].reverse()){if(chosen.size>=PREVIEW)break;chosen.add(key(record));}
    const shown=rows.filter(record=>chosen.has(key(record))),elements=[];
    for(const id of group.views.keys())if(!chosen.has(id)){group.views.get(id).item.remove();group.views.delete(id);}
    for(const record of shown){const id=key(record),view=row(record,group.views.get(id));group.views.set(id,view);elements.push(view.item);}
    elements.forEach((item,index)=>{if(group.list.children[index]!==item)group.list.insertBefore(item,group.list.children[index]||null);});
    const waiting=rows.filter(pending).length;
    group.note.hidden=rows.length<=PREVIEW&&!group.clipped;
    setText(group.note,waiting>PREVIEW?`진행·대기 ${waiting}개 중 ${PREVIEW}개 표시 · 나머지는 자세히 보기`:"최근 활동과 진행 중인 도구를 표시합니다. 이전 활동은 자세히 보기에서 확인하세요.");
  }
  function openDetails(group){
    if(!current())return;
    if(globalThis.WorkspaceProgressView?.open(group.run==="legacy"?null:group.run))return;
    group.note.hidden=false;setText(group.note,"상세 진행 내용을 열지 못했어요. 앱을 다시 열어 주세요.");
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
      const card=el("article",null,"message tool-activity-card"),panel=el("section",null,"tool-activity-detail"),heading=el("div",null,"tool-activity-summary");
      const icon=el("span","≋","tool-activity-symbol"),title=el("span","도구·스킬 활동","tool-activity-title"),count=el("span",null,"tool-activity-count"),status=el("span",null,"tool-activity-group-state"),list=el("ol",null,"tool-activity-list");
      const footer=el("div",null,"tool-activity-footer"),note=el("span",null,"tool-activity-note"),button=el("button","입력·결과 자세히 보기","text-button tool-activity-details-open");button.type="button";button.setAttribute("aria-controls","progress-view");
      icon.setAttribute("aria-hidden","true");card.dataset.runId=run==="legacy"?"":run;heading.append(icon,title,count,status);footer.append(note,button);panel.append(heading,list,footer);card.append(panel);
      group={run,card,count,status,list,note,button,views:new Map(),clipped:false};groups.set(run,group);button.onclick=()=>openDetails(group);place(card,run);
    }
    setText(group.count,String(rows.length)+(group.clipped?"+":""));
    const open=rows.filter(pending),errors=rows.filter(record=>record.state==="error"),unknown=rows.filter(record=>record.state==="interrupted");
    setText(group.status,open.length?`${open.length}개 ${open.some(record=>record.state==="running")?"진행·대기":"요청"}`:errors.length?`${errors.length}개 오류`:unknown.length?"일부 결과 미확인":"결과 수신");
    group.card.dataset.activityState=open.length?"pending":errors.length?"error":unknown.length?"interrupted":"completed";
    group.card.dataset.messageSize=String(rows.reduce((sum,record)=>sum+record.tool.length+record.action.length+record.target.length,0));
    preview(group,rows);if(typeof boundConversation==="function")boundConversation(group.card);
  }
  function sync(){
    const badge=$("tool-activity-badge"),button=$("tool-activity-open"),status=$("status-text");
    if(!badge||!button||!status)return;
    badge.hidden=true;button.hidden=true;status.title="";
    if(!current())return;
    const run=liveRun||(!["starting","running","approval","question"].includes(active.state)?active.lastRunId:null),rows=run?forRun(run):[];
    if(rows.length){button.hidden=false;button.textContent=globalThis.WorkspaceProgressView?"진행 내용":`활동 ${rows.length}`;button.setAttribute("aria-label","이번 요청의 진행 내용 보기");button.onclick=()=>{
      if(!current())return;const group=groups.get(run);if(!group)return;
      openDetails(group);
    };}
    // Waiting for a person, a terminal state, and setting changes always win.
    if(active.choice){status.textContent="다음 단계 선택을 기다려요";return;}
    if(active.state!=="running"||!liveRun||$("requests").children.length){if(baseLabel)status.textContent=baseLabel;return;}
    if(actualActivity?.runId===liveRun){
      setText(status,actualActivity.label);status.title=actualActivity.tool?`${actualActivity.tool} · ${actualActivity.label}`:"";
      if(actualActivity.tool){setText(badge,actualActivity.tool);badge.title=actualActivity.tool;badge.hidden=false;}return;
    }
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
      ignoredRuns=new Set([...records.values()].map(runKey));if(liveRun)ignoredRuns.add(liveRun);
      liveRun=null;reportedLabel="";actualActivity=null;
    }
    if(runId&&!ignoredRuns.has(runId)){if(liveRun!==runId)actualActivity=null;liveRun=runId;}
    if(state!=="running")reportedLabel="";
    if(state==="running"&&label&&![statusLabels.running,"요청을 처리하고 있어요"].includes(label)){reportedLabel=label;actualActivity=null;}
    previousState=state;
    if(["done","stopped","error","idle"].includes(state)){
      reportedLabel="";actualActivity=null;if(active)active.runActivity=null;
      const changed=new Set();for(const [id,record]of records)if(pending(record)){records.set(id,{...record,state:"interrupted"});changed.add(runKey(record));}
      for(const run of changed)refreshGroup(run);
      if(active)active.toolActivity=[...records.values()];
    }
    sync();
  }
  function runActivity(value){
    if(!current()||!["starting","running","approval","question"].includes(active.state)||!value||!phases.has(value.phase))return false;
    const runId=text(value.runId,64),label=text(value.label,300);
    if(!runId||!label||ignoredRuns.has(runId)||runId!==active.lastRunId||typeof value.updatedAt!=="number"||!Number.isFinite(value.updatedAt)||value.updatedAt<0)return false;
    if(actualActivity?.runId===runId&&value.updatedAt<actualActivity.updatedAt)return false;
    const next={runId,label,phase:value.phase,updatedAt:value.updatedAt,tool:text(value.tool,160)};
    const changed=!actualActivity||actualActivity.runId!==runId||actualActivity.label!==label||actualActivity.tool!==next.tool||actualActivity.phase!==next.phase;
    actualActivity=next;active.runActivity={...actualActivity};liveRun=runId;reportedLabel="";if(changed)sync();return true;
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
    while(records.size>LIMIT){
      const oldest=[...records.keys()].find(id=>!pending(records.get(id)))||records.keys().next().value;
      const run=runKey(records.get(oldest));changed.add(run);if(groups.has(run))groups.get(run).clipped=true;records.delete(oldest);
    }
    for(const run of changed)refreshGroup(run);
    active.toolActivity=[...records.values()];sync();
  }
  function restore(values,activity=null){
    previousState=active?.state||"idle";liveRun=active?.lastRunId||null;
    for(const record of(Array.isArray(values)?values:[]).slice(-LIMIT))render(record,true);
    if(!["starting","running","approval","question"].includes(previousState))statusChanged(previousState);
    else if(activity)runActivity(activity);
  }
  return {reset,render,restore,statusChanged,sync,runActivity};
})();
