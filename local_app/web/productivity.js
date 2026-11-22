"use strict";

// Panels share the active task lifecycle. Only explicit continuation creates a summary.
globalThis.WorkspaceProductivityActions = (() => {
  let generation = 0, fileGeneration = 0, context = null, runs = [], priorFocus = null;
  const changes = $("changes-dialog"), branch = $("branch-dialog");
  let closeExplorer = null, comparison = null, mode = "unified", selectedHunk = 0, candidateContext = null;
  const current = ticket => ticket === generation && context === active?.id && !appClosed;
  const canBranch = () => !!active?.sessionId && active?.branch?.status !== "pending" && ["idle", "done"].includes(active.state) && !boot.demo && !sending;
  const pendingContinuations = new Set(), noticeClaims = new Map(), noticeAttempts = new WeakMap();
  let noticeOwner = null, introOwner = null;
  function workView() {
    return !!active && active.kind !== "chat" && !appClosed && !boot.demo &&
      !globalThis.WorkspaceModes?.isChat() && !globalThis.WorkspaceCapabilities?.isOpen() &&
      !globalThis.WorkspaceExtensions?.isOpen?.();
  }
  function canContinue() {
    return workView() && canBranch() && active.trusted !== false &&
      !active.choice && !$("requests").children.length && !!(active.lastRunId || active.messages?.length >= 2);
  }
  function hideNotice() {
    noticeOwner = null;
    $("context-notice").hidden = true;
  }
  function updateNotice() {
    const usage = active?.contextUsage, sid = active?.id;
    if (!usage || !Number.isFinite(usage.usedTokens) || !(usage.windowTokens > 0) ||
        usage.usedTokens * 100 <= usage.windowTokens * 60 || !canContinue()) {
      if (noticeOwner) hideNotice();
      return;
    }
    if (noticeOwner && noticeOwner !== sid) hideNotice();
    if (noticeOwner === sid) return;
    const claim = noticeClaims.get(sid);
    if (claim === "pending" || (claim !== "ready" && active.contextNoticeShown)) return;
    if (document.hidden || (typeof pendingSelection !== "undefined" && pendingSelection) ||
        document.querySelector?.("dialog[open]")) return;
    if (claim === "ready") {
      noticeClaims.delete(sid);
      noticeOwner = sid;
      active.contextNoticeShown = true;
      const percent = Math.round(100 * usage.usedTokens / usage.windowTokens);
      $("context-notice-title").textContent = `이 대화의 컨텍스트를 약 ${percent}% 사용했어요.`;
      $("context-notice").hidden = false;
      return;
    }
    const owner = active, run = active.lastRunId || usage;
    if (noticeAttempts.get(owner) === run) return;
    noticeAttempts.set(owner, run);
    noticeClaims.set(sid, "pending");
    api("/api/session/context-notice", {id:sid}).then(response => {
      if (response.show) noticeClaims.set(sid, "ready");
      else noticeClaims.delete(sid);
      owner.contextNoticeShown = response.contextNoticeShown;
      if (active?.id === sid) updateNotice();
    }).catch(() => { noticeClaims.delete(sid); });
  }
  function renderHandoffIntro() {
    const info = active?.handoff;
    if (!info && introOwner === null) return;
    const intro = $("handoff-origin");
    intro.hidden = !info || !workView();
    if (intro.hidden) {
      if (introOwner !== null) {
        introOwner = null;
        $("handoff-summary").replaceChildren();
        $("handoff-source-open").textContent = "";
      }
      return;
    }
    if (introOwner !== active.id) {
      introOwner = active.id;
      const body = $("handoff-summary");
      body.replaceChildren();
      renderText(body, info.summary);
      intro.open = !active.messages?.length;
      $("handoff-source-open").textContent = info.sourceTitle ? `이전 업무 보기 · ${info.sourceTitle}` : "이전 업무 보기";
    }
    $("handoff-source-open").hidden = !sessions.some(row => row.id === info.sourceTaskId);
  }
  function update(checkNotice = true) {
    if($("branch-origin")) {
      $("branch-origin").hidden = !active?.branch || (!!globalThis.WorkspaceCapabilities?.isOpen()||!!globalThis.WorkspaceExtensions?.isOpen?.());
      $("branch-origin-status").textContent = active?.branch?.status === "pending" ? "첫 요청부터 이전 대화 기록을 이어받아요 (기존 대화 분기)" : "이전 대화 기록을 복사한 업무 (기존 대화 분기)";
      $("branch-source-open").hidden = !sessions.some(row => row.id === active?.branch?.sourceTaskId);
    }
    $("branch-open").hidden = !active || (!!globalThis.WorkspaceCapabilities?.isOpen()||!!globalThis.WorkspaceExtensions?.isOpen?.());
    const busy = pendingContinuations.has(active?.id);
    $("branch-open").disabled = !canBranch() || busy;
    if (branch.open) {
      $("handoff-create").disabled = !canContinue() || busy;
      $("branch-create").disabled = !canBranch() || busy;
    }
    renderHandoffIntro(); if (checkNotice) updateNotice();
    $("changes-open").disabled = !active;
  }
  function release() {
    generation++; fileGeneration++; context = null; runs = [];candidateContext=null;
    resetComparison(); $("changes-files").replaceChildren();
    $("changes-run").replaceChildren(); $("changes-file-name").textContent = "";
    $("changes-run").onchange = null;
  }
  function close() { closeExplorer?.(); if(changes.open) changes.close(); if(branch.open) branch.close(); release(); }
  function contextChanged() { hideNotice(); close(); update(false); }
  function resetComparison() {
    comparison = null; selectedHunk = 0;
    $("changes-content").replaceChildren();
    $("changes-tools").hidden = true;
  }
  function hunkRange(hunk) {
    return `@@ -${hunk.beforeStart},${hunk.beforeCount} +${hunk.afterStart},${hunk.afterCount} @@`;
  }
  function cell(row, side, showEnding) {
    const kind = row?.kind || "blank", node = el("div", null, "patch-cell patch-" + kind);
    const number = (value, label) => {
      const n = el("span", value ?? "", "patch-number");
      if(value != null) n.title = label + " " + value + "행";
      node.append(n);
    };
    if(side !== "after") number(row?.beforeLine, "요청 전");
    if(side !== "before") number(row?.afterLine, "요청 후");
    const mark = {add:"+", remove:"−"}[kind] || "";
    const code = el("code", row?.text ?? "", "patch-code");
    if(row?.highlights?.length) {
      const characters = Array.from(row.text);
      code.replaceChildren();
      let offset = 0;
      for(const [start, end] of row.highlights) {
        code.append(characters.slice(offset, start).join(""),
          el("mark", characters.slice(start, end).join(""), "patch-edit"));
        offset = end;
      }
      code.append(characters.slice(offset).join(""));
    }
    if(row && kind !== "context" && (row.ending === "" || showEnding)) {
      code.append(el("small", {"\r\n":"CRLF","\n":"LF","\r":"CR","":"줄바꿈 없음"}[row.ending], "patch-ending"));
    }
    node.append(el("span", mark, "patch-sign"), code);
    return node;
  }
  function splitRows(host, rows, showEnding) {
    const pair = (before, after) => {
      const row = el("div", null, "patch-pair");
      row.append(cell(before,"before",showEnding), cell(after,"after",showEnding)); host.append(row);
    };
    let index = 0;
    while(index < rows.length) {
      if(rows[index].kind === "context") {
        pair(rows[index], rows[index]); index++; continue;
      }
      const removed = [], added = [];
      while(index < rows.length && rows[index].kind !== "context") {
        const item = rows[index++];
        (item.kind === "remove" ? removed : added).push(item);
      }
      let left = 0, right = 0;
      const appendUntil = (leftEnd, rightEnd) => {
        while(left < leftEnd || right < rightEnd) {
          const before = left < leftEnd ? removed[left++] : null;
          const after = right < rightEnd ? added[right++] : null;
          pair(before, after);
        }
      };
      const oldOffsets = new Map(removed.map((item, i) => [item.beforeLine, i]));
      for(const [j, item] of added.entries()) {
        const i = oldOffsets.get(item.pairedBeforeLine);
        if(i === undefined || i < left) continue;
        appendUntil(i, j);
        pair(removed[left++], added[right++]);
      }
      appendUntil(removed.length, added.length);
    }
  }
  function chooseHunk(index, scroll) {
    const hunks = comparison?.value.hunks;
    if(!hunks?.[index]) return;
    selectedHunk = index;
    const blocks = $("changes-content").querySelectorAll(".patch-hunk");
    blocks.forEach((block, i) => block.firstElementChild.setAttribute("aria-pressed", String(i === index)));
    $("changes-position").textContent = `${index + 1} / ${hunks.length}`;
    $("changes-prev").disabled = index === 0;
    $("changes-next").disabled = index === hunks.length - 1;
    $("changes-question").disabled = !hunks[index].rows.some(row => row.kind !== "context");
    if(scroll) blocks[index]?.scrollIntoView({block:"nearest",inline:"nearest"});
  }
  function renderComparison() {
    if(!comparison) return;
    const {value} = comparison, content = $("changes-content"), split = mode === "split";
    const body = document.createDocumentFragment();
    const header = el("div", null, split ? "patch-head patch-pair" : "patch-head");
    if(split) header.append(el("span","− 요청 전"), el("span","+ 요청 후"));
    else header.append(el("span","전","patch-number"), el("span","후","patch-number"), el("span",""), el("span","내용"));
    body.append(header);
    // Show explicit line endings when the displayed edits use different styles.
    const endings = new Set(value.hunks.flatMap(h => h.rows.filter(r => r.kind !== "context").map(r => r.ending)));
    value.hunks.forEach((hunk, i) => {
      const block = el("section", null, "patch-hunk");
      const heading = el("button", `구간 ${i+1}   ${hunkRange(hunk)}`, "patch-heading");
      heading.type = "button"; heading.onclick = () => chooseHunk(i, false);
      block.append(heading);
      if(split) splitRows(block, hunk.rows, endings.size > 1);
      else for(const row of hunk.rows) block.append(cell(row, "unified", endings.size > 1));
      body.append(block);
    });
    content.dataset.mode = mode; content.replaceChildren(body);
    $("changes-unified").setAttribute("aria-pressed", String(!split));
    $("changes-split").setAttribute("aria-pressed", String(split));
    $("changes-tools").hidden = false; chooseHunk(selectedHunk, selectedHunk > 0);
  }
  $("changes-unified").onclick = () => { mode = "unified"; renderComparison(); };
  $("changes-split").onclick = () => { mode = "split"; renderComparison(); };
  $("changes-prev").onclick = () => chooseHunk(selectedHunk - 1, true);
  $("changes-next").onclick = () => chooseHunk(selectedHunk + 1, true);
  $("changes-question").onclick = () => {
    if(!comparison || !current(comparison.ticket) || $("changes-question").disabled) return;
    const {value,run,file} = comparison, hunk = value.hunks[selectedHunk];
    const edits = hunk.rows.filter(row => row.kind !== "context");
    const sample = edits.slice(0,8).map(row => (row.kind === "add" ? "+" : "-") + row.text.slice(0,180));
    if(edits.length > sample.length || edits.some(row => row.text.length > 180) || value.truncated) sample.push("… (일부 발췌)");
    const origin=run.attribution==="observed"?"명령 실행 중 확인된 미확정 폴더 변경이에요. 다른 업무나 외부 편집의 변경일 수 있어요.\n":"";
    const text = `${origin}${file.name}의 요청 전후 변경 구간을 설명해 주세요.\n요청 ${run.runId}  ${hunkRange(hunk)}\n${sample.join("\n")}`;
    const input = $("prompt"), next = input.value ? input.value + "\n\n" + text : text;
    if(input.disabled || input.readOnly || input.closest?.("[inert]") || next.length > (input.maxLength > 0 ? input.maxLength : 32000)) {
      toast("현재 입력창에 변경 구간을 추가할 수 없어요."); return;
    }
    close(); input.value = next; saveDraft();
    input.dispatchEvent(new Event("input", {bubbles:true}));
    input.focus(); input.setSelectionRange(next.length, next.length);
  };
  async function selectFile(file, run, ticket) {
    if (!current(ticket)) return;
    const request = ++fileGeneration;
    for(const button of $("changes-files").children) button.setAttribute("aria-current", String(button.dataset.file === file.id));
    $("changes-file-name").textContent = file.name;
    $("changes-note").textContent = "저장된 내용을 비교하고 있어요…";
    resetComparison();
    // File switching also invalidates a slower earlier response.
    const selected = file.id;
    $("changes-content").dataset.file = selected;
    try {
      const candidateQuery=candidateContext?`&candidate=${encodeURIComponent(candidateContext)}`:"";
      const value = await api(`/api/changes/file?id=${encodeURIComponent(context)}&run=${encodeURIComponent(run.runId)}&file=${encodeURIComponent(selected)}${candidateQuery}`);
      if(!current(ticket) || request !== fileGeneration) return;
      const reasons = {observation_incomplete:"일부 범위만 확인되어 전후 내용을 확정할 수 없어요.",text_not_captured:"문서 형식이나 크기 제한으로 텍스트를 저장하지 않은 파일이에요.",diff_input_limit:"행 수가 많아 텍스트 비교 범위를 넘었어요.",diff_work_limit:"내용이 복잡해 비교 범위를 넘었어요."};
      let note = reasons[value.reason] || "텍스트 비교를 제공하지 않는 파일이에요.";
      if(value.status === "text") {
        if(Array.isArray(value.hunks)) {
          const rows = value.hunks.flatMap(h => h.rows);
          note = rows.length ? `표시된 줄  +${rows.filter(r => r.kind === "add").length} 추가  −${rows.filter(r => r.kind === "remove").length} 삭제` :
            value.truncated ? "표시할 수 있는 변경 줄이 없어요." : "빈 파일이에요.";
          if(rows.length) { comparison = {ticket, value, run, file}; renderComparison(); }
        } else {
          note = "저장된 요청 전후 비교";
          $("changes-content").replaceChildren(el("pre", String(value.diff || ""), "patch-legacy"));
        }
      }
      if(value.truncated) note += " 표시 한도를 넘어 뒷부분을 생략했어요.";
      $("changes-note").textContent = note;
    } catch(e) { if(current(ticket) && request === fileGeneration) $("changes-note").textContent = e.message; }
  }
  function renderRun(ticket, targetPath) {
    fileGeneration++;
    const run = runs.find(row => row.runId === $("changes-run").value);
    $("changes-run").title = run?.runId || "";
    $("changes-intro").textContent = !run ? "" : run.attribution === "observed"
      ? "명령 실행 중 확인된 폴더 변경이에요. 다른 업무나 외부 편집의 변경이 포함될 수 있으며, 아직 이 업무 결과로 확정하지 않았어요."
      : !["tool","confirmed"].includes(run.attribution) ? "이전 버전에서 폴더 전체를 비교한 기록이에요. 다른 업무에서 바꾼 파일도 있을 수 있어요."
      : "요청 시작과 종료 시점에 저장한 내용이에요. 같은 파일을 함께 편집했다면 다른 작업의 변경도 포함될 수 있어요.";
    $("changes-files").replaceChildren(); resetComparison(); $("changes-file-name").textContent = "";
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
    if(targetPath != null && run?.status !== "capturing") {
      const selected = files.find(file => samePath(file.name, targetPath));
      if(selected) selectFile(selected, run, ticket);
      else {
        $("changes-layout").hidden = true; $("changes-empty").hidden = false;
        $("changes-empty").textContent = "이 요청에 선택한 파일의 전후 비교 기록이 없어요.";
      }
    } else if(files.length) selectFile(files[0], run, ticket);
  }
  function samePath(name, target) {
    const root = (active.workspace || "").replaceAll("\\","/").replace(/\/+$/,"");
    const windows = /^([a-z]:(?:\/|$)|\/\/)/i.test(root);
    const normalize = path => windows ? path.replaceAll("\\","/").toLowerCase() : path;
    const path = normalize(String(target));
    return path === normalize(name) || path === normalize(root + "/" + name);
  }
  async function openChanges({sessionId, runId, path, opener, popup = false, candidateId} = {}) {
    if(globalThis.WorkspaceModes?.isChat())return;
    if(!active || appClosed || (sessionId != null && sessionId !== active.id) || ((path != null||candidateId != null) && !runId)) return;
    const trigger = opener || document.activeElement;
    close(); priorFocus = trigger; context = active.id; candidateContext=candidateId||null;const ticket = ++generation;
    $("changes-intro").textContent = "파일을 선택하면 요청 전후의 내용을 비교할 수 있어요.";
    $("changes-empty").hidden = false; $("changes-empty").textContent = "변경 기록을 불러오고 있어요…";
    $("changes-layout").hidden = true; $("changes-summary").textContent = "";
    if(globalThis.WorkspaceResultExplorer?.openCustom) {
      const node = $("changes-review");
      setPanel("results");
      closeExplorer = globalThis.WorkspaceResultExplorer.openCustom({
        title:"파일 변경 비교", expanded:true, popup, node, opener:trigger, onClose:() => {
          if(generation !== ticket) return;
          changes.append(node); closeExplorer = null; release();
        }
      });
    } else showDialog("changes-dialog");
    try {
      const query = (runId ? `&run=${encodeURIComponent(runId)}` : "")+(candidateId?`&candidate=${encodeURIComponent(candidateId)}`:"");
      const response = await api(`/api/changes?id=${encodeURIComponent(context)}${query}`);
      if(!current(ticket)) return;
      runs = (response.runs || []).slice(0, 8);
      if(runId) runs = runs.filter(run => run.runId === runId);
      for(const run of runs) { const option = el("option", `${when(run.finishedAt || run.startedAt)} · ${run.runId.slice(0,8)}`); option.value = run.runId; $("changes-run").append(option); }
      $("changes-run").disabled = !runs.length||!!candidateId;
      globalThis.WorkspaceAppearance?.enhanceSelect?.($("changes-run"));
      $("changes-run").onchange = () => renderRun(ticket);
      renderRun(ticket, path);
      if(runId && !runs.length) $("changes-empty").textContent = "이 요청의 비교 기록을 찾을 수 없어요. 기록이 만료되었거나 저장되지 않았어요.";
      if(response.errors) $("changes-summary").textContent += " · 일부 기록을 읽지 못했어요";
    } catch(e) { if(current(ticket)) $("changes-empty").textContent = e.message; }
  }
  function openContinuationMenu() {
    if(globalThis.WorkspaceModes?.isChat())return;
    if(!canBranch() || appClosed || pendingContinuations.has(active.id)) return;
    close(); priorFocus = document.activeElement; context = active.id; const ticket = ++generation;
    $("branch-source-title").textContent = active.title;
    $("branch-source-path").textContent = active.workspace;
    $("handoff-create").disabled = !canContinue();
    $("handoff-status").hidden = true;
    $("branch-title").textContent = "새 업무에 무엇을 가져갈까요?";
    $("branch-choice").hidden = false;
    $("branch-status").textContent = "";
    $("branch-create").disabled = false;
    showDialog("branch-dialog");
    return ticket;
  }
  function openBranch() {
    if (openContinuationMenu() !== undefined) $("branch-create").focus();
  }
  async function createHandoff(fromNotice = false) {
    if (!canContinue() || pendingContinuations.has(active.id) ||
        (typeof pendingSelection !== "undefined" && pendingSelection)) return;
    const sid = active.id;
    if (fromNotice) {
      if (noticeOwner !== sid || openContinuationMenu() === undefined) return;
      hideNotice();
      $("branch-title").textContent = "요약으로 이어가기 (handoff)";
      $("branch-choice").hidden = true;
    } else if (!branch.open || context !== sid) return;
    const ticket = generation;
    const sameView = () => current(ticket) && branch.open && workView();
    const selection = typeof selectionGeneration !== "undefined" ? selectionGeneration : null;
    const status = $("handoff-status");
    pendingContinuations.add(sid);
    status.textContent = "이전 업무의 진행 내용과 남은 일을 정리하고 있어요…";
    status.hidden = false;
    update();
    try {
      const {session} = await api("/api/session/continue", {id:sid});
      if (!sessions.some(row => row.id === session.id)) {
        sessions.unshift(sessionMetadata(session));
        renderSessions();
      }
      if (sameView() && (selection === null || selection === selectionGeneration) &&
          !(typeof pendingSelection !== "undefined" && pendingSelection)) {
        if (await selectSession(session.id, {canActivate:sameView}) !== false) {
          if (branch.open) branch.close();
          toast("새 업무에서 이어갈 내용을 정리했어요.");
        }
      } else if (!appClosed && active?.id === sid) toast("업무 목록에 이어갈 새 업무를 추가했어요.");
    } catch (e) {
      if (sameView()) status.textContent = e.message;
      else if (!appClosed && active?.id === sid) toast(e.message);
    } finally {
      pendingContinuations.delete(sid);
      update();
    }
  }
  $("branch-create").onclick = async () => {
    if (!branch.open || context !== active?.id || !workView() || !canBranch() ||
        $("branch-choice").hidden || pendingContinuations.has(context) ||
        (typeof pendingSelection !== "undefined" && pendingSelection)) return;
    const ticket = generation, sid = context;
    const sameView = () => current(ticket) && branch.open && workView();
    const selection = typeof selectionGeneration !== "undefined" ? selectionGeneration : null;
    pendingContinuations.add(sid);
    update(false);
    $("branch-status").textContent = "이전 대화 기록으로 새 업무를 만들고 있어요…";
    let created = false;
    try {
      const {session} = await api("/api/session/branch", {id:sid});
      created = true;
      if (!sessions.some(row => row.id === session.id)) {
        sessions.unshift({...session, messages:undefined, artifacts:undefined});
        renderSessions();
      }
      if (!sameView() || (selection !== null && selection !== selectionGeneration) ||
          (typeof pendingSelection !== "undefined" && pendingSelection)) return;
      if (await selectSession(session.id, {canActivate:sameView}) !== false) {
        if (branch.open) branch.close();
        toast("새 업무를 만들었어요. 첫 요청부터 이전 대화 기록을 이어받아요.");
      }
    } catch (e) {
      if (sameView()) {
        if (created) {
          branch.close();
          toast("새 업무를 만들었어요. 업무 목록에서 열어 주세요.");
        } else $("branch-status").textContent = e.message;
      }
    } finally {
      pendingContinuations.delete(sid);
      update(false);
    }
  };
  for(const dialog of [changes, branch]) dialog.addEventListener("close", () => { if(changes.open || branch.open || closeExplorer) return; release(); if(priorFocus?.isConnected) priorFocus.focus(); });
  $("changes-close").onclick = () => closeExplorer ? closeExplorer() : changes.close();
  $("branch-close").onclick = $("branch-cancel").onclick = () => branch.close();
  $("changes-open").onclick = openChanges; $("branch-open").onclick = openContinuationMenu;
  $("handoff-create").onclick = () => createHandoff();
  $("context-continue").onclick = () => createHandoff(true);
  $("context-stay").onclick = hideNotice;
  $("handoff-source-open").onclick = () => {
    const id = active?.handoff?.sourceTaskId;
    if(id) selectSession(id).catch(e => toast(e.message));
  };
  document.addEventListener?.("visibilitychange", updateNotice);
  document.addEventListener?.("close", updateNotice, true);
  if($("branch-source-open")) $("branch-source-open").onclick = () => {
    const source = active?.branch?.sourceTaskId;
    if(source) selectSession(source).catch(e => toast(e.message));
  };
  update();
  return {openChanges, openBranch, canBranch, contextChanged, update, close};
})();
