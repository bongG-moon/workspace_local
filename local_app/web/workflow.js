"use strict";

globalThis.WorkspaceWorkflow = (() => {
  let snapshot = null, readGeneration = 0, controller = null, editor = null, trustReturn = null;
  const attempts = new Map(), mutations = new Map();
  const current = context => context?.id === active?.id && context.generation === selectionGeneration && !appClosed;
  const capture = () => ({id:active?.id,generation:selectionGeneration});
  const busy = () => busyStates.has(active?.state);
  const pending = () => (snapshot?.queue || []).filter(item => ["queued","needs_review","dispatching"].includes(item.state));
  const locked = () => mutations.has(active?.id);
  const pausedByUser = item => typeof item?.pausedByUser === "boolean" ? item.pausedByUser
    : item?.enabled === false && !(item.kind === "once" && item.nextRunAt == null && ["queued","submitted","done","missed","queue_full","needs_review","previous_pending"].includes(item.lastRun?.status || item.lastStatus));
  const dateLabel = value => value ? new Date(value*1000).toLocaleString("ko-KR") : "미정";
  const weekdayNames = ["월","화","수","목","금","토","일"];
  const scheduleLabel = item => item.kind === "once" ? dateLabel(item.runAt)
    : `${item.kind === "weekly" ? (item.weekdays||[]).map(day=>weekdayNames[day]).join("·")+"요일" : "매일"} ${item.time || ""}`;
  const resultNames = {queued:"실행 대기",dispatching:"전송 중",submitted:"요청 전달",done:"요청 완료",needs_review:"전송 확인 필요",missed:"놓친 실행",previous_pending:"이전 요청 대기 중",queue_full:"대기 목록 가득 참",failed:"실패",cancelled:"취소",paused:"일시 정지",error:"확인 필요",stopped:"중지"};
  function apply(value, context) {
    if (!current(context)) return;
    const next = value.dispatch || value;
    if (!next || !Array.isArray(next.queue) || !Array.isArray(next.schedules)) return;
    if (snapshot?.sessionId === context.id && Number(next.revision) < Number(snapshot.revision)) return;
    snapshot = {...next,sessionId:context.id};render();
  }
  function render() {
    const blocked = sending || !!choiceSubmission || modelChanging || permissionChanging || effortChanging || connectionPreparing || appClosed || locked() || !!globalThis.WorkspaceAttachments?.isUploading();
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
    $("workflow-policy").textContent = snapshot?.policy?.message || "예약은 앱이 켜져 있을 때 실행됩니다. 앱 종료·절전으로 놓친 실행은 다시 확인한 뒤 이어갑니다.";
    const uncertain = queue.some(item=>item.state === "needs_review");
    $("workflow-paused").hidden = !snapshot?.paused && !uncertain;
    $("workflow-paused").textContent = uncertain ? "전송 여부를 확인할 요청이 있습니다. 대화에서 결과를 확인하고 해당 항목을 대기에서 제거한 뒤 이어 실행해 주세요. 확인 없이 자동 재전송하지 않습니다."
      : snapshot?.pauseReason || "오류·중지 또는 앱 재시작으로 대기 요청을 멈췄습니다. 내용을 확인하고 이어 실행해 주세요.";
    $("workflow-resume").hidden = !(snapshot?.paused || uncertain);
    $("workflow-resume").disabled = blocked || busy() || uncertain;
    if(snapshot?.warning)$("workflow-message").textContent=snapshot.warning;
    $("queue-list").replaceChildren();$("queue-empty").hidden = queue.length > 0;
    const context = capture();
    queue.forEach((item,index) => {
      const row = el("article",null,"workflow-row");
      row.append(el("span",item.state === "dispatching" ? "전송 중" : item.state === "needs_review" ? "전송 확인 필요" : `${index+1}번째 요청`,"workflow-state"),el("p",item.text,"workflow-request"));
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
    if (!active || appClosed) return;
    const context=capture(),ticket=++readGeneration;if(controller)controller.abort();controller=new AbortController();
    try {const value=await api(`/api/dispatch?id=${encodeURIComponent(context.id)}`,undefined,controller.signal);if(ticket===readGeneration)apply(value,context);}
    catch(err){if(err.name!=="AbortError"&&current(context)&&$("workflow-dialog").open)$("workflow-message").textContent=err.message;}
  }
  function requestId(body) {
    const fingerprint=JSON.stringify(body);
    if (!attempts.has(fingerprint)) attempts.set(fingerprint,globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    return {fingerprint,id:attempts.get(fingerprint)};
  }
  async function mutate(body,context=capture()) {
    if (!context.id || mutations.has(context.id) || appClosed) return null;
    const creating=["enqueue","steer","schedule"].includes(body.action),payload={id:context.id,...body},attempt=creating?requestId(payload):null;
    if(attempt)payload.clientRequestId=attempt.id;
    mutations.set(context.id,context);render();setStatus(active?.state||"idle");
    try {const value=await api("/api/dispatch",payload);if(attempt)attempts.delete(attempt.fingerprint);apply(value,context);return value;}
    catch(err){if(current(context)){toast(err.message);$("workflow-message").textContent=err.message;}return null;}
    finally {if(mutations.get(context.id)===context)mutations.delete(context.id);render();setStatus(active?.state||"idle");}
  }
  async function requestResume(body={action:"resume"},context=capture()) {
    if(!context.id||!current(context)||locked()||!["resume","schedule_resume"].includes(body.action))return null;
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
  async function send(action) {
    if (!active || locked() || sending || choiceSubmission || appClosed || globalThis.WorkspaceAttachments?.isUploading()) return;
    if(!active.trusted)return chooseFolder(true);
    const text=$("prompt").value,files=[...attachments];if(!text.trim())return $("prompt").focus();
    if (/^\/effort(?:\s|$)/u.test(text.trim())) return toast("현재 요청이 끝난 뒤 Effort를 변경해 주세요. 입력은 그대로 유지합니다.");
    if(globalThis.WorkspaceComposer?.beforeSubmit()===false)return;
    if(action==="steer"&&snapshot?.steer?.supported!==true)return toast("현재 연결에서 바로 반영을 지원하지 않습니다. 끝나고 이어서를 이용해 주세요.");
    const context=capture(),result=await mutate({action,text:text.trim(),attachments:files},context);
    if (!result) return;
    if(sameDraft(context,text,files)){$("prompt").value="";attachments=[];renderAttachments();saveDraft();}
    else if(!current(context)){const draft=drafts.get(context.id);if(draft?.text===text&&JSON.stringify(draft.attachments)===JSON.stringify(files))drafts.delete(context.id);}
    if(current(context))toast(action==="steer"?"현재 요청을 중지하고 이어갈 요청을 등록했습니다.":"현재 요청이 끝나면 이어서 실행합니다.");
  }
  async function reorder(from,to) {const items=pending().filter(item=>item.state==="queued");if(from<0||to<0||to>=items.length)return;[items[from],items[to]]=[items[to],items[from]];return mutate({action:"reorder",order:items.map(item=>item.id)});}
  function localDate(value) {const date=new Date(value*1000);date.setMinutes(date.getMinutes()-date.getTimezoneOffset());return date.toISOString().slice(0,16);}
  function editorMode() {const kind=$("schedule-kind").value;$("schedule-once-fields").hidden=kind!=="once";$("schedule-time-fields").hidden=kind==="once";$("schedule-weekdays").hidden=kind!=="weekly";}
  function openEditor(kind,item=null) {
    if(!active||locked()||appClosed)return;
    if(!active.trusted){chooseFolder(true);$("folder-form").dataset.afterTrust="schedule";return;}
    editor={...capture(),kind,item,attachments:[...(item?.attachments||attachments)]};
    $("request-editor-title").textContent=kind==="queue"?"대기 요청 수정":item?"예약 수정":"실행 예약";
    $("request-editor-context").textContent=`${active.title} · ${basename(active.workspace)}`;
    $("request-editor-text").value=item?.text||$("prompt").value;
    $("request-editor-files").textContent=editor.attachments.length?`자료 ${editor.attachments.length}개: ${editor.attachments.map(basename).join(", ")}`:"첨부 자료 없음";
    $("schedule-fields").hidden=kind==="queue";
    $("schedule-kind").value=item?.kind||"once";$("schedule-at").value=localDate(item?.runAt||Date.now()/1000+3600);$("schedule-time").value=item?.time||"09:00";
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
      const kind=$("schedule-kind").value;schedule={kind,enabled:!pausedByUser(context.item)};
      if(kind==="once"){schedule.runAt=new Date($("schedule-at").value).getTime()/1000;if(!Number.isFinite(schedule.runAt)||schedule.runAt<=Date.now()/1000){$("request-editor-error").textContent="앞으로 실행할 날짜와 시간을 선택해 주세요.";return;}}
      else {schedule.time=$("schedule-time").value;if(!/^\d{2}:\d{2}$/.test(schedule.time))return;if(kind==="weekly"){schedule.weekdays=[...$("schedule-weekday-options").children].map(node=>node.children[0]).filter(input=>input.checked).map(input=>Number(input.value));if(!schedule.weekdays.length){$("request-editor-error").textContent="실행할 요일을 선택해 주세요.";return;}}}
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
  setInterval(refresh,4000);
  render();
  return {render,refresh,send,mutate,openEditor,saveEditor,contextChanged,requestResume,resumeAfterTrust,isSubmitting:locked};
})();
