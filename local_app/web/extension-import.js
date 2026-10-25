/* Loaded only after the user chooses to import an optional extension. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  let hooks, attached = false, stopped = false, generation = 0;
  let phase = "idle", review = null, external = false;
  const busy = () => phase !== "idle";
  const available = () => !stopped && !hooks?.isClosed?.();
  const message = (id, text) => { $(id).textContent = text; $(id).hidden = !text; };
  function controls() {
    $("extension-import-open").disabled = busy() || !available();
    $("extension-import-confirm").disabled = phase !== "review";
    $("extension-import-cancel").disabled = phase === "installing";
    $("extension-import-close").disabled = phase === "installing";
    $("extension-import-dialog").setAttribute("aria-busy", String(phase === "installing"));
  }
  async function discard(token) {
    if (!token) return;
    try { await hooks.api("/api/extensions/import/cancel", {token}); }
    catch (_) { /* The server also expires abandoned previews; never install on retry. */ }
  }
  function dismiss() {
    if (phase === "installing") return;
    generation++;
    const token = review?.token;
    review = null; phase = "idle"; external = false;
    if ($("extension-import-dialog").open) $("extension-import-dialog").close();
    controls();
    void discard(token);
    if (available() && $("settings-dialog").open) $("extension-import-open").focus({preventScroll:true});
  }
  function renderReview(result) {
    $("extension-import-name").textContent = result.extension.name;
    $("extension-import-version").textContent = result.extension.version;
    $("extension-import-destination").textContent = result.destination;
    $("extension-import-server-row").hidden = !result.serverUrl;
    $("extension-import-server").textContent = result.serverUrl || "";
    $("extension-import-sha256").textContent = result.packageSha256 || "";
    $("extension-import-details").open = false;
    $("extension-import-confirm").textContent = result.replacing ? "업데이트" : "설치";
    $("extension-import-confirm").hidden = false;
    $("extension-import-cancel").textContent = "취소";
    message("extension-import-replacing", result.replacing || result.existingDataPreserved
      ? "확장 파일만 교체하며 기존 연결 KEY와 개인 설정은 유지합니다. 기본 서버 주소는 현재 연결 중인 서버와 다를 수 있어요."
      : "");
    const warnings = Array.isArray(result.warnings) ? result.warnings.filter(item => typeof item === "string" && item) : [];
    $("extension-import-warnings").replaceChildren(...warnings.map(text => {
      const item = document.createElement("li"); item.textContent = text; return item;
    }));
    $("extension-import-warnings").hidden = warnings.length === 0;
    message("extension-import-result", "");
    controls();
    hooks.openDialog("extension-import-dialog");
    $("extension-import-cancel").focus({preventScroll:true});
  }
  async function open() {
    if (!available() || busy() || !$("settings-dialog").open) return;
    external = false;
    const request = ++generation;
    phase = "preparing"; controls();
    message("extension-import-status", "설치할 확장 ZIP을 선택해 주세요.");
    try {
      const result = await hooks.api("/api/extensions/import/prepare", {});
      if (request !== generation || !available() || !$("settings-dialog").open) {
        await discard(result?.token); return;
      }
      if (result?.cancelled) { phase = "idle"; message("extension-import-status", ""); return; }
      if (!result?.token || typeof result.extension?.name !== "string" || typeof result.extension?.version !== "string" || typeof result.destination !== "string") {
        await discard(result?.token);
        throw Error("확장 정보를 확인하지 못했어요. 배포받은 ZIP을 다시 확인해 주세요.");
      }
      review = result; phase = "review";
      message("extension-import-status", "설치할 추가 기능의 정보를 확인해 주세요.");
      renderReview(result);
    } catch (error) {
      if (request === generation && available()) {
        phase = "idle";
        message("extension-import-status", error.message || "확장 ZIP을 확인하지 못했어요. 다시 선택해 주세요.");
      }
    } finally { if (request === generation) controls(); }
  }
  async function openPending(requestId) {
    if (!available() || busy() || hooks.canOpenExternal?.() === false) return false;
    external = true;
    const request = ++generation;
    phase = "preparing"; controls();
    try {
      const result = await hooks.api("/api/extensions/import/pending", {requestId});
      if (request !== generation || !available()) return false;
      if (hooks.canOpenExternal?.() === false) {
        phase = "idle"; external = false; controls();
        return false; // Keep the server review for the next ordinary refresh.
      }
      if (!result?.token || typeof result.extension?.name !== "string" || typeof result.destination !== "string") {
        throw Error("확장 정보를 확인하지 못했어요. 확장 파일을 다시 열어 주세요.");
      }
      review = result; phase = "review"; renderReview(result);
      return true;
    } catch (error) {
      if (request === generation) { phase = "idle"; external = false; controls(); }
      throw error;
    }
  }
  async function confirm() {
    if (!available() || phase !== "review" || !review) return;
    const request = generation, packageInfo = review;
    phase = "installing"; controls();
    message("extension-import-result", "추가 기능을 설치하고 있어요.");
    let installed = false;
    try {
      const result = await hooks.api("/api/extensions/import/confirm", {token:packageInfo.token, confirmed:true});
      if (!result?.installed) throw Error("설치 완료를 확인하지 못했어요.");
      installed = true; review = null;
      if (request !== generation || !available()) return;
      await hooks.refresh();
      if (request !== generation || !available()) return;
      message("extension-import-status", `${packageInfo.extension.name} 설치를 완료했어요. 왼쪽 메뉴에서 열 수 있습니다.`);
      phase = "idle";
      $("extension-import-dialog").close();
      controls();
      if ($("settings-dialog").open) $("extension-import-open").focus({preventScroll:true});
    } catch (error) {
      // A lost confirm response is ambiguous: never resubmit automatically.
      // A new explicit import obtains a fresh preview of the installed state.
      review = null;
      if (!installed) void discard(packageInfo.token);
      if (request !== generation || !available()) return;
      phase = "failed"; controls();
      $("extension-import-confirm").hidden = true;
      $("extension-import-cancel").textContent = "닫기";
      const detail = error.message ? ` ${error.message}` : "";
      const text = installed
        ? "설치는 완료했지만 메뉴를 새로 불러오지 못했어요. 앱을 다시 열면 설치한 확장을 확인할 수 있습니다." + detail
        : "설치 완료를 확인하지 못했어요. 아래 내용을 확인한 뒤 ZIP을 다시 선택해 주세요." + detail;
      message("extension-import-result", text);
      message("extension-import-status", text);
      $("extension-import-cancel").focus({preventScroll:true});
    }
  }
  function settingsClosed() {
    if (!external && phase !== "installing") dismiss();
  }
  function attach(options) {
    hooks = options;
    if (attached) return;
    attached = true;
    $("extension-import-confirm").onclick = confirm;
    $("extension-import-cancel").onclick = $("extension-import-close").onclick = dismiss;
    $("extension-import-dialog").oncancel = event => { event.preventDefault(); dismiss(); };
    $("extension-import-dialog").onclose = () => { if (phase !== "idle" && phase !== "installing") dismiss(); };
  }
  function stop() {
    stopped = true; generation++;
    const token = phase === "installing" ? null : review?.token;
    review = null; phase = "idle";
    if ($("extension-import-dialog").open) $("extension-import-dialog").close();
    controls(); void discard(token);
  }
  globalThis.WorkspaceExtensionImport = {attach, open, openPending, settingsClosed, stop, isBusy:busy};
})();
