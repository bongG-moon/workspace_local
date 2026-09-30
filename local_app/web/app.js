"use strict";
const $ = id => document.getElementById(id);
const key = new URLSearchParams(location.hash.slice(1)).get("token");
if (key) { sessionStorage.setItem("workspaceToken", key); history.replaceState(null, "", "/"); }
const token = sessionStorage.getItem("workspaceToken") || "";
let active = null, sessions = [], attachments = [], pollController = null, boot = {}, started = null, previewPath = null;
let selectionGeneration = 0, sending = false, appClosed = false, quitting = false;
let managedRootChoice = null, folderChoiceGeneration = 0;
let previewContext = null, previewGeneration = 0;
let attachmentPicking = false, pathInputContext = null;
let choiceView = null, choiceSubmission = null, modelChanging = false, permissionChanging = false, effortChanging = false, connectionPreparing = false;
const answeredChoices = new Set(), renderedQueuedRequests = new Set();
const drafts = new Map(), streaming = new Map();
const modalStack = [];
let pendingConfirmation = null;
const busyStates = new Set(["starting", "running", "approval", "question"]);
const statusLabels = {idle:"준비됐어요. 원하는 일을 알려 주세요", starting:"기존 업무 환경에 연결하고 있어요", running:"업무를 진행하고 있어요", approval:"실행 전 확인이 필요해요", question:"다음 단계에 필요한 답변을 기다려요", done:"요청을 마쳤어요. 결과를 확인하거나 이어서 요청하세요", error:"잠시 멈췄어요. 연결 상태를 확인해 주세요", stopped:"작업을 멈췄어요. 이미 변경된 파일은 유지됩니다"};
const stateNames = {idle:"대기", starting:"준비 중", running:"진행 중", approval:"승인 대기", question:"답변 대기", done:"응답 완료", error:"확인 필요", stopped:"중지"};
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function basename(path=""){return String(path).split(/[\\/]/).filter(Boolean).pop()||path;}
function showDialog(id){const dialog=$(id);for(let i=modalStack.length-1;i>=0;i--)if(!modalStack[i].open||modalStack[i]===dialog)modalStack.splice(i,1);dialog.showModal();modalStack.push(dialog);}
function toast(text){
  const notice=$("toast"),dialog=[...modalStack].reverse().find(item=>item.open);
  notice.textContent=text;notice.hidden=false;notice.classList.toggle("in-dialog",!!dialog);
  if(dialog){dialog.prepend(notice);notice.scrollIntoView({block:"nearest"});}else document.body.append(notice);
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>notice.hidden=true,6500);
}
function confirmAction({title,message,confirmLabel="확인",danger=false,returnFocus=document.activeElement}){
  // A second click cannot reuse an approval for a different action.
  if(pendingConfirmation)return Promise.resolve(false);
  const dialog=$("action-dialog");
  $("action-title").textContent=title;$("action-message").textContent=message;
  $("action-confirm").textContent=confirmLabel;$("action-confirm").classList.toggle("danger-button",danger);
  dialog.returnValue="";
  pendingConfirmation=new Promise(resolve=>{
    dialog.oncancel=event=>{event.preventDefault();dialog.close("cancel");};
    dialog.onclose=()=>{
      const accepted=dialog.returnValue==="confirm";pendingConfirmation=null;dialog.onclose=dialog.oncancel=null;
      const restoreFocus=()=>{const owner=returnFocus?.closest?.("dialog");if(returnFocus?.isConnected&&!returnFocus.disabled&&(!owner||owner.open))returnFocus.focus();};
      restoreFocus();resolve(accepted);setTimeout(restoreFocus,0);
    };
    $("action-cancel").onclick=$("action-close").onclick=()=>dialog.close("cancel");
    $("action-confirm").onclick=()=>dialog.close("confirm");
    showDialog("action-dialog");$("action-cancel").focus();
  });
  return pendingConfirmation;
}
function error(text){$("error-banner").textContent=text||"";$("error-banner").hidden=!text;$("recovery-actions").hidden=!text||appClosed;}
async function api(path,data,signal){
  const options={headers:{Authorization:`Bearer ${token}`},signal};
  if(data!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(data);}
  let response;try{response=await fetch(path,options);}catch(e){if(e.name==="AbortError")throw e;throw new Error("앱 연결을 확인할 수 없어요. ‘연결 다시 확인’을 눌러 주세요. 요청을 자동으로 다시 보내지는 않습니다.");}
  const value=await response.json();if(!response.ok)throw new Error(value.error||"연결을 확인해 주세요.");return value;
}
function setStatus(state,label){
  if(active)active.state=state;globalThis.WorkspaceSessionImport?.render();const busy=busyStates.has(state),running=state==="starting"||state==="running";
  globalThis.WorkspaceProductivityActions?.update();
  $("status-text").textContent=label||statusLabels[state]||"진행 상태를 확인하고 있어요";$("status").classList.toggle("busy",running);$("status").dataset.state=state;
  const choosing=!!choiceSubmission&&choiceSubmission.sessionId===active?.id,dispatching=!!globalThis.WorkspaceWorkflow?.isSubmitting();
  $("send").hidden=busy;$("send").disabled=busy||!!globalThis.WorkspaceWorkflow?.isSubmitting()||!!globalThis.WorkspaceAttachments?.isUploading()||sending||!!choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||!!boot.error||appClosed;$("stop").hidden=!busy;$("task-title").disabled=!active||busy;$("task-pin").disabled=!active;
  $("prompt").readOnly=sending||choosing||dispatching||appClosed;$("attach").disabled=sending||choosing||attachmentPicking||appClosed;$("attach-path").disabled=sending||choosing||appClosed;
  if(sending||choosing||appClosed)globalThis.WorkspaceComposer?.close();
  if(running&&!started)started=Date.now();if(!running)started=null;if(active){const row=sessions.find(s=>s.id===active.id);if(row)row.state=state;renderSessions();}updateModelControls();updatePermissionControls();renderWorkspaceChoice();renderVerification();globalThis.WorkspaceInlineControls?.render();globalThis.WorkspaceWorkflow?.render();
}
setInterval(()=>{$("elapsed").textContent=["question","approval"].includes(active?.state)?"응답 대기":started?`${Math.floor((Date.now()-started)/1000)}초`:"";},1000);
function when(ts){if(!ts)return "";const d=new Date(ts*1000);return d.toDateString()===new Date().toDateString()?d.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"}):d.toLocaleDateString("ko-KR",{month:"short",day:"numeric"});}
let sessionDragId=null, sessionOrderSaving=false, sessionPointerDrag=null;
function orderedSessions(){
  const order=boot.sessionOrder||{},ranks=new Map((order.ids||[]).map((id,index)=>[id,index]));
  return [...sessions].sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||
    (order.manual?(Number(ranks.has(a.id))-Number(ranks.has(b.id))||
      (ranks.has(a.id)?ranks.get(a.id)-ranks.get(b.id):(b.created||0)-(a.created||0))):
      (b.updated||b.created||0)-(a.updated||a.created||0))||a.id.localeCompare(b.id));
}
function clearSessionDrop(){for(const id of ["sessions","home-recents","all-sessions"])for(const row of $(id).children){row.classList.remove("drop-before","drop-after");row.classList.toggle("dragging",!!sessionPointerDrag?.active&&row.dataset.sessionId===sessionDragId);}}
function focusSessionHandle(id,container){const row=[...$(container).children].find(row=>row.dataset.sessionId===id);row?.querySelector(".session-drag")?.focus();row?.scrollIntoView({block:"nearest"});}
function sessionDragArea(container){
  const rect=container.getBoundingClientRect(),area={left:Math.max(0,rect.left),top:Math.max(0,rect.top),right:Math.min(globalThis.innerWidth||Infinity,rect.right),bottom:Math.min(globalThis.innerHeight||Infinity,rect.bottom)};
  for(let node=container;node;node=node.parentElement){const style=globalThis.getComputedStyle?.(node)||{},bounds=node.getBoundingClientRect();
    if(/auto|scroll|hidden|clip/.test(style.overflowX||"")){area.left=Math.max(area.left,bounds.left);area.right=Math.min(area.right,bounds.right);}
    if(/auto|scroll|hidden|clip/.test(style.overflowY||"")){area.top=Math.max(area.top,bounds.top);area.bottom=Math.min(area.bottom,bounds.bottom);}
  }return area;
}
function sessionDragTarget(drag){
  const container=$(drag.container),area=sessionDragArea(container),{x,y}=drag;
  if(x<area.left||x>area.right||y<area.top||y>area.bottom||area.right<=area.left||area.bottom<=area.top)return null;
  const hit=document.elementFromPoint(x,y);if(!hit||!container.contains(hit))return null;
  let row=hit.closest(".session-row");
  if(!row||row.parentElement!==container){
    // Small gaps between cards still expose the nearest visible insertion edge.
    row=[...container.children].filter(candidate=>candidate.dataset.sessionId).map(candidate=>{
      const bounds=candidate.getBoundingClientRect();return {candidate,distance:y<bounds.top?bounds.top-y:y>bounds.bottom?y-bounds.bottom:0,bounds};
    }).filter(value=>value.bounds.bottom>area.top&&value.bounds.top<area.bottom&&value.distance<=12)
      .sort((a,b)=>a.distance-b.distance)[0]?.candidate;
  }
  const source=sessions.find(item=>item.id===drag.id),target=sessions.find(item=>item.id===row?.dataset.sessionId);
  if(!source||!target||source.id===target.id||!!source.pinned!==!!target.pinned)return null;
  const bounds=row.getBoundingClientRect();return {row,id:target.id,position:y<bounds.top+bounds.height/2?"before":"after"};
}
function paintSessionDrag(drag){clearSessionDrop();drag.target=sessionDragTarget(drag);drag.target?.row.classList.add("drop-"+drag.target.position);}
function scrollSessionDrag(drag){
  if(sessionPointerDrag!==drag||!drag.active)return;drag.frame=null;
  if(!$(drag.container).contains(document.elementFromPoint(drag.x,drag.y)))return;
  const area=sessionDragArea($(drag.container));if(drag.x<area.left||drag.x>area.right||drag.y<area.top||drag.y>area.bottom)return;
  const edge=Math.min(36,(area.bottom-area.top)/4),step=drag.y<area.top+edge?-12:drag.y>area.bottom-edge?12:0;if(!step)return;
  for(let node=$(drag.container);node;node=node.parentElement){const style=globalThis.getComputedStyle?.(node)||{};
    if(!/auto|scroll/.test(style.overflowY||"")||node.scrollHeight<=node.clientHeight+1)continue;
    const before=node.scrollTop;node.scrollTop=Math.max(0,Math.min(node.scrollHeight-node.clientHeight,before+step));
    if(node.scrollTop!==before){paintSessionDrag(drag);drag.frame=requestAnimationFrame(()=>scrollSessionDrag(drag));return;}
  }
}
function finishSessionDrag(event,cancel=false){
  const drag=sessionPointerDrag;if(!drag||(event?.pointerId!==undefined&&event.pointerId!==drag.pointerId))return;
  if(drag.active)event?.preventDefault?.();
  if(Number.isFinite(event?.clientX)&&Number.isFinite(event?.clientY)){drag.x=event.clientX;drag.y=event.clientY;}
  const target=!cancel&&drag.active?sessionDragTarget(drag):null;
  sessionPointerDrag=null;sessionDragId=null;if(drag.frame!==null)globalThis.cancelAnimationFrame?.(drag.frame);
  globalThis.removeEventListener?.("blur",drag.cancel);
  try{if(drag.handle.hasPointerCapture(drag.pointerId))drag.handle.releasePointerCapture(drag.pointerId);}catch(_){}
  clearSessionDrop();renderSessions();
  if(target)return moveSession(drag.id,target.id,target.position,drag.container);
  focusSessionHandle(drag.id,drag.container);
}
function startSessionDrag(event,item,handle,container){
  if(handle.disabled||sessionOrderSaving||appClosed||sessionPointerDrag||event.isPrimary===false||event.button!==0)return;
  const drag={id:item.id,handle,container,pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,x:event.clientX,y:event.clientY,active:false,target:null,frame:null};
  drag.cancel=()=>finishSessionDrag(null,true);sessionPointerDrag=drag;sessionDragId=item.id;
  try{handle.setPointerCapture(event.pointerId);}catch(_){sessionPointerDrag=null;sessionDragId=null;return;}
  handle.focus({preventScroll:true});globalThis.addEventListener?.("blur",drag.cancel);
}
function moveSessionDrag(event){
  const drag=sessionPointerDrag;if(!drag||event.pointerId!==drag.pointerId)return;
  drag.x=event.clientX;drag.y=event.clientY;
  if(!drag.active&&Math.hypot(drag.x-drag.startX,drag.y-drag.startY)<6)return;
  drag.active=true;event.preventDefault();paintSessionDrag(drag);
  if(drag.frame===null)drag.frame=requestAnimationFrame(()=>scrollSessionDrag(drag));
}
async function moveSession(id,targetId,position,container="sessions"){
  if(sessionOrderSaving||appClosed||boot.sessionOrder?.warning)return false;
  const rows=orderedSessions(),source=rows.find(row=>row.id===id),target=rows.find(row=>row.id===targetId);
  if(!source||!target||id===targetId||!!source.pinned!==!!target.pinned)return false;
  const previous=boot.sessionOrder,ids=rows.filter(row=>row.id!==id).map(row=>row.id);
  ids.splice(ids.indexOf(targetId)+(position==="after"?1:0),0,id);
  sessionOrderSaving=true;boot.sessionOrder={manual:true,ids,warning:null};renderSessions();
  try{const result=await api("/api/session/reorder",{id,targetId,position});boot.sessionOrder=result.sessionOrder;toast("업무 순서를 저장했어요.");return true;}
  catch(e){boot.sessionOrder=previous;toast(e.message);return false;}
  finally{sessionOrderSaving=false;renderSessions();focusSessionHandle(id,container);}
}
function sessionRow(item,button,container){
  const row=el("div",null,"session-row"+(container==="home-recents"?" recent-row":""));row.dataset.sessionId=item.id;
  const handle=el("button",null,"session-drag"),grip=el("span",null,"session-drag-grip");
  handle.type="button";handle.draggable=false;handle.disabled=sessionOrderSaving||appClosed||!!boot.sessionOrder?.warning;
  handle.setAttribute("aria-label",`${item.title} 순서 이동. Alt와 위 또는 아래 방향키로 이동`);handle.setAttribute("aria-keyshortcuts","Alt+ArrowUp Alt+ArrowDown");
  handle.title="끌어서 순서 변경 · Alt+↑/↓";grip.setAttribute("aria-hidden","true");handle.append(grip);
  handle.onpointerdown=event=>startSessionDrag(event,item,handle,container);handle.onpointermove=moveSessionDrag;
  handle.onpointerup=event=>finishSessionDrag(event);handle.onpointercancel=event=>finishSessionDrag(event,true);
  handle.onlostpointercapture=event=>finishSessionDrag(event,true);
  handle.onkeydown=event=>{if(event.key==="Escape"&&sessionPointerDrag){event.preventDefault();finishSessionDrag(null,true);return;}if(!event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||event.isComposing||!["ArrowUp","ArrowDown"].includes(event.key))return;
    event.preventDefault();const visible=new Set([...$(container).children].map(row=>row.dataset.sessionId)),group=orderedSessions().filter(other=>visible.has(other.id)&&!!other.pinned===!!item.pinned),index=group.findIndex(other=>other.id===item.id),target=group[index+(event.key==="ArrowUp"?-1:1)];
    if(target)return moveSession(item.id,target.id,event.key==="ArrowUp"?"before":"after",container);};
  const pin=el("button",null,"session-pin"),icon=el("span",null,"session-pin-icon");pin.type="button";pin.disabled=sessionOrderSaving||appClosed;
  pin.setAttribute("aria-label",`${item.title} ${item.pinned?"고정 해제":"고정"}`);pin.setAttribute("aria-pressed",String(!!item.pinned));pin.title=item.pinned?"고정 해제":"상단에 고정";icon.setAttribute("aria-hidden","true");pin.append(icon);
  pin.onclick=async()=>{try{await updateSession(item.id,{pinned:!item.pinned});}catch(e){toast(e.message);}finally{const next=[...$(container).children].find(row=>row.dataset.sessionId===item.id);next?.querySelector(".session-pin")?.focus();}};
  row.append(handle,button,pin);return row;
}
function renderSessions(){
  // Polling must not replace the native drag source before its drop event.
  if(sessionDragId)return;
  let focused=null;for(const container of ["sessions","home-recents","all-sessions"])for(const row of $(container).children)for(const selector of [".session-drag",".session-pin"])if(row.querySelector(selector)===document.activeElement)focused={container,id:row.dataset.sessionId,selector};
  const query=$("session-search").value.trim().toLocaleLowerCase(),items=orderedSessions().filter(s=>(s.title+" "+s.workspace).toLocaleLowerCase().includes(query));
  $("sessions").replaceChildren();$("home-recents").replaceChildren();
  if(boot.sessionOrder?.warning)$("sessions").append(el("p",boot.sessionOrder.warning,"sidebar-empty"));
  for(const item of items){const b=el("button",null,"session"+(item.id===active?.id?" active":""));b.type="button";b.setAttribute("aria-label",`${item.title} · ${stateNames[item.state]||"이어하기"}`);if(item.id===active?.id)b.setAttribute("aria-current","page");b.append(el("span",item.pinned?"고정":"업무","session-kicker"),el("strong",item.title,"session-title"));const meta=el("span",null,"session-meta");meta.append(el("span",stateNames[item.state]||"대기"),el("time",when(item.updated||item.created)));const waiting=globalThis.WorkspaceAttention?.countFor(item.id)||0;if(waiting)b.append(el("span",`응답 대기 ${waiting}`,"session-attention"));b.append(meta);b.title=item.workspace;b.onclick=()=>selectSession(item.id).catch(e=>error(e.message));$("sessions").append(sessionRow(item,b,"sessions"));}
  if(!items.length)$("sessions").append(el("p",query?"찾는 업무가 없어요":"시작한 업무가 여기에 모여요","sidebar-empty"));
  for(const item of items.slice(0,4)){const b=el("button",null,"recent-card");b.type="button";b.append(el("span",item.pinned?"고정한 업무":"이어서 하기","recent-kicker"),el("strong",item.title),el("span",`${basename(item.workspace)} · ${when(item.updated||item.created)}`,"recent-meta"),el("span",stateNames[item.state]||"대기","recent-status"));b.onclick=()=>selectSession(item.id).catch(e=>error(e.message));$("home-recents").append(sessionRow(item,b,"home-recents"));}
  if(!items.length)$("home-recents").append(el("p",query?"검색어를 바꾸어 다시 찾아보세요.":"첫 업무를 시작하면, 다음에 이곳에서 이어갈 수 있어요.","empty-recents"));
  $("tasks-open").hidden=!sessions.length;renderAllSessions();globalThis.WorkspaceCapabilities?.contextChanged();
  if(focused){const row=[...$(focused.container).children].find(row=>row.dataset.sessionId===focused.id);row?.querySelector(focused.selector)?.focus({preventScroll:true});}
}
function renderAllSessions(){
  const query=$("task-search").value.trim().toLocaleLowerCase(),items=orderedSessions().filter(s=>(s.title+" "+s.workspace).toLocaleLowerCase().includes(query));
  $("all-sessions").replaceChildren();$("tasks-empty").hidden=!!items.length;
  for(const item of items){const button=el("button",null,"session all-session"),meta=el("span",null,"session-meta");button.type="button";button.title=item.workspace;button.append(el("span",item.pinned?"고정한 업무":"업무","session-kicker"),el("strong",item.title,"session-title"),el("span",item.workspace,"all-session-path"));meta.append(el("span",stateNames[item.state]||"대기"),el("time",when(item.updated||item.created)));button.append(meta);if(item.id===active?.id)button.setAttribute("aria-current","page");button.onclick=async()=>{const pending=selectSession(item.id),ticket=selectionGeneration;try{if(await pending&&ticket===selectionGeneration)$("tasks-dialog").close();}catch(e){if(ticket===selectionGeneration)toast(e.message);}};$("all-sessions").append(sessionRow(item,button,"all-sessions"));}
}
// Untrusted model text becomes text nodes only, never HTML or remote content.
function inlineText(parent,text){const pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;let pos=0;for(const match of text.matchAll(pattern)){parent.append(document.createTextNode(text.slice(pos,match.index)));const code=match[0][0]==="`";parent.append(el(code?"code":"strong",match[0].slice(code?1:2,code?-1:-2)));pos=match.index+match[0].length;}parent.append(document.createTextNode(text.slice(pos)));}
function renderText(parent,text){
  parent.replaceChildren();const lines=String(text||"").split(/\r?\n/),cells=line=>line.trim().replace(/^\||\|$/g,"").split("|").map(s=>s.trim());let i=0;
  while(i<lines.length){const line=lines[i];if(!line.trim()){i++;continue;}
    if(/^```/.test(line)){const pre=el("pre"),code=el("code");i++;const block=[];while(i<lines.length&&!/^```/.test(lines[i]))block.push(lines[i++]);code.textContent=block.join("\n");pre.append(code);parent.append(pre);if(i<lines.length)i++;continue;}
    if(i+1<lines.length&&line.includes("|")&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){const wrap=el("div",null,"message-table"),table=el("table"),head=el("tr");for(const value of cells(line)){const th=el("th");inlineText(th,value);head.append(th);}const thead=el("thead");thead.append(head);table.append(thead);i+=2;const body=el("tbody");while(i<lines.length&&lines[i].includes("|")&&lines[i].trim()){const tr=el("tr");for(const value of cells(lines[i++])){const td=el("td");inlineText(td,value);tr.append(td);}body.append(tr);}table.append(body);wrap.append(table);parent.append(wrap);continue;}
    const heading=line.match(/^#{1,4}\s+(.+)$/);if(heading){const h=el("h3");inlineText(h,heading[1]);parent.append(h);i++;continue;}
    if(/^\s*(?:[-*]|\d+\.)\s+/.test(line)){const list=el(/^\s*\d+\./.test(line)?"ol":"ul");while(i<lines.length&&/^\s*(?:[-*]|\d+\.)\s+/.test(lines[i])){const li=el("li");inlineText(li,lines[i++].replace(/^\s*(?:[-*]|\d+\.)\s+/,""));list.append(li);}parent.append(list);continue;}
    const p=el("p");inlineText(p,line);parent.append(p);i++;
  }
}
function streamKey(data){return `${data.messageId}:${data.index||0}`;}
const MESSAGE_TEXT_LIMIT=100000,CONVERSATION_MESSAGE_LIMIT=150,CONVERSATION_TEXT_LIMIT=500000;
function messageTruncation(article,truncated){
  let note=article.querySelector(".message-truncated");
  if(truncated&&!note){note=el("small","긴 답변의 앞부분만 표시해요. 이 화면의 표시 제한은 Claude 대화 기록을 변경하지 않아요.","message-interrupted message-truncated");article.append(note);}
  else if(!truncated)note?.remove();
}
function renderBoundedMessageText(body,text){
  // Markup-dense responses stay plain text. This avoids creating tens
  // of thousands of table/list/inline nodes from a bounded but pathological reply.
  let marks=0;const pattern=/\n|\||\*\*|`/g;while(marks<=600&&pattern.exec(text))marks++;
  body.setAttribute("style",marks>600?"white-space:pre-wrap":"");
  body.textContent="";
  if(marks>600){body.replaceChildren();body.textContent=text;}else renderText(body,text);
}
function boundConversation(keep){
  const conversation=$("conversation"),articles=[...conversation.children].filter(node=>node.classList.contains("message"));
  let total=articles.reduce((sum,node)=>sum+Number(node.dataset.messageSize||0),0),count=articles.length,removed=false;
  const over=()=>count>CONVERSATION_MESSAGE_LIMIT||total>CONVERSATION_TEXT_LIMIT;
  const discard=node=>{total-=Number(node.dataset.messageSize||0);count--;node.remove();for(const [key,value]of streaming)if(value===node)streaming.delete(key);removed=true;};
  // Keep ongoing text and the pending user submission while completed history
  // expires first. Approval/choice controls live outside this conversation node.
  for(const node of articles)if(over()&&node!==keep&&!node.classList.contains("streaming")&&!node.classList.contains("pending"))discard(node);
  if(over())for(const node of articles){
    if(!over())break;
    if(node===keep||node.classList.contains("pending")||!node.parentElement&&!node.parent)continue;
    // Many simultaneous text blocks can otherwise bypass the aggregate bound.
    // Retain each live block's identity and bounded prefix whenever possible.
    const old=node.dataset.streamText||"",excess=Math.max(0,total-CONVERSATION_TEXT_LIMIT),length=Math.max(0,old.length-excess);
    if(old.length&&length<old.length){node.dataset.streamText=old.slice(0,length);node.dataset.streamTruncated="true";node.dataset.messageSize=String(length);node.querySelector(".message-body").textContent=node.dataset.streamText;total-=old.length-length;messageTruncation(node,true);}
    if(count>CONVERSATION_MESSAGE_LIMIT)discard(node);
  }
  if(removed&&!conversation.querySelector(".conversation-limit"))conversation.prepend(el("p","최근 대화 일부만 표시하고 있어요. 표시 범위는 최대 150개 메시지·50만 자이며 Claude 기록은 변경하지 않아요.","message-interrupted conversation-limit"));
  if(Array.isArray(active?.messages)){
    const messages=active.messages.slice(-CONVERSATION_MESSAGE_LIMIT).map(message=>typeof message.text==="string"&&message.text.length>MESSAGE_TEXT_LIMIT?{...message,text:message.text.slice(0,MESSAGE_TEXT_LIMIT),uiTruncated:true}:message);
    const size=message=>String(message.text||"").length+(Array.isArray(message.files)?message.files:[]).reduce((sum,path)=>sum+String(path).length,0);
    let characters=messages.reduce((sum,message)=>sum+size(message),0);while(messages.length>1&&characters>CONVERSATION_TEXT_LIMIT)characters-=size(messages.shift());
    active.messages=messages;
  }
}
function renderMessage(message){
  globalThis.WorkspaceStream?.finish(message);
  const raw=String(message.text||""),text=raw.slice(0,MESSAGE_TEXT_LIMIT),truncated=!!message.uiTruncated||raw.length>MESSAGE_TEXT_LIMIT;
  const sk=streamKey(message),existing=message.messageId?streaming.get(sk):null;
  if(existing){renderBoundedMessageText(existing.querySelector(".message-body"),text);existing.classList.remove("streaming");delete existing.dataset.streamText;delete existing.dataset.streamTruncated;existing.dataset.messageSize=String(text.length);streaming.delete(sk);messageTruncation(existing,truncated);boundConversation(existing);return existing;}
  const article=el("article",null,`message ${message.role}`),fileText=Array.isArray(message.files)?message.files.slice(0,12).map(basename).join(" · "):"";article.dataset.messageSize=String(text.length+fileText.length);article.append(el("div",message.role==="user"?"나":"WORKSPACE","message-label"));const body=el("div",null,"message-body");renderBoundedMessageText(body,text);article.append(body);if(fileText)article.append(el("div",fileText,"message-files"));messageTruncation(article,truncated);$("conversation").append(article);boundConversation(article);return article;
}
function applyDelta(data){const sk=streamKey(data);let article=streaming.get(sk);if(!article){article=renderMessage({role:"assistant",text:""});article.classList.add("streaming");article.dataset.streamText="";streaming.set(sk,article);}const old=article.dataset.streamText||"",incoming=String(data.text||""),available=Math.max(0,MESSAGE_TEXT_LIMIT-old.length);if(article.dataset.streamTruncated!=="true")article.dataset.streamText=old+incoming.slice(0,available);if(data.uiTruncated||incoming.length>available)article.dataset.streamTruncated="true";article.dataset.messageSize=String(article.dataset.streamText.length);article.querySelector(".message-body").textContent=article.dataset.streamText;messageTruncation(article,article.dataset.streamTruncated==="true");boundConversation(article);}
function renderDelta(data){if(globalThis.WorkspaceStream)WorkspaceStream.enqueue(data,applyDelta);else applyDelta(data);}
function renderConnection(info){
  const connected=info && info.connected!==false;
  const names=list=>(Array.isArray(list)?list:[]).map(x=>typeof x==="string"?x:x?.name||x?.id||"이름 미제공").join(", ");
  $("settings-runtime").textContent=boot.runtime?`실행 위치: ${boot.runtime.entry}\n설정 위치: ${boot.runtime.configRoot}`:"실행 위치를 아직 확인하지 않았어요.";
  $("settings-status").textContent=boot.demo?"화면 체험 연결 · 실제 AI 호출 없음":boot.error?"기존 Claude 연결 확인이 필요해요":connected?"이 업무의 Claude 연결이 확인됐어요":info?"이전 연결이 종료됐어요. 다음 요청에서 다시 연결합니다":"실행 파일 확인 완료 · 실제 응답은 업무를 시작한 뒤 확인합니다";
  $("connection-badge").textContent=boot.demo?"화면 체험":boot.error?"연결 확인 필요":connected?"업무 연결됨":info?"다음 요청 대기":"기존 Claude 연결";
  $("diagnostics").textContent=info?`현재 모델: ${info.model||"미제공"}\n사용 가능한 스킬: ${names(info.skills)||"CLI 목록 미제공"}\n연결 도구: ${(info.mcp||[]).map(x=>`${x.name}: ${x.status}`).join(", ")||"CLI 목록 미제공"}\n플러그인: ${names(info.plugins)||"CLI 목록 미제공"}`:"업무를 시작하면 현재 모델과 연결 도구를 표시해요.";renderConnectionOptions();updateModelControls();updatePermissionControls();globalThis.WorkspaceInlineControls?.render();globalThis.WorkspaceSessionImport?.render();
}
function connectionLocked(){return !active||busyStates.has(active.state)||sending||!!choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||appClosed;}
function modelOptions(){return (Array.isArray(active?.connection?.availableModels)?active.connection.availableModels:[]).slice(0,100).map(item=>typeof item==="string"?{value:item,displayName:item}:item).filter(item=>item&&typeof item.value==="string"&&item.value);}
function permissionOptions(){const rows=(Array.isArray(active?.connection?.availablePermissionModes)?active.connection.availablePermissionModes:[]).filter(item=>item&&["manual","default","plan","acceptEdits","auto","bypassPermissions"].includes(item.value)&&(item.value!=="bypassPermissions"||active.connection.bypassPermissions?.available===true));return rows.filter(item=>item.value!=="manual"||!rows.some(row=>row.value==="default"));}
function permissionOptionValue(mode,rows=permissionOptions()){if(mode==="manual"||mode==="default")return rows.find(row=>row.value==="default")?.value||rows.find(row=>row.value==="manual")?.value||mode;return mode;}
function effortOptions(){return (Array.isArray(active?.connection?.availableEfforts)?active.connection.availableEfforts:[]).filter(item=>item&&["low","medium","high","xhigh","max"].includes(item.value));}
function renderConnectionOptions(){
  const model=$("model-select"),previousModel=model.value;model.replaceChildren();const custom=el("option","직접 입력");custom.value="";model.append(custom);
  const seen=new Set();for(const item of modelOptions()){if(seen.has(item.value))continue;seen.add(item.value);const option=el("option",item.displayName||item.value);option.value=item.value;model.append(option);}model.value=seen.has(previousModel)?previousModel:"";
  $("model-list-note").textContent=seen.size?"현재 연결이 제공한 모델 목록입니다. 사용할 모델 이름을 직접 입력할 수도 있어요.":"연결에서 모델 목록을 제공하지 않았어요. 연결에서 사용할 모델 이름을 직접 입력할 수 있어요.";
  const mode=$("permission-mode-select"),previousMode=mode.value;mode.replaceChildren();const inherit=el("option","기존 설정 사용");inherit.value="";mode.append(inherit);
  for(const item of permissionOptions()){const option=el("option",item.displayName||item.value);option.value=item.value;mode.append(option);}mode.value=permissionOptions().some(item=>item.value===previousMode)?previousMode:"";
}
function updateModelControls(){const capable=!!active?.connection?.capabilities?.setModel,disabled=connectionLocked()||!capable;$("model-apply").disabled=disabled;$("model-reset").disabled=disabled||!active?.modelOverride;$("model-input").disabled=disabled;$("model-select").disabled=disabled;$("model-message").textContent=modelChanging?"모델 변경을 확인하고 있어요.":!active?"업무를 시작하면 이 대화에서 사용할 모델을 확인할 수 있어요.":!capable?"현재 연결에서 모델 변경을 확인하지 못했어요. 기존 모델을 그대로 사용합니다.":busyStates.has(active.state)?"진행 중인 요청이 끝나면 모델을 바꿀 수 있어요.":`현재: ${active.connection?.model||"기존 모델"} · 이 연결에만 적용하며 Claude 기본 설정은 바꾸지 않아요.`;}
function updatePermissionControls(){
  const info=active?.connection,capable=info?.capabilities?.setPermissionMode===true,locked=connectionLocked();
  $("permission-mode-select").disabled=$("permission-mode-apply").disabled=locked||!capable;
  $("permission-mode-reset").disabled=locked||!info?.permissionModeOverride||info.permissionModeResetAvailable===false;
  const current=permissionOptions().find(item=>item.value===permissionOptionValue(info?.permissionMode)),selected=permissionOptions().find(item=>item.value===$("permission-mode-select").value);
  $("permission-mode-detail").textContent=selected?.description||"기존 Claude 설정을 사용합니다. 개인 설정과 회사 정책은 변경하지 않아요.";
  $("permission-mode-message").textContent=permissionChanging?"승인 모드 변경을 확인하고 있어요.":!active?"업무 연결 후 현재 승인 모드를 확인할 수 있어요.":busyStates.has(active.state)?"진행 중인 요청이 끝나면 승인 모드를 바꿀 수 있어요.":!capable?"현재 연결은 승인 모드 변경을 제공하지 않아요. 기존 승인 흐름을 유지합니다.":`현재: ${current?.displayName||info?.permissionMode||"미확인"}${info?.permissionModeOverride?" · 이 연결에서 선택":" · 기존 설정 상속"}${info?.permissionModeSupport==="unverified"?" · 선택 시 CLI 적용 응답 확인":""}`;
  $("permission-mode-reset-note").textContent=info?.permissionModeResetRequiresReconnect?"기존 설정으로 돌아가면 이 연결을 닫고 다음 요청에서 기존 설정을 다시 불러옵니다.":"이 업무에 적용합니다. 같은 앱에서 다시 연결하면 선택을 이어갑니다.";
}
function applyConnectionState(value){
  if(!active)return;const state=value?.session?.connection||value;
  if(!state||typeof state!=="object")return;
  active.connection=active.connection||{};
  for(const name of ["model","modelOverride","availableModels","effort","effortOverride","availableEfforts","effortSupport","effortSource","effortChangeRequiresReconnect","effortResetRequiresReconnect","effortResetAvailable","capabilities","permissionMode","permissionModeLabel","permissionModeSource","permissionModeCycle","permissionModeOverride","availablePermissionModes","permissionModeSupport","permissionModeResetRequiresReconnect","permissionModeResetAvailable","connected"])if(name in state)active.connection[name]=state[name];
  if("modelOverride" in state)active.modelOverride=state.modelOverride;else if("modelOverride" in value)active.modelOverride=value.modelOverride;
}
function renderVerification(){
  const value=active?.verification,labels={checking:"결과 확인 중", "needs-review":"결과 확인 필요",unverified:"결과 미확인"},box=$("verification-status");
  box.hidden=!value||!labels[value.state];box.replaceChildren();
  if(box.hidden)return;box.dataset.state=value.state;box.append(el("strong",labels[value.state]),el("span",value.message||"요청 종료와 결과 검증은 별도로 확인합니다."));
}
function renderWorkspaceChoice(){
  const choice=active?.choice,container=$("workspace-choice"),key=active&&choice?`${active.id}:${choice.id}`:"";
  if(!choice||choice.schemaVersion!==1||choice.kind!=="html-report-style"||choice.responseMode!=="next-user-message"||!Array.isArray(choice.options)||answeredChoices.has(key)){
    container.replaceChildren();container.hidden=true;choiceView=null;return;
  }
  container.hidden=false;
  if(choiceView?.key!==key){
    container.replaceChildren();const card=el("section",null,"request workspace-choice-card");card.dataset.choiceId=choice.id;
    card.append(el("span","CHOOSE YOUR STYLE","request-kicker"),el("h3",choice.question||"보고서 디자인을 선택해 주세요."));
    const grid=el("div",null,"workspace-choice-grid"),buttons=[];
    for(const option of choice.options){if(!option||typeof option.id!=="string")continue;const button=el("button",null,"workspace-choice-option");button.type="button";button.append(el("strong",option.label||option.id));if(option.description)button.append(el("span",option.description));button.onclick=()=>submitWorkspaceChoice({optionId:option.id},key);buttons.push(button);grid.append(button);}
    card.append(grid);let input=null,custom=null;
    if(choice.allowCustom===true){const label=el("label","다른 의견이나 요청","choice-custom-label");input=el("input");input.type="text";input.maxLength=2000;input.setAttribute("aria-label","보고서 디자인 직접 입력");input.placeholder="원하는 디자인을 직접 입력하세요";custom=el("button","직접 입력한 답변 보내기","quiet-button");custom.type="button";custom.onclick=()=>submitWorkspaceChoice({text:input.value.trim()},key);input.onkeydown=event=>{if(event.key==="Enter"){event.preventDefault();custom.onclick();}};const row=el("div",null,"choice-custom-row");row.append(input,custom);card.append(label,row);buttons.push(custom);}
    const note=el("p",null,"choice-state-note");note.setAttribute("role","status");card.append(note);container.append(card);choiceView={key,sessionId:active.id,choiceId:choice.id,card,buttons,input,note};
  }
  const pending=choiceSubmission?.key===key,ready=["idle","done"].includes(active.state)&&!sending&&!choiceSubmission&&!modelChanging&&!permissionChanging&&!effortChanging&&!connectionPreparing&&!appClosed;
  for(const button of choiceView.buttons)button.disabled=!ready;if(choiceView.input)choiceView.input.disabled=!ready;
  choiceView.note.textContent=pending?"선택한 답변을 보내고 있어요.":ready?"선택한 내용은 이 업무의 다음 요청으로 전달합니다.":"현재 응답이 끝나면 선택할 수 있어요.";
}
async function submitWorkspaceChoice(answer,expectedKey){
  const view=choiceView;if(!view||view.key!==expectedKey||active?.id!==view.sessionId||active?.choice?.id!==view.choiceId||answeredChoices.has(view.key)||!["idle","done"].includes(active.state)||sending||choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||appClosed)return;
  if("text" in answer&&!answer.text)return toast("답변을 입력하거나 위의 디자인을 선택해 주세요.");
  if(!active.trusted){chooseFolder(true);$("folder-form").dataset.afterTrust="choice";return toast("업무 폴더를 다시 확인한 뒤 디자인을 선택해 주세요.");}
  const sid=view.sessionId,ticket=selectionGeneration;choiceSubmission=view;setStatus(active.state);error("");
  // Reserve the message position without displaying an unacknowledged answer.
  const anchor=el("article",null,"message user");anchor.hidden=true;$("conversation").append(anchor);
  try{
    const response=await api("/api/choice",{id:sid,choiceId:view.choiceId,...answer});answeredChoices.add(view.key);
    if(active?.id===sid&&selectionGeneration===ticket){
      const message=[...(response.session?.messages||[])].reverse().find(item=>item.role==="user");
      if(message){anchor.hidden=false;anchor.append(el("div","나","message-label"));const body=el("div",null,"message-body");renderText(body,message.text);anchor.append(body);}else anchor.remove();
      if(active.choice?.id===view.choiceId)active.choice=null;renderWorkspaceChoice();
    }
  }catch(e){anchor.remove();if(active?.id===sid&&selectionGeneration===ticket)error(e.message);}
  finally{if(choiceSubmission===view)choiceSubmission=null;setStatus(active?.state||"idle");renderAttachments();}
}
function saveDraft(){drafts.set(active?.id||"home",{text:$("prompt").value,attachments:[...attachments]});}
function restoreDraft(id){const draft=drafts.get(id||"home");$("prompt").value=draft?.text||"";attachments=[...(draft?.attachments||[])];renderAttachments();}
function taskHeader(){globalThis.WorkspaceSessionImport?.render();globalThis.WorkspaceProductivityActions?.update();$("chat-title").textContent=globalThis.WorkspaceCapabilities?.isOpen()?"스킬·도구":active?.title||"업무 홈";$("task-title").hidden=$("task-pin").hidden=!active;$("task-pin").setAttribute("aria-pressed",String(!!active?.pinned));$("task-pin").setAttribute("aria-label",active?.pinned?"업무 고정 해제":"업무 고정");$("workspace-summary").textContent=active?basename(active.workspace):"자료와 결과를 한곳에서 관리해요";$("workspace-summary").title=active?.workspace||"새 업무 공간 선택";$("folder-name").textContent=active?basename(active.workspace):"업무 공간";$("folder-path").textContent=active?.workspace||"시작할 때 새 공간을 만들거나 기존 폴더를 선택하세요.";$("home-button").setAttribute("aria-current",active||globalThis.WorkspaceCapabilities?.isOpen()?"false":"page");document.querySelector(".app").classList.toggle("task-open",!!active);}
async function selectSession(id,{keepDraft=false}={}){
  globalThis.WorkspaceCapabilities?.close();
  globalThis.WorkspaceComposer?.contextChanged();globalThis.WorkspaceStream?.reset();globalThis.WorkspaceProductivityActions?.contextChanged();globalThis.WorkspaceWorkflow?.contextChanged();closePreview();
  if(!keepDraft)saveDraft();const ticket=++selectionGeneration;if(pollController)pollController.abort();streaming.clear();const item=await api(`/api/session?id=${encodeURIComponent(id)}`);if(ticket!==selectionGeneration)return false;active=item;started=null;error("");
  $("conversation").replaceChildren();$("requests").replaceChildren();$("activity").replaceChildren();renderedQueuedRequests.clear();active.messages.forEach(message=>{renderMessage(message);if(message.requestId)renderedQueuedRequests.add(message.requestId);});(active.requests||[]).forEach(renderRequest);$("welcome").hidden=true;$("conversation").hidden=false;if(!active.messages.length)$("conversation").append(el("p","업무 공간이 준비됐어요. 자료를 선택하거나 바로 요청해 보세요.","conversation-empty"));
  taskHeader();if(!keepDraft)restoreDraft(id);renderConnection(active.connection);setStatus(active.state);renderSessions();refreshFiles();refreshResults();globalThis.WorkspaceStream?.changed();globalThis.WorkspaceWorkflow?.refresh();revealRequest($("requests").children[0]);pollController=new AbortController();poll(id,active.seq||0,pollController.signal);return true;
}
function showHome(clear=false){globalThis.WorkspaceCapabilities?.close();globalThis.WorkspaceComposer?.contextChanged();globalThis.WorkspaceStream?.reset();globalThis.WorkspaceProductivityActions?.contextChanged();globalThis.WorkspaceWorkflow?.contextChanged();closePreview();saveDraft();selectionGeneration++;if(pollController)pollController.abort();active=null;started=null;streaming.clear();if(clear)drafts.delete("home");restoreDraft("home");$("welcome").hidden=false;$("conversation").hidden=true;$("requests").replaceChildren();$("activity").replaceChildren();$("files").replaceChildren();$("file-count").textContent="0";$("results-list").replaceChildren();$("result-count").textContent="0";$("empty-results").hidden=false;taskHeader();error("");setStatus("idle");renderConnection(null);renderSessions();}
async function poll(id,after,signal){while(!signal.aborted&&active?.id===id){try{const result=await api(`/api/events?id=${encodeURIComponent(id)}&after=${after}`,undefined,signal);if(signal.aborted||active?.id!==id)return;for(const event of result.events){handleEvent(event);after=event.seq;}}catch(e){if(e.name==="AbortError")return;error(e.message);return;}}}
function handleEvent(event){
  const d=event.data,area=$("work-area"),nearBottom=area.scrollHeight-area.scrollTop-area.clientHeight<120,sid=active?.id;let newRequest=null;
  if(event.type==="queued_user"&&!renderedQueuedRequests.has(d.requestId)){if(d.requestId)renderedQueuedRequests.add(d.requestId);const message={role:"user",text:d.text,files:d.files||[],requestId:d.requestId};active.messages=active.messages||[];active.messages.push(message);$("conversation").querySelector(".conversation-empty")?.remove();renderMessage(message);}
  if(["assistant","assistant_delta"].includes(event.type)){$("conversation").querySelector(".conversation-empty")?.remove();event.type==="assistant"?renderMessage({role:"assistant",...d}):renderDelta(d);}
  if(event.type==="status"){if(["stopped","error"].includes(d.state))globalThis.WorkspaceStream?.flush();if(d.state==="starting")active.verification=null;if(["stopped","error"].includes(d.state))active.choice=null;setStatus(d.state,d.label);if(d.connection){active.connection=d.connection;if("modelOverride" in d.connection)active.modelOverride=d.connection.modelOverride;renderConnection(d.connection);}if(d.state==="stopped"){$("requests").replaceChildren();for(const node of streaming.values()){node.classList.remove("streaming");node.append(el("small","중지 전까지 받은 내용","message-interrupted"));}streaming.clear();refreshFiles();refreshResults(true);}}
  if(event.type==="connected"){active.connection=d;active.modelOverride=d.modelOverride||null;active.sessionId=d.sessionId;renderConnection(d);globalThis.WorkspaceComposer?.connectionChanged();}
  if(["model_changed","permission_mode_changed","effort_changed"].includes(event.type)){applyConnectionState(d);renderConnection(active.connection);}
  if(event.type==="verification"){active.verification=d;renderVerification();}
  if(event.type==="choice"){if(!answeredChoices.has(`${active.id}:${d.id}`))active.choice=d;renderWorkspaceChoice();}
  if(event.type==="choice_closed"){const id=d.id||d.choiceId;answeredChoices.add(`${active.id}:${id}`);if(active.choice?.id===id)active.choice=null;renderWorkspaceChoice();}
  if(event.type==="request"){newRequest=renderRequest(d);setStatus(d.tool==="AskUserQuestion"?"question":"approval");}
  if(event.type==="request_closed"){for(const n of $("requests").children)if(n.dataset.requestId===d.id)n.remove();if(d.state)setStatus(d.state);else if(!$("requests").children.length&&busyStates.has(active?.state))setStatus("running");}
  if(event.type==="activity"){const labels={Skill:"작업 방식 확인",Read:"자료 읽기",Bash:"업무 도구 실행",Write:"파일 작성",Edit:"파일 수정",Agent:"추가 작업 진행",Task:"추가 작업 진행",AskUserQuestion:"질문 준비"};$("activity").append(el("li",(labels[d.tool]||d.tool||"업무 진행")+(d.skill?" · "+d.skill:"")));while($("activity").children.length>40)$("activity").firstChild.remove();}
  if(event.type==="result"){if(d.branch)active.branch=d.branch;if(d.sessionId)active.sessionId=d.sessionId;active.verification=d.verification||(["needs-review","unverified"].includes(active.verification?.state)?active.verification:{state:"unverified",message:"요청은 끝났지만 결과 검증 상태는 확인하지 못했습니다."});setStatus("done");refreshFiles();refreshResults(true);if(d.budgetWarning)toast(d.budgetWarning);refreshSessionMeta();}if(event.type==="artifacts")refreshResults(true);
  if(event.type==="error"){globalThis.WorkspaceStream?.flush();error(d.message);active.choice=null;setStatus("error");if("resumeSessionId" in d)active.sessionId=d.resumeSessionId;$("requests").replaceChildren();for(const node of streaming.values()){node.classList.remove("streaming");node.append(el("small","연결 중단 전까지 받은 내용","message-interrupted"));}streaming.clear();}if(event.type==="notice")toast(d.message);
  if(["queue_changed","schedule_changed","dispatch_changed","result","status"].includes(event.type))globalThis.WorkspaceWorkflow?.refresh();
  globalThis.WorkspaceCapabilities?.contextChanged(["connected","model_changed","error"].includes(event.type)||(event.type==="status"&&d.state==="stopped"));
  if(globalThis.WorkspaceStream){if(["assistant","queued_user","request","result","choice"].includes(event.type))WorkspaceStream.changed();}
  else if(newRequest)revealRequest(newRequest);
  else if(event.type!=="request"&&nearBottom&&!$("requests").children.length&&!globalThis.WorkspaceCapabilities?.isOpen())requestAnimationFrame(()=>{if(active?.id===sid&&!$("requests").children.length&&!globalThis.WorkspaceCapabilities?.isOpen())area.scrollTop=area.scrollHeight;});
}
function revealRequest(card){
  if(!card)return;const sid=active?.id;
  requestAnimationFrame(()=>{if(active?.id===sid&&[...$("requests").children].includes(card)&&!globalThis.WorkspaceCapabilities?.isOpen())card.scrollIntoView({block:"nearest",inline:"nearest"});});
}
function sessionMetadata(item){return {id:item.id,title:item.title,workspace:item.workspace,connectionState:item.connection?.connected?"live":item.connection?"last-seen":item.connectionState||"unavailable",created:item.created,updated:item.updated,pinned:item.pinned,state:item.state,artifactCount:item.artifactCount??item.artifacts?.length??0};}
async function refreshSessionMeta(){if(!active)return;const id=active.id;try{const next=await api(`/api/session?id=${encodeURIComponent(id)}`);sessions=sessions.map(s=>s.id===id?sessionMetadata(next):s);if(active?.id===id){active.updated=next.updated;active.artifactCount=next.artifactCount;active.title=next.title;taskHeader();}renderSessions();}catch(e){toast(e.message);}}
function approvalSummary(request){
  const input=request.input||{},tool=request.tool||"업무 도구",target=input.file_path||input.path||input.notebook_path;
  const known={Read:["자료를 읽습니다","선택한 자료의 내용을 확인하는 요청이에요."],Write:["파일에 내용을 저장합니다","같은 이름의 파일이 있으면 기존 내용이 바뀔 수 있어요."],Edit:["파일 내용을 수정합니다","아래 변경 전후와 대상 파일을 확인해 주세요."],MultiEdit:["파일의 여러 부분을 수정합니다","대상 파일과 상세 변경 내용을 확인해 주세요."],Glob:["파일을 찾습니다","조건에 맞는 파일 경로를 찾는 요청이에요."],Grep:["자료 안에서 내용을 찾습니다","검색 조건에 맞는 내용을 확인하는 요청이에요."]};
  const [title,description]=known[tool]||[tool==="Bash"?"업무 명령을 실행합니다":"연결된 도구를 사용합니다","파일 변경이나 외부 연결이 포함될 수 있어요. 제공된 설명과 실행 원문을 확인해 주세요."];
  const box=el("div",null,"approval-summary");box.append(el("strong",title),el("p",description));if(target){const row=el("div",null,"approval-target"),path=el("small",String(target));path.tabIndex=0;path.setAttribute("aria-label","대상 파일 전체 경로");row.append(el("span","대상 파일"),el("strong",basename(target)),path);box.append(row);}
  if(typeof input.command==="string"&&input.command){const command=el("div",null,"approval-command"),code=el("pre",input.command);code.tabIndex=0;code.setAttribute("aria-label","실행 명령 전체");command.append(el("span","실행 명령"),code);box.append(command);}
  const explanation=request.description||input.description;if(explanation){const description=el("p","도구가 제공한 설명: "+explanation,"approval-description");description.tabIndex=0;box.append(description);}
  if(typeof request.decisionReason==="string"&&request.decisionReason){const reason=el("div",null,"approval-reason"),copy=el("p",request.decisionReason);copy.tabIndex=0;reason.append(el("strong","확인이 필요한 이유"),copy);box.append(reason);}
  if(request.matchedAskRule&&typeof request.matchedAskRule==="object"){const rule=el("details",null,"approval-detail approval-ask-rule"),data=request.matchedAskRule,raw=el("pre",[data.source,data.toolName,data.ruleContent].filter(value=>typeof value==="string"&&value).join(" · "));raw.tabIndex=0;raw.setAttribute("aria-label","승인 규칙 전체");rule.append(el("summary","기존 승인 규칙에 따라 확인이 필요해요"),raw);box.append(rule);}
  if(tool==="Edit"&&typeof input.old_string==="string"&&typeof input.new_string==="string"){const diff=el("details",null,"approval-detail approval-change-detail"),changes=el("div",null,"approval-diff");diff.append(el("summary","변경 전후 확인"));for(const [label,value,cls]of [["변경 전",input.old_string,"change-before"],["변경 후",input.new_string,"change-after"]]){const block=el("section",null,cls);block.tabIndex=0;block.setAttribute("aria-label",label+" 전체");block.append(el("h4",label),el("pre",value));changes.append(block);}diff.append(changes);box.append(diff);}
  const details=el("details",null,"approval-detail approval-input-detail"),raw=el("pre",JSON.stringify(input,null,2));raw.tabIndex=0;raw.setAttribute("aria-label","도구 입력 원문 전체");details.append(el("summary","실행 원문과 세부 내용"),raw);box.append(details);return box;
}
function renderRequest(request){
  if([...$("requests").children].some(n=>n.dataset.requestId===request.id))return;const card=el("section",null,"request approval-request");card.dataset.requestId=request.id;const questions=request.tool==="AskUserQuestion"?request.input?.questions:null,header=el("div",null,"request-header"),body=el("div",null,"request-body"),selection=el("p",questions?"모든 질문에 답변해 주세요.":"이번만 허용","request-selection"),tool=el("span",questions?`${questions.length||1}개 질문`:request.tool||"업무 도구","request-kicker");tool.title=request.tool||"업무 도구";header.append(el("h3",questions?"어떻게 진행할까요?":"실행 전 확인"),tool);body.tabIndex=0;body.setAttribute("role","region");body.setAttribute("aria-label",questions?"질문과 답변 선택":"실행 내용과 승인 범위");card.append(header,body);const fields=[],permissionInputs=[];let pending=false;
  if(Array.isArray(questions)&&questions.length){for(const [i,q]of questions.entries()){const field=el("fieldset");field.append(el("legend",q.question));const inputs=[];for(const option of q.options||[]){const label=el("label",null,"option"),input=el("input");input.type=q.multiSelect?"checkbox":"radio";input.name=`${request.id}-${i}`;input.value=option.label;const copy=el("span",option.label);if(option.description)copy.append(el("small",option.description));label.append(input,copy);field.append(label);inputs.push(input);}const custom=el("input");custom.type="text";custom.placeholder="직접 답변 입력";custom.setAttribute("aria-label",q.question+" 직접 입력");field.append(custom);fields.push({q,inputs,custom});body.append(field);}}
  else {
    body.append(approvalSummary(request));const scopes=new Set(["session","localSettings","projectSettings","userSettings"]),choices=Array.isArray(request.permissionChoices)?request.permissionChoices.filter(choice=>choice&&typeof choice.id==="string"&&choice.id&&scopes.has(choice.destination)&&(choice.destination==="session"||typeof choice.description==="string"&&choice.description.trim())):[];
    if(choices.length){const scopesDetail=el("details",null,"approval-detail approval-scopes"),scopeSummary=el("summary","승인 범위: 이번만 허용"),field=el("fieldset",null,"permission-scope-options");field.append(el("legend","승인 범위 선택"));
      for(const choice of [{id:"",label:"이번만 허용",rules:[]},...choices]){const label=el("label",null,"option"),input=el("input"),name=choice.label||"이 연결에서 허용";input.type="radio";input.name=`permission-${request.id}`;input.value=choice.id;input.checked=!choice.id;permissionInputs.push(input);const copy=el("span",name);
        input.onchange=()=>{if(input.checked){selection.textContent=name;selection.title=name;scopeSummary.textContent="승인 범위: "+name;}};
        if(choice.id){copy.append(el("small",choice.description||"현재 Claude 연결에만 적용합니다. 새 연결에는 이어지지 않아요."));for(const rule of (Array.isArray(choice.rules)?choice.rules:[])){copy.append(el("code",`${rule.toolName||"도구"} · ${rule.ruleContent||"이 도구 전체"}`,"permission-rule"));}}else copy.append(el("small","이번 요청만 허용하고 승인 규칙은 저장하지 않습니다."));label.append(input,copy);field.append(label);}scopesDetail.append(scopeSummary,field);body.append(scopesDetail);
    }else selection.textContent="이번 요청에만 적용됩니다.";
  }
  const actions=el("div",null,"request-actions"),deny=el("button",questions?"답변하지 않기":"거절","quiet-button"),allow=el("button",questions?"답변하고 계속":"확인하고 승인","send-button");deny.type=allow.type="button";const sid=active.id;
  async function answer(yes){if(pending||active?.id!==sid||![...$("requests").children].includes(card)||appClosed)return;const answers={};if(yes&&questions){for(const field of fields){const values=field.inputs.filter(n=>n.checked).map(n=>n.value);if(field.custom.value.trim()){if(!field.q.multiSelect)values.length=0;values.push(field.custom.value.trim());}if(!values.length)return toast("각 질문의 답변을 선택하거나 입력해 주세요.");answers[field.q.question]=values.join(", ");}}const permissionChoiceId=yes&&!questions?permissionInputs.find(input=>input.checked)?.value:"";pending=true;allow.disabled=deny.disabled=true;for(const input of permissionInputs)input.disabled=true;try{await api("/api/respond",{id:sid,requestId:request.id,allow:yes,answers,...(permissionChoiceId?{permissionChoiceId}:{})});}catch(e){if(active?.id===sid&&[...$("requests").children].includes(card)){error(e.message);pending=false;allow.disabled=deny.disabled=false;for(const input of permissionInputs)input.disabled=false;}}}
  deny.onclick=()=>answer(false);allow.onclick=()=>answer(true);actions.append(deny,allow);const footer=el("div",null,"request-footer");footer.append(selection,actions);card.append(footer);$("requests").append(card);return card;
}
function renderAttachments(){globalThis.WorkspaceComposer?.close();globalThis.WorkspaceAttachments?.renderNote();$("attachments").replaceChildren();for(const path of attachments){const chip=el("span",null,"attachment");chip.title=path;chip.append(el("span",basename(path)));if(globalThis.WorkspaceAttachments?.isCopy(path))chip.append(el("small","복사본","attachment-copy"));const remove=el("button","×");remove.type="button";remove.setAttribute("aria-label",basename(path)+" 첨부 취소");remove.disabled=sending||!!choiceSubmission;remove.onclick=()=>{if(sending||choiceSubmission)return;attachments=attachments.filter(p=>p!==path);globalThis.WorkspaceComposer?.removeFileReference(path);renderAttachments();saveDraft();};chip.append(remove);$("attachments").append(chip);}}
function setPanel(panel){$("source-panel").hidden=panel!=="sources";$("results-panel").hidden=panel!=="results";$("files-tab").setAttribute("aria-selected",String(panel==="sources"));$("results-tab").setAttribute("aria-selected",String(panel==="results"));}
async function refreshFiles(){if(!active)return;const id=active.id;try{const result=await api(`/api/files?id=${encodeURIComponent(id)}`);if(active?.id!==id)return;$("files").replaceChildren();$("file-count").textContent=result.files.length;if(!result.files.length)$("files").append(el("p","이 공간에 자료가 아직 없어요. ‘자료 추가’로 다른 위치의 자료도 연결할 수 있어요.","empty-files"));for(const file of result.files){const b=el("button",null,"file"),ext=file.name.split(".").pop().toUpperCase();b.append(el("span",ext.slice(0,4),"file-extension"),el("span",file.name,"file-name"));b.title=file.path;globalThis.WorkspaceAttachments?.makeDraggable(b,file.path,id);b.onclick=()=>preview(file.path).catch(e=>error(e.message));$("files").append(b);}}catch(e){toast(e.message);}}
async function refreshResults(reveal=false){if(!active)return;const id=active.id;try{const result=await api(`/api/results?id=${encodeURIComponent(id)}`);if(active?.id!==id)return;$("results-list").replaceChildren();const items=(result.artifacts||[]).filter(file=>file.runId===result.lastRunId);$("result-count").textContent=items.length;$("empty-results").hidden=items.length>0;for(const file of items){const card=el("article",null,"result-card");card.append(el("span",file.change==="created"?"새로 확인한 파일":"변경된 파일","result-change"),el("strong",file.name||basename(file.path)),el("p",when(file.observedAt),"result-meta"));card.title=file.path;globalThis.WorkspaceAttachments?.makeDraggable(card,file.path,id);const actions=el("div",null,"result-actions"),open=el("button","미리보기","quiet-button"),external=el("button","기본 앱에서 열기","text-button"),folder=el("button","폴더에서 보기","text-button"),follow=el("button","이 결과로 요청","text-button");const context={sessionId:id,path:file.path};open.onclick=()=>{if(active?.id===id)preview(file.path).catch(e=>error(e.message));};external.onclick=()=>openFileAction("open",context);folder.onclick=()=>openFileAction("reveal",context);external.disabled=folder.disabled=!!boot.demo;follow.onclick=()=>{if(active?.id!==id)return;if(sending||choiceSubmission)return toast("요청 전송이 끝난 뒤 자료를 추가해 주세요.");attachments=[...new Set([...attachments,file.path])].slice(0,12);renderAttachments();$("prompt").focus();toast("요청할 자료에 추가했어요. 원하는 수정 내용을 입력하세요.");};actions.append(open,external,folder,follow);card.append(actions);if(/\.html?$/i.test(file.path))card.append(el("p","기본 앱에서 열면 원본의 스크립트와 외부 연결이 동작할 수 있어요.","external-file-note"));$("results-list").append(card);}if(items.length)$("results-list").append(el("p","요청 전후 이 공간에서 확인한 파일 변경입니다. 다른 프로그램의 변경이 포함될 수 있어요.","result-observation"));if(result.observation?.limited||result.observation?.errors)$("results-list").append(el("p","확인 범위에 제한이 있어요. 필요한 파일은 자료 목록에서도 확인하세요.","result-observation"));if(reveal&&items.length)setPanel("results");}catch(e){toast(e.message);}}
function closePreview(){previewGeneration++;previewContext=null;previewPath=null;if($("preview-dialog").open)$("preview-dialog").close();}
async function openFileAction(action,context=previewContext){if(!context||active?.id!==context.sessionId)return toast("이 파일을 선택한 업무에서 다시 열어 주세요.");if(boot.demo)return;try{await api("/api/open",{id:context.sessionId,path:context.path,action});}catch(e){toast(e.message);}}
async function preview(path){if(!active)return;const id=active.id,ticket=++previewGeneration,data=await api(`/api/preview?id=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}`);if(active?.id!==id||ticket!==previewGeneration)return;previewPath=path;previewContext={sessionId:id,path};$("preview-title").textContent=data.name;$("preview-content").replaceChildren();if(data.kind==="image"){const img=el("img");img.src=data.data;img.alt=data.name;$("preview-content").append(img);}else if(data.kind==="html"){const frame=el("iframe");frame.title=data.name;frame.className="html-preview";frame.setAttribute("sandbox","");frame.setAttribute("referrerpolicy","no-referrer");frame.srcdoc=data.html;$("preview-content").append(el("p",data.message,"composer-note"),frame);}else if(data.kind==="text"){const text=el("div",null,"message-body preview-text");renderText(text,data.text);$("preview-content").append(text);}else $("preview-content").append(el("p",data.message));$("open-file").hidden=$("reveal-file").hidden=!!boot.demo;$("open-text-file").hidden=!!boot.demo||!(/\.(md|txt|csv|tsv|html?)$/i.test(path));$("external-html-note").hidden=!/\.html?$/i.test(path);showDialog("preview-dialog");}
function folderMode(){const managed=$("folder-mode-new").checked;$("folder-existing-fields").hidden=managed;$("folder-new-fields").hidden=!managed;$("folder-input").required=!managed;$("new-workspace-location").textContent=managedRootChoice||boot.managedWorkspaceRoot||boot.workspaceLocationError||"저장 위치를 선택해 주세요.";$("managed-location-note").textContent="이 위치 아래에 이번 업무 전용 폴더를 만듭니다. 기존 업무는 이동하지 않아요.";}
function chooseFolder(resume=false){$("folder-title").textContent=resume?"이전 업무 폴더에서 이어갈까요?":"새 업무를 시작해 볼까요?";$("folder-description").textContent=resume?"기존 대화와 폴더를 그대로 사용합니다. 이 폴더의 Claude 설정·후크·MCP 실행을 확인해 주세요.":"이름을 정하면, 대화와 결과를 함께 모아둘게요.";$("folder-dialog").setAttribute("aria-label",resume?"기존 업무 폴더 확인":"새 업무 시작");$("confirm-folder-label").textContent=resume?"확인하고 이어가기":"업무 시작";folderChoiceGeneration++;managedRootChoice=null;$("folder-input").value=active?.workspace||boot.defaultWorkspace||"";$("trust").checked=false;$("folder-form").dataset.resume=resume?"yes":"no";$("folder-form").dataset.afterTrust="";$("folder-input").readOnly=resume;$("browse-folder").disabled=resume;$("choose-managed").disabled=resume;$("folder-mode-new").disabled=$("folder-mode-existing").disabled=resume;$("folder-mode-new").checked=!resume;$("folder-mode-existing").checked=resume;$("task-name").value=resume?active.title:"";$("task-name").disabled=resume;folderMode();showDialog("folder-dialog");}
async function submit(){
  if(sending||choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||globalThis.WorkspaceAttachments?.isUploading()||globalThis.WorkspaceWorkflow?.isSubmitting()||appClosed)return;
  if(busyStates.has(active?.state)){if(globalThis.WorkspaceWorkflow)return WorkspaceWorkflow.send("enqueue");return;}
  if(!$("prompt").value.trim())return $("prompt").focus();
  if(/^\/effort(?:\s|$)/u.test($("prompt").value.trim())){
    if(globalThis.WorkspaceInlineControls)await WorkspaceInlineControls.handleCommand($("prompt").value);
    else toast("Effort 선택 화면을 불러오지 못했어요. 앱을 다시 열어 주세요. 요청은 전송하지 않았습니다.");
    return;
  }
  if(!active)return chooseFolder();if(!active.trusted)return chooseFolder(true);
  if(globalThis.WorkspaceComposer?.beforeSubmit()===false)return;
  const sid=active.id,text=$("prompt").value.trim(),files=[...attachments];sending=true;setStatus(active.state);renderAttachments();error("");
  $("conversation").querySelector(".conversation-empty")?.remove();
  const pending=renderMessage({role:"user",text,files});pending.classList.add("pending");pending.querySelector(".message-label").textContent="전송 중";globalThis.WorkspaceStream?.jump();
  try{
    await api("/api/send",{id:sid,text,attachments:files,trusted:active.trusted});
    pending.classList.remove("pending");pending.querySelector(".message-label").textContent="나";drafts.delete(sid);
    if(active?.id===sid){$("prompt").value="";attachments=[];renderAttachments();await refreshSessionMeta();}
    // Keep the existing event cursor and streamed text. Re-selecting here could
    // discard partial messages received before the POST acknowledgement.
  }catch(e){pending.remove();error(e.message);}finally{sending=false;setStatus(active?.state||"idle");renderAttachments();}
}
$("composer").onsubmit=e=>{e.preventDefault();submit();};$("prompt").onkeydown=e=>{if(e.defaultPrevented||e.isComposing||e.keyCode===229)return;if(globalThis.WorkspaceComposer?.keydown(e))return;if(globalThis.WorkspaceInlineControls?.keydown(e))return;if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){e.preventDefault();return submit();}};
$("folder-form").onsubmit=async e=>{if(e.submitter?.value!=="ok")return;e.preventDefault();if(!$("trust").checked)return;const button=e.submitter;button.disabled=true;try{if($("folder-form").dataset.resume==="yes"){const id=active.id;await api("/api/trust",{id,trusted:true});if(active?.id!==id)return;active.trusted=true;}else{const draft={text:$("prompt").value,attachments:[...attachments]},managed=$("folder-mode-new").checked;if(managed&&!managedRootChoice&&!boot.managedWorkspaceRoot)throw Error("새 업무를 저장할 위치를 먼저 선택해 주세요.");const item=await api("/api/create",{workspace:managed?undefined:$("folder-input").value,managed,managedRoot:managed?managedRootChoice||undefined:undefined,title:$("task-name").value.trim()||undefined,trusted:true});sessions.unshift(item);drafts.set(item.id,draft);await selectSession(item.id);drafts.delete("home");}$("folder-dialog").close();if($("folder-form").dataset.afterTrust==="choice"){renderWorkspaceChoice();toast("폴더 확인을 마쳤어요. 원하는 디자인을 선택해 주세요.");}else if($("folder-form").dataset.afterTrust==="commands"){await globalThis.WorkspaceComposer?.prepareConnection();}else if($("folder-form").dataset.afterTrust==="schedule"){globalThis.WorkspaceWorkflow?.openEditor("schedule");}else if($("folder-form").dataset.afterTrust==="controls"){await globalThis.WorkspaceInlineControls?.resumeAfterTrust();}else if($("prompt").value.trim())await submit();}catch(e){toast(e.message);}finally{button.disabled=false;}};
$("folder-mode-new").onchange=$("folder-mode-existing").onchange=folderMode;
async function browseWorkspace(managed){const button=$(managed?"choose-managed":"browse-folder"),label=button.textContent,ticket=folderChoiceGeneration;button.disabled=true;button.textContent="선택 창 열림…";try{const initial=managed?managedRootChoice||boot.defaultWorkspace:$("folder-input").value||boot.defaultWorkspace;const d=await api("/api/pick",{kind:"folder",initialDirectory:initial||undefined});if(ticket!==folderChoiceGeneration||!$("folder-dialog").open)return;if(d.paths.length){if(managed){managedRootChoice=d.paths[0];folderMode();}else $("folder-input").value=d.paths[0];$("trust").checked=false;}}catch(e){toast(e.message);}finally{button.textContent=label;button.disabled=$("folder-form").dataset.resume==="yes";if($("folder-dialog").open&&ticket===folderChoiceGeneration)button.focus();}}
$("browse-folder").onclick=()=>browseWorkspace(false);
$("choose-managed").onclick=()=>browseWorkspace(true);
$("attach").onclick=async()=>{if(attachmentPicking||sending||choiceSubmission||appClosed)return;const ticket=selectionGeneration,sid=active?.id||null;attachmentPicking=true;$("attach").disabled=true;try{toast("파일 선택 창을 열고 있어요.");const d=await api("/api/pick",{kind:"files"});if(ticket!==selectionGeneration||sid!==(active?.id||null)||appClosed)return;attachments=[...new Set([...attachments,...d.paths])].slice(0,12);renderAttachments();saveDraft();}catch(e){if(ticket===selectionGeneration&&sid===(active?.id||null))error(e.message);}finally{attachmentPicking=false;setStatus(active?.state||"idle");}};
$("attach-path").onclick=()=>{pathInputContext={id:active?.id||null,generation:selectionGeneration};showDialog("path-dialog");};$("path-form").onsubmit=e=>{if(e.submitter?.value!=="ok")return;if(!pathInputContext||pathInputContext.id!==(active?.id||null)||pathInputContext.generation!==selectionGeneration)return;const p=$("path-input").value.trim().replace(/^"|"$/g,"");if(p)attachments=[...new Set([...attachments,p])].slice(0,12);renderAttachments();saveDraft();$("path-input").value="";};
$("tasks-open").onclick=()=>{$("task-search").value="";renderAllSessions();showDialog("tasks-dialog");$("task-search").focus();};$("tasks-close").onclick=()=>$("tasks-dialog").close();$("task-search").oninput=renderAllSessions;
$("new-chat").onclick=()=>{showHome(true);chooseFolder();};$("home-button").onclick=()=>showHome();$("session-search").oninput=renderSessions;
$("choose-folder").onclick=$("workspace-button").onclick=$("workspace-summary").onclick=()=>chooseFolder(!!active);
document.querySelectorAll(".task-card,[data-prompt].prompt-shortcut").forEach(button=>button.onclick=()=>{$("prompt").value=button.dataset.prompt;$("prompt").focus();});
$("stop").onclick=async()=>{try{await api("/api/stop",{id:active.id});toast("중지 요청을 보냈어요. 완료된 파일 변경은 유지됩니다.");refreshResults();}catch(e){error(e.message);}};
$("refresh-files").onclick=()=>{refreshFiles();refreshResults();};$("files-tab").onclick=()=>setPanel("sources");$("results-tab").onclick=()=>setPanel("results");
$("materials-button").onclick=()=>document.querySelector(".inspector").classList.add("open");$("close-materials").onclick=()=>document.querySelector(".inspector").classList.remove("open");
$("close-preview").onclick=closePreview;$("preview-dialog").oncancel=closePreview;$("open-file").onclick=()=>openFileAction("open");$("reveal-file").onclick=()=>openFileAction("reveal");$("open-text-file").onclick=()=>openFileAction("text");
$("help").onclick=()=>showDialog("help-dialog");$("close-help").onclick=()=>$("help-dialog").close();
function openSettings(){$("hide-window").hidden=boot.window?.hideSupported!==true;$("window-behavior-note").textContent=boot.window?.hideSupported===true?"창을 닫아도 진행 중인 업무와 예약은 계속됩니다. 트레이로 보내면 이 창을 숨기고 나중에 다시 열 수 있습니다. 작업을 멈추려면 ‘완전히 종료’를 선택하세요.":"창을 닫아도 진행 중인 업무와 예약은 계속됩니다. 실행기로 다시 열 수 있습니다. 작업을 멈추고 앱을 종료하려면 ‘완전히 종료’를 선택하세요.";$("model-input").value=active?.modelOverride||"";renderConnection(active?.connection);$("model-select").value=modelOptions().some(item=>item.value===$("model-input").value)?$("model-input").value:"";$("permission-mode-select").value=active?.connection?.permissionModeOverride||"";updatePermissionControls();showDialog("settings-dialog");}
$("settings-open").onclick=$("connection-settings").onclick=openSettings;$("settings-close").onclick=()=>$("settings-dialog").close();
async function reconnect(){const buttons=[$("reconnect"),$("settings-refresh")];buttons.forEach(b=>b.disabled=true);try{const response=await api("/api/reconnect",active?{id:active.id}:{});boot=response;sessions=response.sessions;renderConnection(active?.connection);renderSessions();if(boot.error){error(boot.error);return;}error("");toast("실행 연결을 다시 확인했어요. 이전 요청은 다시 보내지 않았습니다.");if(active)await selectSession(active.id);else setStatus("idle");}catch(e){error(e.message);}finally{buttons.forEach(b=>b.disabled=false);}}
$("reconnect").onclick=$("settings-refresh").onclick=reconnect;
async function setModel(model){
  if(connectionLocked()||!active?.connection?.capabilities?.setModel)return null;
  const id=active.id,ticket=selectionGeneration,stillCurrent=()=>active?.id===id&&selectionGeneration===ticket&&!appClosed;
  modelChanging=true;setStatus(active.state);let failure="";
  try{
    const response=await api("/api/model",{id,model});if(!stillCurrent())return null;
    applyConnectionState(response);renderConnection(active.connection);$("model-input").value=active.modelOverride||"";
    $("model-select").value=modelOptions().some(item=>item.value===$("model-input").value)?$("model-input").value:"";
    toast("이 연결의 모델을 변경했어요. 기존 기본 설정은 유지됩니다.");return {ok:true,response};
  }catch(e){if(!stillCurrent())return null;failure=e.message;return {ok:false,error:failure};}
  finally{modelChanging=false;setStatus(active?.state||"idle");if(stillCurrent()&&failure)$("model-message").textContent=failure;}
}
$("model-apply").onclick=()=>{const model=$("model-input").value.trim();if(!model)return toast("회사에서 사용할 수 있는 모델 이름을 입력해 주세요.");setModel(model);};$("model-reset").onclick=()=>setModel(null);
$("model-select").onchange=()=>{if($("model-select").value)$("model-input").value=$("model-select").value;else $("model-input").focus();};
$("model-input").oninput=()=>{$("model-select").value=modelOptions().some(item=>item.value===$("model-input").value)?$("model-input").value:"";};
async function setPermissionMode(mode){
  if(connectionLocked()||(!active?.connection?.capabilities?.setPermissionMode&&mode!==null))return null;
  if(mode!==null)mode=permissionOptionValue(mode);
  if(mode===null&&active.connection?.permissionModeResetAvailable===false)return {ok:false,error:"변경 전 승인 모드를 확인하지 못해 복원할 수 없어요. 목록에서 지원하는 모드를 선택해 주세요."};
  if(mode!==null&&!permissionOptions().some(item=>item.value===mode)){const message="현재 연결이 제공하는 승인 모드를 선택해 주세요.";toast(message);return {ok:false,error:message};}
  const id=active.id,ticket=selectionGeneration,stillCurrent=()=>active?.id===id&&selectionGeneration===ticket&&!appClosed;
  let bypassConfirmed=false;
  if(mode==="bypassPermissions"){
    bypassConfirmed=await confirmAction({title:"⚠ Bypass 모드를 사용할까요?",message:"Claude가 파일 수정과 명령 실행의 승인 확인을 생략합니다. 이 업무 폴더와 연결된 도구를 신뢰할 때만 사용하세요. 기존 거절 규칙·회사 정책은 CLI가 적용합니다. 현재 앱에서 이 업무를 이어가는 동안 적용하며, 개인 기본 설정은 바꾸지 않습니다.",confirmLabel:"위험을 이해하고 Bypass 사용",danger:true});
    if(!bypassConfirmed||!stillCurrent())return null;
  }
  permissionChanging=true;setStatus(active.state);let failure="";
  try{
    const response=await api("/api/permission-mode",{id,mode,...(bypassConfirmed?{bypassConfirmed:true}:{})});if(!stillCurrent())return null;
    applyConnectionState(response);renderConnection(active.connection);$("permission-mode-select").value=active.connection?.permissionModeOverride||"";
    toast(response.reconnectRequired?"다음 요청에서 기존 승인 설정으로 다시 연결합니다.":"이 연결의 승인 모드를 변경했어요. 기존 설정은 유지됩니다.");return {ok:true,response};
  }catch(e){if(!stillCurrent())return null;failure=e.message;return {ok:false,error:failure};}
  finally{permissionChanging=false;setStatus(active?.state||"idle");if(stillCurrent()&&failure)$("permission-mode-message").textContent=failure;}
}
$("permission-mode-select").onchange=updatePermissionControls;
$("permission-mode-apply").onclick=()=>setPermissionMode($("permission-mode-select").value||null);
$("permission-mode-reset").onclick=()=>setPermissionMode(null);
async function setEffort(effort){
  if(connectionLocked())return null;
  const info=active.connection||{};
  if(effort!==null&&(!info.capabilities?.setEffort||!effortOptions().some(item=>item.value===effort)))return {ok:false,error:"현재 모델에서 지원하는 Effort 값을 선택해 주세요."};
  if(effort===null&&!info.effortOverride)return {ok:true,response:{alreadyInherited:true}};
  if(effort===null&&info.effortResetAvailable===false)return {ok:false,error:"변경 전 Effort를 확인하지 못해 auto로 복원할 수 없어요. 지원하는 수준을 직접 선택해 주세요."};
  const id=active.id,ticket=selectionGeneration,stillCurrent=()=>active?.id===id&&selectionGeneration===ticket&&!appClosed;
  effortChanging=true;setStatus(active.state);
  try{
    const response=await api("/api/effort",{id,effort});if(!stillCurrent())return null;
    applyConnectionState(response);renderConnection(active.connection);return {ok:true,response};
  }catch(e){return stillCurrent()?{ok:false,error:e.message}:null;}
  finally{effortChanging=false;setStatus(active?.state||"idle");}
}
$("task-title").onclick=()=>{if(!active)return;$("rename-input").value=active.title;showDialog("rename-dialog");};
async function updateSession(id,change){
  const pinning=typeof change.pinned==="boolean";
  if(pinning&&(sessionOrderSaving||sessionDragId))throw new Error("업무 순서 변경을 마친 뒤 고정해 주세요.");
  if(pinning){sessionOrderSaving=true;renderSessions();}
  try{const response=await api("/api/session/update",{id,...change}),updated=response.session||response,metadata={title:updated.title,pinned:updated.pinned,updated:updated.updated};sessions=sessions.map(s=>s.id===id?{...s,...metadata}:s);if(active?.id===id){Object.assign(active,metadata);taskHeader();}renderSessions();return updated;}
  finally{if(pinning){sessionOrderSaving=false;renderSessions();}}
}
$("rename-form").onsubmit=async e=>{if(e.submitter?.value!=="ok")return;e.preventDefault();const id=active?.id;if(!id)return;try{await updateSession(id,{title:$("rename-input").value.trim()});$("rename-dialog").close();}catch(e){toast(e.message);}};
$("task-pin").onclick=async()=>{if(!active)return;const id=active.id,pinned=!active.pinned;try{await updateSession(id,{pinned});}catch(e){toast(e.message);}};
let nativeOpening=false;
$("native").onclick=async()=>{
  if(!active)return toast("먼저 업무를 선택해 주세요.");
  if(nativeOpening||appClosed)return;
  if(busyStates.has(active.state)||sending||modelChanging||permissionChanging||effortChanging||connectionPreparing)return toast("진행 중인 요청을 마치거나 중지한 뒤 원본 Claude Code를 열어 주세요.");
  const id=active.id,ticket=selectionGeneration,stillCurrent=()=>active?.id===id&&selectionGeneration===ticket&&!appClosed;
  nativeOpening=true;$("native").disabled=true;saveDraft();
  try{
    await api("/api/stop",{id});
    const deadline=Date.now()+30000;
    while(stillCurrent()){
      const session=await api(`/api/session?id=${encodeURIComponent(id)}`);
      if(!stillCurrent())return;
      // `connected:false` or a previous stopped task state is not cleanup
      // evidence. The server confirms that the owned child has been reaped.
      if(session.connectionStopped===true){
        await api("/api/native",{id});
        if(stillCurrent())toast("기존 Claude 창을 열었어요. 작성 중인 입력은 앱에 남아 있습니다.");
        return;
      }
      if(Date.now()>=deadline)throw new Error("기존 연결의 종료를 아직 확인하지 못했습니다. 잠시 후 원본 Claude Code 열기를 다시 눌러 주세요.");
      await new Promise(resolve=>setTimeout(resolve,200));
    }
  }catch(e){if(stillCurrent())toast(e.message);}
  finally{nativeOpening=false;$("native").disabled=appClosed;}
};
$("hide-window").onclick=async()=>{if(appClosed||boot.window?.hideSupported!==true)return;$("hide-window").disabled=true;saveDraft();try{const result=await api("/api/window/hide",{});if(result.hidden!==true)throw Error(result.message||"이 창을 숨길 수 없습니다. 창을 닫아도 업무는 계속됩니다.");$("settings-dialog").close();}catch(e){toast(e.message);}finally{$("hide-window").disabled=false;}};
$("quit").onclick=async()=>{
  if(quitting||!await confirmAction({title:"앱을 완전히 종료할까요?",message:"진행 중인 업무를 중지하고 앱 연결을 종료합니다. 앱이 꺼져 있는 동안에는 예약도 실행되지 않습니다. 이미 만들어진 파일은 유지됩니다.",confirmLabel:"완전히 종료",danger:true}))return;
  quitting=true;appClosed=true;$("quit").disabled=true;$("settings-dialog").close();
  globalThis.WorkspaceCapabilities?.close();
  globalThis.WorkspaceAttention?.stop();globalThis.WorkspaceProductivityActions?.close();globalThis.WorkspacePalette?.close();globalThis.WorkspaceStream?.reset();globalThis.WorkspaceComposer?.close();closePreview();
  if(pollController)pollController.abort();
  setStatus(active?.state||"idle","앱을 종료하고 있어요");
  error("업무 연결을 정리하고 있어요. 종료 완료 안내가 나올 때까지 잠시 기다려 주세요.");
  try{
    const result=await api("/api/quit",{});
    const confirmed=result.closed===true;
    $("status-text").textContent=confirmed?"앱 종료 완료":"종료 요청 전달됨";
    error(confirmed?"앱 종료가 완료됐어요. 이 창을 닫고 실행기로 다시 열 수 있습니다.":"종료를 요청했어요. 이전 버전은 완료 상태를 알려주지 않습니다. 잠시 후 실행기로 다시 열어 주세요.");
  }catch(e){
    $("status-text").textContent="종료 확인 필요";
    error("종료 완료를 확인하지 못했어요. 설정에서 앱 종료를 다시 누르거나 잠시 후 실행기로 다시 열어 주세요.");
    $("quit").disabled=false;
  }finally{quitting=false;}
};
async function init(){try{boot=await api("/api/bootstrap");sessions=boot.sessions;$("demo-banner").hidden=!boot.demo;renderConnection(null);renderSessions();taskHeader();setPanel("sources");setStatus("idle");globalThis.WorkspaceAttention?.start();if(boot.historyWarning)error(boot.historyWarning);if(boot.error)error(boot.error);}catch(e){error(e.message);$("send").disabled=true;}}
document.querySelectorAll('button[value="cancel"]').forEach(b=>b.setAttribute("formnovalidate",""));document.addEventListener("keydown",e=>{if(e.key==="Escape")document.querySelector(".inspector").classList.remove("open");});
init();
