"use strict";

// These controls use the existing task connection; they never edit Claude settings.
globalThis.WorkspaceInlineControls = (() => {
  const panel = $("composer-controls-panel"), options = $("runtime-panel-options"), extra = $("runtime-panel-extra");
  const buttons = {model:$("composer-model"), effort:$("composer-effort"), permission:$("composer-permission")};
  const titles = {model:"모델 선택", effort:"Effort · 사고 수준", permission:"승인 모드"};
  const restoreKind = control => control === "permissionMode" ? "permission" : control;
  let view = null, pending = false, notice = null, trustReturn = null, preparation = null;
  let positionFrame = null, positionObserver = null, watchingPosition = false;
  function placePanel() {
    if (!view || panel.hidden || !panel.offsetParent) return;
    const anchor = buttons[view.kind], parent = panel.offsetParent;
    const rect = anchor.getBoundingClientRect(), origin = parent.getBoundingClientRect();
    const viewport = globalThis.visualViewport;
    const viewportLeft = viewport?.offsetLeft || 0, viewportTop = viewport?.offsetTop || 0;
    const viewportRight = viewportLeft + (viewport?.width || document.documentElement.clientWidth);
    const viewportBottom = viewportTop + (viewport?.height || document.documentElement.clientHeight);
    // main clips overflow: keep the menu inside both the viewport and the chat pane.
    const clip = panel.closest("main")?.getBoundingClientRect();
    const leftEdge = Math.max(viewportLeft, clip?.left ?? viewportLeft) + 10;
    const rightEdge = Math.min(viewportRight, clip?.right ?? viewportRight) - 10;
    const topEdge = Math.max(viewportTop, clip?.top ?? viewportTop) + 10;
    const bottomEdge = Math.min(viewportBottom, clip?.bottom ?? viewportBottom) - 10;
    if (rect.width <= 0 || rightEdge <= leftEdge || bottomEdge <= topEdge) return;
    const gap = 8, width = Math.min(430, rightEdge - leftEdge);
    const above = Math.max(0, rect.top - gap - topEdge), below = Math.max(0, bottomEdge - rect.bottom - gap);
    const useBelow = above < 160 && below > above;
    panel.style.width = `${width}px`;
    panel.style.maxHeight = `${Math.min(430, useBelow ? below : above)}px`;
    const height = panel.getBoundingClientRect().height;
    const left = Math.max(leftEdge, Math.min(rect.left, rightEdge - width));
    const top = Math.max(topEdge, Math.min(useBelow ? rect.bottom + gap : rect.top - gap - height, bottomEdge - height));
    panel.style.left = `${left - origin.left - parent.clientLeft + parent.scrollLeft}px`;
    panel.style.top = `${top - origin.top - parent.clientTop + parent.scrollTop}px`;
  }
  function schedulePosition() {
    if (!view || panel.hidden || !panel.getBoundingClientRect || positionFrame !== null) return;
    positionFrame = requestAnimationFrame(() => { positionFrame = null; placePanel(); });
  }
  function positionOnScroll(event) {
    if (!panel.contains(event.target)) schedulePosition();
  }
  function watchPosition() {
    if (watchingPosition) return schedulePosition();
    watchingPosition = true;
    globalThis.addEventListener?.("resize", schedulePosition);
    globalThis.addEventListener?.("scroll", positionOnScroll, true);
    globalThis.visualViewport?.addEventListener("resize", schedulePosition);
    globalThis.visualViewport?.addEventListener("scroll", schedulePosition);
    if (typeof ResizeObserver !== "undefined") {
      positionObserver = new ResizeObserver(schedulePosition);
      for (const node of [panel, $("composer"), panel.closest("main"), ...Object.values(buttons)]) {
        if (node) positionObserver.observe(node);
      }
    }
    schedulePosition();
  }
  function unwatchPosition() {
    if (positionFrame !== null) cancelAnimationFrame(positionFrame);
    positionFrame = null; positionObserver?.disconnect(); positionObserver = null; watchingPosition = false;
    globalThis.removeEventListener?.("resize", schedulePosition);
    globalThis.removeEventListener?.("scroll", positionOnScroll, true);
    globalThis.visualViewport?.removeEventListener("resize", schedulePosition);
    globalThis.visualViewport?.removeEventListener("scroll", schedulePosition);
  }
  const context = () => ({id:active?.id || null, selection:selectionGeneration});
  const same = value => value && value.id === (active?.id || null) && value.selection === selectionGeneration && !appClosed;
  const named = (rows, value) => rows.find(row => row.value === value)?.displayName || rows.find(row => row.value === value)?.label || value;
  const stopBlocked = () => !!globalThis.WorkspaceStop?.blocked();
  const blocked = (kind="model") => kind==="permission" ? permissionConnectionLocked() : connectionLocked();
  function close({focus=false} = {}) {
    const old = view; view = null; panel.hidden = true;
    unwatchPosition();
    Object.values(buttons).forEach(button => button.setAttribute("aria-expanded", "false"));
    if (focus && old && same(old)) buttons[old.kind].focus();
  }
  function status(text, isError=false, source=context()) {
    if (!same(source)) return;
    notice = {...source,text,isError,restore:!!controlRestoreState(),pendingPermission:source.kind==="permission"&&active?.connection?.permissionModeChangePending===true}; render();
  }
  function selectedOption(kind,value) {
    const info=active?.connection||{};
    if(kind==="permission")return value===null?!info.permissionModeOverride:permissionOptionValue(info.permissionMode||info.permissionModeOverride)===value;
    if(kind==="effort")return value===null?!info.effortOverride:(info.effortSupport==="confirmed"?info.effort:info.effortOverride||info.effort)===value;
    return value===null?!active?.modelOverride:(active?.modelOverride||info.modelOverride||info.model)===value;
  }
  function optionSignature() {
    const info=active?.connection||{};
    const choices=view?.kind==="model"?modelOptions():view?.kind==="effort"?effortOptions():permissionOptions();
    return JSON.stringify([choices,info.capabilities,info.effortResetAvailable,info.permissionModeResetAvailable,info.controlRestore||null]);
  }
  function render() {
    const info = active?.connection || {};
    const model = active?.modelOverride || info.modelOverride || info.model;
    const effort = info.effortOverride && info.effortSupport !== "confirmed" ? `요청 ${info.effortOverride}` : info.effort || (info.effortOverride ? `요청 ${info.effortOverride}` : active ? "미확인" : "기존 설정");
    const permission = info.permissionModeChangePending===true&&!info.permissionMode ? "변경 확인 중" : info.permissionModeLabel || named(permissionOptions(), permissionOptionValue(info.permissionMode || info.permissionModeOverride)) || "기존 설정";
    const values = {model:named(modelOptions(),model) || "기존 설정", effort, permission};
    for (const [kind,button] of Object.entries(buttons)) {
      button.querySelector("strong").textContent = values[kind]; button.title = titles[kind] + ": " + values[kind];
      button.disabled = appClosed || pending || (active && blocked(kind));
      button.classList.toggle("bypass-active",kind==="permission"&&info.permissionMode==="bypassPermissions");
      button.setAttribute("aria-expanded", String(view?.kind === kind && !panel.hidden));
    }
    if (view && (!same(view) || appClosed || stopBlocked() || (active && busyStates.has(active.state) && (view.kind!=="permission" || !pending&&blocked("permission"))))) close();
    if(view&&!panel.hidden&&view.optionSignature&&view.optionSignature!==optionSignature()){
      const input=extra.querySelector("input"),inputText=input?.value,inputFocused=input===document.activeElement;
      const focused=[...options.children].find(button=>button===document.activeElement)?.dataset.runtimeValue;
      const previousError=$("runtime-panel-error").textContent;
      draw();
      const replacement=extra.querySelector("input");if(replacement&&inputText!==undefined){replacement.value=inputText;if(inputFocused)replacement.focus();}
      if(focused!==undefined)[...options.children].find(button=>button.dataset.runtimeValue===focused)?.focus();
      if(previousError&&controlRestoreState())failure(previousError);
    }
    // Metadata may arrive while this list is open. Update its checkmarks in
    // place so the current value agrees with the footer without losing focus
    // or text in the custom-model input.
    if(view&&!panel.hidden)for(const button of options.children){
      const selected=selectedOption(view.kind,button.dataset.runtimeValue||null);
      button.setAttribute("aria-pressed",String(selected));button.children[button.children.length-1].textContent=selected?"✓":"";
    }
    if(same(notice)&&notice.restore&&!controlRestoreState())notice=null;
    if(same(notice)&&notice.pendingPermission&&info.permissionModeChangePending!==true)notice=null;
    const node = $("composer-control-status"), visibleNotice = same(notice) ? notice : null;
    const changing = modelChanging || permissionChanging || info.permissionModeChangePending===true || effortChanging, restore = controlRestoreState(), issue = controlRestoreIssues()[0];
    const restoreText = restore ? (issue?.message || "이전 선택을 적용하지 못했어요. 사용할 모델·Effort·승인 모드를 확인해 주세요.") : "";
    const text = stopBlocked() ? "중지 상태를 확인한 뒤 모델·Effort·승인 모드를 바꿀 수 있어요." : connectionPreparing ? "Claude 연결에서 선택 항목을 불러오는 중…" : changing ? "변경을 확인하고 있어요…"
      : (visibleNotice?.isError ? visibleNotice.text : restoreText || visibleNotice?.text) || (active && busyStates.has(active.state) ? info.capabilities?.setPermissionModeWhileRunning===true ? "작업 중에도 승인 모드는 바꿀 수 있어요. 모델·Effort는 요청이 끝난 뒤 변경할 수 있어요." : "요청이 끝나면 모델·Effort·승인 모드를 바꿀 수 있어요." : "");
    node.replaceChildren(); node.textContent = text; node.hidden = !text; node.dataset.error = String(Boolean((restore || visibleNotice?.isError) && !changing));
    if (restore && !changing && !connectionPreparing) {
      const kind=restoreKind(issue?.control)||"model", button=el("button",titles[kind],"text-button");button.type="button";
      button.disabled=blocked()||pending;button.onclick=()=>recover();node.append(button);
    }
    if (view && !panel.hidden) watchPosition();
  }
  function message(text) { $("runtime-panel-note").textContent = text; schedulePosition(); }
  function failure(text) { $("runtime-panel-error").textContent = text; $("runtime-panel-error").hidden = !text; schedulePosition(); }
  function action(label, handler) {
    const button = el("button", label, "quiet-button runtime-panel-action"); button.type = "button";
    button.onclick = handler; extra.append(button); return button;
  }
  function option(value, label, description, selected, enabled=true) {
    const button = el("button", null, "runtime-option"), copy = el("span"); button.type = "button";
    copy.append(el("strong", label)); if (description) copy.append(el("small", description));
    button.append(copy,el("span", selected ? "✓" : "")); button.setAttribute("aria-pressed", String(selected));
    button.dataset.runtimeValue=value===null?"":value;
    button.disabled = !enabled || pending || blocked(view?.kind); button.onclick = () => apply(value); options.append(button);
  }
  function draw() {
    if (!view || !same(view)) return close();
    view.optionSignature=optionSignature();
    options.replaceChildren(); extra.replaceChildren(); failure("");
    $("runtime-panel-title").textContent = titles[view.kind];
    options.setAttribute("aria-label", titles[view.kind]);
    if (!active) {
      message("업무를 선택하면 그 연결의 모델과 실행 방식을 여기서 바로 바꿀 수 있어요.");
      action("새 업무 시작", () => { close(); $("new-chat").onclick(); }); return;
    }
    const info = active.connection || {};
    if (view.kind === "model") {
      const can = info.capabilities?.setModel === true;
      message(can ? "선택하면 이 업무 연결에 적용해요. 개인 기본 설정은 유지됩니다." : "현재 연결에서 모델 변경을 제공하지 않아요. 기존 모델을 사용합니다.");
      option(null, "기존 설정 사용", "이 업무에서 처음 확인한 모델로 돌아갑니다.", !active.modelOverride, can);
      for (const row of modelOptions()) option(row.value,row.displayName || row.value,row.description || row.value,
        (active.modelOverride || info.model) === row.value,can);
      if (can) {
        const box = el("div",null,"runtime-custom"), label = el("label","모델 이름 직접 입력"); label.setAttribute("for","inline-model-name");
        const row = el("div",null,"runtime-custom-row"), input = el("input"), applyButton = el("button","적용","quiet-button");
        input.id = "inline-model-name"; input.type = "text"; input.maxLength = 200; input.placeholder = "회사 모델 이름";
        input.setAttribute("aria-label","모델 이름 직접 입력"); applyButton.type = "button";
        const send = () => input.value.trim() ? apply(input.value.trim()) : failure("모델 이름을 입력해 주세요.");
        applyButton.onclick = send; input.onkeydown = event => { if (event.key === "Enter" && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229) {event.preventDefault();send();} };
        row.append(input,applyButton); box.append(label,row); extra.append(box);
      }
    } else if (view.kind === "permission") {
      const can = info.capabilities?.setPermissionMode === true;
      message("선택한 방식은 이 업무에 적용해요. 기존 개인·회사 정책은 바꾸지 않습니다.");
      option(null,"기존 설정 사용",info.permissionModeResetRequiresReconnect ? "다음 요청에서 기존 승인 설정을 다시 불러옵니다." : "원래 승인 방식을 사용합니다.",!info.permissionModeOverride,info.permissionModeResetAvailable !== false && !(busyStates.has(active.state)&&info.permissionModeResetRequiresReconnect===true) && (can || !!info.permissionModeOverride || hasControlRestoreIssue("permissionMode")));
      for (const row of permissionOptions()) option(row.value,(row.risk === 'high' ? '⚠ ' : '')+(row.displayName || row.value),row.description || row.value,
        permissionOptionValue(info.permissionMode || info.permissionModeOverride) === row.value,can);
      if (!can) message("현재 연결은 승인 모드 변경을 제공하지 않아요. 기존 승인 흐름을 유지합니다.");
    } else {
      const can = info.capabilities?.setEffort === true;
      message(can ? "사고 수준을 선택해요. 모델·회사 정책에 따라 실제 적용 수준이 제한될 수 있습니다." : "현재 CLI에서 Effort 변경을 확인하지 못했어요. 기존 설정을 사용합니다.");
      option(null,"auto","이 연결에서 변경 전 확인한 Effort로 돌아갑니다. 개인 설정은 바꾸지 않아요.",!info.effortOverride,(!!info.effortOverride || hasControlRestoreIssue("effort")) && info.effortResetAvailable !== false);
      for (const row of effortOptions()) option(row.value,row.value,row.description || row.value,
        (info.effortSupport === "confirmed" ? info.effort : info.effortOverride || info.effort) === row.value,can);
    }
    showRestoreIssue();
  }
  function showRestoreIssue() {
    const issue=controlRestoreIssues().find(item=>restoreKind(item.control)===view?.kind);
    if(!issue)return;
    message((issue.message || "이전 선택을 적용하지 못했어요.")+" 사용할 값을 확인해 주세요. 작성한 요청은 전송되지 않습니다.");
    if(issue.canUseCurrent===true)action(`현재 CLI 값 사용${issue.currentValue ? " · "+issue.currentValue : ""}`,()=>acceptCurrent(issue.control));
    if(view.kind==="effort")action("모델 선택",()=>open("model"));
  }
  async function prepare(source) {
    if (!same(source) || !active || pending || connectionPreparing || blocked()) return false;
    if (!active.trusted) {
      trustReturn = {...source}; close(); chooseFolder(true); $("folder-form").dataset.afterTrust = "controls"; return false;
    }
    pending = true; connectionPreparing = true; options.replaceChildren(); extra.replaceChildren(); failure("");
    message("기존 Claude 연결을 준비하고 있어요. AI 질문은 보내지 않습니다."); setStatus(active.state);
    const request = {...source};
    try {
      request.promise = globalThis.WorkspaceComposer?.waitForPreparation?.() || api("/api/connect",{id:source.id});
      preparation = request;
      const response = await request.promise;
      if (!same(source)) return false;
      active.connection = response.connection; applyConnectionState(response); renderConnection(active.connection);
    } catch (err) {
      if (same(source) && view?.kind === source.kind) {
        message("연결의 선택 항목을 불러오지 못했어요."); failure(err.message);
        action("다시 불러오기", () => prepare({...view}));
      }
      return false;
    } finally {
      if (preparation === request) preparation = null;
      pending = false; connectionPreparing = false; setStatus(active?.state || "idle");
    }
    if (view && same(source)) draw();
    return same(source) && active?.connection?.connected === true;
  }
  async function open(kind, intent={}) {
    if (view?.kind === kind && !intent.commandDraft && !intent.cycle) return close({focus:true});
    if (appClosed || pending || (active && blocked(kind))) return;
    globalThis.WorkspaceComposer?.close(); view = {...context(),kind,...intent}; panel.hidden = false; notice = null; render();
    const opened = view;
    $("runtime-panel-title").textContent = titles[kind];
    if (active && (!active.trusted || active.connection?.connected !== true)) await prepare({...view}); else draw();
    return view === opened ? opened : null;
  }
  async function recover() {
    if(!controlRestoreState() || blocked() || pending)return;
    const kind=restoreKind(controlRestoreIssues()[0]?.control)||"model";
    if(view?.kind===kind&&same(view)){draw();render();return;}
    return open(kind);
  }
  async function acceptCurrent(control) {
    if(!view||!same(view)||blocked()||pending)return;
    const source={...view};pending=true;failure("");render();
    for(const node of options.children)node.disabled=true;
    try{
      const result=await useCurrentControl(control);if(!same(source))return;
      if(result?.ok){close();status("현재 CLI 값을 사용합니다. 작성한 요청은 전송하지 않았어요.",false,source);$("prompt").focus();}
      else if(result?.error){failure(result.error);status(result.error,true,source);}
      return result;
    }finally{
      pending=false;render();
      if(view&&same(source)){const text=$("runtime-panel-error").textContent;draw();if(text)failure(text);}
    }
  }
  async function apply(value, request=view) {
    if (!request || !same(request) || blocked(request.kind) || pending) return null;
    const source = {...request}; let result;
    pending = true; failure(""); render();
    for (const node of options.children) node.disabled = true;
    try {
      if (source.kind === "model") result = await setModel(value);
      else if (source.kind === "permission") result = await setPermissionMode(value);
      else result = await setEffort(value);
      if (!same(source)) return;
      if (result?.ok) {
        close({focus:!source.commandDraft && !source.cycle});
        if (typeof source.commandDraft === "string" && $("prompt").value === source.commandDraft) {
          $("prompt").value = ""; saveDraft(); globalThis.WorkspaceComposer?.close();
        }
        if (source.commandDraft || source.cycle) $("prompt").focus();
        const actualEffort = active.connection?.effort, confirmedEffort = active.connection?.effortSupport === "confirmed";
        status(source.kind === "effort" ? (value === null ? result.response?.alreadyInherited ? "Effort auto · 기존 설정을 이미 상속하고 있어요." : "Effort auto · 이 연결에서 변경 전 확인한 수준으로 복원했어요." : confirmedEffort && actualEffort
            ? actualEffort === value ? `Effort ${actualEffort} 적용을 확인했어요.` : `Effort 요청 ${value} · 실제 적용 ${actualEffort} (모델·회사 정책 제한)`
            : `Effort ${value} 요청을 반영했어요. 실제 적용 수준은 확인하지 못했습니다.`)
          : source.kind === "model" ? "모델 변경을 확인했어요. 이 업무 연결에만 적용합니다." : result.response?.reconnectRequired ? "다음 요청부터 기존 승인 설정을 사용합니다." : "승인 모드 변경을 확인했어요. 이 연결에만 적용합니다.",false,source);
      } else if (result?.error) { failure(result.error); status(result.error,true,source); }
      return result;
    } finally {
      pending = false; render();
      if (view && same(source)) { const text = $("runtime-panel-error").textContent; draw(); if (text) failure(text); }
    }
  }
  async function resumeAfterTrust() {
    const source = trustReturn; trustReturn = null;
    if (same(source)) {
      view = source; panel.hidden = false; render();
      if (!await prepare(source) || view !== source) return;
      if (source.cycle) await nextPermission(source);
      else if (source.commandValue && $("prompt").value === source.commandDraft) await apply(source.commandValue === "auto" ? null : source.commandValue,source);
    }
  }
  async function handleCommand(text) {
    const match = text.trim().match(/^\/effort(?:\s+([\s\S]*))?$/u);
    if (!match) return false;
    const value = (match[1] || "").trim();
    if (value && !["low","medium","high","xhigh","max","auto"].includes(value)) {
      globalThis.WorkspaceComposer?.close();
      status("/effort 다음에는 low, medium, high, xhigh, max 또는 auto를 입력해 주세요. 지원하는 값은 모델마다 달라요.",true); return true;
    }
    await open("effort",{commandDraft:text,commandValue:value});
    if (value && view?.commandDraft === text && same(view) && active?.trusted && active.connection?.connected === true)
      await apply(value === "auto" ? null : value);
    return true;
  }
  async function nextPermission(source) {
    if (!same(source) || blocked("permission") || pending) return;
    const info = active.connection || {}, supported = new Set(permissionOptions().map(row => row.value));
    const cycle = [...new Set((Array.isArray(info.permissionModeCycle) ? info.permissionModeCycle : []).map(mode=>permissionOptionValue(mode)))]
      .filter(mode => mode !== 'bypassPermissions' && supported.has(mode));
    const current = permissionOptionValue(info.permissionMode);
    if (!info.capabilities?.setPermissionMode || cycle.length < 2 || (!cycle.includes(current) && current !== 'bypassPermissions')) {
      status("현재 연결에서 승인 모드 순환을 확인하지 못했어요. 선택 목록에서 지원하는 모드를 확인해 주세요.",true,source); return;
    }
    // Leaving Bypass is safe through the shortcut; entering it always requires the explicit choice.
    await apply(current === 'bypassPermissions' ? cycle[0] : cycle[(cycle.indexOf(current) + 1) % cycle.length],source);
  }
  async function cyclePermission() {
    const source = {...context(),kind:"permission",cycle:true};
    if (!active || blocked("permission") || pending) return;
    if (!active.trusted || active.connection?.connected !== true) {
      const opened = await open("permission",{cycle:true});
      if (!opened || view !== opened || !same(source) || active?.connection?.connected !== true || !active.trusted) return;
    } else globalThis.WorkspaceComposer?.close();
    await nextPermission(source);
  }
  function keydown(event) {
    if (event.defaultPrevented) return false;
    if (event.key === "Escape" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey
        && !event.isComposing && event.keyCode !== 229 && document.activeElement === $("prompt") && view && !panel.hidden) {
      event.preventDefault(); close(); return true;
    }
    if (event.key !== "Tab" || !event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.keyCode === 229
        || document.activeElement !== $("prompt") || !active || $("prompt").readOnly || blocked("permission")) return false;
    event.preventDefault();
    if (!event.repeat) void cyclePermission();
    return true;
  }
  for (const [kind,button] of Object.entries(buttons)) button.onclick = () => open(kind);
  $("runtime-panel-close").onclick = () => close({focus:true});
  panel.onkeydown = event => {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229
        || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (event.key === "Escape") {event.preventDefault();close({focus:true});return;}
    const items = [...options.children].filter(node => !node.disabled), index = items.indexOf(document.activeElement);
    if (index >= 0 && ["ArrowDown","ArrowUp","Home","End"].includes(event.key)) {
      event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[next]?.focus();
    }
  };
  document.addEventListener("pointerdown", event => {
    if (view && !panel.contains?.(event.target) && !Object.values(buttons).some(button => button.contains?.(event.target))) close();
  });
  function waitForPreparation() {
    return same(preparation) ? preparation.promise : null;
  }
  function restoreFailure(text) {status(text,true);}
  render(); return {render,close,open,recover,restoreFailure,resumeAfterTrust,handleCommand,keydown,cyclePermission,waitForPreparation};
})();
