"use strict";

// Optional UI host. This module is loaded only when bootstrap reports an
// installed extension. Extension code never receives the application's token.
globalThis.WorkspaceExtensions = (() => {
  const ID = /^[a-z0-9][a-z0-9.-]{0,79}$/;
  const REQUEST = /^[A-Za-z0-9_-]{1,80}$/;
  const MAX_MESSAGE = 98304, MAX_REQUESTS = 512;
  let hooks = null, buttons = [], current = null, stylesheet = null;
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const theme = () => document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const plain = value => !!value && typeof value === "object" && !Array.isArray(value);
  const isCurrent = owner => current === owner && !hooks?.isClosed?.();
  function channel() {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, n => n.toString(16).padStart(2, "0")).join("");
  }
  function context() {
    const value = hooks.getContext?.() || {};
    return {
      sessionId: typeof value.sessionId === "string" ? value.sessionId : "",
      kind: value.kind === "work" ? "work" : "chat",
      workspace: value.kind === "work" && typeof value.workspace === "string" ? value.workspace : "",
      title: typeof value.title === "string" ? value.title.slice(0, 200) : ""
    };
  }
  function post(owner, data) {
    if (isCurrent(owner) && owner.frame?.contentWindow)
      owner.frame.contentWindow.postMessage({...data, channel: owner.channel}, "*");
  }
  function externalUrl(value) {
    if (typeof value !== "string" || value.length > 2048 || /[\u0000-\u0020\\]/.test(value))
      throw Error("열 수 있는 웹 주소인지 확인해 주세요.");
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash ||
        /^(?:localhost$|127\.|\[?::1\]?$)/i.test(url.hostname) ||
        [...url.searchParams.keys()].some(key => /token|key|secret|authorization|password/i.test(key)))
      throw Error("인증정보가 없는 HTTP 또는 HTTPS 웹 주소만 열 수 있어요.");
    return url.href;
  }
  function setBusy(owner, busy) {
    owner.busy = busy;
    if (!isCurrent(owner)) return;
    owner.status.textContent = busy ? "확장 기능에서 처리하고 있어요. 다른 화면으로 이동해도 시작한 작업은 계속됩니다." : "";
    owner.status.hidden = !busy;
  }
  function openExternal(owner, payload) {
    const url = externalUrl(payload.url);
    // A real anchor is retained if a browser/runtime does not transfer user
    // activation across postMessage. It opens only on an explicit click.
    owner.external.href = url;
    owner.external.textContent = "웹에서 계속하기";
    owner.external.hidden = false;
    try { globalThis.open(url, "_blank", "noopener,noreferrer"); } catch (_) { /* use the visible link */ }
    return {requested: true, fallbackAvailable: true, notice: "새 창이 열리지 않으면 위의 ‘웹에서 계속하기’를 눌러 주세요."};
  }
  async function message(owner, event) {
    if (!isCurrent(owner) || event.source !== owner.frame?.contentWindow || event.origin !== "null") return;
    const data = event.data;
    if (!plain(data)) return;
    if (data.type === "ax-extension-ready") {
      if (owner.initialized) return;
      owner.initialized = true;
      const {workspace, title, kind} = owner.context;
      post(owner, {type: "ax-extension-init", hostApi: 1, hostFeatures: ["workspaces", "publishFiles"], theme: theme(), context: {workspace, title, kind}});
      owner.loading.remove();
      return;
    }
    if (!owner.initialized || data.type !== "ax-extension-request" || data.channel !== owner.channel ||
        typeof data.requestId !== "string" || !REQUEST.test(data.requestId)) return;
    if (owner.requests.has(data.requestId)) return; // Never replay a mutation.
    const respond = value => post(owner, {type: "ax-extension-response", requestId: data.requestId, ...value});
    let length;
    try { length = JSON.stringify(data).length; } catch (_) { return; }
    if (length > MAX_MESSAGE || (data.payload !== undefined && !plain(data.payload))) return respond({ok: false, error: "확장 요청이 너무 크거나 형식이 올바르지 않아요."});
    if (owner.requests.size >= MAX_REQUESTS) return respond({ok: false, error: "확장 화면을 닫고 다시 열어 주세요."});
    if (typeof data.action !== "string" || (!["host.openExternal", "host.pickDirectory", "host.pickFile", "host.listWorkspaces", "host.selectWorkspace"].includes(data.action) && !owner.extension.actions.includes(data.action)))
      return respond({ok: false, error: "이 확장에서 제공하지 않는 기능이에요."});
    owner.requests.add(data.requestId);
    if (data.action === "host.openExternal") {
      try { respond({ok: true, result: openExternal(owner, data.payload || {})}); }
      catch (_) { respond({ok: false, error: "인증정보가 없는 HTTP 또는 HTTPS 웹 주소만 열 수 있어요."}); }
      return;
    }
    if (owner.busy) return respond({ok: false, error: "앞서 요청한 확장 작업이 끝난 뒤 다시 시도해 주세요."});
    setBusy(owner, true);
    try {
      if (data.action === "host.listWorkspaces") {
        const listed = await hooks.api(`/api/extensions/${owner.extension.id}/workspaces`, {});
        if (!Array.isArray(listed?.items)) throw Error("업무 목록을 확인하지 못했어요. 다시 시도해 주세요.");
        respond({ok: true, result: listed});
      } else if (data.action === "host.selectWorkspace") {
        const sessionId = data.payload?.sessionId;
        if (typeof sessionId !== "string" || !sessionId || sessionId.length > 200)
          throw Error("설치할 업무를 목록에서 선택해 주세요.");
        const selected = await hooks.api(`/api/extensions/${owner.extension.id}/workspace`, {sessionId});
        if (selected?.cancelled !== false || selected.sessionId !== sessionId ||
            typeof selected.path !== "string" || !selected.path ||
            typeof selected.directoryToken !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(selected.directoryToken))
          throw Error("선택한 업무 폴더를 확인하지 못했어요. 다시 선택해 주세요.");
        respond({ok: true, result: selected});
      } else if (data.action === "host.pickDirectory") {
        // The native picker is owned by the host. The child never supplies an
        // arbitrary path or a preferred starting directory.
        const selected = await hooks.api(`/api/extensions/${owner.extension.id}/directory`, {});
        if (selected?.cancelled !== true && (selected?.cancelled !== false ||
            typeof selected.path !== "string" || !selected.path ||
            typeof selected.directoryToken !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(selected.directoryToken)))
          throw Error("선택한 폴더를 확인하지 못했어요. 다시 선택해 주세요.");
        respond({ok: true, result: selected});
      } else if (data.action === "host.pickFile") {
        if (Object.keys(data.payload || {}).length) throw Error("등록할 파일은 파일 선택 창에서 선택해 주세요.");
        const selected = await hooks.api(`/api/extensions/${owner.extension.id}/file`, {});
        if (selected?.cancelled !== true && (selected?.cancelled !== false ||
            typeof selected.name !== "string" || !selected.name ||
            !Number.isSafeInteger(selected.size) || selected.size <= 0 ||
            typeof selected.fileToken !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(selected.fileToken)))
          throw Error("선택한 파일을 확인하지 못했어요. 다시 선택해 주세요.");
        respond({ok: true, result: selected});
      } else {
        const payload = {...(data.payload || {})}, directoryToken = payload.directoryToken, fileToken = payload.fileToken;
        delete payload.directoryToken;
        delete payload.fileToken;
        if (directoryToken !== undefined && (typeof directoryToken !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(directoryToken)))
          throw Error("선택한 폴더가 만료되었어요. 다시 선택해 주세요.");
        if (fileToken !== undefined && (!["inspectPublishSource", "preparePublish"].includes(data.action) ||
            typeof fileToken !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(fileToken)))
          throw Error("등록 파일을 다시 선택한 뒤 등록 내용을 확인해 주세요.");
        const result = await hooks.api(`/api/extensions/${owner.extension.id}/action`, {
          action: data.action, payload, sessionId: owner.context.sessionId || undefined, directoryToken, fileToken
        });
        if (result?.ok !== true) throw Error(result?.error || "확장 작업을 완료하지 못했어요. 상태를 확인한 뒤 다시 시도해 주세요.");
        respond({ok: true, result: result.result});
      }
    } catch (error) {
      respond({ok: false, error: error.message || "확장 작업의 완료를 확인하지 못했어요. 자동으로 다시 실행하지 않습니다."});
    } finally { setBusy(owner, false); }
  }
  function close() {
    const owner = current;
    if (!owner) return;
    current = null;
    if (owner.listener) globalThis.removeEventListener("message", owner.listener);
    owner.observer?.disconnect();
    // Do not abort or automatically repeat an action that may already have
    // changed files. The next open reads its own durable installation state.
    owner.frame?.remove();
    owner.page.remove();
    owner.app.classList.remove("extension-open", "catalog-open");
    hooks.viewChanged?.(false);
    owner.button?.setAttribute("aria-current", "false");

  }
  function style() {
    if (stylesheet) return;
    stylesheet = node("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/extensions.css";
    document.head.append(stylesheet);
  }
  async function open(extension, button) {
    if (!hooks || hooks.isClosed?.()) return;
    if (current?.extension.id === extension.id) return;
    close();
    hooks.beforeOpen?.();
    style();
    const app = document.querySelector(".app"), area = document.getElementById("work-area");
    if (!app || !area) return;
    const page = node("section", undefined, "workspace-extension-page");
    page.id = "workspace-extension-page";
    page.setAttribute("aria-label", extension.name);
    const head = node("div", undefined, "workspace-extension-utility");
    const back = node("button", "", "text-button workspace-extension-back"); back.type = "button";
    back.innerHTML = '<svg class="icon back-icon" aria-hidden="true"><use href="#i-arrow"/></svg>';
    back.append(node("span", context().kind === "chat" ? "대화로 돌아가기" : "업무로 돌아가기"));
    back.onclick = () => { close(); if (button?.isConnected) button.focus({preventScroll: true}); };
    head.append(back, node("small", `추가 기능 · ${extension.version}`));
    const status = node("p", "", "workspace-extension-status"); status.hidden = true; status.setAttribute("role", "status");
    const external = node("a", "", "workspace-extension-external"); external.hidden = true;
    external.target = "_blank"; external.rel = "noopener noreferrer";
    const body = node("div", undefined, "workspace-extension-body"), loading = node("p", "추가 기능을 여는 중이에요.", "workspace-extension-loading");
    body.append(loading); page.append(head, status, external, body); area.append(page);
    const owner = {extension, button, page, app, status, external, body, loading, channel: channel(), context: context(), requests: new Set(), busy: false, initialized: false};
    current = owner; button?.setAttribute("aria-current", "page");
    app.classList.add("extension-open", "catalog-open");
    hooks.viewChanged?.(true);
    area.scrollTop = 0;
    // This is a normal main-page view, not a modal: the sidebar stays usable.
    back.focus({preventScroll: true});
    try {
      const view = await hooks.api(`/api/extensions/${extension.id}/view`, {});
      if (!isCurrent(owner)) return;
      const target = new URL(view?.url, location.origin);
      if (target.origin !== location.origin || target.pathname !== `/extension-ui/${extension.id}` ||
          target.hash || [...target.searchParams.keys()].some(key => key !== "ticket") || !target.searchParams.get("ticket"))
        throw Error("추가 기능 화면의 주소를 확인할 수 없어요.");
      const frame = node("iframe", undefined, "workspace-extension-frame");
      frame.title = extension.name; frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("referrerpolicy", "no-referrer"); frame.setAttribute("allow", "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'");
      owner.frame = frame; owner.listener = event => { void message(owner, event); };
      globalThis.addEventListener("message", owner.listener);
      owner.observer = new MutationObserver(() => post(owner, {type: "ax-extension-theme", theme: theme()}));
      owner.observer.observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
      frame.src = target.pathname + target.search;
      body.append(frame);
    } catch (error) {
      if (isCurrent(owner)) { loading.textContent = error.message || "추가 기능을 열지 못했어요. 앱의 다른 기능은 계속 사용할 수 있습니다."; loading.setAttribute("role", "alert"); }
    }
  }
  function mount(options) {
    const entries = options?.extensions;
    if (!Array.isArray(entries) || !entries.length) return;
    close(); buttons.forEach(button => button.remove()); buttons = [];
    hooks = options;
    const nav = document.querySelector(".primary-nav");
    if (!nav) return;
    nav.classList.remove("workspace-extension-navigation");
    const seen = new Set();
    for (const extension of entries.slice(0, 16)) {
      if (!plain(extension) || typeof extension.id !== "string" || !ID.test(extension.id) || seen.has(extension.id) || extension.hostApi !== 1 ||
          typeof extension.name !== "string" || !extension.name || extension.name.length > 80 || !Array.isArray(extension.actions)) continue;
      seen.add(extension.id);
      const button = node("button", "", "nav-button workspace-extension-menu"); button.type = "button";
      button.dataset.extensionId = extension.id; button.setAttribute("aria-current", "false");
      button.setAttribute("aria-label", extension.name); button.title = extension.name;
      button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-box"/></svg>';
      const label = node("span", extension.name); label.style.overflow = "hidden"; label.style.textOverflow = "ellipsis"; label.style.whiteSpace = "nowrap";
      button.append(label); button.onclick = () => open(extension, button); nav.append(button); buttons.push(button);
    }
    // The base app hides work-only navigation in general chat. Expose only
    // installed extension navigation there; existing work-only buttons retain
    // their own mode rules. No extension means no stylesheet or layout change.
    if (buttons.length) { nav.classList.add("workspace-extension-navigation"); style(); }
  }
  function stop() { close(); buttons.forEach(button => { button.disabled = true; }); }
  return {mount, close, stop, isOpen: () => !!current, title: () => current?.extension.name || ""};
})();
