"use strict";
globalThis.WorkspaceSessionImport = (() => {
  let generation=0, loading=false, selected=null;
  const dialog=$("import-dialog"), message=$("import-message"), list=$("import-sessions"), preview=$("import-preview");
  function status(text){message.textContent=text||"";}
  function busy(value){loading=value;$("import-find").disabled=value;$("import-refresh").disabled=value;$("import-apply").disabled=value||!selected||selected.workspaceAvailable===false;}
  function reset(){selected=null;preview.replaceChildren();$("import-apply").disabled=true;}
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
      status((record.warnings||[]).join(" ")||"최근 대화 미리보기입니다. 불러오기만으로 Claude를 실행하지 않습니다.");
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
  $("import-open").onclick=()=>{if(appClosed)return;reset();status("");showDialog("import-dialog");refresh();};
  $("import-close").onclick=()=>dialog.close();
  dialog.addEventListener("close",()=>{generation++;busy(false);});
  $("import-refresh").onclick=refresh;
  $("import-form").onsubmit=e=>{e.preventDefault();const id=$("import-id").value.trim();if(id)inspect(id);};
  $("import-apply").onclick=async()=>{
    if(loading||!selected||appClosed)return;const ticket=generation;const id=selected.sessionId;busy(true);
    try{
      const result=await api("/api/claude-sessions/import",{sessionId:id});
      if(ticket!==generation||appClosed)return;
      const bootResult=await api("/api/bootstrap");if(ticket!==generation||!dialog.open||appClosed)return;
      sessions=bootResult.sessions;renderSessions();dialog.close();
      await selectSession(result.session.id);
      toast(result.existing?"이미 불러온 업무를 열었어요.":"이전 대화와 원래 작업 폴더를 불러왔어요. 다른 터미널에서 같은 대화를 실행 중이라면 먼저 마쳐 주세요.");
    }catch(e){if(ticket===generation)status(e.message);}finally{if(ticket===generation)busy(false);}
  };
  return {refresh};
})();
