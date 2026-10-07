"use strict";

globalThis.WorkspaceWorkflow = (() => {
  let snapshot = null, readGeneration = 0, controller = null, editor = null, trustReturn = null;
  let overview = null, overviewController = null, overviewGeneration = 0, overviewPage = 0, overviewFocus = null, overviewNavigating = false;
  let overviewFingerprint = "";
  const overviewPageSize = 15;
  const overviewDateFormatter = new Intl.DateTimeFormat("ko-KR",{year:"numeric",month:"numeric",day:"numeric",hour:"numeric",minute:"2-digit"});
  const overviewDateLabel = value => value ? overviewDateFormatter.format(new Date(value*1000)) : "미정";
  const attempts = new Map(), mutations = new Map();
  const current = context => context?.id === active?.id && context.generation === selectionGeneration && !appClosed;
  const capture = () => ({id:active?.id,generation:selectionGeneration});
  const busy = () => busyStates.has(active?.state);
  const stopBlocked = () => !!globalThis.WorkspaceStop?.blocked();
  const queuePolicy = "대기 요청은 정상 완료 후 순서대로 자동 실행됩니다. 오류·중지·연결 종료 시에는 대기를 유지하고, 승인·질문은 응답을 기다립니다.";
  const pending = () => (snapshot?.queue || []).filter(item => ["queued","needs_review","dispatching"].includes(item.state));
  const locked = () => mutations.has(active?.id);
  const pausedByUser = item => typeof item?.pausedByUser === "boolean" ? item.pausedByUser
    : item?.enabled === false && !(item.kind === "once" && item.nextRunAt == null && ["queued","submitted","done","missed","queue_full","needs_review","previous_pending"].includes(item.lastRun?.status || item.lastStatus));
  const dateLabel = value => value ? new Date(value*1000).toLocaleString("ko-KR") : "미정";
  const weekdayNames = ["월","화","수","목","금","토","일"];
  const intervalLabel = minutes => {
    const hours=Math.floor(minutes/60),extra=minutes%60;
    return `${hours?`${hours}시간`:""}${hours&&extra?" ":""}${extra?`${extra}분`:""}마다`;
  };
  const scheduleLabel = item => {
    if(item.kind==="once")return dateLabel(item.runAt);
    if(item.kind==="interval")return `${intervalLabel(item.intervalMinutes)} · ${item.startTime} ~ ${item.endTime}`;
    const repeat=item.kind==="weekly"?`매주 ${(item.weekdays||[]).map(day=>weekdayNames[day]).join("·")}요일`
      :item.kind==="weekdays"?"평일 (월~금)":item.kind==="monthly"?`매월 ${item.dayOfMonth||1}일`:"매일";
    return `${repeat} ${item.time||""}`;
  };
  const resultNames = {queued:"실행 대기",dispatching:"전송 중",submitted:"요청 전달",done:"요청 완료",needs_review:"전송 확인 필요",missed:"놓친 실행",previous_pending:"이전 요청 대기 중",queue_full:"대기 목록 가득 참",failed:"실패",cancelled:"취소",paused:"일시 정지",error:"확인 필요",stopped:"중지"};
  function apply(value, context) {
    if (!current(context)) return;
    const next = value.dispatch || value;
    if (!next || !Array.isArray(next.queue) || !Array.isArray(next.schedules)) return;
    if (snapshot?.sessionId === context.id && Number(next.revision) < Number(snapshot.revision)) return;
    snapshot = {...next,sessionId:context.id};render();
  }
  function render() {
    const blocked = sending || !!choiceSubmission || modelChanging || permissionChanging || effortChanging || connectionPreparing || stopBlocked() || !!globalThis.WorkspaceConnectionRestart?.isCurrent() || appClosed || locked() || !!globalThis.WorkspaceAttachments?.isUploading();
    const queue = pending(), schedules = snapshot?.schedules || [];
    $("followup-actions").hidden = !active || !busy();
    $("followup-queue").disabled = blocked || !active?.trusted;
    $("followup-now").disabled = blocked || !active?.trusted || snapshot?.steer?.supported !== true;
    $("followup-note").textContent = snapshot?.steer?.supported === true
      ? (snapshot.steer.reason || "지금 반영은 현재 요청을 중지한 뒤 같은 대화에서 이어갑니다.")
      : snapshot?.steer?.reason || "현재 연결의 바로 반영 지원을 확인하고 있습니다. 끝나고 이어서 실행할 수 있습니다.";
    $("workflow-open").disabled = !active || appClosed;
    $("schedule-open").disabled = !active || blocked;
    $("workflow-open").textContent = `이어 할 일${queue.length ? ` ${queue.length}` : ""}${schedules.length ? ` · 예약 ${schedules.length}` : ""}`;
    $("workflow-context").textContent = active ? `${active.title} · ${basename(active.workspace)}` : "업무를 선택해 주세요.";
    $("workflow-policy").textContent = `${queuePolicy} ${snapshot?.policy?.message || "예약은 앱이 켜져 있을 때 실행됩니다. 앱 종료·절전으로 놓친 실행은 다시 확인한 뒤 이어갑니다."}`;
    const uncertain = queue.some(item=>item.state === "needs_review");
    $("workflow-paused").hidden = !snapshot?.paused && !uncertain;
    $("workflow-paused").textContent = uncertain ? "전송 여부를 확인할 요청이 있습니다. 대화에서 결과를 확인하고 해당 항목을 대기에서 제거한 뒤 이어 실행해 주세요. 확인 없이 자동 재전송하지 않습니다."
      : snapshot?.pauseReason || "오류·중지·연결 종료 또는 앱 재시작으로 대기 요청을 멈췄습니다. 내용을 확인하고 이어 실행해 주세요.";
    $("workflow-resume").hidden = !(snapshot?.paused || uncertain);
    $("workflow-resume").disabled = blocked || busy() || uncertain;
    if(snapshot?.warning)$("workflow-message").textContent=snapshot.warning;
    $("queue-list").replaceChildren();$("queue-empty").hidden = queue.length > 0;
    const context = capture();
    queue.forEach((item,index) => {
      const row = el("article",null,"workflow-row");
      const waitLabel = stopBlocked() ? "중지 확인 중" : snapshot?.paused ? "일시 정지" : snapshot?.warning || uncertain ? "실행 확인 필요"
        : ["approval","question"].includes(active?.state) || active?.choice ? "응답 대기" : busy() ? "정상 완료 후 자동 실행" : "자동 실행 대기";
      row.append(el("span",item.state === "dispatching" ? "전송 중" : item.state === "needs_review" ? "전송 확인 필요" : `${index+1}번째 · ${waitLabel}`,"workflow-state"),el("p",item.text,"workflow-request"));
      if (item.attachments?.length) row.append(el("small",`자료 ${item.attachments.length}개 · ${item.attachments.map(basename).join(", ")}`,"workflow-files"));
      const actions=el("div",null,"workflow-row-actions");
      function action(label,fn,disabled=false) {const button=el("button",label,"text-button");button.type="button";button.disabled=blocked||item.state==="dispatching"||disabled;button.onclick=()=>{if(current(context))fn();};actions.append(button);}
      const reorderable=queue.filter(entry=>entry.state==="queued"),position=reorderable.findIndex(entry=>entry.id===item.id);
      action("위로",()=>reorder(position,position-1),position<=0);action("아래로",()=>reorder(position,position+1),position<0||position===reorderable.length-1);
      action("수정",()=>openEditor("queue",item),item.state!=="queued");action(item.state==="needs_review"?"대기에서 제거":"취소",()=>mutate({action:"cancel",requestId:item.id},context));
      row.append(actions);$("queue-list").append(row);
    });
    $("schedule-list").replaceChildren();$("schedule-empty").hidden = schedules.length > 0;
    schedules.forEach(item => {
      const row=el("article",null,"workflow-row"),actions=el("div",null,"workflow-row-actions");
      row.dataset.scheduleId=item.id;
      row.append(el("strong",scheduleLabel(item)),el("span",item.enabled ? "예약 중" : pausedByUser(item) ? "일시 정지" : item.kind==="once"&&!item.nextRunAt ? resultNames[item.lastRun?.status]||"실행 없음" : "일시 정지","workflow-state"),el("p",item.text,"workflow-request"));
      row.append(el("small",item.nextRunAt?`다음 실행: ${dateLabel(item.nextRunAt)}`:"다음 실행 없음","workflow-files"));
      if (item.lastRun?.dueAt || item.lastRun?.status) row.append(el("small",`최근 실행: ${dateLabel(item.lastRun.dueAt || item.lastRun.at || item.lastRun.timestamp)} · ${item.lastRun.message || resultNames[item.lastRun.status] || item.lastRun.status || "결과 미확인"}`,"workflow-files"));
      if (item.lastError) row.append(el("p",item.lastError,"workflow-warning"));
      for (const [label,fn] of [["수정",()=>openEditor("schedule",item)],[item.enabled ? "일시 정지" : "예약 재개",()=>item.enabled?mutate({action:"schedule_pause",requestId:item.id},context):requestResume({action:"schedule_resume",requestId:item.id},context)],["삭제",()=>mutate({action:"schedule_cancel",requestId:item.id},context)]]) {
        const button=el("button",label,"text-button");button.type="button";button.disabled=blocked||(label==="예약 재개"&&!item.nextRunAt);button.onclick=()=>{if(current(context))fn();};actions.append(button);
      }
      row.append(actions);$("schedule-list").append(row);
    });
  }
  async function refresh() {
    const overviewRead=refreshOverview();
    if (!active || appClosed) return overviewRead;
    const context=capture(),ticket=++readGeneration;if(controller)controller.abort();controller=new AbortController();
    try {const value=await api(`/api/dispatch?id=${encodeURIComponent(context.id)}`,undefined,controller.signal);if(ticket===readGeneration)apply(value,context);}
    catch(err){if(err.name!=="AbortError"&&current(context)&&$("workflow-dialog").open)$("workflow-message").textContent=err.message;}
    finally {await overviewRead;}
  }
  function renderOverview() {
    if(!$("schedule-overview-dialog")?.open)return;
    const counts=overview?.counts||{},query=$("schedule-overview-search").value.trim().toLocaleLowerCase(),filter=$("schedule-overview-filter").value||"all";
    $("schedule-overview-summary").textContent=`전체 ${counts.total||0} · 예약·실행 중 ${counts.active||0} · 일시 정지 ${counts.paused||0} · 확인 필요 ${counts.attention||0} · 완료 ${counts.completed||0}`;
    const items=(overview?.schedules||[]).filter(item=>(filter==="all"||item.category===filter)&&(!query||[item.title,item.workspaceLabel,item.requestSummary].join(" ").toLocaleLowerCase().includes(query)));
    const pages=Math.max(1,Math.ceil(items.length/overviewPageSize));overviewPage=Math.min(overviewPage,pages-1);
    const list=$("schedule-overview-list");list.replaceChildren();
    $("schedule-overview-empty").hidden=items.length>0;
    $("schedule-overview-empty").textContent=overview?counts.total?"조건에 맞는 예약이 없어요.":"등록한 예약이 없어요. 업무를 선택하고 ‘실행 예약’으로 추가할 수 있습니다.":"예약을 불러오고 있어요.";
    for(const item of items.slice(overviewPage*overviewPageSize,(overviewPage+1)*overviewPageSize)) {
      const row=el("article",null,"schedule-overview-row"),heading=el("div",null,"schedule-overview-row-heading"),status=el("span",item.statusLabel,"schedule-overview-state");
      status.dataset.category=item.category;
      heading.append(el("strong",item.title),status);row.append(heading);
      if(item.workspaceLabel)row.append(el("span",item.workspaceLabel,"schedule-overview-folder"));
      row.append(el("p",item.requestSummary,"schedule-overview-request"));
      const timing=el("div",null,"schedule-overview-timing");
      timing.append(el("span",item.kind==="once"?"한 번":scheduleLabel(item)),el("span",item.nextRunAt?`${item.pausedByUser?"예약 시각":"다음"} ${overviewDateLabel(item.nextRunAt)}`:"다음 실행 없음"));row.append(timing);
      if(item.waitReason)row.append(el("p",item.waitReason,"schedule-overview-reason"));
      if(item.lastRun?.status)row.append(el("small",`최근 ${overviewDateLabel(item.lastRun.dueAt)} · ${resultNames[item.lastRun.status]||"결과 확인 필요"}`,"schedule-overview-last"));
      const button=el("button","예약 관리","secondary-button schedule-overview-manage");button.type="button";button.disabled=overviewNavigating||appClosed;
      button.setAttribute("aria-label",`${item.title} 예약 관리`);button.onclick=()=>manageOverviewSchedule(item);row.append(button);list.append(row);
    }
    $("schedule-overview-prev").disabled=overviewPage===0;$("schedule-overview-next").disabled=overviewPage>=pages-1;
    $("schedule-overview-page").textContent=`${overviewPage+1} / ${pages} · ${items.length}개`;
  }
  async function refreshOverview() {
    if(!$("schedule-overview-dialog")?.open||appClosed||overviewController)return;
    const ticket=overviewGeneration,request=new AbortController();overviewController=request;
    try {
      const value=await api("/api/schedules",undefined,request.signal);
      if(ticket!==overviewGeneration||request.signal.aborted||!$("schedule-overview-dialog").open||appClosed)return;
      if(!Array.isArray(value.schedules)||!value.counts)throw new Error("예약 목록을 확인하지 못했어요. 잠시 후 다시 열어 주세요.");
      // Keep only bounded summaries, never a second copy of requests or logs.
      const next={...value,schedules:value.schedules.slice(0,100)},fingerprint=JSON.stringify(next);
      $("schedule-overview-message").textContent=value.warning||"";
      if(fingerprint!==overviewFingerprint){overview=next;overviewFingerprint=fingerprint;renderOverview();}
    } catch(err) {
      if(err.name!=="AbortError"&&ticket===overviewGeneration&&$("schedule-overview-dialog").open) {
        $("schedule-overview-message").textContent=err.message;
        if(!overview)$("schedule-overview-empty").textContent="예약을 불러오지 못했어요. 연결을 확인하면 자동으로 다시 확인합니다.";
      }
    } finally {if(overviewController===request)overviewController=null;}
  }
  async function openOverview() {
    if(appClosed)return;
    if(!$("schedule-overview-dialog").open) {
      overviewFocus=document.activeElement;overviewGeneration++;overviewPage=0;overview=null;overviewFingerprint="";
      $("schedule-overview-filter").value="all";$("schedule-overview-search").value="";$("schedule-overview-message").textContent="";
      showDialog("schedule-overview-dialog");renderOverview();$("schedule-overview-search").focus();
    }
    await refreshOverview();
  }
  function releaseOverview() {
    if($("schedule-overview-dialog").open)return;
    overviewGeneration++;overviewController?.abort();overviewController=null;overview=null;overviewFingerprint="";overviewNavigating=false;
    $("schedule-overview-list").replaceChildren();
    const previous=overviewFocus;overviewFocus=null;
    if(previous?.isConnected&&!previous.disabled&&(!previous.closest?.("dialog")||previous.closest("dialog").open))previous.focus();
  }
  async function manageOverviewSchedule(item) {
    if(overviewNavigating||appClosed||!$("schedule-overview-dialog").open)return;
    const ticket=overviewGeneration;overviewNavigating=true;renderOverview();
    try {
      const selected=active?.id===item.sessionId||await selectSession(item.sessionId);
      if(!selected||ticket!==overviewGeneration||!$("schedule-overview-dialog").open||appClosed)return;
      overviewFocus=null;$("schedule-overview-dialog").close();render();$("workflow-message").textContent="";showDialog("workflow-dialog");await refresh();
      if(active?.id===item.sessionId&&$("workflow-dialog").open) {
        const row=[...$("schedule-list").children].find(node=>node.dataset.scheduleId===item.id);
        row?.scrollIntoView({block:"nearest"});row?.querySelector("button")?.focus();
      }
    } catch(err) {if(ticket===overviewGeneration&&$("schedule-overview-dialog").open)$("schedule-overview-message").textContent=err.message;}
    finally {overviewNavigating=false;if(ticket===overviewGeneration)renderOverview();}
  }
  function requestId(body) {
    const fingerprint=JSON.stringify(body);
    if (!attempts.has(fingerprint)) attempts.set(fingerprint,globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    return {fingerprint,id:attempts.get(fingerprint)};
  }
  async function mutate(body,context=capture()) {
    if (!context.id || mutations.has(context.id) || appClosed || (current(context)&&globalThis.WorkspaceConnectionRestart?.isCurrent())) return null;
    const creating=["enqueue","steer","schedule"].includes(body.action),payload={id:context.id,...body},attempt=creating?requestId(payload):null;
    if(attempt)payload.clientRequestId=attempt.id;
    mutations.set(context.id,context);render();setStatus(active?.state||"idle");
    try {const value=await api("/api/dispatch",payload);if(attempt)attempts.delete(attempt.fingerprint);apply(value,context);return value;}
    catch(err){if(current(context)){toast(err.message);$("workflow-message").textContent=err.message;}return null;}
    finally {if(mutations.get(context.id)===context)mutations.delete(context.id);render();setStatus(active?.state||"idle");}
  }
  async function requestResume(body={action:"resume"},context=capture()) {
    if(!context.id||!current(context)||locked()||stopBlocked()||!["resume","schedule_resume"].includes(body.action))return null;
    if(!active.trusted){
      trustReturn=null;chooseFolder(true);$("folder-form").dataset.afterTrust="workflow";
      trustReturn={...context,folderGeneration:folderChoiceGeneration,body:{...body}};
      return null;
    }
    return mutate(body,context);
  }
  async function resumeAfterTrust(confirmed) {
    const pending=trustReturn;
    if(!pending||!confirmed||pending.id!==confirmed.id||pending.generation!==confirmed.generation||pending.folderGeneration!==confirmed.folderGeneration)return null;
    trustReturn=null;
    if(!current(pending)||!active.trusted||folderChoiceGeneration!==pending.folderGeneration)return null;
    return mutate(pending.body,pending);
  }
  function sameDraft(context,text,files) {return current(context)&&$("prompt").value===text&&JSON.stringify(attachments)===JSON.stringify(files);}
  function queuedNotice(action,value) {
    const returned=value.dispatch||value;
    const state=snapshot?.sessionId===active?.id&&Number(snapshot.revision)>Number(returned.revision)?snapshot:returned;
    if(state.queue?.some(item=>item.state==="needs_review"))return "요청을 대기에 추가했습니다. 전송 확인이 필요한 항목을 ‘이어 할 일’에서 먼저 확인해 주세요.";
    if(state.warning)return "요청을 대기에 추가했습니다. 실행 상태는 ‘이어 할 일’의 안내를 확인해 주세요.";
    if(stopBlocked())return "요청을 대기에 추가했습니다. 중지 상태가 확인되면 ‘이어 할 일’의 안내를 확인해 주세요.";
    if(state.paused)return "요청을 대기에 추가했습니다. 일시 정지 상태이므로 확인 후 ‘이어 실행’을 눌러 주세요.";
    return action==="steer"?"현재 요청을 중지하고 이어갈 요청을 등록했습니다.":busy()?"현재 요청이 정상 완료되면 순서대로 자동 실행합니다.":"요청을 대기에 추가했습니다. 순서대로 자동 실행합니다.";
  }
  async function send(action) {
    if (!active || locked() || sending || choiceSubmission || appClosed || stopBlocked() || globalThis.WorkspaceConnectionRestart?.isCurrent() || globalThis.WorkspaceAttachments?.isUploading()) return;
    if(!active.trusted)return chooseFolder(true);
    const text=$("prompt").value,files=[...attachments];if(!text.trim())return $("prompt").focus();
    if (/^\/effort(?:\s|$)/u.test(text.trim())) return toast("현재 요청이 끝난 뒤 Effort를 변경해 주세요. 입력은 그대로 유지합니다.");
    if(globalThis.WorkspaceComposer?.beforeSubmit()===false)return;
    if(action==="steer"&&snapshot?.steer?.supported!==true)return toast("현재 연결에서 바로 반영을 지원하지 않습니다. 끝나고 이어서를 이용해 주세요.");
    const context=capture(),result=await mutate({action,text:text.trim(),attachments:files},context);
    if (!result) return;
    if(sameDraft(context,text,files)){$("prompt").value="";attachments=[];renderAttachments();saveDraft();globalThis.WorkspaceInputKeys?.reset();globalThis.WorkspaceShortcuts?.afterSend(context.id);}
    else if(!current(context)){const draft=drafts.get(context.id);if(draft?.text===text&&JSON.stringify(draft.attachments)===JSON.stringify(files)){drafts.delete(context.id);globalThis.WorkspaceShortcuts?.afterSend(context.id);}}
    if(current(context))toast(queuedNotice(action,result));
  }
  async function reorder(from,to) {const items=pending().filter(item=>item.state==="queued");if(from<0||to<0||to>=items.length)return;[items[from],items[to]]=[items[to],items[from]];return mutate({action:"reorder",order:items.map(item=>item.id)});}
  function localDate(value) {const date=new Date(value*1000);date.setMinutes(date.getMinutes()-date.getTimezoneOffset());return date.toISOString().slice(0,16);}
  function timeMinutes(value) {
    if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value))return null;
    const [hours,minutes]=value.split(":").map(Number);return hours*60+minutes;
  }
  function scheduleTiming() {
    const kind=$("schedule-kind").value,rule={kind};
    if(kind==="once") {
      rule.runAt=new Date($("schedule-at").value).getTime()/1000;
      if(!Number.isFinite(rule.runAt)||rule.runAt<=Date.now()/1000)throw Error("앞으로 실행할 날짜와 시간을 선택해 주세요.");
    } else if(kind==="interval") {
      const h=$("schedule-interval-hours").value,m=$("schedule-interval-minutes").value;
      const hours=Number(h),minutes=Number(m),total=hours*60+minutes;
      if(h.trim()===""||m.trim()===""||!Number.isInteger(hours)||!Number.isInteger(minutes)||hours<0||hours>24||minutes<0||minutes>60||total<1||total>1440)throw Error("반복 간격은 합계 1분~24시간으로 입력해 주세요. 시간과 분은 정수여야 합니다.");
      rule.intervalMinutes=total;rule.startTime=$("schedule-start-time").value;rule.endTime=$("schedule-end-time").value;
      if(timeMinutes(rule.startTime)===null||timeMinutes(rule.endTime)===null)throw Error("시작 시간과 종료 시간을 선택해 주세요.");
      if(rule.startTime>=rule.endTime)throw Error("종료 시간은 시작 시간보다 늦어야 합니다.");
    } else {
      if(!["daily","weekdays","weekly","monthly"].includes(kind))throw Error("예약 방식을 선택해 주세요.");
      rule.time=$("schedule-time").value;if(timeMinutes(rule.time)===null)throw Error("실행 시간을 선택해 주세요.");
      if(kind==="weekly") {
        rule.weekdays=[...$("schedule-weekday-options").children].map(node=>node.children[0]).filter(input=>input.checked).map(input=>Number(input.value));
        if(!rule.weekdays.length)throw Error("실행할 요일을 선택해 주세요.");
      } else if(kind==="monthly") {
        rule.dayOfMonth=Number($("schedule-month-day").value);
        if(!Number.isInteger(rule.dayOfMonth)||rule.dayOfMonth<1||rule.dayOfMonth>31)throw Error("매월 실행할 날짜는 1~31일 중 선택해 주세요.");
      }
    }
    return rule;
  }
  function updateSchedulePreview() {
    try {
      const rule=scheduleTiming();let text=scheduleLabel(rule);
      if(rule.kind==="monthly")text+=" · 해당 날짜가 없는 달은 건너뜁니다";
      if(rule.kind==="interval") {
        const start=timeMinutes(rule.startTime),end=timeMinutes(rule.endTime),count=Math.ceil((end-start)/rule.intervalMinutes),samples=[];
        for(let i=0;i<Math.min(count,4);i++){const value=start+i*rule.intervalMinutes;samples.push(`${String(Math.floor(value/60)).padStart(2,"0")}:${String(value%60).padStart(2,"0")}`);}
        text+=` · ${samples.join(" → ")}${count>4?" …":""} · 하루 ${count}회 예약`;
      }
      $("schedule-preview").textContent=text;$("schedule-preview").dataset.invalid="false";
    } catch(error) {$("schedule-preview").textContent=error.message;$("schedule-preview").dataset.invalid="true";}
  }
  function editorMode() {
    const kind=$("schedule-kind").value;
    $("schedule-once-fields").hidden=kind!=="once";$("schedule-time-fields").hidden=kind==="once"||kind==="interval";
    $("schedule-weekdays").hidden=kind!=="weekly";$("schedule-month-fields").hidden=kind!=="monthly";$("schedule-interval-fields").hidden=kind!=="interval";
    updateSchedulePreview();
  }
  function openEditor(kind,item=null) {
    if(!active||locked()||appClosed||globalThis.WorkspaceConnectionRestart?.isCurrent())return;
    if(!active.trusted){chooseFolder(true);$("folder-form").dataset.afterTrust="schedule";return;}
    editor={...capture(),kind,item,attachments:[...(item?.attachments||attachments)]};
    $("request-editor-title").textContent=kind==="queue"?"대기 요청 수정":item?"예약 수정":"실행 예약";
    $("request-editor-context").textContent=`${active.title} · ${basename(active.workspace)}`;
    $("request-editor-text").value=item?.text||$("prompt").value;
    $("request-editor-files").textContent=editor.attachments.length?`자료 ${editor.attachments.length}개: ${editor.attachments.map(basename).join(", ")}`:"첨부 자료 없음";
    $("schedule-fields").hidden=kind==="queue";
    $("schedule-kind").value=item?.kind||"once";$("schedule-at").value=localDate(item?.runAt||Date.now()/1000+3600);$("schedule-time").value=item?.time||"09:00";
    $("schedule-month-day").value=String(item?.dayOfMonth||1);
    $("schedule-interval-hours").value=String(Math.floor((item?.intervalMinutes||30)/60));$("schedule-interval-minutes").value=String((item?.intervalMinutes||30)%60);
    $("schedule-start-time").value=item?.startTime||"09:00";$("schedule-end-time").value=item?.endTime||"18:00";
    $("schedule-weekday-options").replaceChildren();
    weekdayNames.forEach((name,index)=>{const label=el("label"),input=el("input");input.type="checkbox";input.value=String(index);input.checked=(item?.weekdays||[0]).includes(index);label.append(input,el("span",name));$("schedule-weekday-options").append(label);});
    $("request-editor-error").textContent="";editorMode();showDialog("request-editor-dialog");$("request-editor-text").focus();
  }
  async function saveEditor(event) {
    event.preventDefault();const context=editor;if(!context||!current(context)||mutations.has(context.id))return;
    const text=$("request-editor-text").value.trim();if(!text)return $("request-editor-text").focus();
    if(/^\/effort(?:\s|$)/u.test(text)){$("request-editor-error").textContent="현재 요청이 끝난 뒤 Effort를 변경해 주세요. 입력은 그대로 유지합니다.";return;}
    let schedule;
    if(context.kind!=="queue") {
      try {schedule={...scheduleTiming(),enabled:!pausedByUser(context.item)};}
      catch(error){$("request-editor-error").textContent=error.message;return;}
    }
    $("request-editor-save").disabled=true;
    try {
      const result=await mutate({action:context.kind==="queue"?"update":context.item?"schedule_update":"schedule",...(context.item?{requestId:context.item.id}:{}),text,attachments:context.attachments,...(schedule?{schedule}:{})},context);
      if(result&&editor===context&&current(context)){$("request-editor-dialog").close();toast(context.kind==="queue"?"대기 요청을 수정했습니다.":"예약을 저장했습니다. 앱이 켜져 있어야 실행됩니다.");}
    } finally {$("request-editor-save").disabled=false;}
  }
  function contextChanged() {
    readGeneration++;controller?.abort();snapshot=null;editor=null;trustReturn=null;
    if($("request-editor-dialog").open)$("request-editor-dialog").close();
    if($("workflow-dialog").open)$("workflow-dialog").close();
    render();
  }
  $("followup-queue").onclick=()=>send("enqueue");$("followup-now").onclick=()=>send("steer");
  $("workflow-open").onclick=()=>{render();$("workflow-message").textContent="";showDialog("workflow-dialog");refresh();};
  $("workflow-close").onclick=() => $("workflow-dialog").close();
  $("workflow-resume").onclick=()=>requestResume();
  $("folder-dialog").addEventListener?.("cancel",()=>{trustReturn=null;});
  $("folder-form").addEventListener?.("submit",event=>{if(event.submitter?.value!=="ok")trustReturn=null;});
  $("schedule-open").onclick=$("schedule-add").onclick=()=>openEditor("schedule");
  $("request-editor-close").onclick=$("request-editor-cancel").onclick=()=>$("request-editor-dialog").close();
  $("request-editor-form").onsubmit=saveEditor;$("schedule-kind").onchange=editorMode;
  for(const id of ["schedule-at","schedule-time","schedule-month-day","schedule-interval-hours","schedule-interval-minutes","schedule-start-time","schedule-end-time"])$(id).oninput=updateSchedulePreview;
  $("schedule-weekday-options").onchange=updateSchedulePreview;
  $("schedule-overview-open").onclick=openOverview;
  $("schedule-overview-close").onclick=()=>$("schedule-overview-dialog").close();
  $("schedule-overview-dialog").addEventListener("close",releaseOverview);
  $("schedule-overview-filter").onchange=$("schedule-overview-search").oninput=()=>{overviewPage=0;renderOverview();};
  $("schedule-overview-prev").onclick=()=>{overviewPage=Math.max(0,overviewPage-1);renderOverview();};
  $("schedule-overview-next").onclick=()=>{overviewPage++;renderOverview();};
  setInterval(refresh,4000);
  render();
  return {render,refresh,send,mutate,openEditor,saveEditor,contextChanged,requestResume,resumeAfterTrust,openOverview,isSubmitting:locked};
})();
