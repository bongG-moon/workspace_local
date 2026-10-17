"use strict";

// Panel preferences belong to this app only. Narrow windows use temporary
// overlays so resizing never replaces the user's full-width arrangement.
globalThis.WorkspaceLayout = (() => {
  const app = document.querySelector(".app"), sidebar = document.getElementById("sidebar-panel");
  const inspector = document.getElementById("inspector-panel"), sidebarButton = document.getElementById("sidebar-toggle");
  const inspectorButton = document.getElementById("materials-button"), closeButton = document.getElementById("close-materials");
  const miniButton = document.getElementById("mini-mode-toggle");
  const resizer = document.getElementById("inspector-resizer");
  const backdrop = document.getElementById("layout-backdrop"), main = document.querySelector("main");
  if (!app || !sidebar || !inspector || !sidebarButton || !inspectorButton || !backdrop) return null;

  const storageKey = "workspace.layout.v1";
  const defaultInspectorWidth = 292, minInspectorWidth = 260, maxInspectorWidth = 560, minChatWidth = 420;
  const clampWidth = (width, maximum = maxInspectorWidth) => Math.max(minInspectorWidth, Math.min(maximum, Math.round(width)));
  const preferences = {sidebarCollapsed:false, inspectorCollapsed:false, inspectorWidth:defaultInspectorWidth, miniMode:false};
  function readPreferences(saved) {
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return false;
    for (const key of ["sidebarCollapsed", "inspectorCollapsed", "miniMode"]) if (typeof saved[key] === "boolean") preferences[key] = saved[key];
    if (typeof saved.inspectorWidth === "number" && Number.isFinite(saved.inspectorWidth)) preferences.inspectorWidth = clampWidth(saved.inspectorWidth);
    return true;
  }
  try {
    readPreferences(JSON.parse(localStorage.getItem(storageKey)));
  } catch (_) { /* Private/restricted storage must not prevent using panels. */ }
  const sidebarMedia = matchMedia("(max-width: 700px)"), inspectorMedia = matchMedia("(max-width: 1100px)");
  let sidebarNarrow = sidebarMedia.matches, inspectorNarrow = inspectorMedia.matches;
  let sidebarOpen = false, inspectorOpen = false;
  // Mini mode starts with the desktop list visible and keeps its temporary
  // collapse state separate from the user's full-screen arrangement.
  let miniSidebarCollapsed = false;
  let resizeDrag = null, sizeFrame = 0, windowFrame = 0;
  let revision = 0, hydrated = false, saving = false, pendingSave = null, saveWarningShown = false;
  const backgroundInert = new Map(), overlayAttributes = new Map();

  function cachePreferences() {
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch (_) { /* Keep the current arrangement in memory. */ }
  }
  async function flushSave() {
    if (saving || !pendingSave || typeof api !== "function") return;
    const value = pendingSave; pendingSave = null; saving = true;
    try {
      await api("/api/layout", value);
    } catch (_) {
      // Preserve the live/local state. Only a newer queued user change may
      // continue after this failure; the failed snapshot is never retried.
      if (!saveWarningShown && typeof toast === "function") {
        saveWarningShown = true;
        toast("화면 설정을 저장하지 못했어요. 현재 화면은 유지되지만 앱을 다시 열면 이전 설정으로 돌아갈 수 있어요.");
      }
    } finally { saving = false; }
    if (pendingSave) flushSave();
  }
  function save() {
    revision++; cachePreferences();
    if (hydrated) { pendingSave = {...preferences}; flushSave(); }
  }
  function hydrate(saved) {
    if (hydrated) return snapshot();
    hydrated = true;
    // A slow bootstrap must not undo a layout change already made here.
    if (revision) { pendingSave = {...preferences}; flushSave(); return snapshot(); }
    const old = snapshot(), focused = document.activeElement;
    if (!readPreferences(saved)) return old;
    cachePreferences();
    miniSidebarCollapsed = false; sidebarOpen = false; inspectorOpen = false;
    return renderTransition(old, focused);
  }
  function snapshot() {
    const catalogOpen = app.classList.contains("catalog-open") || app.classList.contains("chat-mode");
    const miniMode = preferences.miniMode;
    const sidebarCollapsed = sidebarNarrow ? !sidebarOpen : miniMode ? miniSidebarCollapsed : preferences.sidebarCollapsed;
    const inspectorCollapsed = miniMode || (inspectorNarrow ? !inspectorOpen : preferences.inspectorCollapsed);
    return {miniMode, sidebarCollapsed, inspectorCollapsed, sidebarOverlay:sidebarNarrow && !sidebarCollapsed,
      inspectorOverlay:inspectorNarrow && !inspectorCollapsed && !catalogOpen,
      inspectorVisible:!inspectorCollapsed && !catalogOpen, sidebarNarrow, inspectorNarrow,
      preferences:{...preferences}};
  }
  function restoreFocus(button) {
    if (button?.isConnected && !button.disabled) button.focus({preventScroll:true});
  }
  function inspectorMaximum() {
    // Measure the current rail, including its collapsed width, rather than
    // duplicating responsive CSS dimensions in JavaScript.
    return Math.max(minInspectorWidth, Math.min(maxInspectorWidth,
      Math.floor(app.clientWidth - sidebar.getBoundingClientRect().width - minChatWidth)));
  }
  function inspectorResizable(state = snapshot()) {
    return !!resizer && !state.inspectorNarrow && state.inspectorVisible && !state.inspectorOverlay && !state.sidebarOverlay;
  }
  function renderInspectorSize(state = snapshot()) {
    const maximum = inspectorMaximum(), width = clampWidth(resizeDrag ? resizeDrag.width : preferences.inspectorWidth, maximum);
    const value = `${width}px`;
    if (app.style.getPropertyValue("--inspector-resized-width") !== value) app.style.setProperty("--inspector-resized-width", value);
    if (!resizer) return;
    const enabled = inspectorResizable(state);
    if (!enabled && document.activeElement === resizer) restoreFocus(state.miniMode ? miniButton : inspectorButton);
    resizer.hidden = !enabled; resizer.inert = !enabled; resizer.tabIndex = enabled ? 0 : -1;
    const attributes = {"role":"separator", "aria-orientation":"vertical", "aria-controls":"inspector-panel",
      "aria-valuemin":minInspectorWidth, "aria-valuemax":maximum, "aria-valuenow":width, "aria-valuetext":`${width}픽셀`};
    for (const [key, attribute] of Object.entries(attributes)) {
      if (resizer.getAttribute(key) !== String(attribute)) resizer.setAttribute(key, String(attribute));
    }
  }
  function queueInspectorSize() {
    if (!sizeFrame) sizeFrame = requestAnimationFrame(() => { sizeFrame = 0; renderInspectorSize(); });
  }
  function finishResize(commit = false) {
    if (!resizeDrag) return false;
    const finished = resizeDrag;
    resizeDrag = null;
    if (sizeFrame) { cancelAnimationFrame(sizeFrame); sizeFrame = 0; }
    app.classList.remove("inspector-resizing");
    // Clear the drag before releasing capture: lostpointercapture can fire
    // synchronously, and a completed drag must not be cancelled a second time.
    try { if (resizer.hasPointerCapture(finished.pointerId)) resizer.releasePointerCapture(finished.pointerId); } catch (_) { /* Capture may already be gone. */ }
    if (commit && finished.width !== finished.startWidth && preferences.inspectorWidth !== finished.width) {
      preferences.inspectorWidth = finished.width; save();
    }
    renderInspectorSize();
    return true;
  }
  function moveResize(event) {
    if (!resizeDrag || event.pointerId !== resizeDrag.pointerId || !Number.isFinite(event.clientX)) return;
    resizeDrag.width = clampWidth(resizeDrag.startWidth + resizeDrag.startX - event.clientX, resizeDrag.maximum);
    queueInspectorSize();
  }
  function startResize(event) {
    if (event.button !== 0 || event.isPrimary === false || resizeDrag || !inspectorResizable() || !Number.isFinite(event.clientX)) return;
    const maximum = inspectorMaximum(), width = clampWidth(preferences.inspectorWidth, maximum);
    resizeDrag = {pointerId:event.pointerId, startX:event.clientX, startWidth:width, width, maximum};
    try { resizer.setPointerCapture(event.pointerId); } catch (_) { finishResize(); return; }
    event.preventDefault(); restoreFocus(resizer);
    app.classList.add("inspector-resizing");
  }
  function resizeKey(event) {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || !inspectorResizable()) return;
    const maximum = inspectorMaximum(), width = clampWidth(preferences.inspectorWidth, maximum), step = event.shiftKey ? 50 : 10;
    let next;
    switch (event.key) {
      case "ArrowLeft": next = clampWidth(width + step, maximum); break;
      case "ArrowRight": next = clampWidth(width - step, maximum); break;
      case "Home": next = minInspectorWidth; break;
      case "End": next = maximum; break;
      case "Enter": next = defaultInspectorWidth; break;
      default: return;
    }
    event.preventDefault(); event.stopPropagation(); finishResize();
    if ((next !== width || event.key === "Enter") && next !== preferences.inspectorWidth) {
      preferences.inspectorWidth = next; save();
    }
    renderInspectorSize();
  }
  function resetInspectorWidth(event) {
    if (!inspectorResizable()) return;
    event.preventDefault(); finishResize();
    if (preferences.inspectorWidth !== defaultInspectorWidth) { preferences.inspectorWidth = defaultInspectorWidth; save(); }
    renderInspectorSize();
  }
  function overlayPanel(state = snapshot()) {
    return state.inspectorOverlay ? inspector : state.sidebarOverlay ? sidebar : null;
  }
  function focusable(panel) {
    return [...panel.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter(node =>
      !node.disabled && node.tabIndex >= 0 && !node.closest('[hidden],[inert]') && node.getClientRects().length);
  }
  function focusOverlay(panel) {
    const preferred = panel === inspector ? closeButton : sidebarButton;
    const target = preferred && panel.contains(preferred) && !preferred.disabled ? preferred : focusable(panel)[0];
    if (target) restoreFocus(target);
  }
  function overlaySemantics(panel, background) {
    // Restore the exact prior state instead of making unrelated UI interactive.
    for (const [node, previous] of backgroundInert) if (!background.includes(node)) {
      node.inert = previous; backgroundInert.delete(node);
    }
    for (const node of background) if (node && !backgroundInert.has(node)) {
      backgroundInert.set(node, node.inert); node.inert = true;
    }
    for (const [node, attributes] of overlayAttributes) if (node !== panel) {
      for (const [name, value] of Object.entries(attributes)) {
        if (value === null) node.removeAttribute(name); else node.setAttribute(name, value);
      }
      overlayAttributes.delete(node);
    }
    if (panel && !overlayAttributes.has(panel)) {
      overlayAttributes.set(panel, {role:panel.getAttribute('role'), 'aria-modal':panel.getAttribute('aria-modal')});
      panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
    }
  }
  function render() {
    const focused = document.activeElement;
    finishResize();
    const state = snapshot();
    const panel = overlayPanel(state);
    overlaySemantics(panel, panel === inspector ? [main, sidebar] : panel === sidebar ? [main, inspector] : []);
    app.classList.toggle("mini-mode", state.miniMode);
    app.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
    app.classList.toggle("inspector-collapsed", state.inspectorCollapsed);
    app.classList.toggle("sidebar-overlay", state.sidebarOverlay);
    app.classList.toggle("inspector-overlay", state.inspectorOverlay);
    // The icon rail stays interactive except while another panel is modal.
    inspector.hidden = !state.inspectorVisible;
    inspector.inert = !state.inspectorVisible;
    sidebarButton.setAttribute("aria-controls", "sidebar-panel");
    sidebarButton.setAttribute("aria-expanded", String(!state.sidebarCollapsed));
    sidebarButton.title = state.sidebarCollapsed ? "업무 목록 펼치기" : "업무 목록 접기";
    sidebarButton.setAttribute("aria-label", sidebarButton.title);
    // The panel owns its close action; the header only offers reopening.
    inspectorButton.hidden = state.inspectorVisible;
    inspectorButton.setAttribute("aria-controls", "inspector-panel");
    inspectorButton.setAttribute("aria-expanded", String(state.inspectorVisible));
    inspectorButton.title = "자료와 결과 펼치기";
    inspectorButton.setAttribute("aria-label", inspectorButton.title);
    if (miniButton) {
      miniButton.setAttribute("aria-pressed", String(state.miniMode));
      miniButton.setAttribute("aria-label", "간편 화면");
      miniButton.title = state.miniMode ? "간편 화면 끄기 · 전체 도구 표시" : "간편 화면 켜기 · 채팅과 업무 목록만 표시";
    }
    backdrop.hidden = !state.sidebarOverlay && !state.inspectorOverlay;
    renderInspectorSize(state);
    if (state.inspectorVisible && focused === inspectorButton) restoreFocus(closeButton);
    return state;
  }
  function setSidebar(open, {focus=false} = {}) {
    if (sidebarNarrow) sidebarOpen = open;
    else if (preferences.miniMode) miniSidebarCollapsed = !open;
    else { preferences.sidebarCollapsed = !open; save(); }
    if (open && inspectorNarrow) inspectorOpen = false;
    const state = render();
    if (state.sidebarOverlay) focusOverlay(sidebar);
    else if (focus) restoreFocus(sidebarButton);
  }
  function setInspector(open, {focus=false} = {}) {
    // An explicit request for a panel leaves mini mode. Closing an already
    // hidden panel must not replace the full-screen inspector preference.
    if (preferences.miniMode) {
      if (!open) return snapshot();
      setMiniMode(false);
    }
    if (inspectorNarrow) inspectorOpen = open;
    else { preferences.inspectorCollapsed = !open; save(); }
    if (open && sidebarNarrow) sidebarOpen = false;
    const state = render();
    if (state.inspectorOverlay) focusOverlay(inspector);
    else if (focus) restoreFocus(state.inspectorVisible ? closeButton : inspectorButton);
  }
  function toggleSidebar() { setSidebar(snapshot().sidebarCollapsed, {focus:true}); }
  function toggleInspector() { setInspector(!snapshot().inspectorVisible, {focus:true}); }
  function openInspector() { setInspector(true); }
  function closeInspector(options = {focus:true}) { setInspector(false, options); }
  function closeSidebar(options = {focus:true}) { setSidebar(false, options); }
  function setMiniMode(enabled) {
    enabled = !!enabled;
    if (preferences.miniMode === enabled) return snapshot();
    const old = snapshot(), focused = document.activeElement;
    preferences.miniMode = enabled; miniSidebarCollapsed = false;
    sidebarOpen = false; inspectorOpen = false;
    save();
    return renderTransition(old, focused);
  }
  function renderTransition(old, focused) {
    globalThis.WorkspaceInlineControls?.close?.({focus:false});
    globalThis.WorkspaceComposer?.close?.();
    if (preferences.miniMode && app.classList.contains("catalog-open")) globalThis.WorkspaceCapabilities?.close?.();
    const state = render();
    // Keep the textarea and its selection untouched. Only recover focus when
    // its old surface disappeared, including CSS-only controls in mini mode.
    if (focused && ((!state.inspectorVisible && inspector.contains(focused)) ||
        (focused === resizer && !inspectorResizable(state)) ||
        (old.sidebarOverlay && sidebar.contains(focused)) ||
        focused.closest('[hidden],[inert]') || !focused.getClientRects().length)) restoreFocus(state.inspectorVisible && focused === inspectorButton ? closeButton : miniButton);
    return state;
  }
  function toggleMiniMode() { return setMiniMode(!preferences.miniMode); }
  function closeOverlay() {
    const state = snapshot();
    if (state.inspectorOverlay) { closeInspector(); return true; }
    if (state.sidebarOverlay) { closeSidebar(); return true; }
    return false;
  }
  function dismissOverlays({focus=true} = {}) {
    const state = snapshot(), target = state.inspectorOverlay ? inspectorButton : state.sidebarOverlay ? sidebarButton : null;
    sidebarOpen = false; inspectorOpen = false;
    render();
    if (focus && target) restoreFocus(target);
    return !!target;
  }
  function refresh() {
    const old = snapshot(), focused = document.activeElement;
    if (sidebarNarrow !== sidebarMedia.matches || inspectorNarrow !== inspectorMedia.matches) {
      sidebarOpen = false; inspectorOpen = false;
    }
    sidebarNarrow = sidebarMedia.matches; inspectorNarrow = inspectorMedia.matches;
    const state = render();
    if (!state.inspectorVisible && inspector.contains(focused)) restoreFocus(state.miniMode ? miniButton : inspectorButton);
    else if (!old.sidebarCollapsed && state.sidebarCollapsed && sidebar.contains(focused)) restoreFocus(sidebarButton);
    return state;
  }

  sidebarButton.addEventListener("click", toggleSidebar);
  inspectorButton.addEventListener("click", toggleInspector);
  miniButton?.addEventListener("click", toggleMiniMode);
  closeButton?.addEventListener("click", () => closeInspector());
  backdrop.addEventListener("click", closeOverlay);
  for (const id of ["home-button", "new-chat", "import-open", "capabilities-open", "settings-open", "schedule-overview-open", "shortcuts-open", "help"]) {
    document.getElementById(id)?.addEventListener("click", () => {
      if (snapshot().sidebarOverlay) closeSidebar({focus:false});
    }, {capture:true});
  }
  for (const media of [sidebarMedia, inspectorMedia]) {
    if (media.addEventListener) media.addEventListener("change", refresh);
    else media.addListener(refresh);
  }
  if (resizer) {
    resizer.addEventListener("pointerdown", startResize);
    resizer.addEventListener("pointermove", moveResize);
    resizer.addEventListener("pointerup", event => {
      if (!resizeDrag || event.pointerId !== resizeDrag.pointerId) return;
      moveResize(event); finishResize(true);
    });
    for (const name of ["pointercancel", "lostpointercapture"]) resizer.addEventListener(name, event => {
      if (resizeDrag && event.pointerId === resizeDrag.pointerId) finishResize();
    });
    resizer.addEventListener("keydown", resizeKey);
    resizer.addEventListener("dblclick", resetInspectorWidth);
  }
  window.addEventListener("blur", () => finishResize());
  window.addEventListener("resize", () => {
    if (!windowFrame) windowFrame = requestAnimationFrame(() => { windowFrame = 0; refresh(); });
  });
  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === "Escape" && finishResize()) { event.preventDefault(); event.stopPropagation(); return; }
    if (document.querySelector("dialog[open]")) return;
    const panel = overlayPanel();
    if (event.key === "Tab" && panel) {
      const items = focusable(panel), first = items[0], last = items[items.length - 1];
      const focused = document.activeElement;
      if (!items.length || !items.includes(focused) || (!event.shiftKey && focused === last) || (event.shiftKey && focused === first)) {
        event.preventDefault();
        if (items.length) restoreFocus(event.shiftKey ? last : first); else focusOverlay(panel);
      }
      return;
    }
    if (event.key !== "Escape" || event.shiftKey) return;
    if (["composer-controls-panel", "composer-suggestions"].some(id => {
      const panel = document.getElementById(id); return panel && !panel.hidden;
    })) return;
    if (closeOverlay()) { event.preventDefault(); event.stopPropagation(); }
  });
  document.addEventListener("focusin", event => {
    const panel = overlayPanel();
    if (panel && !panel.contains(event.target) && !document.querySelector("dialog[open]")) focusOverlay(panel);
  });
  render();
  // Deferred scripts can finish after a fast local bootstrap response.
  if (typeof boot === "object" && boot && Array.isArray(boot.sessions)) hydrate(boot.layout);
  return {snapshot, hydrate, setMiniMode, toggleMiniMode, toggleSidebar, toggleInspector, openInspector, closeInspector, closeSidebar, dismissOverlays, refresh};
})();
