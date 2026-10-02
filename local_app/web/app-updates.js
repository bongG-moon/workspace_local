"use strict";

// Startup discovery is bounded by the server; focus and progress use local
// status reads. Installation is sent only from an explicit button click.
globalThis.WorkspaceAppUpdates = (() => {
  const $=id=>document.getElementById(id);
  const stableVersion=value=>typeof value==="string"&&/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(value);
  const statuses=new Set(["idle","checking","current","available","downloading","ready","launching","error","disabled"]);
  const transfers=new Set(["downloading","ready","launching"]);
  const startupDelays=[1200,2500,5000,10000,15000];
  let hooks=null,bound=false,started=false,stopped=false,timer=null,pending=false,startupReads=0;
  let snapshot={currentVersion:"",status:"idle",autoCheck:true,lastChecked:null,release:null,progress:null,error:null,canInstall:false};
  let localError="",startupWarning="",returnFocus=null,installSubmitted=false,installUncertain=false,closingForHandoff=false,handoffDialogsClosed=false;
  let installFailureNotified=false,startupOfferDone=false,offerTimer=null,startupReady=false,lastStartupSequence=0;

  function newer(version,current){
    if(!stableVersion(version)||!stableVersion(current))return false;
    const left=version.split(".").map(Number),right=current.split(".").map(Number);
    for(let i=0;i<3;i++){if(left[i]!==right[i])return left[i]>right[i];}return false;
  }
  function normalize(value){
    if(!value||typeof value!=="object"||!statuses.has(value.status))return null;
    const currentVersion=stableVersion(value.currentVersion)?value.currentVersion:"";
    const provider=value.source==null?"github":value.source.provider;
    const source={provider,label:typeof value.source?.label==="string"?value.source.label.slice(0,120)
      :provider==="github"?"GitHub 공개 배포":provider==="gitlab"?"사내 배포 서버":"배포 서버"};
    const release=value.release&&newer(value.release.version,currentVersion)?{
      version:value.release.version,title:String(value.release.title||`Company Workspace ${value.release.version}`),
      notes:typeof value.release.notes==="string"?value.release.notes:"업데이트 내용이 아직 제공되지 않았어요.",
      publishedAt:value.release.publishedAt,
      // Never use a URL supplied by release notes or remote metadata.
      url:provider==="github"?`https://github.com/bongG-moon/workspace_local/releases/tag/v${value.release.version}`:""
    }:null;
    return {currentVersion,source,status:value.status,autoCheck:value.autoCheck!==false,lastChecked:value.lastChecked,
      startupSequence:Number.isSafeInteger(value.startupSequence)&&value.startupSequence>=0?value.startupSequence:0,
      release,progress:typeof value.progress==="number"&&Number.isFinite(value.progress)?Math.max(0,Math.min(100,value.progress)):null,
      error:typeof value.error==="string"?value.error:null,canInstall:value.canInstall===true};
  }
  function dateText(value,includeTime=false){
    if(value==null||value==="")return "아직 확인하지 않았어요";
    const date=new Date(typeof value==="number"?value*(value<1e12?1000:1):value);
    if(!Number.isFinite(date.getTime()))return "날짜 확인 필요";
    return includeTime?date.toLocaleString("ko-KR",{year:"numeric",month:"long",day:"numeric",hour:"2-digit",minute:"2-digit"})
      :date.toLocaleDateString("ko-KR",{year:"numeric",month:"long",day:"numeric"});
  }
  function visible(){return document.hidden!==true&&($("settings-dialog")?.open||$("app-update-dialog")?.open);}
  function busy(){return pending||snapshot.status==="checking"||transfers.has(snapshot.status);}
  function offerPending(){return started&&startupReady&&!stopped&&!startupOfferDone&&snapshot.autoCheck&&snapshot.status==="available"&&!!snapshot.release&&!localError;}
  function offerUpdate(){
    if(!offerPending()||document.hidden===true||pending||installSubmitted||installUncertain)return false;
    if([...document.querySelectorAll("dialog[open]")].some(dialog=>dialog!==$("app-update-dialog")))return false;
    const focus=document.activeElement;
    if(focus&&(focus.isContentEditable||["INPUT","TEXTAREA","SELECT"].includes(focus.tagName)))return false;
    if(hooks.canOfferUpdate?.()===false)return false;
    openNotes(true);return true;
  }
  function contextChanged(){
    if(!offerPending()||offerTimer!==null)return;
    // Let a closing dialog finish restoring focus before offering this one.
    offerTimer=setTimeout(()=>{offerTimer=null;offerUpdate();},0);
  }
  function wake(){
    if(stopped)return;
    if(document.hidden!==true){startupReads=0;offerUpdate();}
    schedule(document.hidden!==true);
  }
  function message(){
    if(localError)return localError;
    if(snapshot.status==="error")return snapshot.error||"업데이트를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.";
    if(snapshot.status==="checking")return "새 버전이 있는지 확인하고 있어요.";
    if(snapshot.status==="downloading")return "업데이트를 다운로드하고 있어요. 현재 업무를 계속할 수 있습니다.";
    if(snapshot.status==="ready")return "다운로드를 마쳤어요. 안전하게 전환할 준비를 하고 있습니다.";
    if(snapshot.status==="launching")return "현재 업무를 마친 뒤 새 버전으로 전환합니다.";
    if(snapshot.release)return `새 버전 ${snapshot.release.version}을 사용할 수 있어요.`;
    if(snapshot.status==="current")return "최신 버전을 사용하고 있어요.";
    if(!snapshot.autoCheck)return "자동 확인이 꺼져 있어요. 필요할 때 직접 확인할 수 있습니다.";
    return "업데이트 상태를 확인할 수 있어요.";
  }
  function render(){
    if(!$("app-update-status"))return;
    const release=snapshot.release,available=!!release,label=message(),isBusy=busy();
    const sidebarButton=$("app-update-sidebar-badge"),sidebarLabel=release?`새 버전 ${release.version} 업데이트`:"새 버전 업데이트";
    sidebarButton.hidden=!available;sidebarButton.disabled=!available;
    sidebarButton.setAttribute("aria-label",sidebarLabel);sidebarButton.setAttribute("title",sidebarLabel);
    sidebarButton.setAttribute("aria-expanded",String($("app-update-dialog").open===true));
    $("app-update-settings-badge").hidden=!available;
    $("settings-open").setAttribute("aria-label","설정");$("settings-open").setAttribute("title","설정");
    $("app-update-current").textContent=snapshot.currentVersion||"확인 중";
    $("app-update-latest").textContent=release?.version||(snapshot.status==="current"?snapshot.currentVersion:"확인 전");
    $("app-update-source").textContent=snapshot.source?.label||"배포 서버";
    $("app-update-checked").textContent=dateText(snapshot.lastChecked,true);
    $("app-update-auto").checked=snapshot.autoCheck;$("app-update-auto").disabled=isBusy||installUncertain;
    $("app-update-check").disabled=isBusy;
    $("app-update-check").textContent=snapshot.status==="checking"?"확인 중…":"지금 확인";
    $("app-update-notes-open").hidden=!available;$("app-update-notes-open").disabled=!available;
    $("app-update-status").textContent=label;$("app-update-dialog-status").textContent=label;
    $("app-update-warning").textContent=startupWarning;$("app-update-warning").hidden=!startupWarning;
    $("app-update-section").dataset.state=localError?"error":snapshot.status;
    $("app-update-dialog").dataset.state=localError?"error":snapshot.status;
    $("app-update-title").textContent=release?.title||"앱 업데이트";
    $("app-update-release-meta").textContent=release?`버전 ${release.version} · ${release.publishedAt?dateText(release.publishedAt):"게시일 확인 필요"}`:"";
    $("app-update-notes").textContent=release?.notes||"업데이트 내용을 확인하고 있어요.";
    $("app-update-release-link").hidden=!release?.url;
    $("app-update-release-link").setAttribute("href",release?.url||"#");
    $("app-update-install").hidden=!available;
    $("app-update-install").disabled=!available||!snapshot.canInstall||isBusy||installSubmitted||installUncertain;
    $("app-update-install").textContent=snapshot.status==="downloading"?"다운로드 중…":transfers.has(snapshot.status)?"전환 준비 중…":"업데이트하기";
    $("app-update-dismiss").textContent="다음에 하기";
    $("app-update-install-note").hidden=!available||snapshot.canInstall||transfers.has(snapshot.status);
    const failed=!!localError||snapshot.status==="error";
    $("app-update-retry").hidden=!failed;$("app-update-dialog-retry").hidden=!failed;
    $("app-update-retry").disabled=isBusy;$("app-update-dialog-retry").disabled=isBusy;
    const downloading=snapshot.status==="downloading";
    for(const prefix of ["app-update","app-update-dialog"]){
      $(prefix+"-progress-wrap").hidden=!downloading;
      const progress=$(prefix+"-progress");
      if(snapshot.progress==null)progress.removeAttribute("value");else progress.value=snapshot.progress;
      $(prefix+"-progress-label").textContent=snapshot.progress==null?"다운로드 준비 중":`다운로드 ${Math.round(snapshot.progress)}%`;
    }
  }
  function closeForHandoff(){
    if(!installSubmitted||handoffDialogsClosed||!["ready","launching"].includes(snapshot.status)||closingForHandoff)return;
    handoffDialogsClosed=true;
    closingForHandoff=true;
    if($("app-update-dialog").open)$("app-update-dialog").close();
    if($("settings-dialog").open)$("settings-dialog").close();
    closingForHandoff=false;
  }
  function notifyInstallFailure(message){
    if(!installSubmitted||installFailureNotified||$("settings-dialog").open||$("app-update-dialog").open)return;
    installFailureNotified=true;hooks.notify?.(message);
  }
  function observe(value){
    const next=normalize(value);if(!next)return false;
    if(next.startupSequence>lastStartupSequence){
      lastStartupSequence=next.startupSequence;
      // Native window reopen is a new offer opportunity. Ordinary focus and
      // status reads retain the same sequence and cannot reset dismissal.
      startupOfferDone=$("app-update-dialog").open===true;
    }
    if(next.status==="error")notifyInstallFailure((next.error||"업데이트를 마치지 못했어요.")+" 설정의 ‘앱 업데이트’에서 다시 시도할 수 있습니다.");
    const wasUncertain=installUncertain;
    snapshot=next;localError="";installUncertain=false;
    if(snapshot.status==="error"||snapshot.status==="current"||wasUncertain&&!transfers.has(snapshot.status))installSubmitted=false;
    if(!["idle","checking"].includes(snapshot.status))startupReads=startupDelays.length;
    render();closeForHandoff();offerUpdate();return true;
  }
  function schedule(immediate=false){
    clearTimeout(timer);timer=null;
    if(!started||stopped||pending)return;
    if(document.hidden===true&&!transfers.has(snapshot.status))return;
    let delay;
    if(transfers.has(snapshot.status))delay=1000;
    else if(visible())delay=4000;
    else if(startupReads<startupDelays.length)delay=startupDelays[startupReads];
    else return;
    timer=setTimeout(()=>{timer=null;if(!visible()&&!transfers.has(snapshot.status))startupReads++;void read();},immediate?0:delay);
  }
  async function request(data){
    if(pending||!hooks||stopped)return false;
    pending=true;localError="";clearTimeout(timer);timer=null;render();
    try{
      const result=await hooks.api("/api/app-update",data);
      if(stopped)return false;
      if(!observe(result))throw new Error("업데이트 상태를 확인하지 못했어요.");
      return true;
    }catch(error){
      if(stopped)return false;
      if(data?.action==="install"){
        installUncertain=true;startupReads=0;
        localError="업데이트 요청 상태를 확인하지 못했어요. ‘지금 확인’을 눌러 현재 상태를 확인해 주세요.";
        notifyInstallFailure("업데이트 요청 상태를 확인하지 못했어요. 설정의 ‘앱 업데이트’에서 ‘지금 확인’을 눌러 주세요.");
      }else{localError=error.message||"업데이트를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.";startupReads=startupDelays.length;}
      return false;
    }finally{pending=false;if(data?.action==="startup")startupReady=true;
      if(!stopped){render();offerUpdate();schedule();}}
  }
  function read(){return request();}
  function check(){return request(installUncertain?undefined:{action:"check"});}
  async function install(){
    if(busy()||installSubmitted||installUncertain||!snapshot.canInstall||!snapshot.release)return false;
    installSubmitted=true;handoffDialogsClosed=false;installFailureNotified=false;
    const ok=await request({action:"install",version:snapshot.release.version});
    // An ambiguous reply is resolved by local status reads, never an automatic
    // second install. Errors with a confirmed server state allow an explicit retry.
    if(!ok&&!installUncertain)installSubmitted=false;
    return ok;
  }
  function openNotes(automatic=false){
    if(stopped||!snapshot.release)return;
    startupOfferDone=true;clearTimeout(offerTimer);offerTimer=null;
    returnFocus=document.activeElement&&document.activeElement!==document.body?document.activeElement:$("settings-open");render();
    $("app-update-dialog").dataset.offer=automatic?"startup":"manual";
    if($("app-update-offer-intro"))$("app-update-offer-intro").hidden=!automatic;
    if(!$("app-update-dialog").open)hooks.openDialog("app-update-dialog");
    $("app-update-sidebar-badge").setAttribute("aria-expanded","true");
    $(automatic?"app-update-dismiss":"app-update-close").focus();schedule(!automatic);
  }
  function bind(){
    $("app-update-check").onclick=check;
    $("app-update-retry").onclick=$("app-update-dialog-retry").onclick=()=>localError?read():check();
    $("app-update-auto").onchange=()=>request({action:"configure",autoCheck:$("app-update-auto").checked});
    $("app-update-sidebar-badge").onclick=$("app-update-notes-open").onclick=()=>openNotes();$("app-update-install").onclick=install;
    $("app-update-close").onclick=$("app-update-dismiss").onclick=()=>$("app-update-dialog").close();
    $("app-update-dialog").oncancel=event=>{event.preventDefault();$("app-update-dialog").close();};
    $("app-update-dialog").onclose=()=>{
      $("app-update-sidebar-badge").setAttribute("aria-expanded","false");
      const target=returnFocus?.hidden?$("settings-open"):returnFocus,owner=target?.closest?.("dialog");
      if(!closingForHandoff&&target?.isConnected&&!target.disabled&&(!owner||owner.open))target.focus();
      returnFocus=null;schedule();
    };
    document.addEventListener("visibilitychange",wake);
    document.addEventListener("close",contextChanged,true);
    document.addEventListener("focusin",contextChanged);
    globalThis.addEventListener?.("focus",wake);
  }
  function start(initial,warning){
    if(started||!hooks)return;
    started=true;stopped=false;startupWarning=typeof warning==="string"?warning:"";observe(initial);render();
    // Wait for the window-start response before offering a cached release, so
    // its sequence cannot reopen an offer dismissed during the first request.
    if(snapshot.autoCheck)void request({action:"startup"});else{startupReady=true;schedule();}
  }
  return {attach:value=>{hooks=value;if(!bound){bind();bound=true;}},start,observe,contextChanged,
    settingsOpened:()=>{render();schedule(true);},settingsClosed:()=>schedule(),
    stop:()=>{stopped=true;clearTimeout(timer);clearTimeout(offerTimer);timer=offerTimer=null;
      document.removeEventListener?.("visibilitychange",wake);document.removeEventListener?.("close",contextChanged,true);
      document.removeEventListener?.("focusin",contextChanged);globalThis.removeEventListener?.("focus",wake);}};
})();
