"use strict";

// Loaded once. Storage is inspected only when this dialog is explicitly opened
// or refreshed; no timers, background polling, or per-keystroke inventories.
globalThis.WorkspaceAttachmentStorage = (() => {
  let services = null, nodes = null, value = null, working = false, ticket = 0;
  const closed = () => services?.isClosed?.() === true;
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const bytes = amount => {
    const size = Math.max(0, Number(amount) || 0);
    return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB`
      : size >= 1024 ? `${Math.ceil(size / 1024)} KB` : `${size} B`;
  };
  function build() {
    if (nodes) return;
    const dialog = make("dialog", null, "attachment-storage-dialog");
    dialog.id = "attachment-storage-dialog";
    dialog.setAttribute("aria-labelledby", "attachment-storage-title");
    const header = make("div", null, "dialog-head"), title = make("h2", "첨부 저장공간");
    title.id = "attachment-storage-title";
    const close = make("button", "닫기", "text-button");close.type = "button";close.onclick = () => dialog.close();
    header.append(title, close);
    const description = make("p", "앱에 끌어 넣은 파일의 복사본만 관리합니다. 원본 파일과 작업 결과물은 그대로 유지돼요.", "attachment-storage-description");
    const usage = make("strong", "저장공간 확인 중", "attachment-storage-usage");
    const meter = make("progress");meter.max = 1;meter.value = 0;meter.setAttribute("aria-label", "첨부 저장공간 사용량");
    const stats = make("dl", null, "attachment-storage-stats");
    const breakdown = {};
    for (const [key, label] of [["removableBytes", "정리 가능한 복사본"], ["protectedBytes", "대화·초안·예약에서 사용"], ["retainedBytes", "사용 여부가 불명확해 보존"], ["graceBytes", "최근 추가해 잠시 보존"]]) {
      const row = make("div"), count = make("dd", "—");row.append(make("dt", label), count);stats.append(row);breakdown[key] = count;
    }
    const preview = make("ul", null, "attachment-storage-preview");preview.setAttribute("aria-label", "정리할 파일 예시");
    const explanation = make("p", "전송하지 않고 더 이상 참조하지 않는 복사본만 정리합니다. 최근 5분 안에 추가한 파일은 보존하며, 화면이 느려지지 않도록 한 번에 최대 100개씩 정리합니다.", "attachment-storage-description");
    const status = make("p", "", "attachment-storage-message");status.setAttribute("role", "status");status.setAttribute("aria-live", "polite");
    const actions = make("div", null, "attachment-storage-actions");
    const refresh = make("button", "다시 확인", "quiet-button"), cleanup = make("button", "미사용 복사본 정리", "primary");
    refresh.type = cleanup.type = "button";refresh.onclick = () => refreshData();cleanup.onclick = () => clean();
    actions.append(refresh, cleanup);
    dialog.append(header, description, usage, meter, stats, preview, explanation, status, actions);
    document.body.append(dialog);
    dialog.addEventListener("close", () => { ticket++; });
    nodes = {dialog, usage, meter, breakdown, preview, status, refresh, cleanup};
  }
  function busy(flag) {
    working = flag;nodes.refresh.disabled = flag || closed();
    nodes.cleanup.disabled = flag || closed() || !value || value.removableCount < 1 || !!value.warning;
    nodes.dialog.setAttribute("aria-busy", String(flag));
  }
  function render(data) {
    value = data;
    nodes.usage.textContent = `${bytes(data.usedBytes)} / ${bytes(data.maxBytes)}`;
    nodes.meter.max = Math.max(1, data.maxBytes || 1);nodes.meter.value = Math.max(0, data.usedBytes || 0);
    for (const [key, node] of Object.entries(nodes.breakdown)) node.textContent = bytes(data[key]);
    nodes.preview.replaceChildren();
    for (const file of Array.isArray(data.preview) ? data.preview.slice(0, 8) : []) {
      const row = make("li");row.append(make("span", file.name), make("span", bytes(file.size)));nodes.preview.append(row);
    }
    nodes.preview.hidden = !nodes.preview.childElementCount;
    nodes.cleanup.textContent = data.removableCount ? `미사용 ${data.removableCount}개 정리` : "미사용 복사본 정리";
    nodes.status.textContent = data.warning || (data.uploadingCount ? "파일 복사가 진행 중입니다. 전송 중인 파일은 정리하지 않습니다."
      : data.removableCount ? `${bytes(data.removableBytes)}를 정리할 수 있어요.` : "지금 정리할 수 있는 복사본이 없어요.");
  }
  async function flush() {
    const persistence = globalThis.WorkspaceDraftPersistence;
    if (!persistence || await persistence.flush() === false) throw Error("작성 중인 첨부를 저장하지 못했어요. 초안 저장 상태를 확인한 뒤 다시 시도해 주세요.");
  }
  async function refreshData() {
    if (working || closed()) return;
    const current = ++ticket;busy(true);nodes.status.textContent = "현재 초안과 저장공간을 확인하고 있어요.";
    try {
      await flush();
      if (closed()) return;
      const data = await services.api("/api/attachments/storage");
      if (current === ticket && nodes.dialog.open) render(data);
    } catch (error) {if (current === ticket && nodes.dialog.open) nodes.status.textContent = error.message;}
    finally {busy(false);}
  }
  async function clean() {
    if (working || closed() || !value?.removableCount) return;
    busy(true);
    try {
      const accepted = await confirmAction({title:"미사용 첨부 복사본을 정리할까요?", message:`최대 ${value.removableCount}개, ${bytes(value.removableBytes)}의 앱 복사본을 정리합니다. 대화·초안·예약에 쓰이는 자료와 원본 파일은 유지합니다.`, confirmLabel:"복사본 정리", returnFocus:nodes.cleanup});
      if (!accepted || !nodes.dialog.open || closed()) return;
      await flush();
      if (closed()) return;
      const data = await services.api("/api/attachments/cleanup", {});
      render(data);
      nodes.status.textContent = `${data.removedCount || 0}개, ${bytes(data.removedBytes)}를 정리했어요.${data.skippedCount ? " 사용 중이거나 변경된 파일은 보존했습니다." : ""}${data.removableCount ? ` 남은 ${data.removableCount}개는 정리를 한 번 더 눌러 이어갈 수 있어요.` : ""}`;
    } catch (error) {nodes.status.textContent = error.message;}
    finally {busy(false);}
  }
  async function open() {
    if (!services || closed()) return;
    build();
    if (!nodes.dialog.open) (services.showDialog || showDialog)(nodes.dialog.id);
    await refreshData();
  }
  function mount(options) {
    services = options;
    const button = document.getElementById("settings-storage-open");
    if (button) button.onclick = open;
  }
  return {mount, open};
})();
