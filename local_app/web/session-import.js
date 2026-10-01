"use strict";
globalThis.WorkspaceSessionImport = (() => {
  let generation=0, loading=false, selected=null, returnFocus=null;
  const dialog=$("import-dialog"), message=$("import-message"), list=$("import-sessions"), preview=$("import-preview");
  function status(text){message.textContent=text||"";}
  function busy(value){loading=value;$("import-find").disabled=value;$("import-refresh").disabled=value;$("import-apply").disabled=value||!selected||selected.workspaceAvailable===false;}
  function reset(){selected=null;preview.replaceChildren();$("import-apply").disabled=true;}
  function render(){
    const shown=!!active?.imported&&!globalThis.WorkspaceCapabilities?.isOpen();
    $("imported-context").hidden=!shown;
    if(!shown)return;
    const connected=!!active.connection&&active.connection.connected!==false;
    $("imported-context-label").textContent=`이전 Claude 세션을 업무로 추가했어요 · ${String(active.sessionId||"").slice(0,8)} · ${connected?"같은 대화에서 이어가는 중":"아래 입력창에서 후속 질문을 보낼 수 있어요"}`;
    $("imported-continue").hidden=connected;
    $("imported-continue").disabled=appClosed||["starting","running","approval","question"].includes(active.state);
  }
  async function inspect(id){
    if(loading||appClosed)return; const ticket=++generation;reset();busy(true);status("이전 대화와 작업 폴더를 확인하고 있어요…");
    try{
      const record=await api(`/api/claude-sessions?sessionId=${encodeURIComponent(id.trim())}`);
      if(ticket!==generation||!dialog.open||appClosed)return;
      selected=record;preview.append(el("h3",record.title),el("p",record.workspace,"import-path"));
      const meta=el("p",`세션 ${record.sessionId}`,"muted");preview.append(meta);
      for(const row of (record.messages||[]).slice(-6)){
        const article=el("article",null,"import-message");article.append(el("strong",row.role==="user"?"나":"Claude"),el("p",String(row.text||"").slice(0,1600)));preview.append(article);
      }
      status((record.warnings||[]).join(" ")||"최근 대화 미리보기입니다. 아래 ‘업무에 추가하고 이어가기’를 누르면 같은 세션과 폴더를 업무로 등록합니다. 불러오기만으로 Claude를 실행하지 않습니다.");
    }catch(e){if(ticket===generation)status(e.message);}finally{if(ticket===generation)busy(false);}
  }
  async function refresh(){
    if(loading||appClosed)return;const ticket=++generation;reset();busy(true);status("현재 Claude 환경의 대화를 찾고 있어요…");list.replaceChildren();
    try{
      const result=await api("/api/claude-sessions");if(ticket!==generation||!dialog.open||appClosed)return;
      for(const row of result.sessions||[]){
        const button=el("button",null,"import-session");button.type="button";
        button.append(el("strong",row.title||"이전 대화"),el("small",row.workspace),el("small",row.sessionId,"muted"));
        button.onclick=()=>{ $("import-id").value=row.sessionId;inspect(row.sessionId); };list.append(button);
      }
      status((result.warnings||[]).join(" ")||((result.sessions||[]).length?"대화를 선택하거나 세션 ID로 찾을 수 있어요.":"확인할 수 있는 대화가 없습니다. 현재 Claude 계정과 설정 위치를 확인해 주세요."));
    }catch(e){if(ticket===generation)status(e.message);}finally{if(ticket===generation)busy(false);}
  }
  $("import-open").onclick=()=>{
    if(appClosed||dialog.open)return;
    const folder=$("folder-dialog");
    if(folder.open&&$("folder-form").dataset.resume==="yes")return;
    const caller=document.activeElement, owner=caller?.closest?.("dialog");
    returnFocus=folder.open||caller===$("import-open")||owner===$("palette-dialog")?$("new-chat"):caller;
    if(folder.open){folderChoiceGeneration++;folder.close();}
    reset();status("");showDialog("import-dialog");$("import-id").focus();refresh();
  };
  $("import-close").onclick=()=>dialog.close();
  dialog.addEventListener("close",()=>{
    generation++;busy(false);
    const caller=returnFocus;returnFocus=null;
    if(appClosed||!caller)return;
    const owner=caller.closest?.("dialog");
    const target=caller.isConnected&&!caller.disabled&&!caller.hidden&&(!owner||owner.open)?caller:$("new-chat");
    if(target.isConnected&&!target.disabled)target.focus({preventScroll:true});
  });
  $("import-refresh").onclick=refresh;
  $("import-form").onsubmit=e=>{e.preventDefault();const id=$("import-id").value.trim();if(id)inspect(id);};
  $("import-apply").onclick=async()=>{
    if(loading||!selected||appClosed)return;const ticket=generation;const id=selected.sessionId;busy(true);
    try{
      const result=await api("/api/claude-sessions/import",{sessionId:id});
      if(result.restored)hiddenSessionIds.delete(result.session.id);
      if(ticket!==generation||appClosed)return;
      const bootResult=await api("/api/bootstrap");if(ticket!==generation||!dialog.open||appClosed)return;
      sessions=bootResult.sessions;renderSessions();returnFocus=null;dialog.close();
      await selectSession(result.session.id);
      render();$("prompt").focus();
      toast(result.existing?"이미 불러온 업무를 열었어요. 아래 입력창에서 이어서 요청하세요.":"같은 Claude 세션을 업무에 추가했어요. 아래 입력창에서 이어서 요청하세요. 기존 터미널에서는 이 세션의 작업을 먼저 마쳐 주세요.");
    }catch(e){if(ticket===generation)status(e.message);}finally{if(ticket===generation)busy(false);}
  };
  $("imported-continue").onclick=async()=>{await globalThis.WorkspaceComposer?.prepareConnection();render();};
  return {refresh,render};
})();
