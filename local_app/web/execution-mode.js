"use strict";

// Execution privileges are chosen by Windows when launching the app.
// This read-only label does not poll, restart processes, or save a preference.
globalThis.WorkspaceExecutionMode = (() => {
  function start(mode) {
    const current = document.getElementById("execution-mode-current");
    if (!current) return;
    const administrator = mode === "administrator";
    current.textContent = mode === "normal" || administrator
      ? `현재: ${administrator ? "관리자 권한" : "일반 권한"}`
      : "현재 실행 권한을 확인하지 못했어요.";
    current.dataset.mode = administrator ? "administrator" : mode === "normal" ? "normal" : "unknown";
  }
  return {start};
})();
