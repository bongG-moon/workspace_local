"use strict";

// Local registration controls only. The server owns the bounded, version-event
// request; ordinary UI activity never sends installation data to the web.
globalThis.WorkspaceRegistration = (() => {
  const $=id=>document.getElementById(id);
  const notice="설치 또는 업데이트가 완료되면 결과를 한 번 전송합니다.\n이 집계 기능은 평소 앱 사용 중 사용 정보를 수집하지 않습니다.";
  const pythonWhitespace="[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
  const trim=value=>String(value).replace(new RegExp("^"+pythonWhitespace+"+|"+pythonWhitespace+"+$","g"),"");
  let hooks=null,started=false,stopped=false,bound=false,saving=false,dismissed=false,offerQueued=false;
  let snapshot={enabled:false,employeeRegistered:false,needsEmployee:false};
  let mode="first",returnFocus=null,revision=0;
  const required=()=>snapshot.enabled&&!snapshot.employeeRegistered;
  const startupPending=()=>started&&!stopped&&!dismissed&&required()&&snapshot.needsEmployee;
  function normalize(value){
    return {enabled:value?.enabled===true,employeeRegistered:value?.employeeRegistered===true,
      needsEmployee:value?.needsEmployee===true};
  }
  function valid(input){const size=Array.from(trim(input?.value||"")).length;return size>0&&size<=8;}
  function validate(prefix,showEmpty=false){
    const input=$(prefix+"-input"),size=Array.from(trim(input?.value||"")).length;
    const message=size>8?"앞뒤 공백을 제외하고 8자 이내로 입력해 주세요.":showEmpty&&!size?"사번을 입력해 주세요.":"";
    $(prefix+"-count").textContent=`현재 ${size}자`;
    $(prefix+"-error").textContent=message;
    input.setAttribute("aria-invalid",String(!!message));
    if(prefix==="registration")$("registration-save").disabled=saving||!valid(input);
    return valid(input);
  }
  function render(){
    $("registration-settings").hidden=!snapshot.enabled;
    $("registration-settings-open").textContent=snapshot.employeeRegistered?"사번 변경":"사번 등록";
    $("registration-settings-open").disabled=saving||stopped;
    $("registration-save").textContent=saving?"저장 중…":mode==="first"?"저장하고 시작":"저장";
    $("registration-input").disabled=saving;
    $("registration-close").disabled=$("registration-later").disabled=saving;
    $("app-update-registration").hidden=!required();
    $("app-update-registration-input").disabled=saving;
    validate("registration");
    hooks?.changed?.();
  }
  function observe(value){snapshot=normalize(value);render();contextChanged();}
  function close(){if(!saving)$("registration-dialog").close();}
  function open(kind){
    if(!started||stopped||saving||!snapshot.enabled||hooks?.isClosed?.())return false;
    const dialog=$("registration-dialog");
    // Update registration lives inside its existing dialog, never another layer.
    if($("app-update-dialog").open)return false;
    if(kind==="first"&&document.querySelector("dialog[open]"))return false;
    mode=kind;returnFocus=document.activeElement;
    $("registration-title").textContent=kind==="first"?"사번을 등록해 주세요":snapshot.employeeRegistered?"사번 변경":"사번 등록";
    $("registration-description").textContent=kind==="first"
      ?"설치 현황 확인을 위해 사번을 처음 한 번만 입력해 주세요."
      :"다음 설치 및 업데이트 집계에 사용할 사번을 입력해 주세요.";
    $("registration-later").textContent=kind==="first"?"나중에":"취소";
    $("registration-input").value="";validate("registration");render();
    if(!dialog.open)hooks.openDialog("registration-dialog");
    $("registration-input").focus();return true;
  }
  function offer(){
    if(!startupPending()||document.hidden===true||hooks?.isClosed?.()||hooks?.canPrompt?.()===false)return;
    open("first");
  }
  function contextChanged(){
    if(!startupPending()||offerQueued)return;
    offerQueued=true;
    // A local close event may still be restoring the previous dialog's focus.
    Promise.resolve().then(()=>{offerQueued=false;offer();});
  }
  async function save(prefix){
    if(saving||stopped||!snapshot.enabled||hooks?.isClosed?.())return false;
    if(!validate(prefix,true)){$(prefix+"-input").focus();return false;}
    const employeeId=trim($(prefix+"-input").value),request=++revision;
    saving=true;render();
    try{
      const result=await hooks.api("/api/app-registration",{employeeId});
      if(stopped||request!==revision)return false;
      const next=normalize(result);
      if(!next.enabled||!next.employeeRegistered)throw new Error("사번을 저장하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.");
      snapshot=next;
      $(prefix+"-input").value="";
      dismissed=true;
      if(prefix==="registration"&&$("registration-dialog").open)$("registration-dialog").close();
      return true;
    }catch(error){
      if(!stopped&&request===revision)$(prefix+"-error").textContent=error.message||"사번을 저장하지 못했어요. 다시 시도해 주세요.";
      return false;
    }finally{
      if(request===revision){saving=false;if(!stopped){
        // Keep the server error next to the untouched input until it changes.
        const message=$(prefix+"-error").textContent;
        render();$(prefix+"-error").textContent=message;
      }}
    }
  }
  async function beforeUpdate(){
    if(stopped||saving)return false;
    if(!required())return true;
    return save("app-update-registration");
  }
  function bind(){
    for(const prefix of ["registration","app-update-registration"]){
      $(prefix+"-input").addEventListener("input",()=>validate(prefix));
    }
    $("registration-form").addEventListener("submit",event=>{event.preventDefault();void save("registration");});
    $("registration-close").onclick=$("registration-later").onclick=close;
    $("registration-dialog").oncancel=event=>{event.preventDefault();close();};
    $("registration-dialog").onclose=()=>{
      dismissed=true;$("registration-input").value="";
      const target=returnFocus,owner=target?.closest?.("dialog");returnFocus=null;
      if(!stopped&&target?.isConnected&&!target.disabled&&!target.hidden&&(!owner||owner.open))target.focus();
      hooks?.changed?.();
    };
    $("registration-settings-open").onclick=()=>open("settings");
    $("registration-notice").textContent=$("app-update-registration-notice").textContent=notice;
    document.addEventListener("close",contextChanged,true);
    document.addEventListener("focusin",contextChanged);
    document.addEventListener("visibilitychange",contextChanged);
  }
  function start(value){if(started||!hooks)return;started=true;observe(value);offer();}
  function stop(){
    stopped=true;revision++;saving=false;
    $("registration-input").value=$("app-update-registration-input").value="";
    if($("registration-dialog").open)$("registration-dialog").close();
    document.removeEventListener?.("close",contextChanged,true);
    document.removeEventListener?.("focusin",contextChanged);
    document.removeEventListener?.("visibilitychange",contextChanged);
  }
  return {attach:value=>{hooks=value;if(!bound){bind();bound=true;}},start,observe,beforeUpdate,
    hasStartupPrompt:startupPending,isSaving:()=>saving,contextChanged,stop};
})();
