"use strict";

// Interactive CLI gestures that can be represented by the GUI's real actions.
// A terminal-only action never silently becomes a different destructive action.
globalThis.WorkspaceShortcuts = (() => {
  const input=$("prompt"), stashes=new Map();
  let prefix=null, searchContext=null, matches=[], selected=0, composing=false, searchComposing=false, stopPending=null;
  const key=event=>String(event.key).toLowerCase();
  const id=()=>active?.id||"home";
  const snapshot=()=>({text:input.value,attachments:[...attachments],selectionStart:input.selectionStart??input.value.length,selectionEnd:input.selectionEnd??input.value.length});
  const history=()=>((active?.messages||[]).filter(row=>row.role==="user"&&typeof row.text==="string").slice(-100));
  const editable=()=>!appClosed&&!input.readOnly&&!sending&&!choiceSubmission&&!globalThis.WorkspaceAttachments?.isUploading();
  const clean=event=>!event.defaultPrevented&&!event.isComposing&&event.keyCode!==229&&!composing;
  const consume=event=>{event.preventDefault();return true;};
  function restore(draft){
    input.value=draft.text;attachments=[...(draft.attachments||draft.files||[])];
    input.setSelectionRange?.(draft.selectionStart??input.value.length,draft.selectionEnd??input.value.length);renderAttachments();saveDraft();
    globalThis.WorkspaceInputKeys?.reset();globalThis.WorkspaceComposer?.close();input.focus();
  }
  function edited(){saveDraft();globalThis.WorkspaceComposer?.refresh();}
  function newline(event){
    consume(event);if(!editable())return true;
    const start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
    if(!document.execCommand?.("insertText",false,"\n")){
      if(input.setRangeText)input.setRangeText("\n",start,end,"end");
      else{input.value=input.value.slice(0,start)+"\n"+input.value.slice(end);input.setSelectionRange?.(start+1,start+1);}
    }
    edited();return true;
  }
  async function stop(){
    if(globalThis.WorkspaceStop?.request)return WorkspaceStop.request();
    if(!active||!busyStates.has(active.state)||stopPending||appClosed||globalThis.WorkspaceConnectionRestart?.isCurrent())return;
    const owner=active.id;stopPending=owner;
    try{await api("/api/stop",{id:owner});if(active?.id===owner){toast("중지 요청을 보냈어요. 완료된 파일 변경은 유지됩니다.");void refreshResults();}}
    catch(error){if(active?.id===owner)toast(error.message);}
    finally{if(stopPending===owner)stopPending=null;}
  }
  function sendNow(){
    if(busyStates.has(active?.state)){
      toast("현재 연결은 Claude 터미널의 즉시 전송을 지원하지 않아요. 입력은 유지했어요. Enter로 대기열에 넣거나 ‘지금 반영’의 중지·재개 방식을 선택할 수 있습니다.");
      return;
    }
    return submit();
  }
  function queue(){
    if(!editable())return;
    if(globalThis.WorkspaceStop?.blocked()){saveDraft();return toast("현재 작업의 중지 상태를 확인한 뒤 다시 보내 주세요. 작성한 요청은 유지됩니다.");}
    if(!active)return chooseFolder();
    if(!globalThis.WorkspaceWorkflow?.send)return toast("대기열 기능을 불러오지 못했어요. 입력을 보관한 뒤 화면을 다시 열어 주세요.");
    return globalThis.WorkspaceWorkflow?.send("enqueue");
  }
  function priority(event){
    if(!clean(event)||!editable()){prefix=null;return false;}
    if(!prefix)return false;
    const valid=prefix.id===id()&&Date.now()-prefix.at<3000;prefix=null;
    if(!valid)return false;
    if(event.key==="Escape")return consume(event);
    if(event.key==="Enter"&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.shiftKey){consume(event);if(!event.repeat)void queue();return true;}
    if(key(event)==="s"&&event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.shiftKey){consume(event);if(!event.repeat)void sendNow();return true;}
    return false;
  }
  function stash(){
    const current=snapshot(),previous=stashes.get(id());
    if(previous&&!current.text&&!current.attachments.length){
      stashes.delete(id());restore(previous);toast("보관한 입력과 첨부를 복원했어요.");return;
    }
    if(!current.text&&!current.attachments.length)return;
    const size=JSON.stringify(current).length;
    if((!previous&&stashes.size>=50)||size+[...stashes.entries()].reduce((sum,[owner,row])=>sum+(owner===id()?0:JSON.stringify(row).length),0)>200000){toast("임시 보관 공간이 찼어요. 기존 입력을 복원한 뒤 사용해 주세요.");return;}
    stashes.set(id(),current);restore({text:"",attachments:[]});toast("입력과 첨부를 잠시 보관했어요. Ctrl+S로 다시 가져올 수 있습니다.");
  }
  function afterSend(owner){
    const draft=stashes.get(owner);
    if(!draft)return;
    if(owner===id()&&!input.value&&!attachments.length){stashes.delete(owner);restore(draft);}
    else if(owner!==id()&&!drafts.get(owner)?.text&&!drafts.get(owner)?.attachments?.length){stashes.delete(owner);drafts.set(owner,draft);}
  }
  const exportStashes=()=>[...stashes].map(([id,draft])=>({id,...draft}));
  function restoreStashes(rows=[],{includeArchived=false}={}){
    const conflicts=[];
    for(const row of rows){const {id:owner,...draft}=row,previous=stashes.get(owner);if(!includeArchived&&(owner!=="home"&&!sessions.some(item=>item.id===owner)||globalThis.WorkspaceDraftPersistence?.allowStashRestore(owner)===false)||previous&&JSON.stringify(previous)!==JSON.stringify(draft))conflicts.push(owner);else {stashes.set(owner,draft);globalThis.WorkspaceDraftPersistence?.changed(owner);}}
    return conflicts;
  }
  function selectedHistory(){
    if(!searchContext||searchContext.id!==id()||searchContext.generation!==selectionGeneration||!editable())return;
    const row=matches[selected];if(!row)return;
    // Copy for editing, never submit or replay a previous approval.
    restore(row);$("input-history-dialog").close();
  }
  function renderHistory(){
    const query=$("input-history-search").value.toLocaleLowerCase();
    matches=history().slice().reverse().filter(row=>row.text.toLocaleLowerCase().includes(query)).slice(0,40);
    selected=Math.max(0,Math.min(selected,matches.length-1));const list=$("input-history-results");list.replaceChildren();
    matches.forEach((row,index)=>{const button=el("button",row.text.slice(0,240),"input-history-row");button.type="button";button.classList.toggle("selected",index===selected);button.setAttribute("role","option");button.setAttribute("aria-selected",String(index===selected));button.id="input-history-option-"+index;button.onclick=()=>{selected=index;selectedHistory();};list.append(button);});
    $("input-history-search").setAttribute("aria-activedescendant",matches.length?"input-history-option-"+selected:"");
    $("input-history-empty").hidden=matches.length>0;
    list.children[selected]?.scrollIntoView?.({block:"nearest"});
  }
  function search(){
    searchContext={id:id(),generation:selectionGeneration};selected=0;$("input-history-search").value="";renderHistory();
    showDialog("input-history-dialog");$("input-history-search").focus();
  }
  function details(){
    const rows=[...$("conversation").querySelectorAll(".tool-activity-detail,.execution-detail")];
    if(!rows.length){toast("이 업무에서 받은 도구 기록이 아직 없어요.");return;}
    const open=rows.some(row=>!row.open);for(const row of rows){row.open=open;row.ontoggle?.();}
  }
  function keydown(event){
    if(!clean(event)||document.activeElement!==input||!editable())return false;
    const plain=!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.shiftKey;
    const ctrl=event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.shiftKey;
    const alt=event.altKey&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey;
    if((ctrl&&key(event)==="j")||(alt&&event.key==="Enter"))return newline(event);
    if(ctrl&&key(event)==="x"&&input.selectionStart===input.selectionEnd){consume(event);if(!event.repeat)prefix={id:id(),at:Date.now()};return true;}
    if(event.key==="Enter"&&(ctrl||event.metaKey&&!event.ctrlKey&&!event.altKey&&!event.shiftKey)){consume(event);if(!event.repeat)void sendNow();return true;}
    if(plain&&event.key==="Escape"){if(busyStates.has(active?.state)){consume(event);if(!event.repeat)void stop();return true;}return false;}
    if(ctrl&&key(event)==="c"){
      if(input.selectionStart!==input.selectionEnd||globalThis.getSelection?.()?.toString())return false;
      consume(event);if(event.repeat)return true;
      if(busyStates.has(active?.state))void stop();else restore({text:"",attachments:[]});return true;
    }
    if(alt&&["m","p"].includes(key(event))){
      consume(event);if(event.repeat)return true;
      if(!active)return toast("업무를 선택한 뒤 설정을 바꿀 수 있어요."),true;
      if(key(event)==="m"?permissionConnectionLocked():connectionLocked())return toast(key(event)==="m"?"현재 연결에서 승인 모드를 바꿀 수 없어요. 연결 또는 중지 상태를 확인해 주세요.":"현재 요청이 끝난 뒤 모델을 바꿔 주세요."),true;
      if(key(event)==="m")void globalThis.WorkspaceInlineControls?.cyclePermission();else void globalThis.WorkspaceInlineControls?.open("model");return true;
    }
    if(ctrl&&["r","s","o"].includes(key(event))){consume(event);if(!event.repeat)({r:search,s:stash,o:details})[key(event)]();return true;}
    if((ctrl&&["b","d","g","t","l"].includes(key(event)))||(alt&&["t","o"].includes(key(event)))){
      consume(event);if(!event.repeat)toast("이 키의 터미널 전용 기능은 원본 Claude Code에서 사용할 수 있어요. 입력과 현재 작업은 그대로 유지합니다.");return true;
    }
    return false;
  }
  input.addEventListener?.("compositionstart",()=>{composing=true;prefix=null;});
  input.addEventListener?.("compositionend",()=>{composing=false;});
  input.addEventListener?.("blur",()=>{prefix=null;});
  const searchInput=$("input-history-search");
  searchInput.addEventListener?.("compositionstart",()=>{searchComposing=true;});
  searchInput.addEventListener?.("compositionend",()=>{searchComposing=false;});
  searchInput.oninput=()=>{selected=0;renderHistory();};
  searchInput.onkeydown=event=>{
    if(!clean(event)||searchComposing)return;
    if(event.key==="Escape"){consume(event);$("input-history-dialog").close();return;}
    if(event.key==="Enter"&&!event.shiftKey&&!event.altKey&&!event.ctrlKey&&!event.metaKey){consume(event);selectedHistory();return;}
    if(["ArrowUp","ArrowDown"].includes(event.key)||(event.ctrlKey&&key(event)==="r")){
      consume(event);if(matches.length){selected=(selected+(event.key==="ArrowUp"?-1:1)+matches.length)%matches.length;renderHistory();}
    }
  };
  $("input-history-close").onclick=()=>$("input-history-dialog").close();
  $("input-history-dialog").onclose=()=>{searchContext=null;matches=[];input.focus();};
  globalThis.WorkspaceInputKeys?.attach({input,context:()=>({id:id(),history:history()}),capture:snapshot,restore,edited});
  return {keydown,priority,stop,search,afterSend,exportStashes,restoreStashes,hasStashes:()=>stashes.size>0};
})();
