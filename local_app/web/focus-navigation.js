"use strict";

// WebView activation and dialog restoration may focus a control without any
// keyboard navigation. Keep that focus (and caret/Tab order), but only draw
// navigation rings after an intentional keyboard action. Load before CSS.
(() => {
  const root = document.documentElement;
  const editable = target => !!target?.closest?.('input:not([type="button"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"]):not([type="range"]),textarea,[contenteditable]:not([contenteditable="false"]),[role="textbox"]');
  const actionable = target => !!target?.closest?.('button,a[href],summary,select,[role="button"],[role="tab"],[role="menuitem"],[role="separator"],input[type="checkbox"],input[type="radio"],input[type="range"]');
  const navigationKeys = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);
  function setMode(mode) {
    if (root.dataset.focusNavigation !== mode) root.dataset.focusNavigation = mode;
  }
  setMode("pointer");
  document.addEventListener("pointerdown", () => setMode("pointer"), {capture:true, passive:true});
  document.addEventListener("keydown", event => {
    // IME, typing, global shortcuts and OS activation are not focus navigation.
    if (event.isComposing || event.keyCode === 229 || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === "Tab" || (!editable(event.target) &&
        (navigationKeys.has(event.key) || ((event.key === "Enter" || event.key === " ") && actionable(event.target))))) {
      setMode("keyboard");
    }
  }, true);
  document.addEventListener("click", event => {
    // Keyboard/assistive activation can arrive without a DOM key event.
    // Programmatic click() must not turn automatic focus into keyboard focus.
    if (event.isTrusted && event.detail === 0 && actionable(event.target)) setMode("keyboard");
  }, true);
  window.addEventListener("blur", event => { if (event.target === window) setMode("pointer"); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) setMode("pointer"); });
})();
