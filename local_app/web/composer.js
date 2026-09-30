"use strict";

// Completion is scoped to the active task. Catalog browsing never supplies input candidates.
globalThis.WorkspaceComposer = (() => {
  const input = $("prompt"), box = $("composer-suggestions"), list = $("composer-suggestion-list");
  let generation = 0, controller = null, timer = null, composing = false;
  let current = null, rows = [], selected = 0, preparing = null, dismissed = false;
  const preparationAttempts = new Set();
  const pathKey = value => String(value).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  function fileReference(path, workspace = active?.workspace || "") {
    const full = path.replace(/\\/g, "/"), root = workspace.replace(/\\/g, "/").replace(/\/+$/, "");
    const reference = root && pathKey(full).startsWith(pathKey(root) + "/") ? full.slice(root.length + 1) : full;
    return "@" + (/[\s"]/u.test(reference) ? JSON.stringify(reference) : reference);
  }

  function trigger() {
    if (sending || choiceSubmission || appClosed || composing || input.readOnly) return null;
    const value = input.value, cursor = typeof input.selectionStart === "number" ? input.selectionStart : value.length;
    if (typeof input.selectionEnd === "number" && input.selectionEnd !== cursor) return null;
    const before = value.slice(0, cursor), slash = before.match(/(?:^|\s)\/([^\s/]{0,160})$/u);
    const quoted = !slash && before.match(/(?:^|\s)@"((?:[^"\\\n]|\\.){0,512})$/u);
    const file = !slash && !quoted && before.match(/(?:^|\s)@([^\s@"]{0,512})$/u);
    const terminal = !slash && !quoted && !file && /^\s*!/.test(value);
    if (!slash && !file && !quoted && !terminal) return null;
    const kind = terminal ? "terminal" : slash ? "slash" : "file";
    const start = terminal ? 0 : slash ? before.lastIndexOf("/") : before.lastIndexOf(quoted ? '@"' : "@");
    let end = cursor + value.slice(cursor).match(/^[^\s]*/u)[0].length;
    if (quoted) {
      end = value.length;
      for (let index = cursor; index < value.length; index++) {
        if (value[index] === "\\") { index++; continue; }
        if (value[index] === '"') { end = index + 1; break; }
        if (value[index] === "\n") { end = index; break; }
      }
    }
    return {kind, query:(slash ? slash[1] : quoted ? quoted[1].replace(/\\(["\\])/g, "$1") : file ? file[1] : "").trim(), start, end, cursor, value,
      sessionId:active?.id || null, workspace:active?.workspace || "", selection:selectionGeneration};
  }
  function same(value) {
    const now = trigger();
    return now && value && ["kind","query","start","end","cursor","value","sessionId","workspace","selection"].every(key => now[key] === value[key]);
  }
  function close() {
    generation++; clearTimeout(timer); if (controller) controller.abort(); controller = null;
    current = null; rows = []; selected = 0; box.hidden = true; list.replaceChildren();
    input.setAttribute("aria-expanded", "false"); input.setAttribute("aria-activedescendant", "");
    $("composer-connect").hidden = true; $("composer-native").hidden = true;
  }
  function activeOption() {
    for (const [index, node] of [...list.children].entries()) node.setAttribute("aria-selected", String(index === selected));
    const node = list.children[selected];
    input.setAttribute("aria-activedescendant", node?.id || ""); node?.scrollIntoView?.({block:"nearest"});
  }
  function render(response = {}) {
    list.replaceChildren(); box.hidden = false; input.setAttribute("aria-expanded", "true");
    $("composer-suggestion-title").textContent = current?.kind === "terminal" ? "Claude Code 터미널 명령"
      : current?.kind === "file" ? "파일 참조" : "스킬·명령";
    $("composer-suggestion-context").textContent = `${active ? basename(active.workspace) : "공통 스킬"} · ${rows.length ? `${rows.length}개 후보` : active ? "현재 업무 기준" : "업무 선택 전"}`;
    $("composer-connect").hidden = !active || current?.kind !== "slash" || response.connectRequired !== true || Boolean(preparing);
    $("composer-connect").disabled = Boolean(preparing);
    $("composer-connect").textContent = preparing ? "명령 불러오는 중…" : "Claude 명령 불러오기";
    $("composer-native").hidden = current?.kind !== "terminal" && !rows.some(row => row.supported === false);
    $("composer-suggestion-note").textContent = rows.length
      ? "↑ ↓ 이동 · Tab / Enter 선택 · Esc 닫기" + (response.discovery ? " · 설치 정보 기준이며 실행 시 Claude가 확인합니다." : "") + (response.limited ? " · 검색어를 더 입력해 주세요." : "")
      : response.message || (preparing?.id === active?.id && active ? "Claude 명령을 자동으로 불러오고 있어요. 질문은 전송하지 않습니다."
      : !active ? "설치된 공통 스킬에서 일치하는 항목을 찾지 못했어요. 업무를 선택하면 해당 폴더의 명령도 확인할 수 있어요."
      : response.connectRequired ? "연결에서 명령 목록을 확인하지 못했어요. ‘Claude 명령 불러오기’로 다시 시도할 수 있어요."
      : response.message || (response.limited
        ? current?.kind === "file" ? "파일 목록을 일부만 확인했어요. 자료 추가에서 필요한 파일을 직접 선택할 수 있어요." : "현재 업무 연결의 호출 목록을 충분히 확인하지 못했어요. 연결 상태를 확인해 주세요."
        : current?.kind === "file" ? "일치하는 파일이 없어요. 자료 추가로 다른 위치의 파일도 선택할 수 있어요."
        : current?.query ? "현재 연결이 보고한 목록에 일치하는 명령이 없어요." : "현재 연결이 보고한 스킬·명령 목록이 비어 있어요."));
    const ticket = generation;
    rows.forEach((row, index) => {
      const button = el("button", null, "composer-suggestion" + (row.supported === false ? " unavailable" : ""));
      button.type = "button"; button.id = `composer-option-${ticket}-${index}`; button.setAttribute("role", "option");
      button.tabIndex = -1; button.setAttribute("aria-disabled", String(row.supported === false));
      const symbol = el("span", current?.kind === "file" ? "@" : "/", "completion-symbol"); symbol.setAttribute("aria-hidden", "true");
      const copy = el("span", null, "completion-copy"), title = el("strong");
      const label = row.label || row.invocation || basename(row.path), query = current?.query || "";
      const match = query ? label.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
      if (match < 0) title.textContent = label;
      else title.append(document.createTextNode(label.slice(0, match)), el("mark", label.slice(match, match + query.length)), document.createTextNode(label.slice(match + query.length)));
      copy.append(title);
      if (row.description) copy.append(el("small", row.description));
      button.append(symbol, copy, el("span", row.supported === false ? "터미널" : current?.kind === "file" ? "파일" : row.source === "workspace" ? "설정" : row.source === "installed" ? (row.scope === "folder" ? "폴더 스킬" : "공통 스킬") : "Claude", "completion-kind"));
      if (row.supported === false) button.append(el("small", row.reason || "이 앱에서 실행 지원을 확인하지 못했습니다.", "completion-unavailable"));
      button.onmousedown = event => event.preventDefault();
      button.onclick = () => choose(index, ticket); list.append(button);
    });
    activeOption();
  }
  async function refresh() {
    const request = trigger(); if (!request) { close(); return; }
    const ticket = ++generation; if (controller) controller.abort(); controller = new AbortController();
    current = request; rows = []; selected = 0; render({message:"목록을 확인하고 있어요."});
    if (request.kind === "terminal") {
      render({message:"! 명령은 원본 Claude Code의 셸 모드에서 실행하세요. 입력 내용은 그대로 유지됩니다."}); return;
    }
    if (request.kind === "file" && !request.sessionId) {
      render({message:"업무를 선택하면 그 폴더의 파일을 바로 추천해요. ‘새 업무’에서 폴더를 선택해 주세요."}); return;
    }
    try {
      const response = await api("/api/completions", {id:request.sessionId, kind:request.kind, query:request.query,
        ...(request.kind === "file" ? {attachments:[...attachments]} : {})}, controller.signal);
      if (ticket !== generation || !same(request)) return;
      const seen = new Set();
      rows = (Array.isArray(response.items) ? response.items : []).slice(0,40).filter(row => {
        if (!row || typeof row.id !== "string" || seen.has(row.id)) return false;
        if (request.kind === "file" ? typeof row.path !== "string" : typeof row.invocation !== "string") return false;
        seen.add(row.id); return true;
      }).map(row => ({...row, supported:request.kind === "file" ? row.supported !== false : row.supported === true}));
      render({...response,limited:response.limited === true || (Array.isArray(response.items) && response.items.length > 40)});
      if (request.kind === "slash" && response.connectRequired === true && active?.trusted
          && !preparing && !preparationAttempts.has(active.id) && !busyStates.has(active.state)) {
        return prepareConnection({automatic:true});
      }
    } catch (err) {
      if (err.name === "AbortError" || ticket !== generation || !same(request)) return;
      rows = []; render({message:err.message});
    }
  }
  function schedule() {
    globalThis.WorkspaceInlineControls?.close();
    dismissed = false;
    close(); current = trigger(); if (!current) return;
    render({message:current.kind === "file" ? "파일을 찾고 있어요." : "사용 가능한 명령을 찾고 있어요."});
    timer = setTimeout(refresh, 140);
  }
  function choose(index, ticket = generation) {
    const row = rows[index], request = current;
    if (!row || ticket !== generation || !same(request)) { close(); return; }
    if (!row.supported) return toast(row.reason || "이 항목은 설정의 원본 Claude Code에서 확인해 주세요.");
    let replacement = "";
    if (request.kind === "file") {
      const exists = attachments.some(path => pathKey(path) === pathKey(row.path));
      if (!exists && attachments.length >= 12) return toast("자료는 한 번에 12개까지 추가할 수 있어요.");
      if (!exists) attachments = [...attachments, row.path];
      replacement = fileReference(row.path, request.workspace)
        + (/^\s/.test(request.value.slice(request.end)) ? "" : " ");
    } else {
      const invocation = row.invocation.replace(/^\/+/, "");
      if (!invocation || /[\s\x00-\x1f]/u.test(invocation)) return toast("호출 이름을 확인하지 못했어요. 원본 Claude Code에서 확인해 주세요.");
      replacement = "/" + invocation + (request.value.slice(request.end).startsWith(" ") ? "" : " ");
    }
    const value = request.value.slice(0, request.start) + replacement + request.value.slice(request.end), cursor = request.start + replacement.length;
    close(); input.value = value; input.focus();
    if (input.setSelectionRange) input.setSelectionRange(cursor, cursor); else input.selectionStart = input.selectionEnd = cursor;
    renderAttachments(); saveDraft();
  }
  function keydown(event) {
    if (event.defaultPrevented) return false;
    if (composing || event.isComposing || event.keyCode === 229) return true;
    if (box.hidden) return false;
    const plain = !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
    if (event.key === "Escape" && plain) { event.preventDefault(); dismissed = true; close(); return true; }
    if (!rows.length) return false;
    if ((plain && ["ArrowDown","ArrowUp"].includes(event.key))
        || (event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && ["n","p"].includes(event.key))) {
      const next = event.key === "ArrowDown" || event.key === "n";
      event.preventDefault(); selected = (selected + (next ? 1 : rows.length - 1)) % rows.length; activeOption(); return true;
    }
    if (["Enter","Tab"].includes(event.key) && plain) { event.preventDefault(); choose(selected); return true; }
    return false;
  }
  $("composer-connect").onmousedown = event => event.preventDefault();
  async function prepareConnection({automatic=false} = {}) {
    if (!active || preparing || sending || choiceSubmission || appClosed) return;
    if (!active.trusted) { chooseFolder(true); $("folder-form").dataset.afterTrust = "commands"; return; }
    const context = {id:active.id, selection:selectionGeneration}; preparing = context;
    preparationAttempts.add(context.id);
    $("composer-connect").disabled = true; $("composer-connect").textContent = "명령 불러오는 중…";
    try {
      // Completion and Shift+Tab can ask for the same connection in one key
      // sequence. Share only the in-flight request for this exact task/view.
      context.promise = globalThis.WorkspaceInlineControls?.waitForPreparation?.()
        || api("/api/connect", {id:context.id});
      const response = await context.promise;
      if (active?.id !== context.id || selectionGeneration !== context.selection || appClosed) return;
      active.connection = response.connection; renderConnection(response.connection);
      if (!automatic) input.focus();
      if (!dismissed && document.activeElement === input && trigger()) await refresh();
    } catch (err) {
      if (active?.id === context.id && selectionGeneration === context.selection && !appClosed) {
        if (current && document.activeElement === input) render({message:err.message, connectRequired:true});
        else if (!automatic) toast(err.message);
      }
    } finally {
      if (preparing === context) {
        preparing = null; $("composer-connect").disabled = false; $("composer-connect").textContent = "Claude 명령 불러오기";
        if (current?.kind === "slash" && !box.hidden && active?.id === context.id && !active.connection?.connected) $("composer-connect").hidden = false;
      }
    }
  }
  $("composer-connect").onclick = prepareConnection;
  $("composer-native").onmousedown = event => event.preventDefault();
  $("composer-native").onclick = () => { close(); $("native").onclick(); };
  function removeFileReference(path) {
    const references = new Set([fileReference(path), fileReference(path, "")]);
    for (const reference of references) {
      const escaped = reference.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      input.value = input.value.replace(new RegExp(`(^|\\s)${escaped}(?=\\s|$)`, "giu"), "$1");
    }
    saveDraft(); close();
  }
  function beforeSubmit() {
    if (/^\s*!/.test(input.value)) {
      refresh(); toast("! 셸 명령은 ‘원본 Claude Code 열기’에서 실행해 주세요. 입력 내용은 보존했어요."); return false;
    }
    return true;
  }
  const priorInput = input.oninput;
  input.oninput = event => { priorInput?.call(input, event); saveDraft(); schedule(); };
  input.oncompositionstart = () => { composing = true; close(); };
  input.oncompositionend = () => { composing = false; schedule(); };
  input.onclick = schedule;
  input.onfocus = () => { if (box.hidden) schedule(); };
  input.onkeyup = event => { if (["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) schedule(); };
  input.onblur = event => { if (!box.contains?.(event?.relatedTarget)) close(); };
  box.onfocusout = event => { if (event.relatedTarget !== input && !box.contains?.(event.relatedTarget)) close(); };
  input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-controls", "composer-suggestion-list"); input.setAttribute("aria-expanded", "false");
  function connectionChanged() {
    if (!dismissed && document.activeElement === input && trigger()) return refresh();
    close();
  }
  function waitForPreparation() {
    return preparing?.id === active?.id && preparing?.selection === selectionGeneration ? preparing.promise : null;
  }
  return {refresh, keydown, close, beforeSubmit, removeFileReference, prepareConnection, waitForPreparation, connectionChanged, contextChanged:close};
})();
