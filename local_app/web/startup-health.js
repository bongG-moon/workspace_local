"use strict";

// Loaded before the deferred app scripts. This observes readiness; it does not
// retry scripts, replay requests, change browser profiles, or infer root cause.
globalThis.WorkspaceStartupHealth = (() => {
  const definitions = {
    app:{file:"app.js",buttons:["new-chat","settings-open"]},
    stream:{file:"rendering.js",global:"WorkspaceStream"},
    attachments:{file:"attachments.js",global:"WorkspaceAttachments"},
    "path-picker":{file:"path-picker.js",global:"WorkspacePathPicker"},
    workflow:{file:"workflow.js",global:"WorkspaceWorkflow",buttons:["workflow-open","schedule-open"]},
    composer:{file:"composer.js",global:"WorkspaceComposer"},
    "inline-controls":{file:"inline-controls.js",global:"WorkspaceInlineControls",buttons:["composer-model","composer-effort","composer-permission"]},
    attention:{file:"attention.js",global:"WorkspaceAttention",buttons:["attention-open"]},
    desktop:{file:"desktop.js",global:"WorkspaceDesktop",buttons:["desktop-open"]},
    "session-import":{file:"session-import.js",global:"WorkspaceSessionImport",buttons:["import-open"]},
    capabilities:{file:"capabilities.js",global:"WorkspaceCapabilities",buttons:["capabilities-open"]},
    productivity:{file:"productivity.js",global:"WorkspaceProductivityActions",buttons:["changes-open","branch-open"]},
    palette:{file:"palette.js",global:"WorkspacePalette",buttons:["palette-open"]},
    layout:{file:"layout.js",global:"WorkspaceLayout",buttons:["sidebar-toggle","materials-button"],listeners:true},
    "rich-content":{file:"rich-content.js",global:"WorkspaceRichContent"},
    "execution-view":{file:"execution-view.js",global:"WorkspaceExecutionView"},
    "tool-activity":{file:"tool-activity.js",global:"WorkspaceToolActivity"}
  };
  const names=Object.keys(definitions), states=new Map(names.map(name=>[name,{status:"pending",reason:""}]));
  const fileNames=new Map(names.map(name=>[definitions[name].file,name]));
  const buttonOwners=new Map(names.flatMap(name=>(definitions[name].buttons||[]).map(id=>[id,name])));
  const began=Date.now(), documentId=globalThis.crypto?.randomUUID?.()||"ui-"+Math.random().toString(36).slice(2)+Date.now().toString(36);
  const storageKey="workspace.uiRecovery.v1", records=[], reported=new Set(), queue=[];
  let hooks=null, reporter=directReport, drain=null, sent=0, timer=null, pageReady=false, bootstrap=false, bootFailed=false;
  let recoveryPending=false, recovering=false, restored=false, notice="", waitingNotice=false, forceNotice=false, stopped=false;
  const elapsed=()=>Math.max(0,Math.min(3600000,Date.now()-began));
  const $=id=>document.getElementById(id);
  const missing=()=>names.filter(name=>states.get(name).status!=="ready");
  function moduleFor(value) {try {const path=new URL(value,location.href).pathname;return fileNames.get(path.slice(path.lastIndexOf("/")+1))||null;}catch(_){return null;}}
  function emit(event,module,status,reason="",details={},publish=false) {
    const record={documentId,event,module,status,reason,elapsedMs:elapsed(),...details};
    records.push(record);if(records.length>64)records.shift();
    if(publish){const key=[event,module,status,reason].join(":");if(!reported.has(key)&&reported.size<12){reported.add(key);queue.push(record);void flush();}}
  }
  async function directReport(record) {
    // This path still works if app.js never evaluates. Credentials are used
    // only for the fixed authenticated local endpoint and never enter records.
    const token=new URLSearchParams((location.hash||"").slice(1)).get("token")||sessionStorage.getItem("workspaceToken");
    if(!token||typeof fetch!=="function")return;
    await fetch("/api/ui-health",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+token},body:JSON.stringify(record),keepalive:true,credentials:"same-origin"});
  }
  function flush() {
    if(drain)return drain;if(!reporter||!queue.length||sent>=12)return Promise.resolve();
    drain=(async()=>{while(queue.length&&sent<12){sent++;try{await reporter(queue.shift());}catch(_){/* A diagnostic failure never changes app behavior. */}}})().finally(()=>{drain=null;});
    return drain;
  }
  function transition(name,status,reason="") {const previous=states.get(name);if(previous.status===status&&previous.reason===reason)return;states.set(name,{status,reason});emit("module",name,status,reason,{},status==="failed");}
  function show() {
    const area=$("startup-health"),text=$("startup-health-text"),retry=$("startup-health-reload");if(!area||!text||!retry)return;
    const absent=missing(),failed=absent.some(name=>states.get(name).status==="failed");
    const visible=!!notice||!!absent.length&&(failed||forceNotice||elapsed()>1800);
    area.hidden=!visible;area.dataset.state=failed?"failed":"pending";
    text.textContent=notice||(failed?"일부 화면 기능을 준비하지 못했어요. 화면을 다시 열어 복구할 수 있습니다.":"화면 기능을 준비하고 있어요…");
    retry.hidden=!failed&&!notice;retry.disabled=recovering;retry.onclick=requestRecovery;
    const recheck=$("startup-health-check");if(recheck){recheck.hidden=!failed;recheck.onclick=()=>{notice="";forceNotice=true;check();};}
  }
  function check() {
    if(stopped)return;
    for(const name of names){
      const def=definitions[name],current=states.get(name);
      const loaded=name==="app"?bootstrap:!!globalThis[def.global];
      const handlers=(def.buttons||[]).every(id=>def.listeners?!!$(id):typeof $(id)?.onclick==="function");
      if(loaded&&handlers){transition(name,"ready");continue;}
      if(current.status==="failed")continue;
      if(bootFailed&&name==="app")transition(name,"failed","bootstrap-failed");
      else if((pageReady&&name!=="app")||elapsed()>=15000)transition(name,"failed",elapsed()>=15000?"timeout":!loaded?"missing-global":"missing-handler");
    }
    const absent=missing();
    if(!absent.length){if(waitingNotice){notice="";waitingNotice=false;}emit("startup","app","ready","",{missing:[]},true);if(timer!==null){clearInterval(timer);timer=null;}}
    else if(((pageReady&&(bootstrap||bootFailed))||elapsed()>=15000)&&absent.some(name=>states.get(name).status==="failed")){
      // A failed early resource does not make unrelated, still-loading modules
      // failed. Publish the aggregate only after startup has settled or timed out.
      emit("startup","app","failed","",{missing:absent},true);
      if(timer!==null)clearInterval(timer);timer=null;
    }
    show();
  }
  function markFailure(module,reason,event) {
    if(!module)return;
    const details={};for(const [out,key] of [["line","lineno"],["column","colno"]])if(Number.isInteger(event?.[key]))details[out]=Math.max(0,Math.min(1000000,event[key]));
    emit("error",module,"failed",reason,details,true);
    // A runtime action error is observable but must not retroactively erase a
    // successfully initialized module or classify it as a loading failure.
    if(states.get(module).status!=="ready")transition(module,"failed",reason);
    notice="일부 화면 동작을 완료하지 못했어요. 화면을 다시 열어 복구할 수 있습니다.";show();
  }
  function resource(event) {
    const target=event.target;if(target?.tagName!=="SCRIPT")return;
    const module=moduleFor(target.src);if(!module)return;
    if(event.type==="error"){transition(module,"failed","resource-error");show();}
    else {emit("resource",module,"ready","resource-load");check();}
  }
  function validSnapshot(value) {
    if(!value||value.version!==1||!Number.isFinite(value.savedAt)||Date.now()-value.savedAt>600000||value.savedAt>Date.now()+10000)return false;
    if(value.sessionId!==null&&(typeof value.sessionId!=="string"||!/^[A-Za-z0-9_-]{1,128}$/.test(value.sessionId)))return false;
    if(!Array.isArray(value.drafts)||value.drafts.length>50)return false;
    let size=0;const seen=new Set();
    for(const row of value.drafts){if(!row||typeof row.id!=="string"||!/^[A-Za-z0-9_-]{1,128}$/.test(row.id)||seen.has(row.id)||typeof row.text!=="string"||row.text.length>100000||!Array.isArray(row.attachments)||row.attachments.length>12)return false;seen.add(row.id);size+=row.text.length;for(const path of row.attachments){if(typeof path!=="string"||path.length>4096||/[\u0000-\u001f]/.test(path))return false;size+=path.length;}}
    return size<=500000;
  }
  async function requestRecovery() {
    if(recovering||stopped)return false;
    if(!hooks){notice="입력한 내용이 있다면 먼저 복사한 뒤 앱 창을 다시 열어 주세요.";show();return false;}
    if(!hooks.canReload()){notice="요청 전송이나 파일 추가가 끝난 뒤 화면을 다시 열어 주세요.";show();return false;}
    recovering=true;show();
    try{
      emit("recovery","app","pending","reload-requested",{},true);
      await Promise.race([flush(),new Promise(resolve=>setTimeout(resolve,250))]);
      // The report wait leaves normal input and actions available. Recheck
      // admission and capture the latest draft in the same turn as the reload.
      if(stopped||!hooks.canReload()){recovering=false;notice="요청 전송이나 파일 추가가 끝난 뒤 화면을 다시 열어 주세요.";show();return false;}
      let snapshot={version:1,savedAt:Date.now(),...hooks.capture()};
      if(!restored){const oldRaw=sessionStorage.getItem(storageKey);if(oldRaw&&oldRaw.length<=2500000){const old=JSON.parse(oldRaw);if(validSnapshot(old)){const draftMap=new Map(old.drafts.map(row=>[row.id,row]));for(const row of snapshot.drafts)draftMap.set(row.id,row);snapshot={...snapshot,sessionId:snapshot.sessionId||old.sessionId,drafts:[...draftMap.values()]};}}}
      if(!validSnapshot(snapshot))throw new Error("snapshot limit");
      sessionStorage.setItem(storageKey,JSON.stringify(snapshot));
      if(sessionStorage.getItem(storageKey)!==JSON.stringify(snapshot))throw new Error("storage failed");
      // A single explicit reload retains this tab's draft handoff and server.
      location.reload();return true;
    }catch(_){recovering=false;notice="초안을 안전하게 보관하지 못해 화면을 다시 열지 않았어요. 입력 내용을 복사한 뒤 다시 시도해 주세요.";emit("recovery","app","failed","storage-unavailable",{},true);show();return false;}
  }
  async function restoreRecovery() {
    if(!bootstrap||!pageReady||!hooks||recoveryPending||restored)return;
    recoveryPending=true;
    try{
      const raw=sessionStorage.getItem(storageKey);if(!raw){restored=true;return;}
      if(raw.length>2500000)throw new Error("invalid snapshot");
      const value=JSON.parse(raw);if(!validSnapshot(value)){sessionStorage.removeItem(storageKey);throw new Error("invalid snapshot");}
      const result=await hooks.restore(value);
      sessionStorage.removeItem(storageKey);restored=true;
      if(result?.missingSession)notice="이전 업무를 찾지 못해 초안을 새 업무 입력창에 복원했어요.";
      emit("recovery","app","restored","",{},true);
    }catch(_){notice="이전 화면의 초안을 아직 복원하지 못했어요. 화면을 다시 열어 재시도할 수 있습니다.";emit("recovery","app","failed","restore-failed",{},true);}
    finally{recoveryPending=false;show();}
  }
  function attach(value) {hooks=value;reporter=typeof value.report==="function"?value.report:directReport;void flush();check();}
  function bootstrapReady() {bootstrap=true;bootFailed=false;check();return restoreRecovery();}
  function bootstrapFailed() {bootFailed=true;transition("app","failed","bootstrap-failed");check();}
  function loaded() {pageReady=true;emit("document","app","ready");check();void restoreRecovery();}
  document.addEventListener("error",resource,true);
  document.addEventListener("load",resource,true);
  globalThis.addEventListener("error",event=>{if(event.target?.tagName==="SCRIPT")return;markFailure(moduleFor(event.filename),"runtime-error",event);});
  globalThis.addEventListener("unhandledrejection",()=>markFailure("app","unhandled-rejection"));
  document.addEventListener("DOMContentLoaded",loaded,{once:true});
  document.addEventListener("click",event=>{
    const node=event.target?.closest?.("button"),owner=buttonOwners.get(node?.id);if(!owner)return;
    check();if(states.get(owner).status==="ready"&&bootstrap)return;
    event.preventDefault();event.stopImmediatePropagation();forceNotice=true;
    waitingNotice=states.get(owner).status!=="failed";notice=waitingNotice?"화면 기능을 준비 중이에요. 잠시 뒤 다시 눌러 주세요.":"이 화면 기능을 준비하지 못했어요. 화면을 다시 열어 주세요.";show();
  },true);
  globalThis.addEventListener("pagehide",()=>{stopped=true;if(timer!==null)clearInterval(timer);timer=null;});
  emit("document","app","pending","",{},true);
  timer=setInterval(check,250);
  if(document.readyState!=="loading")loaded();
  return {attach,bootstrapReady,bootstrapFailed,check,requestRecovery,snapshot:()=>({documentId,elapsedMs:elapsed(),bootstrap,pageReady,modules:Object.fromEntries(states),events:records.map(row=>({...row})),missing:missing()})};
})();
