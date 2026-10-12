"use strict";

// Execution privileges are chosen by Windows when launching the app.
// This read-only label does not poll, restart processes, or save a preference.
globalThis.WorkspaceExecutionMode = (() => {
  function configure() { /* Read-only: no launcher download or polling. */ }
  function start(mode, launcher) {
    const current = document.getElementById("execution-mode-current");
    if (!current) return;
    const administrator = mode === "administrator";
    current.textContent = mode === "normal" || administrator
      ? `현재: ${administrator ? "관리자 권한" : "일반 권한"}`
      : "현재 실행 권한을 확인하지 못했어요.";
    current.dataset.mode = administrator ? "administrator" : mode === "normal" ? "normal" : "unknown";
    if (!launcher || typeof launcher !== "object") return;
    const info = document.getElementById("launcher-version-info");
    const note = document.getElementById("launcher-migration-note");
    if (info) info.textContent = `앱 ${launcher.appVersion || "미확인"} · EXE 실행기 ${launcher.entryVersion || "미확인 (VBS 또는 구형 실행기)"}`;
    if (note) note.textContent = launcher.repairRecommended
      ? "내부 앱만 업데이트하면 예전 EXE의 실행 방식은 남을 수 있어요. 배포받은 최신 EXE로 실행해 주세요."
      : "이 실행기는 Windows에서 선택한 실행 권한을 유지해요. 현재 EXE를 그대로 사용하시면 됩니다.";
  }
  return {start, configure};
})();
