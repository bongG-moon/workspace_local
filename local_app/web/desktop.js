"use strict";
globalThis.WorkspaceDesktop = (() => {
  let snapshot={preferences:{enabled:true,completed:true,attention:true,errors:true},inbox:[],unreadCount:0}, changing=false, navigation=null;
  const labels={completed:"작업 완료",attention:"응답 필요",error:"확인 필요"};
  function render(){
    const prefs=snapshot.preferences||{},list=$("desktop-items");list.replaceChildren();
    $("desktop-count").textContent=String(snapshot.unreadCount||0);$("desktop-count").hidden=!snapshot.unreadCount;
    $("desktop-open").setAttribute("aria-label",`알림 ${snapshot.unreadCount||0}개 읽지 않음`);
    $("desktop-toggle").textContent=prefs.enabled?"PC 알림 끄기":"PC 알림 켜기";$("desktop-toggle").disabled=changing||appClosed;
    for(const key of ["completed","attention","errors"]){const input=$("desktop-"+key);input.checked=prefs[key]===true;input.disabled=changing||appClosed;}
    $("desktop-message").textContent=snapshot.warning||((snapshot.nativeAvailable?"창을 닫아도 PC 알림을 요청합니다. ":"현재는 앱 안의 알림 목록을 사용할 수 있습니다. ")+(snapshot.deliveryNote||""));
    for(const item of snapshot.inbox||[]){
      const button=el("button",null,"desktop-item"+(item.read?"":" unread"));button.type="button";
      button.append(el("strong",item.title),el("span",labels[item.kind]||"알림"),el("small",new Date(item.createdAt*1000).toLocaleString("ko-KR")));
      button.onclick=async()=>{try{const selected=await selectSession(item.sessionId);if(selected===false)return;$("desktop-dialog").close();const result=await api("/api/notifications",{action:"read",notificationId:item.id});apply(result.desktop);}catch(e){toast(e.message);}};
      list.append(button);
    }
    $("desktop-empty").hidden=!!snapshot.inbox?.length;
  }
  function apply(value){if(value&&typeof value==="object"&&Array.isArray(value.inbox)){snapshot={...snapshot,...value};render();}}
  async function configure(change){if(changing||appClosed)return;changing=true;render();try{const result=await api("/api/notifications",{action:"configure",preferences:change});apply(result.desktop);}catch(e){toast(e.message);}finally{changing=false;render();}}
  async function follow(value){
    if(!value?.id||!value.sessionId||value.id===navigation||appClosed)return;
    navigation=value.id;
    try {if(sessionStorage.getItem("workspaceNavigation")===value.id)return;sessionStorage.setItem("workspaceNavigation",value.id);}catch(_){}
    try{await selectSession(value.sessionId);}catch(e){toast(e.message);}
  }
  async function presence(){
    if(appClosed)return;
    try{await api("/api/notifications",{action:"view",id:active?.id||null,visible:document.visibilityState==="visible"&&document.hasFocus?.()===true});}catch(_){}
  }
  $("desktop-open").onclick=()=>{render();showDialog("desktop-dialog");};$("desktop-close").onclick=()=>$("desktop-dialog").close();
  $("desktop-read").onclick=async()=>{try{const result=await api("/api/notifications",{action:"read"});apply(result.desktop);}catch(e){toast(e.message);}};
  $("desktop-toggle").onclick=()=>configure({enabled:!snapshot.preferences.enabled});
  for(const key of ["completed","attention","errors"])$("desktop-"+key).onchange=()=>configure({[key]:$("desktop-"+key).checked});
  return {apply,follow,presence};
})();
