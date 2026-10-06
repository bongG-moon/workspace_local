"use strict";

globalThis.WorkspaceExecutionMode = (() => {
  const $=id=>document.getElementById(id), modes=new Set(["normal","administrator"]);
  let hooks=null, state={current:"normal",supported:false,state:"idle",target:null,error:null};
  let selected=null, pending=false, timer=null, error="";
  const label=mode=>mode==="administrator"?"관리자 권한":"일반 권한";
  const changing=()=>pending||state.state!=="idle";
  const phases={requesting:["Windows 권한 확인 중…","Windows 승인과 실행 환경을 확인하고 있어요. 승인창이 남아 있으면 먼저 확인해 주세요."],
    coordinating:["전환 연결 중…","승인된 실행 권한으로 앱 전환을 연결하고 있어요."],
    waiting_for_work:["작업 완료 대기 중…","모든 업무의 진행 중인 작업과 승인·답변·결과 확인 요청을 마치면 권한을 전환합니다. 메인 화면의 전환 안내에서 취소할 수 있어요."],
    preserving_drafts:["작성 내용 보관 중…","입력과 첨부를 보관하기 위해 설정 창을 닫고 전환을 이어갑니다."],
    restarting:["앱 다시 시작 중…","작성 중인 내용과 첨부 자료를 보관했어요. 선택한 권한으로 앱을 다시 시작합니다."]};
  function render(){
    if(!$("execution-mode-select"))return;
    const select=$("execution-mode-select"),mode=selected||state.target||state.current;
    select.value=mode;select.disabled=!state.supported||changing();
    $("execution-mode-current").textContent=`현재: ${label(state.current)}`;
    $("execution-mode-current").dataset.mode=state.current;
    $("execution-mode-apply").disabled=!state.supported||changing()||mode===state.current;
    const phase=phases[state.phase||state.state];
    $("execution-mode-apply-label").textContent=changing()?(phase?.[0]||"전환 준비 중…"):"선택한 권한으로 다시 시작";
    $("execution-mode-description").textContent=mode==="administrator"
      ?"앱과 Claude가 관리자 권한으로 실행됩니다. Claude의 도구와 명령도 같은 권한을 사용합니다. Windows 승인창이 나올 수 있어요."
      :"앱과 Claude를 일반 권한으로 실행합니다. 기존 Windows 보안 설정과 Claude 개인 설정은 변경하지 않습니다.";
    $("execution-mode-message").textContent=error||state.error||(changing()?(phase?.[1]||"현재 작업과 확인 요청을 마친 뒤, 작성 중인 내용을 보관하고 권한을 전환합니다."):!state.supported?"실행 권한 전환은 Windows 앱에서 사용할 수 있습니다.":"선택은 앱에 저장됩니다. 전환 시 모든 업무 연결을 다시 시작하며, 예약은 내용을 확인한 뒤 재개하세요.");
  }
  function observe(value){
    if(!value||!modes.has(value.current)||!["idle","requesting","waiting"].includes(value.state))return false;
    state=value;
    // Reopening settings must not block the authenticated draft capture forever.
    // Other dialogs and human approvals retain their existing handoff priority.
    if(changing()&&["preserving_drafts","restarting"].includes(state.phase)&&$("settings-dialog")?.open)$("settings-dialog").close();
    render();return true;
  }
  function schedule(){
    if(timer!==null)clearTimeout(timer);timer=null;
    if(changing()||$("settings-dialog")?.open)timer=setTimeout(read,changing()?1500:10000);
  }
  async function read(){
    if(!hooks)return;
    try{observe(await hooks.api("/api/execution-mode"));error="";render();}catch(e){error="권한 전환 상태를 확인하지 못했습니다. 기존 창의 입력은 유지합니다.";render();}
    schedule();
  }
  async function apply(){
    const target=selected||state.current;
    if(changing()||!state.supported||target===state.current)return;
    pending=true;error="";render();
    try{
      if(target==="administrator"&&!await hooks.confirm({title:"관리자 권한으로 다시 시작할까요?",message:"앱과 Claude, Claude가 실행하는 명령에 관리자 권한을 적용합니다. 현재 작업을 마친 뒤 대화와 작성 중인 입력을 보존해 다시 시작합니다. Claude의 승인 모드는 그대로 유지됩니다.",confirmLabel:"관리자 권한으로 다시 시작",danger:true}))return;
      observe(await hooks.api("/api/execution-mode",{mode:target}));selected=null;
      if(state.state!=="idle")$("settings-dialog").close();
    }catch(e){error=e.message||"권한 전환 요청을 확인하지 못했습니다.";}
    finally{pending=false;render();schedule();}
  }
  function attach(value){
    hooks=value;
    $("execution-mode-select").onchange=()=>{selected=modes.has($("execution-mode-select").value)?$("execution-mode-select").value:null;error="";render();};
    $("execution-mode-apply").onclick=apply;
  }
  return {attach,start:observe,settingsOpened:read,settingsClosed:schedule};
})();
