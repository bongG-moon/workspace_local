"use strict";

// Release history is read-only and independent from update/install state.
// Opening this dialog is the only automatic trigger for a remote history check.
globalThis.WorkspaceReleaseHistory = (() => {
  const $=id=>document.getElementById(id),endpoint="/api/app-update-history";
  const stable=value=>typeof value==="string"&&/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(value);
  const plain=(value,limit)=>typeof value==="string"?value.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g,"").slice(0,limit):"";
  let hooks=null,bound=false,stopped=false,generation=0,pending=false,timer=null,controller=null,returnFocus=null,polls=0;
  let selectedVersion="",optionKey="",localError="";
  let snapshot={status:"idle",currentVersion:"",source:{provider:"",label:"배포 서버"},entries:[],checkedAt:null,error:null};

  function safeLink(value,provider,version,origin){
    // GitLab's numeric project ID does not identify a trusted browser URL.
    // Keep it non-clickable until the API exposes a configured origin contract.
    if(origin!=="published"||provider!=="github"||typeof value!=="string"||value.length>2048
      ||/[\s\x00-\x1f\x7f\\]/.test(value))return "";
    try{
      const url=new URL(value);
      if(url.protocol!=="https:"||!url.hostname||url.username||url.password||url.search||url.hash)return "";
      if(provider==="github"&&url.href!==`https://github.com/bongG-moon/workspace_local/releases/tag/v${version}`)return "";
      return url.href;
    }catch{return "";}
  }
  function normalize(value){
    if(!value||!["idle","loading","ready","error"].includes(value.status)||!Array.isArray(value.entries))return null;
    const provider=value.source?.provider,byVersion=new Map();
    for(const row of value.entries.slice(0,30)){
      if(!row||!stable(row.version)||!["published","bundled"].includes(row.origin))continue;
      const noteSource=row.origin==="published"?(row.noteSource===undefined?"deployment":row.noteSource):null;
      if(row.origin==="published"&&!["deployment","gitlab-release","package"].includes(noteSource))continue;
      if(provider==="github"&&row.origin==="published"&&noteSource!=="deployment")continue;
      const prior=byVersion.get(row.version);
      if(prior&&(prior.origin==="published"||row.origin==="bundled"))continue;
      const internal=provider==="gitlab"&&row.origin==="published";
      const notes=internal&&noteSource==="package"?"":plain(row.notes,32768);
      // Server records outrank bundled explanations even if a malformed reply
      // contains both. A package record cannot borrow notes from the app bundle.
      byVersion.set(row.version,{version:row.version,title:plain(row.title,300)||`AX Workspace ${row.version}`,
        notes:notes.trim()?notes:internal?(noteSource==="gitlab-release"?"당시 릴리스 설명을 확인할 수 없습니다.":"당시 배포 설명을 확인할 수 없습니다."):"이 버전의 변경 내용이 아직 제공되지 않았어요.",
        emptyNotes:!notes.trim(),origin:row.origin,noteSource,
        publishedAt:typeof row.publishedAt==="string"&&Number.isFinite(Date.parse(row.publishedAt))?row.publishedAt:null,
        url:safeLink(row.url,provider,row.version,row.origin)});
    }
    return {status:value.status,currentVersion:stable(value.currentVersion)?value.currentVersion:"",
      source:{provider,label:plain(value.source?.label,120)||"배포 서버"},entries:[...byVersion.values()],
      checkedAt:typeof value.checkedAt==="number"&&Number.isFinite(value.checkedAt)?value.checkedAt:null,
      error:plain(value.error,1000)||null};
  }
  function dateText(value){return new Date(value).toLocaleDateString("ko-KR",{year:"numeric",month:"long",day:"numeric"});}
  function sourceLabel(row){
    if(row.origin==="bundled")return "앱 변경 이력";
    if(snapshot.source.provider!=="gitlab")return "배포 이력";
    return {deployment:"사내 배포 설명","gitlab-release":"GitLab 릴리스 설명",package:"배포 파일 기록"}[row.noteSource];
  }
  function entryDate(row){
    if(row.origin==="bundled")return "앱에 포함된 변경 내역";
    if(snapshot.source.provider!=="gitlab")return row.publishedAt?dateText(row.publishedAt):"게시일 확인 필요";
    const label={deployment:"배포 기록일","gitlab-release":"릴리스 등록일",package:"패키지 등록일"}[row.noteSource];
    return `${label} ${row.publishedAt?dateText(row.publishedAt):"확인 필요"}`;
  }
  function text(id,value){const node=$(id);if(node.textContent!==value)node.textContent=value;}
  function render(){
    const rows=snapshot.entries;
    if(!rows.some(row=>row.version===selectedVersion))selectedVersion=rows.find(row=>row.version===snapshot.currentVersion)?.version||rows[0]?.version||"";
    const row=rows.find(item=>item.version===selectedVersion),select=$("release-history-version");
    const key=JSON.stringify([snapshot.currentVersion,snapshot.source.provider,rows.map(item=>[item.version,item.origin,item.noteSource])]);
    if(key!==optionKey){
      optionKey=key;
      const options=rows.map(item=>{const node=document.createElement("option");node.value=item.version;
        node.textContent=`${item.version}${item.version===snapshot.currentVersion?" · 현재 버전":""} · ${sourceLabel(item)}`;return node;});
      select.replaceChildren(...options);
    }
    select.value=selectedVersion;select.disabled=!rows.length;
    $("release-history-content").hidden=!row;$("release-history-empty").hidden=!!row;
    text("release-history-source",snapshot.source.label);
    text("release-history-help",snapshot.source.provider==="gitlab"
      ?row?.noteSource==="package"?"사내 서버의 파일 기록입니다. 설명이 남아 있지 않거나 조회 권한·지원 범위가 제한된 경우입니다. ‘앱 변경 이력’은 설치본의 설명입니다."
      :"사내 서버에 남은 기록을 표시해요. ‘앱 변경 이력’은 설치본의 설명이며 사내 배포 기록과 다를 수 있어요."
      :"‘앱 변경 이력’은 설치본에 포함된 설명이며 실제 배포 기록과 다를 수 있어요.");
    text("release-history-count",`${rows.length}개 버전`);
    text("release-history-detail-title",row?.title||"");
    text("release-history-meta",row?`${sourceLabel(row)} · ${entryDate(row)}`:"");
    $("release-history-current").hidden=!row||row.version!==snapshot.currentVersion;
    text("release-history-notes",row?.notes||"");
    $("release-history-notes").dataset.empty=String(row?.emptyNotes===true);
    const link=$("release-history-link");link.hidden=!row?.url;link.setAttribute("href",row?.url||"#");
    link.textContent=snapshot.source.provider==="gitlab"?"GitLab에서 보기 ↗":"GitHub에서 보기 ↗";
    const loading=pending||snapshot.status==="loading",failure=localError||(snapshot.status==="error"?snapshot.error||"배포 이력을 확인하지 못했어요.":"");
    text("release-history-status",failure?(rows.length?`${failure} 저장된 변경 내역은 계속 볼 수 있어요.`:failure):loading?"배포 이력을 확인하고 있어요.":snapshot.checkedAt?`마지막 확인 · ${dateText(snapshot.checkedAt*1000)}`:rows.length?rows.some(item=>item.origin==="published")?"저장된 배포 기록을 볼 수 있어요.":"앱에 포함된 변경 내역을 볼 수 있어요.":"표시할 변경 내역이 아직 없어요.");
    $("release-history-dialog").dataset.state=failure?"error":loading?"loading":snapshot.status;
    $("release-history-refresh").disabled=loading;
    $("release-history-refresh").setAttribute("aria-busy",String(loading));
    $("release-history-open").setAttribute("aria-expanded",String($("release-history-dialog").open===true));
  }
  function clearPoll(){clearTimeout(timer);timer=null;}
  function schedule(){
    clearPoll();
    if(stopped||!$("release-history-dialog").open||pending||snapshot.status!=="loading"||document.hidden===true)return;
    if(polls>=30){localError="확인이 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.";snapshot={...snapshot,status:"error"};render();return;}
    timer=setTimeout(()=>{timer=null;polls++;void request();},2000);
  }
  async function request(data,epoch=generation){
    if(stopped||epoch!==generation||pending||!hooks||!$("release-history-dialog").open)return false;
    pending=true;localError="";clearPoll();controller=new AbortController();render();
    try{
      const value=await hooks.api(endpoint,data,controller.signal);
      if(epoch!==generation||!$("release-history-dialog").open)return false;
      const next=normalize(value);if(!next)throw new Error("변경 내역을 확인하지 못했어요.");
      snapshot=next;return true;
    }catch(error){
      if(epoch===generation&&$("release-history-dialog").open){localError=plain(error?.message,1000)||"배포 서버에 연결하지 못했어요.";snapshot={...snapshot,status:"error"};}
      return false;
    }finally{if(epoch===generation){pending=false;controller=null;render();schedule();}}
  }
  async function open(){
    if(stopped||!hooks||$("release-history-dialog").open)return;
    const epoch=++generation;pending=false;polls=0;localError="";
    returnFocus=document.activeElement;hooks.openDialog("release-history-dialog");render();$("release-history-close").focus();
    await request(undefined,epoch);
    // A close/reopen cannot let the earlier request start another check.
    if(epoch===generation&&$("release-history-dialog").open)await request({action:"check",force:false},epoch);
  }
  function closed(){
    generation++;clearPoll();controller?.abort();controller=null;pending=false;
    const owner=returnFocus?.closest?.("dialog");
    const target=returnFocus?.isConnected&&!returnFocus.hidden&&!returnFocus.disabled&&(!owner||owner.open)?returnFocus:$("settings-open");
    returnFocus=null;render();target?.focus();
  }
  function bind(){
    $("release-history-open").onclick=open;
    $("release-history-close").onclick=$("release-history-dismiss").onclick=()=>$("release-history-dialog").close();
    $("release-history-dialog").oncancel=event=>{event.preventDefault();$("release-history-dialog").close();};
    $("release-history-dialog").onclose=closed;
    $("release-history-dialog").onkeydown=event=>{
      if(event.key!=="Tab"||event.ctrlKey||event.altKey||event.metaKey)return;
      const controls=[...$("release-history-dialog").querySelectorAll('button:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]')]
        .filter(node=>node.getClientRects().length&&!node.hidden);
      const first=controls[0],last=controls.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    };
    $("release-history-version").onchange=()=>{selectedVersion=$("release-history-version").value;render();$("release-history-notes").scrollTop=0;};
    $("release-history-refresh").onclick=()=>{if(pending||snapshot.status==="loading")return;polls=0;return request({action:"check",force:true});};
    document.addEventListener("visibilitychange",()=>{if(document.hidden)clearPoll();else schedule();});
    globalThis.addEventListener?.("pagehide",stop);
    render();
  }
  function stop(){stopped=true;generation++;clearPoll();controller?.abort();controller=null;pending=false;}
  return {attach:value=>{hooks=value;if(!bound){bound=true;bind();}},stop};
})();
