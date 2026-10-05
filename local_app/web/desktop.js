"use strict";

// Reuse the attention poll and its small durable inbox. Reading never approves a request.
globalThis.WorkspaceDesktop = (() => {
  let snapshot={preferences:{enabled:true,completed:true,attention:true,errors:true},inbox:[],unreadCount:0};
  let waiting=[], waitingKnown=false, changing=false, reading=false, navigation=null, filter="all", page=0;
  let dataKey="", settingsKey="", listKey="", rows=[], returnFocus=null;
  const PAGE_SIZE=30, labels={completed:"작업 완료",attention:"응답 필요",error:"확인 필요"};
  const filters=[["all","전체"],["unread","읽지 않음"],["attention","응답 필요"],["completed","완료"],["error","확인 필요"]];
  const dialog=$("desktop-dialog"), list=$("desktop-items"), tabs=$("desktop-filters"), tabButtons=new Map();
  const matches=(row,scope)=>scope==="all" || (scope==="unread"?!row.read:scope==="attention"?row.pending>0:row.kind===scope);
  const text=(node,value)=>{if(node&&node.textContent!==String(value))node.textContent=String(value);};
  function mergeRows(){
    const groups=new Map(), latest=new Map(), result=[], receipts=new Map((snapshot.inbox||[]).map(item=>[item.id,item]));
    for(const item of waiting){
      if(!item || typeof item.sessionId!=="string" || typeof item.id!=="string")continue;
      if(!groups.has(item.sessionId))groups.set(item.sessionId,{title:item.title||"업무",ids:new Set(),kinds:new Set(),receipts:new Set(),summary:""});
      const group=groups.get(item.sessionId);group.ids.add(item.id);group.kinds.add(item.kind);
      if(!group.summary&&typeof item.summary==="string")group.summary=item.summary;
      if(typeof item.notificationId==="string")group.receipts.add(item.notificationId);
    }
    // Older attention receipts for a task are superseded by its current request state.
    for(const item of snapshot.inbox||[]){
      if(!item || typeof item.id!=="string" || typeof item.sessionId!=="string")continue;
      if(item.kind==="attention"){
        if(latest.has(item.sessionId))continue;
        latest.set(item.sessionId,item);
        if(groups.has(item.sessionId))continue;
      }
      result.push({...item,pending:0,readIds:item.read?[]:[item.id],status:item.kind==="attention"?(waitingKnown?"현재 대기 없음":"응답 상태 확인 중"):labels[item.kind]||"알림"});
    }
    const pending=[];
    for(const [sessionId,group] of groups){
      const kinds=group.kinds, retained=[...group.receipts].map(id=>receipts.get(id)).filter(Boolean);
      // Exact receipt IDs keep a previous read request from hiding a later request.
      const current=group.receipts.size?retained:(latest.has(sessionId)?[latest.get(sessionId)]:[]);
      const receipt=current[0], read=group.receipts.size?retained.length===group.ids.size&&retained.every(item=>item.read):receipt?.read===true;
      const status=kinds.size>1?"승인·답변 대기":kinds.has("approval")?"승인 대기":kinds.has("question")?"답변 대기":"선택 대기";
      pending.push({...(receipt||{}),id:"pending:"+sessionId,sessionId,title:group.title,kind:"attention",
        summary:group.summary||receipt?.summary||"",
        read,readIds:current.filter(item=>!item.read).map(item=>item.id),pending:group.ids.size,status:status+(group.ids.size>1?` ${group.ids.size}건`:""),receipt:!!receipt});
    }
    return [...pending,...result];
  }
  function renderSettings(){
    const prefs=snapshot.preferences||{}, key=JSON.stringify([prefs,changing,appClosed,snapshot.warning,snapshot.nativeAvailable,snapshot.deliveryNote]);
    if(key===settingsKey)return;settingsKey=key;
    text($("desktop-toggle"),prefs.enabled?"PC 알림 끄기":"PC 알림 켜기");$("desktop-toggle").disabled=changing||appClosed;
    for(const key of ["completed","attention","errors"]){const input=$("desktop-"+key);input.checked=prefs[key]===true;input.disabled=changing||appClosed;}
    const deliveryHelp=snapshot.nativeAvailable?(boot.window?.closeBehavior==="background"?"창의 X를 누르면 트레이에서 계속 실행하며 알려드려요. ":"앱이 실행 중일 때 PC 알림을 요청합니다. "):"현재는 앱 안의 알림 목록을 사용할 수 있습니다. ";
    text($("desktop-message"),snapshot.warning||(deliveryHelp+(snapshot.deliveryNote||"")));
  }
  function renderHeader(){
    const unread=rows.filter(row=>!row.read).length, pending=rows.reduce((sum,row)=>sum+row.pending,0);
    text($("desktop-count"),unread);$("desktop-count").hidden=!unread;
    $("desktop-open").setAttribute("aria-label",`통합 알림함 · 읽지 않은 알림 ${unread}개 · 응답이 필요한 요청 ${pending}개`);
    $("desktop-open").classList.toggle("has-pending",pending>0);
  }
  async function markRead(identifier){
    if(reading||appClosed)return;reading=true;renderList();
    const ids=Array.isArray(identifier)?identifier:identifier?[identifier]:null;
    try{const result=await api("/api/notifications",{action:"read",...(ids?.length===1?{notificationId:ids[0]}:ids?{notificationIds:ids}:{})});apply(result.desktop);}
    catch(e){toast(e.message);}finally{reading=false;listKey="";renderList();}
  }
  async function openRow(row){
    if(appClosed)return;
    const pendingNow=waiting.some(item=>item.sessionId===row.sessionId);
    if(row.pending&&!pendingNow){toast("이 요청은 더 이상 응답을 기다리지 않아요. 알림 목록을 갱신했어요.");listKey="";renderList();return;}
    try{
      if(await selectSession(row.sessionId)===false)return;
      close(false);
      if(row.readIds.length)await markRead(row.readIds);
      if(pendingNow)$("requests").scrollIntoView?.({block:"nearest"});
    }catch(e){toast(e.message);}
  }
  function renderList(){
    if(!dialog.open)return;
    const visible=rows.filter(row=>matches(row,filter));page=Math.min(page,Math.max(0,Math.ceil(visible.length/PAGE_SIZE)-1));
    const key=JSON.stringify([dataKey,filter,page,reading,appClosed]);
    if(key===listKey)return;listKey=key;
    list.setAttribute("role","tabpanel");list.setAttribute("aria-labelledby","inbox-filter-"+filter);
    for(const [scope,button] of tabButtons){
      const count=rows.filter(row=>matches(row,scope)).length;
      text(button,`${filters.find(row=>row[0]===scope)[1]} ${count}`);
      button.setAttribute("aria-selected",String(scope===filter));button.tabIndex=scope===filter?0:-1;
    }
    const focused=document.activeElement?.dataset?.inboxKey, focusTargets=new Map();
    list.replaceChildren();
    const shown=visible.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
    for(const row of shown){
      const card=el("article",null,"desktop-item"+(row.read?"":" unread")+(row.pending?" needs-response":""));
      const button=el("button",null,"inbox-task");button.type="button";button.dataset.inboxKey="open:"+row.id;focusTargets.set(button.dataset.inboxKey,button);
      const meta=el("span",null,"inbox-item-meta"), status=el("span",row.status,"inbox-state");
      meta.append(status,el("span",row.read?"읽음":"읽지 않음","inbox-read-state"));
      button.append(el("strong",row.title||"업무"));
      if(row.summary)button.append(el("span",row.summary,"inbox-request-summary"));
      button.append(meta);
      if(row.createdAt)button.append(el("time",new Date(row.createdAt*1000).toLocaleString("ko-KR",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}),"inbox-time"));
      button.setAttribute("aria-label",`${row.title||"업무"}${row.summary?` · ${row.summary}`:""} · ${row.status} · ${row.read?"읽음":"읽지 않음"} · 업무 열기`);
      button.onclick=()=>openRow(row);card.append(button);
      if(row.readIds.length){
        const read=el("button","읽음","text-button inbox-read");read.type="button";read.disabled=reading||appClosed;
        read.dataset.inboxKey="read:"+row.id;focusTargets.set(read.dataset.inboxKey,read);read.setAttribute("aria-label",`${row.title||"업무"} 알림 읽음으로 표시`);
        read.onclick=()=>markRead(row.readIds);card.append(read);
      }
      list.append(card);
    }
    if(visible.length>PAGE_SIZE){
      const pagination=el("div",null,"inbox-pagination");
      const previous=el("button","이전","text-button"),next=el("button","다음","text-button");previous.type=next.type="button";
      previous.disabled=page===0;next.disabled=(page+1)*PAGE_SIZE>=visible.length;
      previous.onclick=()=>{page--;renderList();list.scrollTop=0;};next.onclick=()=>{page++;renderList();list.scrollTop=0;};
      pagination.append(previous,el("span",`${page+1} / ${Math.ceil(visible.length/PAGE_SIZE)}`),next);list.append(pagination);
    }
    $("desktop-empty").hidden=visible.length>0;
    text($("desktop-empty"),filter==="attention"?"지금 응답을 기다리는 요청이 없어요.":filter==="unread"?"읽지 않은 알림이 없어요.":"이 분류에는 아직 알림이 없어요.");
    const pending=rows.reduce((sum,row)=>sum+row.pending,0);
    text($("desktop-summary"),`${pending?`응답이 필요한 요청 ${pending}개 · `:""}읽음으로 표시해도 승인하거나 답변하지 않습니다.`);
    $("desktop-read").disabled=reading||appClosed||!(snapshot.inbox||[]).some(row=>!row.read);
    if(focused){const target=focusTargets.get(focused)||tabButtons.get(filter);target?.focus();}
  }
  function apply(value,attention){
    if(value&&typeof value==="object"&&Array.isArray(value.inbox)){
      // Read receipts only move forward. A poll started before a read reply may finish later.
      const read=new Set(snapshot.inbox.filter(item=>item.read).map(item=>item.id));
      snapshot={...snapshot,...value,inbox:value.inbox.map(item=>read.has(item.id)&&!item.read?{...item,read:true}:item)};
    }
    if(attention&&Array.isArray(attention.items)){waiting=attention.items;waitingKnown=true;}
    const key=JSON.stringify([snapshot.inbox,waiting,waitingKnown]);
    if(key!==dataKey){dataKey=key;rows=mergeRows();renderHeader();}
    renderSettings();renderList();
  }
  async function configure(change){if(changing||appClosed)return;changing=true;renderSettings();try{const result=await api("/api/notifications",{action:"configure",preferences:change});apply(result.desktop);}catch(e){toast(e.message);}finally{changing=false;renderSettings();}}
  async function follow(value){
    if(!value?.id||!value.sessionId||value.id===navigation||appClosed)return;navigation=value.id;
    try{if(sessionStorage.getItem("workspaceNavigation")===value.id)return;sessionStorage.setItem("workspaceNavigation",value.id);}catch(_){}
    try{await selectSession(value.sessionId);}catch(e){toast(e.message);}
  }
  async function presence(){if(appClosed)return;try{await api("/api/notifications",{action:"view",id:active?.id||null,visible:document.visibilityState==="visible"&&document.hasFocus?.()===true});}catch(_){}}
  function open(scope="all"){
    if(appClosed)return;
    filter=filters.some(row=>row[0]===scope)?scope:"all";page=0;listKey="";returnFocus=document.activeElement;
    showDialog("desktop-dialog");renderList();tabButtons.get(filter)?.focus();
  }
  function close(restore=true){
    const focus=returnFocus;returnFocus=null;dialog.close();
    if(restore&&focus?.isConnected&&!focus.disabled)focus.focus();
  }
  if(tabs)for(const [scope,label] of filters){
    const button=el("button",label,"inbox-filter");button.type="button";button.setAttribute("role","tab");button.setAttribute("aria-controls","desktop-items");button.id="inbox-filter-"+scope;
    button.onclick=()=>{filter=scope;page=0;renderList();};
    button.onkeydown=event=>{
      if(event.defaultPrevented||event.isComposing||event.keyCode===229||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return;
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();
      const index=filters.findIndex(row=>row[0]===scope),next=event.key==="Home"?0:event.key==="End"?filters.length-1:(index+(event.key==="ArrowRight"?1:-1)+filters.length)%filters.length;
      filter=filters[next][0];page=0;renderList();tabButtons.get(filter).focus();
    };
    tabButtons.set(scope,button);tabs.append(button);
  }
  dialog.oncancel=event=>{event.preventDefault();close();};
  dialog.onclose=()=>{list.replaceChildren();listKey="";};
  $("desktop-open").onclick=()=>open();$("desktop-close").onclick=()=>close();$("desktop-read").onclick=()=>markRead();
  $("desktop-toggle").onclick=()=>configure({enabled:!snapshot.preferences.enabled});
  for(const key of ["completed","attention","errors"])$("desktop-"+key).onchange=()=>configure({[key]:$("desktop-"+key).checked});
  return {apply,follow,presence,open,close,confirmShutdown:confirmationId=>confirmShutdownChallenge(confirmationId)};
})();
