"use strict";

// A read-only inventory. Metadata never becomes HTML or an executable request.
globalThis.WorkspaceCapabilities = (() => {
  let opened = false, kind = "skills", data = null, generation = 0;
  let controller = null, loading = false, refreshTimer = null;
  const kinds = ["skills", "tools", "mcp", "commands"];
  const names = {skills:"스킬", tools:"도구", mcp:"연결 서버", commands:"명령"};
  const sources = {
    company:"회사 배포", corporate:"회사 배포", unknown:"출처 미확인", managed:"회사 관리", user:"사용자 설치", personal:"개인 설치",
    project:"프로젝트", plugin:"플러그인", external:"외부 플러그인", local:"프로젝트 로컬"
  };
  const scopeNames = {user:"사용자 전체", personal:"개인 전체", project:"현재 프로젝트", shared:"회사 공통", company:"회사 공통", unknown:"범위 미확인"};
  const serverStates = {connected:"연결됨", pending:"연결 대기", failed:"연결 실패", disabled:"사용 안 함", "needs-auth":"인증 필요"};
  const app = () => document.querySelector(".app");
  const expanded = {internal:false, reference:false};
  const invocationKey = row => (row.invocation || "").trim().replace(/^\/+/, "");
  const beforeConnection = () => data ? ["awaiting-runtime", "no-session"].includes(data.status) : !active;

  function group() { return data?.runtime?.groups?.[kind] || {reported:false, items:[]}; }
  function contextLine() {
    $("capabilities-context").textContent = active
      ? `${active.title} · ${basename(active.workspace)}`
      : "선택된 업무가 없어요. 업무 폴더에 따라 사용할 수 있는 기능이 달라집니다.";
  }
  function statusLine() {
    contextLine();
    const attention = active && ["approval", "question"].includes(active.state)
      ? " 현재 업무가 답변을 기다리고 있어요. ‘업무로 돌아가기’에서 확인하세요." : "";
    $("capabilities-status").textContent = loading
      ? "기능 목록을 확인하고 있어요. AI 요청은 보내지 않습니다." + attention
      : (data?.notice || "확인할 업무를 선택해 주세요.") + attention;
  }
  function skillInventory() {
    const merged = new Map();
    for (const [origin, rows] of [["installed", data?.installed?.skills || []],
                                  ["runtime", data?.runtime?.groups?.skills?.items || []]]) {
      rows.forEach((row, index) => {
        // Only an exact, fully qualified invocation proves these are the same skill.
        // Never merge plugin:report into another:report or an unqualified report.
        const invocation = invocationKey(row);
        const key = invocation ? `call:${invocation}` : `${origin}:${index}`;
        const previous = merged.get(key);
        merged.set(key, previous ? {...row, ...previous,
          description: row.description || previous.description,
          _runtime: previous._runtime || origin === "runtime",
          _installed: previous._installed || origin === "installed"}
          : {...row, _runtime:origin === "runtime", _installed:origin === "installed"});
      });
    }
    const result = {skills:[], internal:[], reference:[]};
    for (const row of merged.values()) {
      const category = row.userInvocable === false || row.kind === "internal" ? "internal"
        : row.kind === "reference" || !invocationKey(row) ? "reference" : "skills";
      result[category].push({...row, category});
    }
    return result;
  }
  function countLabel(type, inventory) {
    const runtime = data?.runtime?.groups?.[type];
    if (type === "skills" && (runtime?.reported || data?.installed?.state === "discovered")) {
      return String(inventory.skills.length);
    }
    return runtime?.reported ? String(runtime.items.length)
      : loading && !data ? "확인 중" : beforeConnection() ? "연결 전" : "미확인";
  }
  function tabState(inventory) {
    for (const type of kinds) {
      const tab = $(`capabilities-${type}-tab`);
      tab.setAttribute("aria-selected", String(type === kind));
      tab.tabIndex = type === kind ? 0 : -1;
      const count = tab.querySelector("[data-count]");
      const label = countLabel(type, inventory);
      if (count) count.textContent = label;
      tab.setAttribute("aria-label", `${names[type]} · ${label}${/^\d+$/.test(label) ? "개" : ""}`);
    }
    $("capabilities-panel").setAttribute("aria-labelledby", `capabilities-${kind}-tab`);
  }
  function matches(row, query) {
    return [row.name, row.description, row.invocation, row.source, sources[row.source], row.scope, scopeNames[row.scope], row.server, row.kind, row.pluginNamespace]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query);
  }
  function addSection(label, rows, origin, category = "") {
    if (!rows.length) return;
    const collapsed = category === "internal" || category === "reference";
    const section = el(collapsed ? "details" : "section", null,
      collapsed ? "capability-group capability-secondary" : "capability-group");
    section.append(el(collapsed ? "summary" : "h3", `${label} · ${rows.length}`, "capability-group-title"));
    if (collapsed) {
      section.open = Boolean($("capabilities-search").value.trim()) || expanded[category];
      section.ontoggle = () => { expanded[category] = section.open; };
      section.append(el("p", category === "internal"
        ? "Claude가 내부적으로 참고하는 보조 스킬이에요. 직접 호출하는 일반 스킬 목록에서는 제외했어요."
        : "설치된 참고 자료예요. 직접 호출하는 스킬이 아니며, Claude가 필요할 때 내용을 참고할 수 있어요.",
        "capability-secondary-note"));
    }
    const cards = el("div", null, "capability-grid");
    for (const row of rows) {
      const card = el("article", null, "capability-card");
      const header = el("div", null, "capability-card-header");
      header.append(el("h4", row.name, "capability-name"));
      const live = data?.runtime?.state === "live";
      const fromRuntime = origin === "runtime" || row._runtime;
      let badge = fromRuntime ? live ? "연결에서 확인" : "마지막 연결 정보" : "설치에서 확인";
      if (data?.status === "demo") badge = "체험용 가상 항목";
      header.append(el("span", badge, "capability-badge"));
      card.append(header);
      card.append(el("p", row.description || "제공된 설명이 없어요.", "capability-description"));
      const metadata = el("div", null, "capability-metadata");
      if (row.invocation && row.userInvocable !== false && !["internal", "reference"].includes(row.category)) {
        metadata.append(el("code", "/" + invocationKey(row)));
      }
      const namespace = row.pluginNamespace || invocationKey(row).split(":").slice(0, -1).join(":");
      if (namespace) metadata.append(el("span", `플러그인: ${namespace}`));
      if (row.source) metadata.append(el("span", `출처: ${sources[row.source] || row.source}`));
      if (row.scope) metadata.append(el("span", `범위: ${scopeNames[row.scope] || row.scope}`));
      if (kind === "tools") metadata.append(el("span", row.kind === "mcp" ? `연결 도구${row.server ? " · " + row.server : ""}` : "Claude 도구"));
      if (kind === "mcp") metadata.append(el("span", `${live ? "연결 상태" : "마지막 상태"}: ${serverStates[row.status] || row.status || "미제공"}`));
      if (row.explicitOnly && !collapsed) metadata.append(el("span", "직접 요청하는 스킬"));
      if (row._installed && row._runtime) metadata.append(el("span", "설치 정보도 확인됨"));
      if (metadata.children.length) card.append(metadata);
      cards.append(card);
    }
    section.append(cards);
    $("capabilities-list").append(section);
  }
  function render() {
    const inventory = skillInventory();
    tabState(inventory); statusLine();
    const list = $("capabilities-list"); list.replaceChildren();
    const current = group(), query = $("capabilities-search").value.trim().toLocaleLowerCase();
    const filtered = rows => rows.filter(row => matches(row, query));
    let visibleCount = 0;
    if (kind === "skills") {
      for (const [category, label] of [["skills", "일반 스킬"], ["internal", "내부 보조 스킬"], ["reference", "참고 자료"]]) {
        const rows = filtered(inventory[category]); visibleCount += rows.length;
        addSection(label, rows, "merged", category);
      }
    } else {
      const rows = filtered(current.items || []); visibleCount = rows.length;
      const label = data?.status === "demo" ? "가상 예시" : data?.runtime?.state === "live" ? "현재 연결에서 확인" : "마지막 연결에서 확인";
      addSection(label, rows, "runtime");
    }
    const empty = $("capabilities-empty");
    empty.hidden = visibleCount > 0;
    empty.textContent = loading && !data ? "기능 목록을 불러오고 있어요." : query ? "검색어와 일치하는 항목이 없어요. 이름이나 설명으로 다시 찾아보세요."
      : !active ? "먼저 새 업무를 만들거나 최근 업무를 선택해 주세요."
      : kind === "skills" && data?.installed?.state === "discovered" ? "확인한 설치 목록에 일반 스킬이 없어요."
      : current.reported ? `CLI가 보고한 ${names[kind]} 목록이 비어 있어요.`
      : beforeConnection() ? `첫 요청으로 Claude에 연결하면 ${names[kind]} 목록을 확인할 수 있어요.`
      : `현재 Claude 연결에서 ${names[kind]} 목록을 전달받지 못했어요. 항목이 없다는 뜻은 아닙니다.`;
    const notes = [...(data?.warnings || [])];
    if (current.limited) notes.push("목록이 길어 일부 항목만 표시합니다.");
    if (kind === "skills" && data?.installed?.limited) notes.push("설치 정보는 확인 범위 내의 일부 항목입니다.");
    if (data && !current.reported && active && !beforeConnection() && !(kind === "skills" && data.installed?.state === "discovered")) {
      notes.push(`현재 Claude 연결에서 ${names[kind]} 목록을 제공하지 않았어요. 항목이 없다는 뜻은 아닙니다.`);
    }
    $("capabilities-warning").textContent = notes.join(" ");
    $("capabilities-warning").hidden = notes.length === 0;
    const installedNotice = data?.installed?.state !== "discovered" && data?.installed?.notice;
    $("capabilities-installed-note").textContent = kind === "skills"
      ? installedNotice ? `${installedNotice} 이 화면에서는 목록만 조회합니다.`
        : "설치 정보와 연결에서 확인한 스킬을 함께 보여드려요. 설치만 확인한 스킬은 실제 연결에 로드되지 않을 수 있어요. 이 화면에서는 목록만 조회합니다."
      : kind === "commands" ? "원본 Claude CLI 전용 명령이 포함될 수 있어요. 이 화면에서는 목록만 확인합니다."
      : "목록에 표시돼도 실행 권한이나 성공을 보장하지 않습니다. 실제 사용 시 기존 승인과 회사 정책을 따릅니다.";
    const summary = $("capabilities-summary");
    summary.replaceChildren(); summary.hidden = !data;
    const model = data?.runtime?.model;
    if (model) summary.append(el("span", `모델 · ${model}`, "capability-summary-item"));
    if (kind === "skills") {
      if (data?.installed?.state === "discovered" || current.reported) {
        summary.append(el("span", `일반 스킬 ${inventory.skills.length}개`, "capability-summary-item"));
        for (const [type, label] of [["internal", "내부 보조"], ["reference", "참고 자료"]]) {
          if (inventory[type].length) summary.append(el("span", `${label} ${inventory[type].length}개`, "capability-summary-item"));
        }
      }
      summary.append(el("span", "스킬 숫자는 설치·연결 목록의 중복을 합친 일반 스킬 수예요. 내부 보조와 참고 자료는 아래에 따로 표시해요.", "capability-count-note"));
    } else {
      summary.append(el("span", "숫자는 Claude 연결에서 확인한 전체 항목 수예요. ‘연결 전’·‘미확인’은 0개라는 뜻이 아닙니다.", "capability-count-note"));
    }
  }
  async function refresh() {
    const ticket = ++generation, id = active?.id || null;
    if (controller) controller.abort();
    controller = new AbortController(); loading = true;
    $("capabilities-refresh").disabled = true; statusLine();
    try {
      const response = await api("/api/capabilities" + (id ? `?id=${encodeURIComponent(id)}` : ""), undefined, controller.signal);
      if (!opened || ticket !== generation || (active?.id || null) !== id) return;
      data = response; loading = false; render();
    } catch (err) {
      if (err.name === "AbortError" || ticket !== generation || !opened) return;
      data = null; loading = false; render();
      $("capabilities-status").textContent = "목록을 불러오지 못했어요. 새로고침으로 다시 확인해 주세요.";
      $("capabilities-warning").textContent = err.message;
      $("capabilities-warning").hidden = false;
    } finally {
      if (ticket === generation) { loading = false; $("capabilities-refresh").disabled = false; }
    }
  }
  function open() {
    opened = true; app().classList.add("catalog-open");
    $("capabilities-view").hidden = false;
    $("capabilities-open").setAttribute("aria-current", "page");
    $("home-button").setAttribute("aria-current", "false");
    $("chat-title").textContent = "스킬·도구";
    $("work-area").scrollTop = 0;
    data = null; loading = true; render(); refresh();
    $("capabilities-search").focus();
  }
  function close() {
    if (!opened) return;
    opened = false; generation++; loading = false;
    if (controller) controller.abort();
    clearTimeout(refreshTimer);
    app().classList.remove("catalog-open");
    $("capabilities-view").hidden = true;
    $("capabilities-open").setAttribute("aria-current", "false");
    taskHeader();
  }
  function contextChanged(reload = false) {
    if (!opened) return;
    statusLine();
    if (reload) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 100); }
  }
  $("capabilities-open").onclick = open;
  $("capabilities-back").onclick = () => { close(); $("capabilities-open").focus(); };
  $("capabilities-refresh").onclick = refresh;
  $("capabilities-search").oninput = render;
  for (const type of kinds) {
    const tab = $(`capabilities-${type}-tab`);
    tab.onclick = () => { kind = type; render(); };
    tab.onkeydown = event => {
      const index = kinds.indexOf(type);
      const next = event.key === "ArrowRight" ? (index + 1) % kinds.length
        : event.key === "ArrowLeft" ? (index + kinds.length - 1) % kinds.length
        : event.key === "Home" ? 0 : event.key === "End" ? kinds.length - 1 : -1;
      if (next < 0) return;
      event.preventDefault(); kind = kinds[next]; render(); $(`capabilities-${kind}-tab`).focus();
    };
  }
  return {open, close, contextChanged, isOpen: () => opened};
})();
