"use strict";

// A version handoff uses the existing attention loop. Drafts stay in this tab
// until an authenticated, acknowledged local handoff has preserved them.
globalThis.WorkspaceUpgrade = (() => {
  const MAX_BYTES=256*1024, ignored=new Set(), requests=new Set();
  let hooks=null, ready=false, current=null, locked=false, capturing=false, cancelling=false, stopping=false;
  let stopUncertainRevision=null;
  let restoredId=null, serial=0, focusBefore=null, localError="", latestRevision=-1;
  let recovery=null, recoveryNotice="", releasing=false, adding=false;
  const addedRows=new Set();
  const $=id=>document.getElementById(id);
  const dialog=()=>$("upgrade-dialog");
  const safeId=id=>typeof id==="string"&&/^[a-f0-9]{32}$/i.test(id);
  const versionUpdate=value=>{
    const valid=version=>typeof version==="string"&&/^\d+\.\d+\.\d+$/.test(version);
    if(!valid(value?.sourceVersion)||!valid(value?.targetVersion))return false;
    const source=value.sourceVersion.split(".").map(Number),target=value.targetVersion.split(".").map(Number);
    for(let i=0;i<3;i++)if(source[i]!==target[i])return target[i]>source[i];return false;
  };
  const unattended=path=>/^\/api\/(?:events(?:\?|$)|attention(?:\/|\?|$)|ui-health(?:\?|$)|drafts(?:\?|$)|upgrade(?:\?|$)|window\/presence(?:\?|$))/.test(path);
  const canStopForUpdate=()=>!!current?.canStop&&["waiting","capture"].includes(current.stage)&&!stopping&&!cancelling&&!capturing&&stopUncertainRevision===null;
  function begin(path,mutation){
    if(locked&&mutation&&!unattended(path))throw new Error("새 버전으로 전환하고 있어요. 잠시 기다리거나 전환을 취소해 주세요.");
    if(unattended(path))return ()=>{};
    const request={mutation};requests.add(request);return ()=>requests.delete(request);
  }
  function freeze(value){
    if(locked===value)return;
    locked=value;
    if(value){
      focusBefore=document.activeElement;
      focusBefore=globalThis.WorkspaceAppUpdates?.handoffStarting(current)||focusBefore;
      if(!dialog().open)dialog().showModal();
    }else{
      if(dialog().open)dialog().close();
      if(focusBefore?.isConnected&&!focusBefore.disabled)focusBefore.focus();
      focusBefore=null;
    }
  }
  function render(){
    globalThis.WorkspaceAppUpdates?.handoffChanged(current,locked);
    const notice=$("upgrade-notice");if(!notice)return;
    notice.hidden=!current&&!localError&&!recoveryNotice;
    $("upgrade-recovery-open").hidden=!recovery;
    const target=current?.targetVersion?` ${current.targetVersion}`:"";
    const mode=current?.executionMode,isUpdate=versionUpdate(current)||globalThis.WorkspaceAppUpdates?.canYieldForHandoff(current)===true;
    const modeLabel=isUpdate?"":mode==="administrator"?"관리자 권한":mode==="normal"?"일반 권한":"";
    $("upgrade-notice-text").textContent=localError||(!current&&recoveryNotice)||(current?.stage==="waiting"
      ?modeLabel?`현재 작업과 응답을 마치면 ${modeLabel}으로 다시 시작해요.`:`현재 작업과 응답을 마치면 새 버전${target}으로 자동 전환해요.`
      :current?.stage==="failed"?(current.error||"버전 전환을 마치지 못했어요. 작성 중인 내용은 이 창에 유지하고 있습니다.")
      :modeLabel?`작성 중인 내용과 첨부 자료를 보관한 뒤 ${modeLabel}으로 다시 시작해요.`:"작성 중인 내용과 첨부 자료를 보관한 뒤 새 버전으로 전환해요.");
    $("upgrade-stop").hidden=!current?.canStop||!["waiting","capture"].includes(current.stage);
    $("upgrade-stop").disabled=!canStopForUpdate();
    $("upgrade-stop").textContent=stopping?"중지 준비 중…":"작업 중지 후 업데이트";
    $("upgrade-notice-cancel").hidden=!current;$("upgrade-notice-cancel").disabled=cancelling||stopping||["committing","closed"].includes(current?.stage);
    const restarting=["committing","closed"].includes(current?.stage);
    $("upgrade-title").textContent=localError?"전환 상태를 확인하고 있어요":modeLabel?`${modeLabel}으로 다시 시작하고 있어요`:restarting?"새 버전으로 다시 시작합니다":"다시 시작할 준비를 하고 있어요";
    $("upgrade-detail").textContent=localError||(restarting?current?.stopRequested?"진행 중인 작업과 연결을 중지하고 새 버전으로 다시 시작합니다. 대화와 파일, 이어 할 일은 유지하며 중지한 요청은 자동 재실행하지 않습니다.":"앱이 잠시 닫힌 뒤 자동으로 열려요. 업데이트 안내창에서 진행 상태를 계속 확인할 수 있습니다."
      :current?.stage==="captured"?current?.stopRequested?"작성 중인 내용과 첨부 자료를 보관했어요. 진행 중인 작업을 중지한 뒤 새 버전으로 다시 시작합니다.":"작성 중인 내용과 첨부 자료를 보관했어요. 잠시 후 새 버전으로 다시 시작합니다."
      :"작성 중인 내용과 첨부 자료를 보관하고 있어요. 잠시만 기다려 주세요.");
    $("upgrade-dialog").dataset.stage=current?.stage||"capture";
    $("upgrade-cancel").disabled=cancelling||stopping||restarting;
    $("upgrade-cancel").textContent=cancelling?"취소 확인 중…":"전환 취소";
  }
  function clear(){current=null;serial++;capturing=false;cancelling=false;stopping=false;stopUncertainRevision=null;latestRevision=-1;freeze(false);render();}
  async function cancel(){
    if(!current||cancelling||stopping||["committing","closed"].includes(current.stage))return false;
    const owner=current.requestId,ticket=serial;cancelling=true;render();
    try{
      const response=await hooks.api("/api/upgrade",{action:"cancel",requestId:owner});
      if(ticket!==serial||current?.requestId!==owner)return false;
      if(response.ok!==true||response.upgrade&&(response.upgrade.requestId!==owner||response.upgrade.stage!=="cancelled"))throw new Error("전환 취소를 아직 확인하지 못했어요. 잠시 후 다시 시도해 주세요.");
      ignored.add(owner);if(ignored.size>20)ignored.delete(ignored.values().next().value);
      localError="";clear();return true;
    }catch(error){
      if(ticket===serial){localError=error.message||"전환 취소를 확인하지 못했어요. 입력한 내용은 이 창에 유지하고 있습니다.";render();}
      return false;
    }finally{if(ticket===serial){cancelling=false;render();}}
  }
  async function stopForUpdate(){
    if(!canStopForUpdate()||!hooks.confirmStopForUpdate)return false;
    const owner=current.requestId,ticket=serial;let submitted=false,submittedRevision=null;
    stopping=true;render();
    try{
      if(!await hooks.confirmStopForUpdate()||ticket!==serial||current?.requestId!==owner)return false;
      if(!current.canStop||!["waiting","capture"].includes(current.stage))return false;
      if([...requests].some(request=>request.mutation)||hooks.canStopCapture?.()!==true||document.querySelector("dialog[open]:not(#app-update-dialog)"))throw new Error("현재 자료 선택이나 전송 준비를 마친 뒤 다시 시도해 주세요. 진행 중인 작업은 아직 중지하지 않았습니다.");
      freeze(true);localError="";render();
      const payload={action:"stop",requestId:owner,revision:current.revision,confirmed:true,snapshot:hooks.capture()};
      if(new TextEncoder().encode(JSON.stringify(payload)).length>MAX_BYTES)throw new Error("작성 중인 내용이 많아 중지 후 업데이트를 진행하지 않았어요. 초안을 나누어 보관한 뒤 다시 시도해 주세요.");
      await globalThis.WorkspaceDraftPersistence?.flush();
      submitted=true;submittedRevision=payload.revision;const response=await hooks.api("/api/upgrade",payload);
      if(ticket!==serial||current?.requestId!==owner)return false;
      if(response.ok!==true||response.upgrade?.requestId!==owner||response.upgrade.stage!=="captured"||response.upgrade.stopRequested!==true){
        const failure=new Error(response.upgrade?.error||"작업 중지 후 전환 준비를 확인하지 못했어요. 현재 창과 입력 내용을 유지합니다.");
        if(response.ok!==true)failure.upgrade=response.upgrade;
        throw failure;
      }
      if((response.upgrade.revision||0)>=latestRevision){current=response.upgrade;latestRevision=Math.max(latestRevision,current.revision||0);}
      stopUncertainRevision=null;
      render();return true;
    }catch(error){
      if(ticket===serial){
        localError=error.message||"작업 중지 후 업데이트를 준비하지 못했어요.";
        const rejected=error.upgrade;
        if(rejected?.requestId===owner&&["waiting","capture"].includes(rejected.stage)&&Number.isInteger(rejected.revision)&&rejected.revision>=latestRevision){
          current=rejected;latestRevision=rejected.revision;stopUncertainRevision=null;freeze(false);
        }else if(!submitted)freeze(false);
        else if(latestRevision<=submittedRevision){
          stopUncertainRevision=submittedRevision;
          localError+=" 전환 상태가 불확실합니다. ‘전환 취소’를 눌러 확인한 뒤 다시 시도해 주세요.";
        }else if(["waiting","capture"].includes(current?.stage))freeze(false);
        render();
      }
      return false;
    }finally{if(ticket===serial){stopping=false;render();}}
  }
  function canCapture(){
    const ownUpdate=globalThis.WorkspaceAppUpdates?.canYieldForHandoff(current)===true;
    return ready&&hooks&&!locked&&!capturing&&!cancelling&&!stopping&&!requests.size&&hooks.canCapture()
      &&!document.querySelector(ownUpdate?"dialog[open]:not(#app-update-dialog)":"dialog[open]");
  }
  async function capture(){
    if(current?.stage!=="capture"||!canCapture())return;
    const owner=current.requestId,ticket=serial;capturing=true;localError="";
    try{
      // No await between the lock and snapshot: user input cannot enter a gap.
      freeze(true);render();
      const snapshot=hooks.capture(),payload={action:"capture",requestId:owner,revision:current.revision,snapshot};
      if(new TextEncoder().encode(JSON.stringify(payload)).length>MAX_BYTES){
        localError="작성 중인 내용이 많아 자동 전환을 진행하지 않았어요. 내용을 보관한 뒤 다시 실행해 주세요.";
        const message=localError;freeze(false);render();if(await cancel())hooks.failed?.(message);return;
      }
      await globalThis.WorkspaceDraftPersistence?.flush();
      const response=await hooks.api("/api/upgrade",payload);
      if(ticket!==serial||current?.requestId!==owner)return;
      if(Number.isInteger(response.upgrade?.revision)&&response.upgrade.revision<latestRevision)return;
      if(response.ok===true&&response.upgrade?.requestId===owner&&response.upgrade.stage==="captured"){
        current=response.upgrade;latestRevision=Math.max(latestRevision,current.revision||0);render();
      }else if(response.upgrade?.requestId===owner&&["waiting","capture"].includes(response.upgrade.stage)){
        current=response.upgrade;freeze(false);render();
      }else throw new Error("작성 중인 내용의 보관을 확인하지 못했어요. 전환을 취소하고 있습니다.");
    }catch(error){
      if(ticket!==serial||current?.requestId!==owner)return;
      if(error.upgrade?.requestId===owner&&["waiting","capture"].includes(error.upgrade.stage)
          &&Number.isInteger(error.upgrade.revision)&&error.upgrade.revision>=latestRevision){
        current=error.upgrade;latestRevision=current.revision;freeze(false);render();return;
      }
      // A lost acknowledgement may still mean the server saved the snapshot.
      // Keep editing paused until cancellation is confirmed, never replay send.
      localError=error.message||"입력한 내용을 보관하지 못했어요.";const message=localError;render();if(await cancel())hooks.failed?.(message);
    }finally{if(ticket===serial)capturing=false;}
  }
  async function observe(value,attention){
    if(!ready||!hooks)return;
    if(!value){if(current&&!((stopping&&locked)||stopUncertainRevision!==null))clear();return;}
    if(!safeId(value.requestId)||ignored.has(value.requestId))return;
    if(current?.requestId!==value.requestId){clear();current=value;localError="";}
    const revision=Number.isInteger(value.revision)?value.revision:0,previousRevision=latestRevision;
    if(revision<latestRevision)return;
    latestRevision=revision;current=value;
    if(stopUncertainRevision!==null&&revision>stopUncertainRevision)stopUncertainRevision=null;
    if(["cancelled","expired"].includes(value.stage)){ignored.add(value.requestId);localError="";clear();return;}
    // Attention may still report waiting/capture while the explicit stop
    // snapshot is being flushed or its POST is in flight. Keep that snapshot
    // frozen until the attempt finishes; a stale heartbeat must not admit edits.
    if(["waiting","failed"].includes(value.stage)){if((!stopping&&stopUncertainRevision===null)||value.stage==="failed")freeze(false);render();return;}
    if(["captured","committing","closed"].includes(value.stage)){freeze(true);render();return;}
    if(value.stage==="capture"&&revision>previousRevision&&!stopping&&stopUncertainRevision===null)freeze(false);
    render();
    // The server is authoritative, but a just-rendered human question must not
    // be covered by a handoff modal while its attention response catches up.
    if(value.stage==="capture"&&!attention?.items?.length&&!Number(attention?.total))await capture();
  }
  async function bootstrap(value){
    if(document.readyState==="loading")await new Promise(resolve=>document.addEventListener("DOMContentLoaded",resolve,{once:true}));
    if(typeof value?.upgradeWarning==="string"&&value.upgradeWarning){recoveryNotice=value.upgradeWarning;render();}
    const restore=value?.upgradeRestore;
    if(!restore||!safeId(restore.requestId)||restoredId===restore.requestId||!hooks){ready=true;return;}
    if(!restore.snapshot||!Array.isArray(restore.snapshot.drafts)){ready=true;return;}
    recovery=restore;addedRows.clear();
    try{
      // Existing drafts (including explicit edits to empty) win in the shared
      // screen-recovery hook. Restoring never executes an AI request.
      const outcome=await hooks.restore(restore.snapshot);
      if(outcome?.selectionChanged||outcome?.conflicts?.length){
        recoveryNotice="현재 입력과 겹치는 이전 초안을 별도로 보관했어요. ‘복원·정리’에서 확인해 주세요.";
        render();return;
      }
      await globalThis.WorkspaceDraftPersistence?.flush();
      const response=await hooks.api("/api/upgrade",{action:"restored",requestId:restore.requestId});
      if(response.ok!==true)throw new Error("restore acknowledgement missing");
      restoredId=restore.requestId;recovery=null;recoveryNotice="";render();
      if(!globalThis.WorkspaceAppUpdates?.completed(restore))hooks.restored?.(outcome);
    }catch(error){localError="이전 초안의 복원을 마치지 못했어요. ‘복원·정리’에서 내용을 확인할 수 있습니다.";render();}
    finally{ready=true;}
  }
  const recoveryRows=()=>recovery?[...recovery.snapshot.drafts,...(recovery.snapshot.stashes||[]).map(row=>({...row,stashed:true}))]:[];
  function recoveryRow(){
    const row=recoveryRows()[Number($("upgrade-recovery-task").value)||0];
    $("upgrade-recovery-text").value=row?.text||"";
    $("upgrade-recovery-files").textContent=row?.attachments?.length?row.attachments.join("\n"):"첨부 자료 없음";
    const add=$("upgrade-recovery-restore");
    add.disabled=adding||releasing||!row||(!row.text&&!row.attachments.length)||addedRows.has(Number($("upgrade-recovery-task").value)||0);
    add.textContent=adding?"복원 중…":addedRows.has(Number($("upgrade-recovery-task").value)||0)?"입력창에 복원됨":"이 초안 복원";
  }
  async function addSelected(){
    if(!recovery||adding||releasing||!hooks.addRecoveredDraft)return;
    const owner=recovery,index=Number($("upgrade-recovery-task").value)||0,row=recoveryRows()[index];
    if(!row||addedRows.has(index)||(!row.text&&!row.attachments.length))return;
    adding=true;recoveryRow();$("upgrade-recovery-release").disabled=true;
    try{
      hooks.addRecoveredDraft(row);addedRows.add(index);
      await globalThis.WorkspaceDraftPersistence?.flush();
      if(recovery===owner)$("upgrade-recovery-message").textContent="현재 입력창에 내용을 추가했어요. 요청은 자동으로 보내지 않습니다. 확인을 마치면 보관 기록을 지울 수 있어요.";
    }catch(error){if(recovery===owner)$("upgrade-recovery-message").textContent=error.message||"복원을 마치지 못했어요. 이전 초안 기록은 유지합니다.";}
    finally{adding=false;if(recovery===owner){recoveryRow();$("upgrade-recovery-release").disabled=releasing;}}
  }
  function openRecovery(){
    if(!recovery)return;
    const select=$("upgrade-recovery-task");select.replaceChildren();
    recoveryRows().forEach((row,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=(row.id==="home:chat"?"일반 대화 홈":["home","home:work"].includes(row.id)?"업무 홈":hooks.taskTitle?.(row.id)||"이전 업무 "+row.id.slice(0,8))+(row.stashed?" · 임시 보관":"");select.append(option);});
    select.value="0";recoveryRow();$("upgrade-recovery-message").textContent="확인 전까지 이전 초안 기록을 유지합니다.";
    if(hooks.openRecovery)hooks.openRecovery("upgrade-recovery-dialog");else $("upgrade-recovery-dialog").showModal();
  }
  async function releaseRecovery(){
    if(!recovery||releasing||adding||!hooks.confirmRecovery)return;
    const owner=recovery;releasing=true;$("upgrade-recovery-release").disabled=true;
    try{
      if(!await hooks.confirmRecovery()||recovery!==owner)return;
      await globalThis.WorkspaceDraftPersistence?.flush();
      const response=await hooks.api("/api/upgrade",{action:"restored",requestId:owner.requestId});
      if(response.ok!==true)throw new Error("보관 해제를 확인하지 못했어요.");
      if(recovery!==owner)return;
      restoredId=owner.requestId;recovery=null;recoveryNotice=localError="";$("upgrade-recovery-dialog").close();render();
    }catch(error){$("upgrade-recovery-message").textContent=error.message||"초안 기록을 유지하고 있어요. 다시 시도해 주세요.";}
    finally{releasing=false;$("upgrade-recovery-release").disabled=false;}
  }
  function attach(value){
    hooks=value;
    $("upgrade-notice-cancel").onclick=$("upgrade-cancel").onclick=cancel;
    $("upgrade-stop").onclick=stopForUpdate;
    $("upgrade-recovery-open").onclick=openRecovery;
    $("upgrade-recovery-close").onclick=()=>$("upgrade-recovery-dialog").close();
    $("upgrade-recovery-task").onchange=recoveryRow;
    $("upgrade-recovery-restore").onclick=addSelected;
    $("upgrade-recovery-select").onclick=()=>{$("upgrade-recovery-text").focus();$("upgrade-recovery-text").select();};
    $("upgrade-recovery-release").onclick=releaseRecovery;
    dialog().oncancel=event=>{event.preventDefault();void cancel();};
  }
  // Capture phase intercepts app shortcuts and drops as well as pointer input.
  // The modal's ordinary navigation and cancel action remain usable.
  function guard(event){
    if(!locked)return;
    const inside=event.target?.closest?.("#upgrade-dialog")===dialog();
    const navigation=event.type==="keydown"&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&["Tab","Enter"," ","Escape"].includes(event.key);
    if(inside&&(event.type!=="keydown"||navigation))return;
    event.preventDefault();event.stopImmediatePropagation();
  }
  for(const type of ["keydown","click","submit","drop","paste","beforeinput"])globalThis.addEventListener?.(type,guard,true);
  return {attach,bootstrap,observe,begin,cancel,stopForUpdate,canStopForUpdate,isLocked:()=>locked};
})();
