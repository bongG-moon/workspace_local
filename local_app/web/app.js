"use strict";
const $ = id => document.getElementById(id);
const key = new URLSearchParams(location.hash.slice(1)).get("token");
if (key) { sessionStorage.setItem("workspaceToken", key); history.replaceState(null, "", "/"); }
const token = sessionStorage.getItem("workspaceToken") || "";
let active = null, sessions = [], attachments = [], pollController = null, boot = {}, started = null, previewPath = null;
let selectionGeneration = 0, sending = false, appClosed = false, quitting = false;
const drafts = new Map(), streaming = new Map();
const modalStack = [];
let pendingConfirmation = null;
const busyStates = new Set(["starting", "running", "approval", "question"]);
const statusLabels = {idle:"준비됐어요. 원하는 일을 알려 주세요", starting:"기존 업무 환경에 연결하고 있어요", running:"업무를 진행하고 있어요", approval:"실행 전 확인이 필요해요", question:"다음 단계에 필요한 답변을 기다려요", done:"요청을 마쳤어요. 결과를 확인하거나 이어서 요청하세요", error:"잠시 멈췄어요. 연결 상태를 확인해 주세요", stopped:"작업을 멈췄어요. 이미 변경된 파일은 유지됩니다"};
const stateNames = {idle:"대기", starting:"준비 중", running:"진행 중", approval:"승인 대기", question:"답변 대기", done:"완료", error:"확인 필요", stopped:"중지"};
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
  if(active)active.state=state;const busy=busyStates.has(state),running=state==="starting"||state==="running";
  $("status-text").textContent=label||statusLabels[state]||"진행 상태를 확인하고 있어요";$("status").classList.toggle("busy",running);$("status").dataset.state=state;
  $("send").disabled=busy||sending||!!boot.error||appClosed;$("stop").hidden=!busy;$("task-title").disabled=!active||busy;$("task-pin").disabled=!active;
  $("prompt").readOnly=sending||appClosed;$("attach").disabled=$("attach-path").disabled=sending||appClosed;
  if(running&&!started)started=Date.now();if(!running)started=null;if(active){const row=sessions.find(s=>s.id===active.id);if(row)row.state=state;renderSessions();}updateModelControls();
}
setInterval(()=>{$("elapsed").textContent=["question","approval"].includes(active?.state)?"응답 대기":started?`${Math.floor((Date.now()-started)/1000)}초`:"";},1000);
function when(ts){if(!ts)return "";const d=new Date(ts*1000);return d.toDateString()===new Date().toDateString()?d.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"}):d.toLocaleDateString("ko-KR",{month:"short",day:"numeric"});}
function orderedSessions(){return [...sessions].sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||(b.updated||b.created||0)-(a.updated||a.created||0));}
function renderSessions(){
  const query=$("session-search").value.trim().toLocaleLowerCase(),items=orderedSessions().filter(s=>(s.title+" "+s.workspace).toLocaleLowerCase().includes(query));
  $("sessions").replaceChildren();$("home-recents").replaceChildren();
  for(const item of items){const b=el("button",null,"session"+(item.id===active?.id?" active":""));b.type="button";b.setAttribute("aria-label",`${item.title} · ${stateNames[item.state]||"이어하기"}`);if(item.id===active?.id)b.setAttribute("aria-current","page");b.append(el("span",item.pinned?"고정":"업무","session-kicker"),el("strong",item.title,"session-title"));const meta=el("span",null,"session-meta");meta.append(el("span",stateNames[item.state]||"대기"),el("time",when(item.updated||item.created)));b.append(meta);b.title=item.workspace;b.onclick=()=>selectSession(item.id).catch(e=>error(e.message));$("sessions").append(b);}
  if(!items.length)$("sessions").append(el("p",query?"찾는 업무가 없어요":"시작한 업무가 여기에 모여요","sidebar-empty"));
  for(const item of items.slice(0,4)){const b=el("button",null,"recent-card");b.type="button";b.append(el("span",item.pinned?"고정한 업무":"이어서 하기","recent-kicker"),el("strong",item.title),el("span",`${basename(item.workspace)} · ${when(item.updated||item.created)}`,"recent-meta"),el("span",stateNames[item.state]||"대기","recent-status"));b.onclick=()=>selectSession(item.id).catch(e=>error(e.message));$("home-recents").append(b);}
  if(!items.length)$("home-recents").append(el("p",query?"검색어를 바꾸어 다시 찾아보세요.":"첫 업무를 시작하면, 다음에 이곳에서 이어갈 수 있어요.","empty-recents"));
  $("tasks-open").hidden=!sessions.length;renderAllSessions();
}
function renderAllSessions(){
  const query=$("task-search").value.trim().toLocaleLowerCase(),items=orderedSessions().filter(s=>(s.title+" "+s.workspace).toLocaleLowerCase().includes(query));
  $("all-sessions").replaceChildren();$("tasks-empty").hidden=!!items.length;
  for(const item of items){const button=el("button",null,"session all-session"),meta=el("span",null,"session-meta");button.type="button";button.title=item.workspace;button.append(el("span",item.pinned?"고정한 업무":"업무","session-kicker"),el("strong",item.title,"session-title"),el("span",item.workspace,"all-session-path"));meta.append(el("span",stateNames[item.state]||"대기"),el("time",when(item.updated||item.created)));button.append(meta);if(item.id===active?.id)button.setAttribute("aria-current","page");button.onclick=async()=>{const pending=selectSession(item.id),ticket=selectionGeneration;try{if(await pending&&ticket===selectionGeneration)$("tasks-dialog").close();}catch(e){if(ticket===selectionGeneration)toast(e.message);}};$("all-sessions").append(button);}
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
function renderMessage(message){
  const sk=streamKey(message),existing=message.messageId?streaming.get(sk):null;
  if(existing){renderText(existing.querySelector(".message-body"),message.text);existing.classList.remove("streaming");streaming.delete(sk);return existing;}
  const article=el("article",null,`message ${message.role}`);article.append(el("div",message.role==="user"?"나":"WORKSPACE","message-label"));const body=el("div",null,"message-body");renderText(body,message.text);article.append(body);if(message.files?.length)article.append(el("div",message.files.map(basename).join(" · "),"message-files"));$("conversation").append(article);return article;
}
function renderDelta(data){const sk=streamKey(data);let article=streaming.get(sk);if(!article){article=renderMessage({role:"assistant",text:""});article.classList.add("streaming");article.dataset.streamText="";streaming.set(sk,article);}article.dataset.streamText+=data.text;article.querySelector(".message-body").textContent=article.dataset.streamText;}
function renderConnection(info){
  const connected=info && info.connected!==false;
  const names=list=>(Array.isArray(list)?list:[]).map(x=>typeof x==="string"?x:x?.name||x?.id||"이름 미제공").join(", ");
  $("settings-runtime").textContent=boot.runtime?`실행 위치: ${boot.runtime.entry}\n설정 위치: ${boot.runtime.configRoot}`:"실행 위치를 아직 확인하지 않았어요.";
  $("settings-status").textContent=boot.demo?"화면 체험 연결 · 실제 AI 호출 없음":boot.error?"기존 Claude 연결 확인이 필요해요":connected?"이 업무의 Claude 연결이 확인됐어요":info?"이전 연결이 종료됐어요. 다음 요청에서 다시 연결합니다":"실행 파일 확인 완료 · 실제 응답은 업무를 시작한 뒤 확인합니다";
  $("connection-badge").textContent=boot.demo?"화면 체험":boot.error?"연결 확인 필요":connected?"업무 연결됨":info?"다음 요청 대기":"기존 Claude 연결";
  $("diagnostics").textContent=info?`현재 모델: ${info.model||"미제공"}\n사용 가능한 스킬: ${names(info.skills)||"CLI 목록 미제공"}\n연결 도구: ${(info.mcp||[]).map(x=>`${x.name}: ${x.status}`).join(", ")||"CLI 목록 미제공"}\n플러그인: ${names(info.plugins)||"CLI 목록 미제공"}`:"업무를 시작하면 현재 모델과 연결 도구를 표시해요.";updateModelControls();
}
function updateModelControls(){const capable=!!active?.connection?.capabilities?.setModel,disabled=!active||busyStates.has(active.state)||!capable;$("model-apply").disabled=disabled;$("model-reset").disabled=disabled||!active?.modelOverride;$("model-message").textContent=!active?"업무를 시작하면 이 대화에서 사용할 모델을 확인할 수 있어요.":!capable?"현재 연결에서 모델 변경을 확인하지 못했어요. 기존 모델을 그대로 사용합니다.":busyStates.has(active.state)?"진행 중인 요청이 끝나면 모델을 바꿀 수 있어요.":`현재: ${active.connection?.model||"기존 모델"} · 이 연결에만 적용하며 회사 기본 설정은 바꾸지 않아요.`;}
function saveDraft(){drafts.set(active?.id||"home",{text:$("prompt").value,attachments:[...attachments]});}
function restoreDraft(id){const draft=drafts.get(id||"home");$("prompt").value=draft?.text||"";attachments=[...(draft?.attachments||[])];renderAttachments();}
function taskHeader(){$("chat-title").textContent=globalThis.WorkspaceCapabilities?.isOpen()?"스킬·도구":active?.title||"업무 홈";$("task-title").hidden=$("task-pin").hidden=!active;$("task-pin").setAttribute("aria-pressed",String(!!active?.pinned));$("task-pin").setAttribute("aria-label",active?.pinned?"업무 고정 해제":"업무 고정");$("workspace-summary").textContent=active?basename(active.workspace):"자료와 결과를 한곳에서 관리해요";$("workspace-summary").title=active?.workspace||"새 업무 공간 선택";$("folder-name").textContent=active?basename(active.workspace):"업무 공간";$("folder-path").textContent=active?.workspace||"시작할 때 새 공간을 만들거나 기존 폴더를 선택하세요.";$("home-button").setAttribute("aria-current",active||globalThis.WorkspaceCapabilities?.isOpen()?"false":"page");document.querySelector(".app").classList.toggle("task-open",!!active);}
async function selectSession(id,{keepDraft=false}={}){
  globalThis.WorkspaceCapabilities?.close();
  if(!keepDraft)saveDraft();const ticket=++selectionGeneration;if(pollController)pollController.abort();streaming.clear();const item=await api(`/api/session?id=${encodeURIComponent(id)}`);if(ticket!==selectionGeneration)return false;active=item;started=null;error("");
  $("conversation").replaceChildren();$("requests").replaceChildren();$("activity").replaceChildren();active.messages.forEach(renderMessage);(active.requests||[]).forEach(renderRequest);$("welcome").hidden=true;$("conversation").hidden=false;if(!active.messages.length)$("conversation").append(el("p","업무 공간이 준비됐어요. 자료를 선택하거나 바로 요청해 보세요.","conversation-empty"));
  taskHeader();if(!keepDraft)restoreDraft(id);renderConnection(active.connection);setStatus(active.state);renderSessions();refreshFiles();refreshResults();pollController=new AbortController();poll(id,active.seq||0,pollController.signal);return true;
}
function showHome(clear=false){globalThis.WorkspaceCapabilities?.close();saveDraft();selectionGeneration++;if(pollController)pollController.abort();active=null;started=null;streaming.clear();if(clear)drafts.delete("home");restoreDraft("home");$("welcome").hidden=false;$("conversation").hidden=true;$("requests").replaceChildren();$("activity").replaceChildren();$("files").replaceChildren();$("file-count").textContent="0";$("results-list").replaceChildren();$("result-count").textContent="0";$("empty-results").hidden=false;taskHeader();error("");setStatus("idle");renderConnection(null);renderSessions();}
async function poll(id,after,signal){while(!signal.aborted&&active?.id===id){try{const result=await api(`/api/events?id=${encodeURIComponent(id)}&after=${after}`,undefined,signal);if(signal.aborted||active?.id!==id)return;for(const event of result.events){handleEvent(event);after=event.seq;}}catch(e){if(e.name==="AbortError")return;error(e.message);return;}}}
function handleEvent(event){
  const d=event.data,area=$("work-area"),nearBottom=area.scrollHeight-area.scrollTop-area.clientHeight<120;
  if(["assistant","assistant_delta"].includes(event.type)){$("conversation").querySelector(".conversation-empty")?.remove();event.type==="assistant"?renderMessage({role:"assistant",...d}):renderDelta(d);}
  if(event.type==="status"){setStatus(d.state,d.label);if(d.connection){active.connection=d.connection;renderConnection(d.connection);}if(d.state==="stopped"){$("requests").replaceChildren();for(const node of streaming.values()){node.classList.remove("streaming");node.append(el("small","중지 전까지 받은 내용","message-interrupted"));}streaming.clear();refreshFiles();refreshResults(true);}}
  if(event.type==="connected"){active.connection=d;active.sessionId=d.sessionId;renderConnection(d);}
  if(event.type==="model_changed"){active.modelOverride=d.modelOverride;if(active.connection)active.connection.model=d.model;renderConnection(active.connection);}
  if(event.type==="request"){renderRequest(d);setStatus(d.tool==="AskUserQuestion"?"question":"approval");}
  if(event.type==="request_closed"){for(const n of $("requests").children)if(n.dataset.requestId===d.id)n.remove();if(d.state)setStatus(d.state);else if(!$("requests").children.length&&busyStates.has(active?.state))setStatus("running");}
  if(event.type==="activity"){const labels={Skill:"작업 방식 확인",Read:"자료 읽기",Bash:"업무 도구 실행",Write:"파일 작성",Edit:"파일 수정",Agent:"추가 작업 진행",Task:"추가 작업 진행",AskUserQuestion:"질문 준비"};$("activity").append(el("li",(labels[d.tool]||d.tool||"업무 진행")+(d.skill?" · "+d.skill:"")));while($("activity").children.length>40)$("activity").firstChild.remove();}
  if(event.type==="result"){setStatus("done");refreshFiles();refreshResults(true);if(d.budgetWarning)toast(d.budgetWarning);refreshSessionMeta();}if(event.type==="artifacts")refreshResults(true);
  if(event.type==="error"){error(d.message);setStatus("error");if("resumeSessionId" in d)active.sessionId=d.resumeSessionId;$("requests").replaceChildren();for(const node of streaming.values()){node.classList.remove("streaming");node.append(el("small","연결 중단 전까지 받은 내용","message-interrupted"));}streaming.clear();}if(event.type==="notice")toast(d.message);
  globalThis.WorkspaceCapabilities?.contextChanged(["connected","model_changed","error"].includes(event.type)||(event.type==="status"&&d.state==="stopped"));
  if(!globalThis.WorkspaceCapabilities?.isOpen()&&(nearBottom||event.type==="request"))requestAnimationFrame(()=>{area.scrollTop=area.scrollHeight;});
}
async function refreshSessionMeta(){if(!active)return;const id=active.id;try{const next=await api(`/api/session?id=${encodeURIComponent(id)}`);sessions=sessions.map(s=>s.id===id?next:s);if(active?.id===id){active.updated=next.updated;active.artifactCount=next.artifactCount;active.title=next.title;taskHeader();}renderSessions();}catch(e){toast(e.message);}}
function approvalSummary(request){
  const input=request.input||{},tool=request.tool||"업무 도구",target=input.file_path||input.path||input.notebook_path;
  const known={Read:["자료를 읽습니다","선택한 자료의 내용을 확인하는 요청이에요."],Write:["파일에 내용을 저장합니다","같은 이름의 파일이 있으면 기존 내용이 바뀔 수 있어요."],Edit:["파일 내용을 수정합니다","아래 변경 전후와 대상 파일을 확인해 주세요."],MultiEdit:["파일의 여러 부분을 수정합니다","대상 파일과 상세 변경 내용을 확인해 주세요."],Glob:["파일을 찾습니다","조건에 맞는 파일 경로를 찾는 요청이에요."],Grep:["자료 안에서 내용을 찾습니다","검색 조건에 맞는 내용을 확인하는 요청이에요."]};
  const [title,description]=known[tool]||[tool==="Bash"?"업무 명령을 실행합니다":"연결된 도구를 사용합니다","파일 변경이나 외부 연결이 포함될 수 있어요. 제공된 설명과 실행 원문을 확인해 주세요."];
  const box=el("div",null,"approval-summary");box.append(el("strong",title),el("p",description));if(target){const row=el("div",null,"approval-target");row.append(el("span","대상 파일"),el("strong",basename(target)),el("small",String(target)));box.append(row);}
  const explanation=request.description||input.description;if(explanation)box.append(el("p","도구가 제공한 설명: "+explanation,"approval-description"));
  if(tool==="Edit"&&typeof input.old_string==="string"&&typeof input.new_string==="string"){const changes=el("div",null,"approval-diff");for(const [label,value,cls]of [["변경 전",input.old_string,"change-before"],["변경 후",input.new_string,"change-after"]]){const block=el("section",null,cls);block.append(el("h4",label),el("pre",value));changes.append(block);}box.append(changes);}
  const details=el("details",null,"approval-detail");details.append(el("summary","실행 원문과 세부 내용"),el("pre",JSON.stringify(input,null,2)));if(!known[tool])details.open=true;box.append(details);return box;
}
function renderRequest(request){
  if([...$("requests").children].some(n=>n.dataset.requestId===request.id))return;const card=el("section",null,"request");card.dataset.requestId=request.id;const questions=request.tool==="AskUserQuestion"?request.input.questions:null;card.append(el("span",questions?"YOUR CHOICE":"REVIEW & APPROVE","request-kicker"),el("h3",questions?"어떻게 진행할까요?":"진행 전에 확인해 주세요"));const fields=[];
  if(Array.isArray(questions)&&questions.length){for(const [i,q]of questions.entries()){const field=el("fieldset");field.append(el("legend",q.question));const inputs=[];for(const option of q.options||[]){const label=el("label",null,"option"),input=el("input");input.type=q.multiSelect?"checkbox":"radio";input.name=`${request.id}-${i}`;input.value=option.label;const body=el("span",option.label);if(option.description)body.append(el("small",option.description));label.append(input,body);field.append(label);inputs.push(input);}const custom=el("input");custom.type="text";custom.placeholder="직접 답변 입력";custom.setAttribute("aria-label",q.question+" 직접 입력");field.append(custom);fields.push({q,inputs,custom});card.append(field);}}
  else card.append(approvalSummary(request),el("p","이번 요청에만 적용됩니다.","approval-scope"));
  const actions=el("div",null,"request-actions"),deny=el("button",questions?"답변하지 않기":"거절","quiet-button"),allow=el("button",questions?"답변하고 계속":"확인하고 승인","send-button");deny.type=allow.type="button";const sid=active.id;
  async function answer(yes){const answers={};if(yes&&questions){for(const field of fields){const values=field.inputs.filter(n=>n.checked).map(n=>n.value);if(field.custom.value.trim()){if(!field.q.multiSelect)values.length=0;values.push(field.custom.value.trim());}if(!values.length)return toast("각 질문의 답변을 선택하거나 입력해 주세요.");answers[field.q.question]=values.join(", ");}}allow.disabled=deny.disabled=true;try{await api("/api/respond",{id:sid,requestId:request.id,allow:yes,answers});}catch(e){error(e.message);allow.disabled=deny.disabled=false;}}
  deny.onclick=()=>answer(false);allow.onclick=()=>answer(true);actions.append(deny,allow);card.append(actions);$("requests").append(card);
}
function renderAttachments(){$("attachments").replaceChildren();for(const path of attachments){const chip=el("span",null,"attachment");chip.title=path;chip.append(el("span",basename(path)));const remove=el("button","×");remove.type="button";remove.setAttribute("aria-label",basename(path)+" 첨부 취소");remove.disabled=sending;remove.onclick=()=>{if(sending)return;attachments=attachments.filter(p=>p!==path);renderAttachments();};chip.append(remove);$("attachments").append(chip);}}
function setPanel(panel){$("source-panel").hidden=panel!=="sources";$("results-panel").hidden=panel!=="results";$("files-tab").setAttribute("aria-selected",String(panel==="sources"));$("results-tab").setAttribute("aria-selected",String(panel==="results"));}
async function refreshFiles(){if(!active)return;const id=active.id;try{const result=await api(`/api/files?id=${encodeURIComponent(id)}`);if(active?.id!==id)return;$("files").replaceChildren();$("file-count").textContent=result.files.length;if(!result.files.length)$("files").append(el("p","이 공간에 자료가 아직 없어요. ‘자료 추가’로 다른 위치의 자료도 연결할 수 있어요.","empty-files"));for(const file of result.files){const b=el("button",null,"file"),ext=file.name.split(".").pop().toUpperCase();b.append(el("span",ext.slice(0,4),"file-extension"),el("span",file.name,"file-name"));b.title=file.path;b.onclick=()=>preview(file.path).catch(e=>error(e.message));$("files").append(b);}}catch(e){toast(e.message);}}
async function refreshResults(reveal=false){if(!active)return;const id=active.id;try{const result=await api(`/api/results?id=${encodeURIComponent(id)}`);if(active?.id!==id)return;$("results-list").replaceChildren();const items=(result.artifacts||[]).filter(file=>file.runId===result.lastRunId);$("result-count").textContent=items.length;$("empty-results").hidden=items.length>0;for(const file of items){const card=el("article",null,"result-card");card.append(el("span",file.change==="created"?"새로 확인한 파일":"변경된 파일","result-change"),el("strong",file.name||basename(file.path)),el("p",when(file.observedAt),"result-meta"));card.title=file.path;const actions=el("div",null,"result-actions"),open=el("button","결과 확인","quiet-button"),follow=el("button","이 결과로 요청","text-button");open.onclick=()=>preview(file.path).catch(e=>error(e.message));follow.onclick=()=>{if(sending)return toast("요청 전송이 끝난 뒤 자료를 추가해 주세요.");attachments=[...new Set([...attachments,file.path])].slice(0,12);renderAttachments();$("prompt").focus();toast("요청할 자료에 추가했어요. 원하는 수정 내용을 입력하세요.");};actions.append(open,follow);card.append(actions);$("results-list").append(card);}if(items.length)$("results-list").append(el("p","요청 전후 이 공간에서 확인한 파일 변경입니다. 다른 프로그램의 변경이 포함될 수 있어요.","result-observation"));if(result.observation?.limited||result.observation?.errors)$("results-list").append(el("p","확인 범위에 제한이 있어요. 필요한 파일은 자료 목록에서도 확인하세요.","result-observation"));if(reveal&&items.length)setPanel("results");}catch(e){toast(e.message);}}
async function preview(path){const id=active.id,data=await api(`/api/preview?id=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}`);if(active?.id!==id)return;previewPath=path;$("preview-title").textContent=data.name;$("preview-content").replaceChildren();if(data.kind==="image"){const img=el("img");img.src=data.data;img.alt=data.name;$("preview-content").append(img);}else if(data.kind==="html"){const frame=el("iframe");frame.title=data.name;frame.className="html-preview";frame.setAttribute("sandbox","");frame.setAttribute("referrerpolicy","no-referrer");frame.srcdoc=data.html;$("preview-content").append(el("p",data.message,"composer-note"),frame);}else if(data.kind==="text"){const text=el("div",null,"message-body preview-text");renderText(text,data.text);$("preview-content").append(text);}else $("preview-content").append(el("p",data.message));$("open-file").hidden=/\.html?$/i.test(path)||boot.demo;showDialog("preview-dialog");}
function folderMode(){const managed=$("folder-mode-new").checked;$("folder-existing-fields").hidden=managed;$("folder-new-fields").hidden=!managed;$("folder-input").required=!managed;$("new-workspace-location").textContent=boot.managedWorkspaceRoot||"앱이 관리하는 업무 폴더";}
function chooseFolder(resume=false){$("folder-input").value=active?.workspace||boot.defaultWorkspace||"";$("trust").checked=false;$("folder-form").dataset.resume=resume?"yes":"no";$("folder-input").readOnly=resume;$("browse-folder").disabled=resume;$("folder-mode-new").disabled=$("folder-mode-existing").disabled=resume;$("folder-mode-new").checked=!resume;$("folder-mode-existing").checked=resume;$("task-name").value=resume?active.title:"";$("task-name").disabled=resume;folderMode();showDialog("folder-dialog");}
async function submit(){
  if(sending||busyStates.has(active?.state)||appClosed)return;
  if(!$("prompt").value.trim())return $("prompt").focus();
  if(!active)return chooseFolder();if(!active.trusted)return chooseFolder(true);
  const sid=active.id,text=$("prompt").value.trim(),files=[...attachments];sending=true;setStatus(active.state);renderAttachments();error("");
  $("conversation").querySelector(".conversation-empty")?.remove();
  const pending=renderMessage({role:"user",text,files});pending.classList.add("pending");pending.querySelector(".message-label").textContent="전송 중";
  try{
    await api("/api/send",{id:sid,text,attachments:files,trusted:active.trusted});
    pending.classList.remove("pending");pending.querySelector(".message-label").textContent="나";drafts.delete(sid);
    if(active?.id===sid){$("prompt").value="";attachments=[];renderAttachments();await refreshSessionMeta();}
    // Keep the existing event cursor and streamed text. Re-selecting here could
    // discard partial messages received before the POST acknowledgement.
  }catch(e){pending.remove();error(e.message);}finally{sending=false;setStatus(active?.state||"idle");renderAttachments();}
}
$("composer").onsubmit=e=>{e.preventDefault();submit();};$("prompt").onkeydown=e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){e.preventDefault();submit();}};
$("folder-form").onsubmit=async e=>{if(e.submitter?.value!=="ok")return;e.preventDefault();if(!$("trust").checked)return;const button=e.submitter;button.disabled=true;try{if($("folder-form").dataset.resume==="yes"){await api("/api/trust",{id:active.id,trusted:true});active.trusted=true;}else{const draft={text:$("prompt").value,attachments:[...attachments]},managed=$("folder-mode-new").checked;const item=await api("/api/create",{workspace:managed?undefined:$("folder-input").value,managed,title:$("task-name").value.trim()||undefined,trusted:true});sessions.unshift(item);drafts.set(item.id,draft);await selectSession(item.id);drafts.delete("home");}$("folder-dialog").close();if($("prompt").value.trim())await submit();}catch(e){toast(e.message);}finally{button.disabled=false;}};
$("folder-mode-new").onchange=$("folder-mode-existing").onchange=folderMode;
$("browse-folder").onclick=async()=>{const button=$("browse-folder"),label=button.textContent;button.disabled=true;button.textContent="선택 창 열림…";try{const d=await api("/api/pick",{kind:"folder"});if(d.paths.length)$("folder-input").value=d.paths[0];}catch(e){toast(e.message);}finally{button.textContent=label;button.disabled=$("folder-form").dataset.resume==="yes";if($("folder-dialog").open)$("folder-input").focus();}};
$("attach").onclick=async()=>{try{toast("파일 선택 창을 열고 있어요.");const d=await api("/api/pick",{kind:"files"});attachments=[...new Set([...attachments,...d.paths])].slice(0,12);renderAttachments();}catch(e){error(e.message);}};
$("attach-path").onclick=()=>showDialog("path-dialog");$("path-form").onsubmit=e=>{if(e.submitter?.value!=="ok")return;const p=$("path-input").value.trim().replace(/^"|"$/g,"");if(p)attachments=[...new Set([...attachments,p])].slice(0,12);renderAttachments();$("path-input").value="";};
$("tasks-open").onclick=()=>{$("task-search").value="";renderAllSessions();showDialog("tasks-dialog");$("task-search").focus();};$("tasks-close").onclick=()=>$("tasks-dialog").close();$("task-search").oninput=renderAllSessions;
$("new-chat").onclick=()=>{showHome(true);chooseFolder();};$("home-button").onclick=()=>showHome();$("session-search").oninput=renderSessions;
$("choose-folder").onclick=$("workspace-button").onclick=$("workspace-summary").onclick=()=>chooseFolder(!!active);$("choose-managed").onclick=()=>chooseFolder();
document.querySelectorAll(".task-card,[data-prompt].prompt-shortcut").forEach(button=>button.onclick=()=>{$("prompt").value=button.dataset.prompt;$("prompt").focus();});
$("stop").onclick=async()=>{try{await api("/api/stop",{id:active.id});toast("중지 요청을 보냈어요. 완료된 파일 변경은 유지됩니다.");refreshResults();}catch(e){error(e.message);}};
$("refresh-files").onclick=()=>{refreshFiles();refreshResults();};$("files-tab").onclick=()=>setPanel("sources");$("results-tab").onclick=()=>setPanel("results");
$("materials-button").onclick=()=>document.querySelector(".inspector").classList.add("open");$("close-materials").onclick=()=>document.querySelector(".inspector").classList.remove("open");
$("close-preview").onclick=()=>$("preview-dialog").close();$("open-file").onclick=async()=>{try{await api("/api/open",{id:active.id,path:previewPath});}catch(e){toast(e.message);}};
$("help-learn-open").onclick=()=>{$("help-dialog").close();$("learn-open").click();};
$("help").onclick=()=>showDialog("help-dialog");$("close-help").onclick=()=>$("help-dialog").close();
function openSettings(){$("model-input").value=active?.modelOverride||"";renderConnection(active?.connection);showDialog("settings-dialog");}
$("settings-open").onclick=$("connection-settings").onclick=openSettings;$("settings-close").onclick=()=>$("settings-dialog").close();
async function reconnect(){const buttons=[$("reconnect"),$("settings-refresh")];buttons.forEach(b=>b.disabled=true);try{const response=await api("/api/reconnect",active?{id:active.id}:{});boot=response;sessions=response.sessions;renderConnection(active?.connection);renderSessions();if(boot.error){error(boot.error);return;}error("");toast("실행 연결을 다시 확인했어요. 이전 요청은 다시 보내지 않았습니다.");if(active)await selectSession(active.id);else setStatus("idle");}catch(e){error(e.message);}finally{buttons.forEach(b=>b.disabled=false);}}
$("reconnect").onclick=$("settings-refresh").onclick=reconnect;
async function setModel(model){if(!active)return;const id=active.id;$("model-apply").disabled=$("model-reset").disabled=true;try{const response=await api("/api/model",{id,model});if(active?.id===id){active.modelOverride=response.modelOverride;if(active.connection)active.connection.model=response.session?.connection?.model||active.connection.model;renderConnection(active.connection);$("model-input").value=active.modelOverride||"";}toast("이 연결의 모델을 변경했어요. 기존 기본 설정은 유지됩니다.");}catch(e){$("model-message").textContent=e.message;}finally{if(active?.id===id){const supported=!!active?.connection?.capabilities?.setModel;$("model-apply").disabled=!supported||busyStates.has(active.state);$("model-reset").disabled=!supported||!active.modelOverride;}}}
$("model-apply").onclick=()=>{const model=$("model-input").value.trim();if(!model)return toast("회사에서 사용할 수 있는 모델 이름을 입력해 주세요.");setModel(model);};$("model-reset").onclick=()=>setModel(null);
$("task-title").onclick=()=>{if(!active)return;$("rename-input").value=active.title;showDialog("rename-dialog");};
async function updateSession(id,change){const response=await api("/api/session/update",{id,...change}),updated=response.session||response,metadata={title:updated.title,pinned:updated.pinned,updated:updated.updated};sessions=sessions.map(s=>s.id===id?{...s,...metadata}:s);if(active?.id===id){Object.assign(active,metadata);taskHeader();}renderSessions();return updated;}
$("rename-form").onsubmit=async e=>{if(e.submitter?.value!=="ok")return;e.preventDefault();const id=active?.id;if(!id)return;try{await updateSession(id,{title:$("rename-input").value.trim()});$("rename-dialog").close();}catch(e){toast(e.message);}};
$("task-pin").onclick=async()=>{if(!active)return;const id=active.id,pinned=!active.pinned;try{await updateSession(id,{pinned});}catch(e){toast(e.message);}};
$("native").onclick=async()=>{if(!active)return toast("먼저 업무를 선택해 주세요.");try{await api("/api/native",{id:active.id});toast("기존 Claude 창을 열었어요.");}catch(e){toast(e.message);}};
$("quit").onclick=async()=>{
  if(quitting||!await confirmAction({title:"앱을 종료할까요?",message:"진행 중인 업무를 중지하고 앱 연결을 종료합니다. 이미 만들어진 파일은 유지됩니다.",confirmLabel:"앱 종료",danger:true}))return;
  quitting=true;appClosed=true;$("quit").disabled=true;$("settings-dialog").close();
  globalThis.WorkspaceCapabilities?.close();
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
async function init(){try{boot=await api("/api/bootstrap");sessions=boot.sessions;$("demo-banner").hidden=!boot.demo;renderConnection(null);renderSessions();taskHeader();setPanel("sources");setStatus("idle");if(boot.historyWarning)error(boot.historyWarning);if(boot.error)error(boot.error);}catch(e){error(e.message);$("send").disabled=true;}}
document.querySelectorAll('button[value="cancel"]').forEach(b=>b.setAttribute("formnovalidate",""));document.addEventListener("keydown",e=>{if(e.key==="Escape")document.querySelector(".inspector").classList.remove("open");});
init();
