"use strict";

// Execution privileges are chosen by Windows when launching the app.
// This read-only label does not poll, restart processes, or save a preference.
globalThis.WorkspaceExecutionMode = (() => {
  let api = null, busy = false, controller = null;
  function configure(options) {
    api = options?.api;
    const button = document.getElementById("launcher-prepare");
    if (button && !button.dataset.bound) {
      button.dataset.bound = "true";
      button.addEventListener("click", prepare);
    }
  }
  async function prepare() {
    if (busy || typeof api !== "function") return;
    const button = document.getElementById("launcher-prepare");
    const message = document.getElementById("launcher-prepare-message");
    busy = true; button.disabled = true;
    message.textContent = "확인된 배포 서버에서 최신 실행기를 준비하고 있어요…";
    controller = new AbortController();
    try {
      let result = await api("/api/launcher/prepare", {}, controller.signal);
      // Finite feedback only for this explicit user action; never a background
      // process/privilege monitor or a permanent additional polling timer.
      const deadline = Date.now() + 250000;
      while (result?.launcher?.status === "preparing" && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (controller.signal.aborted) return;
        result = await api("/api/app-update", undefined, controller.signal);
      }
      message.textContent = result?.launcher?.message || "실행기 준비 상태를 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요.";
    } catch (error) {
      if (error?.name !== "AbortError") message.textContent = error?.message || "실행기를 준비하지 못했어요. 다시 시도해 주세요.";
    } finally {
      busy = false; controller = null; button.disabled = false;
    }
  }
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
      ? "내부 앱만 업데이트하면 예전 EXE의 실행 방식은 남을 수 있어요. 아래에서 최신 바로가기를 만든 뒤 사용해 주세요."
      : "이 실행기는 Windows에서 선택한 실행 권한을 유지해요. 바로가기로도 같은 방식으로 실행할 수 있어요.";
  }
  return {start, configure};
})();
