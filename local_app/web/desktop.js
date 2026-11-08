"use strict";

// Reuse the attention poll and its small durable inbox. Reading never approves a request.
globalThis.WorkspaceDesktop = (() => {
  let snapshot={preferences:{enabled:true,completed:true,attention:true,errors:true},inbox:[],unreadCount:0};
  let waiting=[], waitingKnown=false, changing=false, reading=null, navigation=null, following=null, navigationRetry=0, filter="all", page=0;
  const readQueue=new Set();let readCurrent=new Set(), readFeedback=false, readRetry=0, observedViews=new Map(), failedNavigation=null;
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
  function markRead(identifier,{automatic=false}={}){
    if(appClosed)return Promise.resolve(false);
    // Capture the displayed snapshot. Later arrivals are not "all read".
    const ids=Array.isArray(identifier)?identifier:identifier?[identifier]:(snapshot.inbox||[]).filter(row=>!row.read).map(row=>row.id);
    if(!ids.length)return Promise.resolve(true);
    for(const id of ids)if(!readCurrent.has(id))readQueue.add(id);readFeedback ||= !automatic;
    if(reading)return reading;
    reading=Promise.resolve().then(async()=>{
      let ok=true;
      while(readQueue.size&&!appClosed){
        const unread=new Set((snapshot.inbox||[]).filter(row=>!row.read).map(row=>row.id));
        const batch=[...readQueue].filter(id=>unread.has(id)), feedback=readFeedback;
        readQueue.clear();readFeedback=false;if(!batch.length)continue;
        readCurrent=new Set(batch);
        try{
          const result=await api("/api/notifications",{action:"read",...(batch.length===1?{notificationId:batch[0]}:{notificationIds:batch})});
          apply(result.desktop);readRetry=0;
        }catch(e){ok=false;readRetry=Date.now()+5000;if(feedback)toast(e.message);}
        finally{readCurrent.clear();}
      }
      return ok;
    }).finally(()=>{reading=null;if(!readQueue.size)readFeedback=false;listKey="";renderList();});
    renderList();return reading;
  }
  function viewVisible(){
    return !appClosed&&!!active&&!pendingSelection&&document.visibilityState==="visible"&&document.hasFocus?.()===true&&
      !$("task-view").hidden&&!$("conversation").hidden&&!secondaryViewOpen()&&
      !document.querySelector("dialog[open]")&&!globalThis.WorkspaceUpgrade?.isLocked?.();
  }
  function viewed(){
    if(!viewVisible()||Date.now()<readRetry)return;
    const observed=observedViews.get(active.id);
    // Pair receipt identities with the same attention reply's transcript seq.
    // A receipt whose messages/requests are still loading remains unread.
    if(!observed||!Number.isSafeInteger(active.seq)||active.seq<observed.seq)return;
    const ids=(snapshot.inbox||[]).filter(row=>row.sessionId===active.id&&!row.read&&observed.ids.has(row.id)).map(row=>row.id);
    if(ids.length)void markRead(ids,{automatic:true});
  }
  async function openRow(row){
    if(appClosed)return;
    const pendingNow=waiting.some(item=>item.sessionId===row.sessionId);
    if(row.pending&&!pendingNow){toast("이 요청은 더 이상 응답을 기다리지 않아요. 알림 목록을 갱신했어요.");listKey="";renderList();return;}
    try{
      if(await selectSession(row.sessionId)===false)return;
      close(false);
      const generation=selectionGeneration;
      if(row.readIds.length)await markRead(row.readIds);
      if(pendingNow&&active?.id===row.sessionId&&selectionGeneration===generation&&!pendingSelection)$("requests").scrollIntoView?.({block:"nearest"});
    }catch(e){toast(e.message);}
  }
  function renderList(){
    if(!dialog.open)return;
    const visible=rows.filter(row=>matches(row,filter));page=Math.min(page,Math.max(0,Math.ceil(visible.length/PAGE_SIZE)-1));
    const key=JSON.stringify([dataKey,filter,page,!!reading,appClosed]);
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
        const read=el("button","읽음","text-button inbox-read");read.type="button";read.disabled=!!reading||appClosed;
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
    $("desktop-read").disabled=!!reading||appClosed||!(snapshot.inbox||[]).some(row=>!row.read);
    if(focused){const target=focusTargets.get(focused)||tabButtons.get(filter);target?.focus();}
  }
  function apply(value,attention){
    const accepted=value&&typeof value==="object"&&Array.isArray(value.inbox)&&
      !(Number.isSafeInteger(value.revision)&&Number.isSafeInteger(snapshot.revision)&&value.revision<snapshot.revision);
    if(accepted){
      // Read receipts only move forward. A poll started before a read reply may finish later.
      const read=new Set(snapshot.inbox.filter(item=>item.read).map(item=>item.id));
      snapshot={...snapshot,...value,inbox:value.inbox.map(item=>read.has(item.id)&&!item.read?{...item,read:true}:item)};
    }
    if(accepted&&Array.isArray(attention?.sessions)){
      const grouped=new Map();
      for(const item of value.inbox){if(!grouped.has(item.sessionId))grouped.set(item.sessionId,new Set());grouped.get(item.sessionId).add(item.id);}
      observedViews=new Map(attention.sessions.filter(row=>Number.isSafeInteger(row.seq)&&grouped.has(row.id))
        .map(row=>[row.id,{seq:row.seq,ids:grouped.get(row.id)}]));
    }
    if(attention&&Array.isArray(attention.items)){waiting=attention.items;waitingKnown=true;}
    const key=JSON.stringify([snapshot.inbox,waiting,waitingKnown]);
    if(key!==dataKey){dataKey=key;rows=mergeRows();renderHeader();}
    renderSettings();renderList();viewed();
  }
  async function configure(change){if(changing||appClosed)return;changing=true;renderSettings();try{const result=await api("/api/notifications",{action:"configure",preferences:change});apply(result.desktop);}catch(e){toast(e.message);}finally{changing=false;renderSettings();}}
  async function follow(value){
    if(!value?.id||!value.sessionId||value.id===navigation||following===value.id||
      failedNavigation?.id===value.id&&Date.now()<navigationRetry||appClosed)return;
    try{if(sessionStorage.getItem("workspaceNavigation")===value.id)return;}catch(_){}
    const consume=()=>{
      navigation=value.id;navigationRetry=0;failedNavigation=null;
      try{sessionStorage.setItem("workspaceNavigation",value.id);}catch(_){}
    };
    // User navigation after a failed lookup cancels that old OS click.
    if(failedNavigation?.id===value.id&&failedNavigation.generation!==selectionGeneration){consume();return;}
    following=value.id;
    try{
      const selected=await selectSession(value.sessionId);
      if(following!==value.id)return;
      // false is a superseding selection/home action, not a network failure.
      consume();if(selected===false)return;
    }catch(e){if(following===value.id){
      if(e.transportFailure===true||e.httpStatus>=500||[408,429].includes(e.httpStatus)){
        failedNavigation={id:value.id,generation:selectionGeneration};navigationRetry=Date.now()+5000;
      }else consume();
      toast(e.message);
    }}
    finally{if(following===value.id)following=null;}
  }
  async function presence(){if(appClosed)return;viewed();try{await api("/api/notifications",{action:"view",id:active?.id||null,visible:viewVisible()});}catch(_){}}
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
  globalThis.addEventListener?.("focus",viewed);
  document.addEventListener("visibilitychange",viewed);
  document.addEventListener("close",viewed,true);
  return {apply,follow,presence,viewed,open,close,confirmShutdown:confirmationId=>confirmShutdownChallenge(confirmationId)};
})();
