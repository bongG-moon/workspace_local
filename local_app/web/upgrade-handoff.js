"use strict";

// A version handoff uses the existing attention loop. Drafts stay in this tab
// until an authenticated, acknowledged local handoff has preserved them.
globalThis.WorkspaceUpgrade = (() => {
  const MAX_BYTES=256*1024, ignored=new Set(), requests=new Set();
  let hooks=null, ready=false, current=null, locked=false, capturing=false, cancelling=false;
  let restoredId=null, serial=0, focusBefore=null, localError="", latestRevision=-1;
  let recovery=null, recoveryNotice="", releasing=false;
  const $=id=>document.getElementById(id);
  const dialog=()=>$("upgrade-dialog");
  const safeId=id=>typeof id==="string"&&/^[a-f0-9]{32}$/i.test(id);
  const unattended=path=>/^\/api\/(?:events(?:\?|$)|attention(?:\/|\?|$)|ui-health(?:\?|$)|drafts(?:\?|$)|upgrade(?:\?|$)|window\/presence(?:\?|$))/.test(path);
  function begin(path,mutation){
    if(locked&&mutation&&!unattended(path))throw new Error("새 버전으로 전환하고 있어요. 잠시 기다리거나 전환을 취소해 주세요.");
    if(unattended(path))return ()=>{};
    const request={};requests.add(request);return ()=>requests.delete(request);
  }
  function freeze(value){
    if(locked===value)return;
    locked=value;
    if(value){
      focusBefore=document.activeElement;
      if(!dialog().open)dialog().showModal();
    }else{
      if(dialog().open)dialog().close();
      if(focusBefore?.isConnected&&!focusBefore.disabled)focusBefore.focus();
      focusBefore=null;
    }
  }
  function render(){
    const notice=$("upgrade-notice");if(!notice)return;
    notice.hidden=!current&&!localError&&!recoveryNotice;
    $("upgrade-recovery-open").hidden=!recovery;
    const target=current?.targetVersion?` ${current.targetVersion}`:"";
    const mode=current?.executionMode,modeLabel=mode==="administrator"?"관리자 권한":mode==="normal"?"일반 권한":"";
    $("upgrade-notice-text").textContent=localError||(!current&&recoveryNotice)||(current?.stage==="waiting"
      ?modeLabel?`현재 작업과 응답을 마치면 ${modeLabel}으로 다시 시작해요.`:`현재 작업과 응답을 마치면 새 버전${target}으로 자동 전환해요.`
      :current?.stage==="failed"?(current.error||"버전 전환을 마치지 못했어요. 작성 중인 내용은 이 창에 유지하고 있습니다.")
      :modeLabel?`작성 중인 내용과 첨부 자료를 보관한 뒤 ${modeLabel}으로 다시 시작해요.`:"작성 중인 내용과 첨부 자료를 보관한 뒤 새 버전으로 전환해요.");
    $("upgrade-notice-cancel").hidden=!current;$("upgrade-notice-cancel").disabled=cancelling||current?.stage==="committing";
    $("upgrade-title").textContent=localError?"전환 상태를 확인하고 있어요":modeLabel?`${modeLabel}으로 다시 시작하고 있어요`:"새 버전으로 전환하고 있어요";
    $("upgrade-detail").textContent=localError||"작성 중인 내용과 첨부 자료를 그대로 이어서 사용할 수 있도록 보관하고 있어요. 잠시만 기다려 주세요.";
    $("upgrade-cancel").disabled=cancelling||current?.stage==="committing";
    $("upgrade-cancel").textContent=cancelling?"취소 확인 중…":"전환 취소";
  }
  function clear(){current=null;serial++;capturing=false;cancelling=false;latestRevision=-1;freeze(false);render();}
  async function cancel(){
    if(!current||cancelling||current.stage==="committing")return false;
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
  function canCapture(){
    return ready&&hooks&&!locked&&!capturing&&!cancelling&&!requests.size&&hooks.canCapture()
      &&!document.querySelector("dialog[open]");
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
    if(!value){if(current)clear();return;}
    if(!safeId(value.requestId)||ignored.has(value.requestId))return;
    if(current?.requestId!==value.requestId){clear();current=value;localError="";}
    const revision=Number.isInteger(value.revision)?value.revision:0,previousRevision=latestRevision;
    if(revision<latestRevision)return;
    latestRevision=revision;current=value;
    if(["cancelled","expired"].includes(value.stage)){ignored.add(value.requestId);localError="";clear();return;}
    if(["waiting","failed"].includes(value.stage)){freeze(false);render();return;}
    if(["captured","committing","closed"].includes(value.stage)){freeze(true);render();return;}
    if(value.stage==="capture"&&revision>previousRevision)freeze(false);
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
    recovery=restore;
    try{
      // Existing drafts (including explicit edits to empty) win in the shared
      // screen-recovery hook. Restoring never executes an AI request.
      const outcome=await hooks.restore(restore.snapshot);
      if(outcome?.selectionChanged||outcome?.conflicts?.length){
        recoveryNotice="새 창의 입력 내용은 유지했어요. 덮어쓰지 않은 이전 초안도 별도로 보관했으니 확인해 주세요.";
        render();return;
      }
      await globalThis.WorkspaceDraftPersistence?.flush();
      const response=await hooks.api("/api/upgrade",{action:"restored",requestId:restore.requestId});
      if(response.ok!==true)throw new Error("restore acknowledgement missing");
      restoredId=restore.requestId;recovery=null;recoveryNotice="";render();
      hooks.restored?.(outcome);
    }catch(error){localError="이전 창의 입력 내용 복원을 마치지 못했어요. 창을 다시 열면 다시 확인합니다.";render();}
    finally{ready=true;}
  }
  const recoveryRows=()=>recovery?[...recovery.snapshot.drafts,...(recovery.snapshot.stashes||[]).map(row=>({...row,stashed:true}))]:[];
  function recoveryRow(){
    const row=recoveryRows()[Number($("upgrade-recovery-task").value)||0];
    $("upgrade-recovery-text").value=row?.text||"";
    $("upgrade-recovery-files").textContent=row?.attachments?.length?row.attachments.join("\n"):"첨부 자료 없음";
  }
  function openRecovery(){
    if(!recovery)return;
    const select=$("upgrade-recovery-task");select.replaceChildren();
    recoveryRows().forEach((row,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=(row.id==="home"?"업무 홈":hooks.taskTitle?.(row.id)||"이전 업무 "+row.id.slice(0,8))+(row.stashed?" · 임시 보관":"");select.append(option);});
    select.value="0";recoveryRow();$("upgrade-recovery-message").textContent="확인 전까지 이전 초안 기록을 유지합니다.";
    if(hooks.openRecovery)hooks.openRecovery("upgrade-recovery-dialog");else $("upgrade-recovery-dialog").showModal();
  }
  async function releaseRecovery(){
    if(!recovery||releasing||!hooks.confirmRecovery)return;
    const owner=recovery;releasing=true;$("upgrade-recovery-release").disabled=true;
    try{
      if(!await hooks.confirmRecovery()||recovery!==owner)return;
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
    $("upgrade-recovery-open").onclick=openRecovery;
    $("upgrade-recovery-close").onclick=()=>$("upgrade-recovery-dialog").close();
    $("upgrade-recovery-task").onchange=recoveryRow;
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
  return {attach,bootstrap,observe,begin,cancel,isLocked:()=>locked};
})();
