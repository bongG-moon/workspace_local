"use strict";

// Loaded before the stylesheets: the server supplies the saved preference on
// the root element, so a saved dark mode never needs a white first frame.
globalThis.WorkspaceAppearance = (() => {
  const root=document.documentElement, valid=value=>["light","dark","system"].includes(value);
  const media=globalThis.matchMedia?.("(prefers-color-scheme: dark)");
  let preference=valid(root.dataset.appearance)?root.dataset.appearance:"light", busy=false, ready=false;
  function render(){
    const dark=preference==="dark"||(preference==="system"&&media?.matches);
    root.dataset.theme=dark?"dark":"light";root.dataset.appearance=preference;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content",dark?"dark":"light");
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",dark?"#1b1e29":"#e9e7f6");
    const select=document.getElementById("appearance-select");
    if(select){select.value=preference;select.disabled=busy;}
    const toggle=document.getElementById("appearance-toggle");
    if(toggle){
      const action=dark?"밝은 모드로 전환":"어두운 모드로 전환";
      toggle.disabled=busy||!ready;
      toggle.setAttribute("aria-label",action);
      toggle.setAttribute("title",`${preference==="system"?"시스템 설정 따르는 중 · ":""}${action}`);
      toggle.setAttribute("aria-busy",String(busy));
      document.getElementById("appearance-toggle-icon")?.setAttribute("href",dark?"#i-sun":"#i-moon");
    }
  }
  media?.addEventListener?.("change",()=>{if(preference==="system")render();});
  async function change(requested){
    if(busy||!ready)return;
    const note=document.getElementById("appearance-message");
    if(!valid(requested))return render();
    busy=true;render();note.textContent="화면 모드를 적용하고 있어요.";
    try{
      const result=await api("/api/appearance",{theme:requested});
      if(!valid(result.theme))throw Error("화면 모드를 확인하지 못했어요.");
      preference=result.theme;
      note.textContent=result.nativeApplied===false?"선택을 저장했어요. 앱 창을 다시 열면 제목 표시줄에도 적용됩니다.":"선택한 화면 모드를 저장했어요.";
    }catch(e){
      note.textContent=e.message||"화면 모드를 저장하지 못했어요. 다시 시도해 주세요.";
      if(typeof toast==="function")toast(note.textContent);
    }
    finally{busy=false;render();}
  }
  function start(value){
    if(valid(value?.theme))preference=value.theme;
    ready=true;
    const select=document.getElementById("appearance-select");
    if(select)select.onchange=()=>change(select.value);
    const toggle=document.getElementById("appearance-toggle");
    if(toggle)toggle.onclick=()=>change(root.dataset.theme==="dark"?"light":"dark");
    render();
  }
  render();
  return {start, systemChanged:()=>{if(preference==="system")render();}};
})();
