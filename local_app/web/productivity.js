"use strict";

// On-demand panels: no background scan, timers, transcript cache, or AI request.
globalThis.WorkspaceProductivityActions = (() => {
  let generation = 0, fileGeneration = 0, context = null, runs = [], branching = false, priorFocus = null;
  const changes = $("changes-dialog"), branch = $("branch-dialog");
  const current = ticket => ticket === generation && context === active?.id && !appClosed;
  const canBranch = () => !!active?.sessionId && active?.branch?.status !== "pending" && ["idle", "done"].includes(active.state) && !boot.demo && !sending;
  function update() {
    if($("branch-origin")) {
      $("branch-origin").hidden = !active?.branch || !!globalThis.WorkspaceCapabilities?.isOpen();
      $("branch-origin-status").textContent = active?.branch?.status === "pending" ? "분기 준비 · 첫 요청부터 독립된 Claude 대화로 이어가요" : "원본 대화에서 분기한 업무";
      $("branch-source-open").hidden = !sessions.some(row => row.id === active?.branch?.sourceTaskId);
    }
    $("branch-open").hidden = !active;
    $("branch-open").disabled = !canBranch();
    $("changes-open").disabled = !active;
  }
  function release() {
    generation++; fileGeneration++; context = null; runs = [];
    $("changes-files").replaceChildren(); $("changes-content").replaceChildren();
    $("changes-run").replaceChildren(); $("changes-file-name").textContent = "";
  }
  function close() { if(changes.open) changes.close(); if(branch.open) branch.close(); release(); }
  function contextChanged() { close(); update(); }
  async function selectFile(file, run, ticket) {
    if (!current(ticket)) return;
    const request = ++fileGeneration;
    for(const button of $("changes-files").children) button.setAttribute("aria-current", String(button.dataset.file === file.id));
    $("changes-file-name").textContent = file.name;
    $("changes-note").textContent = "저장된 내용을 비교하고 있어요…";
    $("changes-content").replaceChildren();
    // File switching also invalidates a slower earlier response.
    const selected = file.id;
    $("changes-content").dataset.file = selected;
    try {
      const value = await api(`/api/changes/file?id=${encodeURIComponent(context)}&run=${encodeURIComponent(run.runId)}&file=${encodeURIComponent(selected)}`);
      if(!current(ticket) || request !== fileGeneration || !changes.open) return;
      const reasons = {observation_incomplete:"일부 범위만 확인되어 전후 내용을 확정할 수 없어요.",text_not_captured:"문서 형식이나 크기 제한으로 텍스트를 저장하지 않은 파일이에요.",diff_input_limit:"행 수가 많아 텍스트 비교 범위를 넘었어요.",diff_work_limit:"내용이 복잡해 비교 범위를 넘었어요."};
      $("changes-note").textContent = reasons[value.reason] || (value.status === "text" ? "− 이전 내용  ·  + 이후 내용" : "텍스트 비교를 제공하지 않는 파일이에요.");
      if(value.truncated) $("changes-note").textContent += " 비교 범위를 넘는 부분은 생략했어요.";
      const fragment = document.createDocumentFragment();
      for(const line of String(value.diff || "").split("\n").slice(0, 1600)) {
        const row = el("span", (line || " "), line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : line.startsWith("@@") ? "diff-hunk" : "diff-context");
        fragment.append(row);
      }
      $("changes-content").replaceChildren(fragment);
    } catch(e) { if(current(ticket) && request === fileGeneration) $("changes-note").textContent = e.message; }
  }
  function renderRun(ticket) {
    fileGeneration++;
    const run = runs.find(row => row.runId === $("changes-run").value);
    $("changes-files").replaceChildren(); $("changes-content").replaceChildren(); $("changes-file-name").textContent = "";
    const files = run?.files || [];
    $("changes-layout").hidden = !files.length; $("changes-empty").hidden = !!files.length;
    $("changes-empty").textContent = run?.status === "capturing" ? "업무가 끝나면 전후 변경을 비교할 수 있어요." : "이 요청에서 비교할 파일 변경을 확인하지 못했어요. 이전 기록이나 제외된 파일은 포함되지 않아요.";
    $("changes-summary").textContent = `${files.length}개 파일`;
    if(run?.before?.limited || run?.after?.limited || run?.before?.errors || run?.after?.errors) $("changes-summary").textContent += " · 일부 범위만 확인";
    for(const file of files) {
      const button = el("button", null, "change-file"); button.type = "button"; button.dataset.file = file.id;
      button.append(el("span", {created:"추가",modified:"변경",deleted:"삭제"}[file.change] || "확인", "change-kind"), el("strong", file.name));
      button.title = file.name; button.onclick = () => selectFile(file, run, ticket);
      $("changes-files").append(button);
    }
    if(files.length) selectFile(files[0], run, ticket);
  }
  async function openChanges() {
    if(!active || appClosed) return;
    close(); priorFocus = document.activeElement; context = active.id; const ticket = ++generation;
    $("changes-empty").hidden = false; $("changes-empty").textContent = "변경 기록을 불러오고 있어요…";
    $("changes-layout").hidden = true; $("changes-summary").textContent = "";
    showDialog("changes-dialog");
    try {
      const response = await api(`/api/changes?id=${encodeURIComponent(context)}`);
      if(!current(ticket) || !changes.open) return;
      runs = (response.runs || []).slice(0, 8);
      for(const run of runs) { const option = el("option", `${when(run.finishedAt || run.startedAt)} · ${run.runId.slice(0,8)}`); option.value = run.runId; $("changes-run").append(option); }
      $("changes-run").disabled = !runs.length;
      $("changes-run").onchange = () => renderRun(ticket);
      renderRun(ticket);
      if(response.errors) $("changes-summary").textContent += " · 일부 기록을 읽지 못했어요";
    } catch(e) { if(current(ticket)) $("changes-empty").textContent = e.message; }
  }
  async function openBranch() {
    if(!canBranch()) return;
    close(); priorFocus = document.activeElement; context = active.id; const ticket = ++generation;
    $("branch-source-title").textContent = active.title;
    $("branch-source-path").textContent = active.workspace;
    $("branch-status").textContent = "분기할 수 있는 Claude 기록을 확인하고 있어요…";
    $("branch-create").disabled = true; showDialog("branch-dialog");
    try {
      const response = await api(`/api/session/branch?id=${encodeURIComponent(context)}`);
      if(!current(ticket) || !branch.open) return;
      $("branch-status").textContent = response.reason || "새 대화에 입력을 보내기 전에는 AI 작업을 실행하지 않아요.";
      $("branch-create").disabled = response.available !== true;
    } catch(e) { if(current(ticket)) $("branch-status").textContent = e.message; }
  }
  $("branch-create").onclick = async () => {
    if(branching || !context || $("branch-create").disabled) return;
    const ticket = generation, sid = context; branching = true; $("branch-create").disabled = true;
    $("branch-status").textContent = "독립된 대화를 준비하고 있어요…";
    try {
      const result = await api("/api/session/branch", {id:sid});
      // Retain the created task even if the user closed the panel mid-request.
      sessions.unshift({...result.session, messages:undefined, artifacts:undefined}); renderSessions();
      if(current(ticket) && branch.open) { branch.close(); await selectSession(result.session.id); toast("분기를 준비했어요. 첫 요청부터 별도 Claude 세션으로 이어갑니다."); }
    } catch(e) { if(current(ticket)) $("branch-status").textContent = e.message; }
    finally { branching = false; if(current(ticket)) $("branch-create").disabled = false; }
  };
  for(const dialog of [changes, branch]) dialog.addEventListener("close", () => { if(changes.open || branch.open) return; release(); if(priorFocus?.isConnected) priorFocus.focus(); });
  $("changes-close").onclick = () => changes.close();
  $("branch-close").onclick = $("branch-cancel").onclick = () => branch.close();
  $("changes-open").onclick = openChanges; $("branch-open").onclick = openBranch;
  if($("branch-source-open")) $("branch-source-open").onclick = () => {
    const source = active?.branch?.sourceTaskId;
    if(source) selectSession(source).catch(e => toast(e.message));
  };
  update();
  return {openChanges, openBranch, canBranch, contextChanged, update, close};
})();
