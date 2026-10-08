"use strict";
globalThis.WorkspaceArchivedTasks = (() => {
  let mounted = null;
  function mount(hooks) {
    if (mounted) return mounted;
    const dialog = document.createElement("dialog");
    dialog.id = "archived-tasks-dialog";
    dialog.className = "archived-tasks-dialog";
    dialog.setAttribute("aria-labelledby", "archived-tasks-title");
    dialog.innerHTML = `<div class="dialog-head"><div><h2 id="archived-tasks-title">보관한 업무</h2><p>목록에서 지운 업무를 다시 꺼내세요. 대화와 작업 파일은 그대로 유지됩니다.</p></div><button type="button" class="icon-button" id="archived-tasks-close" aria-label="보관한 업무 닫기">×</button></div><label class="sr-only" for="archived-tasks-query">보관한 업무 검색</label><input id="archived-tasks-query" type="search" maxlength="200" placeholder="업무 이름이나 폴더로 검색" autocomplete="off"><p id="archived-tasks-status" class="archived-tasks-status" role="status" aria-live="polite"></p><div id="archived-tasks-list" class="archived-tasks-list"></div><div class="archived-tasks-footer"><small>복원만으로 Claude를 실행하거나 예약을 재개하지 않습니다.</small><div><button type="button" class="quiet-button" id="archived-tasks-prev">이전</button><button type="button" class="quiet-button" id="archived-tasks-next">다음</button></div></div>`;
    document.body.append(dialog);
    const find = id => document.getElementById(id);
    const list = find("archived-tasks-list"), status = find("archived-tasks-status"), query = find("archived-tasks-query");
    const close = find("archived-tasks-close"), prev = find("archived-tasks-prev"), next = find("archived-tasks-next");
    let ticket = 0, timer = null, offset = 0, total = 0, restoring = false, caller = null;
    const isClosed = () => hooks.isClosed?.() === true;
    function controls(loading = false) {
      prev.disabled = loading || restoring || offset === 0;
      next.disabled = loading || restoring || offset + 50 >= total;
      query.disabled = restoring; close.disabled = restoring;
      for (const button of list.querySelectorAll("button")) button.disabled = restoring;
    }
    async function restore(id) {
      if (restoring || isClosed()) return;
      restoring = true; ticket++; controls(); status.textContent = "업무 기록을 확인하고 있어요…";
      try {
        const result = await hooks.api("/api/sessions/restore", {id});
        if (isClosed()) return;
        dialog.close();
        await hooks.onRestore?.(result);
        hooks.toast?.("업무를 목록으로 복원했어요. 연결은 직접 이어갈 때 시작됩니다.");
      } catch (error) { if (dialog.open) status.textContent = error.message || "업무를 복원하지 못했습니다."; }
      finally { restoring = false; controls(); }
    }
    async function load() {
      if (!dialog.open || restoring || isClosed()) return;
      const current = ++ticket;
      controls(true); status.textContent = "보관한 업무를 찾고 있어요…";
      try {
        const result = await hooks.api(`/api/sessions/archived?q=${encodeURIComponent(query.value.trim())}&offset=${offset}&limit=50`);
        if (current !== ticket || !dialog.open || isClosed()) return;
        total = result.total || 0;
        list.replaceChildren();
        for (const row of result.sessions || []) {
          const article = document.createElement("article"); article.className = "archived-task";
          const info = document.createElement("div"), title = document.createElement("strong"), path = document.createElement("small"), date = document.createElement("small");
          title.textContent = row.title || "새 업무"; path.textContent = row.workspace || "";
          const value = new Date(Number(row.updated || row.created) * 1000);
          date.textContent = Number.isFinite(value.getTime()) ? `최근 사용 ${value.toLocaleDateString()}` : "";
          info.append(title, path, date);
          const button = document.createElement("button"); button.type = "button"; button.className = "quiet-button";
          button.textContent = "목록에 복원"; button.setAttribute("aria-label", `${row.title || "새 업무"} 목록에 복원`);
          button.onclick = () => restore(row.id); article.append(info, button); list.append(article);
        }
        status.textContent = result.warning || (total ? `${total}개 중 ${offset + 1}–${Math.min(offset + 50, total)}개 · 현재 업무 ${result.activeCount}/${result.activeLimit}` : "보관한 업무가 없습니다.");
      } catch (error) { if (current === ticket && dialog.open) { list.replaceChildren(); status.textContent = error.message || "목록을 불러오지 못했습니다."; } }
      finally { if (current === ticket) controls(); }
    }
    function open() {
      if (dialog.open || isClosed()) return;
      caller = document.activeElement; offset = 0; query.value = "";
      hooks.showDialog ? hooks.showDialog(dialog.id) : dialog.showModal();
      query.focus(); load();
    }
    close.onclick = () => dialog.close();
    dialog.addEventListener("cancel", event => { if (restoring) event.preventDefault(); });
    dialog.addEventListener("close", () => {
      ticket++; clearTimeout(timer); list.replaceChildren();
      if (!restoring && caller?.isConnected && !caller.disabled) caller.focus({preventScroll: true});
      caller = null;
    });
    query.oninput = () => { clearTimeout(timer); ticket++; offset = 0; timer = setTimeout(load, 250); };
    prev.onclick = () => { offset = Math.max(0, offset - 50); load(); };
    next.onclick = () => { offset += 50; load(); };
    const trigger = find("archivedTasksOpen"); if (trigger) trigger.onclick = open;
    mounted = {open}; return mounted;
  }
  return {mount, open: () => mounted?.open()};
})();
