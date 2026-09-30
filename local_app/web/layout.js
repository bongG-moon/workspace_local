"use strict";

// Panel preferences belong to this app only. Narrow windows use temporary
// overlays so resizing never replaces the user's full-width arrangement.
globalThis.WorkspaceLayout = (() => {
  const app = document.querySelector(".app"), sidebar = document.getElementById("sidebar-panel");
  const inspector = document.getElementById("inspector-panel"), sidebarButton = document.getElementById("sidebar-toggle");
  const inspectorButton = document.getElementById("materials-button"), closeButton = document.getElementById("close-materials");
  const backdrop = document.getElementById("layout-backdrop"), main = document.querySelector("main");
  if (!app || !sidebar || !inspector || !sidebarButton || !inspectorButton || !backdrop) return null;

  const storageKey = "workspace.layout.v1";
  const preferences = {sidebarCollapsed:false, inspectorCollapsed:false};
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const key of Object.keys(preferences)) if (typeof saved[key] === "boolean") preferences[key] = saved[key];
    }
  } catch (_) { /* Private/restricted storage must not prevent using panels. */ }
  const sidebarMedia = matchMedia("(max-width: 700px)"), inspectorMedia = matchMedia("(max-width: 1100px)");
  let sidebarNarrow = sidebarMedia.matches, inspectorNarrow = inspectorMedia.matches;
  let sidebarOpen = false, inspectorOpen = false;
  const backgroundInert = new Map(), overlayAttributes = new Map();

  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch (_) { /* Keep the current arrangement in memory. */ }
  }
  function snapshot() {
    const catalogOpen = app.classList.contains("catalog-open");
    const sidebarCollapsed = sidebarNarrow ? !sidebarOpen : preferences.sidebarCollapsed;
    const inspectorCollapsed = inspectorNarrow ? !inspectorOpen : preferences.inspectorCollapsed;
    return {sidebarCollapsed, inspectorCollapsed, sidebarOverlay:sidebarNarrow && !sidebarCollapsed,
      inspectorOverlay:inspectorNarrow && !inspectorCollapsed && !catalogOpen,
      inspectorVisible:!inspectorCollapsed && !catalogOpen, sidebarNarrow, inspectorNarrow,
      preferences:{...preferences}};
  }
  function restoreFocus(button) {
    if (button.isConnected && !button.disabled) button.focus({preventScroll:true});
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
    const state = snapshot();
    const panel = overlayPanel(state);
    overlaySemantics(panel, panel === inspector ? [main, sidebar] : panel === sidebar ? [main, inspector] : []);
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
    inspectorButton.setAttribute("aria-controls", "inspector-panel");
    inspectorButton.setAttribute("aria-expanded", String(state.inspectorVisible));
    inspectorButton.title = state.inspectorVisible ? "자료와 결과 접기" : "자료와 결과 펼치기";
    inspectorButton.setAttribute("aria-label", inspectorButton.title);
    backdrop.hidden = !state.sidebarOverlay && !state.inspectorOverlay;
    return state;
  }
  function setSidebar(open, {focus=false} = {}) {
    if (sidebarNarrow) sidebarOpen = open;
    else { preferences.sidebarCollapsed = !open; save(); }
    if (open && inspectorNarrow) inspectorOpen = false;
    const state = render();
    if (state.sidebarOverlay) focusOverlay(sidebar);
    else if (focus) restoreFocus(sidebarButton);
  }
  function setInspector(open, {focus=false} = {}) {
    if (inspectorNarrow) inspectorOpen = open;
    else { preferences.inspectorCollapsed = !open; save(); }
    if (open && sidebarNarrow) sidebarOpen = false;
    const state = render();
    if (state.inspectorOverlay) focusOverlay(inspector);
    else if (focus) restoreFocus(inspectorButton);
  }
  function toggleSidebar() { setSidebar(snapshot().sidebarCollapsed, {focus:true}); }
  function toggleInspector() { setInspector(!snapshot().inspectorVisible, {focus:true}); }
  function openInspector() { setInspector(true); }
  function closeInspector(options = {focus:true}) { setInspector(false, options); }
  function closeSidebar(options = {focus:true}) { setSidebar(false, options); }
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
    if (!state.inspectorVisible && inspector.contains(focused)) restoreFocus(inspectorButton);
    else if (!old.sidebarCollapsed && state.sidebarCollapsed && sidebar.contains(focused)) restoreFocus(sidebarButton);
    return state;
  }

  sidebarButton.addEventListener("click", toggleSidebar);
  inspectorButton.addEventListener("click", toggleInspector);
  closeButton?.addEventListener("click", () => closeInspector());
  backdrop.addEventListener("click", closeOverlay);
  for (const id of ["home-button", "new-chat", "import-open", "palette-open", "capabilities-open", "settings-open", "help"]) {
    document.getElementById(id)?.addEventListener("click", () => {
      if (snapshot().sidebarOverlay) closeSidebar({focus:false});
    }, {capture:true});
  }
  for (const media of [sidebarMedia, inspectorMedia]) {
    if (media.addEventListener) media.addEventListener("change", refresh);
    else media.addListener(refresh);
  }
  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
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
  return {snapshot, toggleSidebar, toggleInspector, openInspector, closeInspector, closeSidebar, dismissOverlays, refresh};
})();
