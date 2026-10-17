"use strict";

// The dedicated host supplies WebView2.ZoomFactor. Browser/monitor scale is not
// inferred: a Windows DPI change must not be shown as a user zoom gesture.
globalThis.WorkspaceZoomFeedback = (() => {
  let surface=null, value=null, timer=null, current=null;
  function hide(){
    if(timer!==null){clearTimeout(timer);timer=null;}
    if(!surface)return;
    try{if(surface.matches(":popover-open"))surface.hidePopover();}catch{}
    surface.hidden=true;
  }
  function build(){
    if(surface)return;
    surface=document.createElement("div");
    surface.id="workspace-zoom-feedback";
    surface.className="workspace-zoom-feedback";
    surface.hidden=true;
    surface.setAttribute("popover","manual");
    surface.setAttribute("role","status");
    surface.setAttribute("aria-live","polite");
    surface.setAttribute("aria-atomic","true");
    const icon=document.createElement("span");
    icon.className="zoom-feedback-icon";
    icon.setAttribute("aria-hidden","true");
    icon.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4M7.5 10.5h6M10.5 7.5v6"/></svg>';
    const label=document.createElement("span");
    label.className="zoom-feedback-label";
    label.textContent="화면 크기";
    value=document.createElement("strong");
    value.className="zoom-feedback-value";
    surface.append(icon,label,value);
    document.body.append(surface);
  }
  function update(payload){
    const factor=payload?.factor;
    if(typeof factor!=="number"||!Number.isFinite(factor)||factor<0.25||factor>5)return false;
    current=Math.round(factor*100);
    if(payload.visible!==true)return true;
    build();
    value.textContent=`${current}%`;
    // Native popover is non-modal and does not focus. Promote it above an open
    // dialog without moving the input caret or changing the dialog's lifetime.
    if(timer!==null)clearTimeout(timer);
    surface.hidden=false;
    try{
      if(surface.matches(":popover-open"))surface.hidePopover();
      surface.showPopover();
    }catch{} // Older engines still show the fixed overlay without focus changes.
    timer=setTimeout(hide,1800);
    return true;
  }
  globalThis.addEventListener("pagehide",hide);
  return {update,hide,get percent(){return current;}};
})();
