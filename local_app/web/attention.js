"use strict";

// One global read loop observes all tasks. It never changes their execution state.
globalThis.WorkspaceAttention = (() => {
  let running = false, generation = 0, timer = null, controller = null, binding = false, lastBind = 0;
  let items = [], total = 0, native = {}, windowTitle = "", seen = new Set(), enabled = false, readInFlight = null;
  const notifications = new Map(), preferenceKey = "workspaceBrowserNotifications";
  const visible = () => document.visibilityState === "visible" && document.hasFocus?.() === true;
  const waitingLabel = kind => ({approval:"승인 대기",question:"답변 대기",choice:"디자인 선택 대기"}[kind] || "응답 대기");
  function storeSeen() { try { sessionStorage.setItem("workspaceAttentionSeen:" + windowTitle, JSON.stringify([...seen].slice(-300))); } catch (_) {} }
  function setWindowTitle(value) {
    if (typeof value !== "string" || !value || value === windowTitle) return;
    windowTitle = value; document.title = value; seen = new Set();
    try { const saved = JSON.parse(sessionStorage.getItem("workspaceAttentionSeen:" + value) || "[]"); if (Array.isArray(saved)) seen = new Set(saved.filter(id => typeof id === "string").slice(-300)); } catch (_) {}
  }
  function notificationSettings() {
    const supported = typeof globalThis.Notification === "function", permission = supported ? Notification.permission : "unsupported";
    $("notifications-toggle").disabled = !supported;
    $("notifications-toggle").textContent = enabled ? "브라우저 알림 끄기" : "브라우저 알림 켜기";
    $("notifications-message").textContent = !supported ? "이 환경은 브라우저 알림을 지원하지 않아요. 앱의 대기 배지로 확인할 수 있어요."
      : permission === "denied" ? "브라우저에서 알림을 차단했어요. 대기 배지는 계속 표시합니다."
      : enabled && permission === "granted" ? "새 승인·질문 대기를 알려드려요. 알림에는 업무 이름만 표시합니다."
      : "원할 때 켜 주세요. 알림에는 업무 이름만 표시하며 파일 내용이나 실행 명령은 넣지 않습니다.";
    $("native-attention-message").textContent = native.bound ? "이 앱 창의 작업 표시줄 강조가 연결됐어요. 다른 앱의 포커스는 바꾸지 않습니다."
      : "작업 표시줄 강조를 연결하지 못한 환경에서도 앱의 대기 배지와 선택한 브라우저 알림을 사용할 수 있어요.";
  }
  async function openTask(sessionId, pendingId) {
    if (pendingId && !items.some(item => item.id === pendingId)) return toast("이미 처리된 요청이에요.");
    try { if (await selectSession(sessionId)) $("attention-dialog").close(); } catch (err) { toast(err.message); }
  }
  function render() {
    $("attention-count").textContent = String(total); $("attention-open").hidden = total === 0;
    $("attention-open").setAttribute("aria-label", `응답이 필요한 요청 ${total}개`);
    const list = $("attention-items"); list.replaceChildren(); $("attention-empty").hidden = total > 0;
    for (const item of items) {
      const button = el("button", null, "attention-item"); button.type = "button";
      button.append(el("strong", item.title || "업무"), el("span", waitingLabel(item.kind)));
      button.onclick = () => openTask(item.sessionId, item.id); list.append(button);
    }
    notificationSettings(); renderSessions();
  }
  function apply(snapshot) {
    globalThis.WorkspaceDesktop?.apply(snapshot.desktop);
    globalThis.WorkspaceDesktop?.follow(snapshot.navigation);
    setWindowTitle(snapshot.windowTitle);
    native = snapshot.native && typeof snapshot.native === "object" ? snapshot.native : {};
    const unique = new Set();
    items = (Array.isArray(snapshot.items) ? snapshot.items : []).filter(item => {
      if (!item || typeof item.id !== "string" || typeof item.sessionId !== "string" || unique.has(item.id)) return false;
      unique.add(item.id); return true;
    });
    total = Number.isInteger(snapshot.total) && snapshot.total >= items.length ? snapshot.total : items.length;
    for (const [id, notice] of notifications) if (!unique.has(id)) { notice.close(); notifications.delete(id); }
    for (const item of items) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      if ((snapshot.desktop?.nativeAvailable && snapshot.desktop?.preferences?.enabled && snapshot.desktop?.preferences?.attention) || !enabled || typeof globalThis.Notification !== "function" || Notification.permission !== "granted" || (visible() && active?.id === item.sessionId)) continue;
      try {
        const notice = new Notification("Company Workspace · 응답이 필요해요", {body:`${item.title || "업무"} · ${waitingLabel(item.kind)}`, tag:item.id});
        notifications.set(item.id, notice);
        notice.onclick = () => { if (items.some(row => row.id === item.id)) { globalThis.focus?.(); openTask(item.sessionId, item.id); } notice.close(); };
      } catch (_) { /* The badge remains available when browser delivery fails. */ }
    }
    if (seen.size > 300) seen = new Set([...seen].slice(-300));
    storeSeen(); render();
  }
  async function bindIfFocused() {
    if (!running || appClosed || !windowTitle || !native.supported || native.bound || !visible() || binding || Date.now() - lastBind < 10000) return;
    binding = true; lastBind = Date.now(); const ticket = generation;
    try { const response = await api("/api/attention/bind", {}); if (running && ticket === generation) { native = response.native || response; notificationSettings(); } }
    catch (_) { /* A foreground race must not disable the read-only badge. */ }
    finally { binding = false; }
  }
  async function refresh() {
    if (!running || appClosed || readInFlight) return;
    clearTimeout(timer); const ticket = generation, request = {}; readInFlight = request; controller = new AbortController();
    try {
      const snapshot = await api("/api/attention", undefined, controller.signal);
      if (!running || ticket !== generation || appClosed) return;
      apply(snapshot); await bindIfFocused(); await globalThis.WorkspaceDesktop?.presence();
    } catch (err) {
      if (err.name !== "AbortError" && running && ticket === generation) $("native-attention-message").textContent = "대기 알림 연결을 다시 확인하고 있어요. 업무 요청을 다시 보내지는 않습니다.";
    } finally { if (readInFlight === request) readInFlight = null; if (running && ticket === generation && !appClosed) timer = setTimeout(refresh, 2000); }
  }
  function stop() {
    running = false; generation++; clearTimeout(timer); controller?.abort(); readInFlight = null;
    for (const notice of notifications.values()) notice.close(); notifications.clear();
  }
  function start() {
    if (running || appClosed) return;
    running = true; generation++;
    try { enabled = sessionStorage.getItem(preferenceKey) === "yes"; } catch (_) {}
    notificationSettings(); refresh();
  }
  $("attention-open").onclick = () => { render(); showDialog("attention-dialog"); };
  $("attention-close").onclick = () => $("attention-dialog").close();
  $("notifications-toggle").onclick = async () => {
    if (typeof globalThis.Notification !== "function") return;
    if (enabled) { enabled = false; for (const notice of notifications.values()) notice.close(); notifications.clear(); }
    else {
      try { const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission; enabled = permission === "granted"; }
      catch (_) { enabled = false; }
    }
    try { sessionStorage.setItem(preferenceKey, enabled ? "yes" : "no"); } catch (_) {}
    notificationSettings();
  };
  globalThis.addEventListener?.("focus", bindIfFocused);
  document.addEventListener("visibilitychange", () => { if (visible()) bindIfFocused(); });
  globalThis.addEventListener?.("pagehide", stop);
  if (boot.sessions) start();
  return {start, stop, refresh, countFor:id => items.filter(item => item.sessionId === id).length};
})();
