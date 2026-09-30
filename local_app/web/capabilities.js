"use strict";

// Read-only inventory with an explicit, trusted connection preparation action.
// Metadata never becomes HTML or an executable request.
globalThis.WorkspaceCapabilities = (() => {
  let opened = false, kind = "skills", data = null, generation = 0;
  let controller = null, loading = false, refreshTimer = null;
  let scope = "common", selectedFolder = "", selectedSession = null, folderOptionsKey = "";
  let commonSession = null, commonExplicit = false, preparing = null;
  const isLive = item => item?.connection?.connected === true || item?.connectionState === "live";
  const kinds = ["skills", "tools", "mcp", "commands"];
  const names = {skills:"스킬", tools:"도구", mcp:"연결 서버", commands:"명령"};
  const sources = {
    company:"회사 배포", corporate:"회사 배포", unknown:"출처 미확인", managed:"회사 관리", user:"사용자 설치", personal:"개인 설치",
    project:"프로젝트", plugin:"플러그인", external:"외부 플러그인", local:"프로젝트 로컬"
  };
  const scopeNames = {user:"사용자 공통", personal:"사용자 공통", project:"선택한 폴더 범위", shared:"회사 공통", company:"회사 공통", unknown:"범위 미확인"};
  const serverStates = {connected:"연결됨", pending:"연결 대기", failed:"연결 실패", disabled:"사용 안 함", "needs-auth":"인증 필요"};
  const app = () => document.querySelector(".app");
  const expanded = {internal:false, reference:false, runtime:false, builtin:false};
  const invocationKey = row => (row.invocation || "").trim().replace(/^\/+/, "");
  const runtimeSession = () => scope === "common" ? commonSession : selectedSession;
  const beforeConnection = () => data ? ["awaiting-runtime", "no-session", "installed-only"].includes(data.status) : !runtimeSession();
  const installedAvailable = () => ["discovered", "partial"].includes(data?.installed?.state);
  const installedPartial = () => data?.installed?.state === "partial" || data?.installed?.limited === true;
  const contextKey = () => [scope, scope === "folder" ? pathKey(selectedFolder) : "", runtimeSession()?.id || ""].join("\n");
  const pathKey = value => (value || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  function sessionFolders() {
    const folders = new Map();
    const recent = [...sessions].sort((a, b) => (b.updated || b.created || 0) - (a.updated || a.created || 0));
    for (const item of [active, ...recent].filter(Boolean)) {
      if (typeof item.workspace !== "string" || !item.workspace || !item.id) continue;
      const key = pathKey(item.workspace);
      if (!folders.has(key)) folders.set(key, {workspace:item.workspace, sessions:[]});
      const rows = folders.get(key).sessions;
      if (!rows.some(row => row.id === item.id)) rows.push(item);
    }
    return [...folders.values()];
  }
  function selectFolder(value) {
    const folder = sessionFolders().find(item => pathKey(item.workspace) === pathKey(value));
    selectedFolder = folder?.workspace || "";
    const match = folder?.sessions.find(isLive) || folder?.sessions.find(item => item.id === selectedSession?.id) || folder?.sessions[0];
    selectedSession = match ? {id:match.id, title:match.title, workspace:folder.workspace} : null;
  }
  function syncFolders(chooseDefault = false) {
    if (scope === "common") return syncConnections();
    const folders = sessionFolders(), select = $("capabilities-folder");
    const key = "folders:" + JSON.stringify(folders.map(item => item.workspace));
    if (key !== folderOptionsKey || !select.children.length) {
      folderOptionsKey = key;
      const placeholder = el("option", folders.length ? "업무 폴더를 선택하세요" : "등록된 업무 폴더가 없어요");
      placeholder.value = ""; placeholder.disabled = true;
      select.replaceChildren(placeholder);
      for (const folder of folders) {
        const option = el("option", `${basename(folder.workspace)} — ${folder.workspace}`);
        option.value = folder.workspace; option.title = folder.workspace; select.append(option);
      }
    }
    const exists = folders.some(item => pathKey(item.workspace) === pathKey(selectedFolder));
    selectFolder(exists ? selectedFolder : chooseDefault ? folders[0]?.workspace : "");
    select.value = selectedFolder; select.disabled = folders.length === 0;
  }
  function syncConnections() {
    const rows = sessionFolders().flatMap(folder => folder.sessions);
    const previous = rows.find(item => item.id === commonSession?.id);
    const selected = (commonExplicit && previous) || rows.find(item=>item.id===active?.id && isLive(item)) || rows.find(isLive) || previous || rows.find(item => item.id === active?.id) || rows[0];
    commonSession = selected ? {id:selected.id, title:selected.title, workspace:selected.workspace} : null;
    const select = $("capabilities-folder"), key = "connections:" + JSON.stringify(rows.map(item => [item.id,item.title,item.workspace]));
    if (key !== folderOptionsKey || !select.children.length) {
      folderOptionsKey = key;
      const placeholder = el("option", rows.length ? "연결 목록의 기준 업무를 선택하세요" : "등록된 업무가 없어요");
      placeholder.value = ""; placeholder.disabled = true; select.replaceChildren(placeholder);
      for (const item of rows) {
        const option = el("option", `${item.title || "업무"} — ${item.workspace}`);
        option.value = item.id; option.title = item.workspace; select.append(option);
      }
    }
    select.value = commonSession?.id || ""; select.disabled = rows.length === 0;
  }
  const emptyFolderNotice = () => sessionFolders().length
    ? "목록을 확인할 업무 폴더를 드롭다운에서 선택해 주세요."
    : "등록된 업무 폴더가 없어요. 새 업무를 만들면 이곳에서 폴더를 선택할 수 있어요.";

  function group() { return data?.runtime?.groups?.[kind] || {reported:false, items:[]}; }
  function contextLine() {
    $("capabilities-scope-select").value = scope;
    $("capabilities-folder").value = scope === "common" ? commonSession?.id || "" : selectedFolder;
    $("capabilities-folder").title = scope === "common" ? commonSession?.workspace || "" : selectedFolder;
    $("capabilities-folder").hidden = false;
    $("capabilities-folder-label").hidden = false;
    $("capabilities-folder-label").textContent = scope === "common" ? "연결 기준 업무" : "업무 폴더";
    $("capabilities-context").textContent = data?.schemaVersion === 1 ? active
      ? `${active.title} · ${basename(active.workspace)}`
      : "선택된 업무가 없어요. 업무 폴더에 따라 사용할 수 있는 기능이 달라집니다."
      : scope === "common" ? `사용자 공통 설치 스킬${commonSession ? ` · 연결 근거: ${commonSession.title} · ${commonSession.workspace}` : " · 연결 기준 업무 없음"}`
      : selectedFolder ? `${selectedFolder}${selectedSession ? ` · 연결 근거: ${selectedSession.title}` : " · 설치 정보만 조회"}`
      : emptyFolderNotice();
  }
  function statusLine() {
    contextLine();
    const selected=runtimeSession();
    $("capabilities-connect").disabled=!!preparing||!selected||appClosed;
    $("capabilities-connect").textContent=preparing ? "연결 목록 확인 중…" : "연결하고 목록 확인";
    const attention = active && ["approval", "question"].includes(active.state)
      ? " 현재 업무가 답변을 기다리고 있어요. ‘업무로 돌아가기’에서 확인하세요." : "";
    $("capabilities-status").textContent = loading
      ? "기능 목록을 확인하고 있어요. AI 요청은 보내지 않습니다." + attention
      : (data?.notice || (scope === "folder" ? selectedFolder ? "선택한 업무 폴더의 기능 목록을 확인합니다." : emptyFolderNotice() : "공통 설치 스킬과 선택한 업무 연결의 보고 목록을 구분해 확인합니다.")) + attention;
  }
  function skillInventory() {
    if (data?.schemaVersion === 2) {
      const result = {skills:[], internal:[], reference:[], runtime:[]}, seen = new Set();
      const runtime = data?.runtime?.groups?.skills?.items || [];
      for (const [index, row] of (data?.installed?.skills || []).entries()) {
        const key = row.candidateId || `unidentified:${index}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const category = row.userInvocable === false || row.kind === "internal" ? "internal"
          : row.kind === "reference" || !invocationKey(row) ? "reference" : "skills";
        // A shared invocation is not installation identity. Only an explicit
        // candidate ID from the runtime establishes the same discovered item.
        const confirmed = Boolean(row.candidateId && runtime.some(item => item.candidateId === row.candidateId));
        result[category].push({...row, category, _installed:true, _runtime:confirmed});
      }
      result.runtime = runtime.map(row => ({...row, _runtime:true}));
      const candidates = [...result.skills, ...result.internal, ...result.reference];
      for (const row of candidates) {
        row._duplicateOrigin = candidates.some(other => other !== row && other.name === row.name
          && invocationKey(other) === invocationKey(row));
      }
      return result;
    }
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
    if (type === "skills" && data?.schemaVersion === 2) {
      if (installedAvailable()) return installedPartial() && !inventory.skills.length ? "미확인" : String(inventory.skills.length);
      if (runtime?.reported) return String(runtime.items.length);
      return "미확인";
    }
    if (type === "skills" && data?.schemaVersion !== 2 && (runtime?.reported || installedAvailable())) {
      return String(inventory.skills.length);
    }
    return runtime?.reported ? String(runtime.items.length)
      : loading && !data ? "확인 중" : beforeConnection() ? (runtimeSession()?"연결 필요":"업무 선택") : "CLI 미제공";
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
    return [row.name, row.displayName, row.description, row.invocation, row.source, sources[row.source], row.scope, scopeNames[row.scope], row.server, row.kind, row.pluginNamespace, row.originLabel, row.scopeLabel]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query);
  }
  function addSection(label, rows, origin, category = "") {
    if (!rows.length) return;
    const collapsed = ["internal", "reference", "runtime", "builtin"].includes(category);
    const section = el(collapsed ? "details" : "section", null,
      collapsed ? "capability-group capability-secondary" : "capability-group");
    section.append(el(collapsed ? "summary" : "h3", `${label} · ${rows.length}`, "capability-group-title"));
    if (collapsed) {
      section.open = Boolean($("capabilities-search").value.trim()) || expanded[category];
      section.ontoggle = () => { expanded[category] = section.open; };
      section.append(el("p", category === "internal"
        ? "Claude가 내부적으로 참고하는 보조 스킬이에요. 직접 호출하는 일반 스킬 목록에서는 제외했어요."
        : category === "runtime" ? "Claude 연결이 보고한 목록이에요. 같은 호출명이 설치 목록에 있어도 어느 설치 항목인지 확정하지 않습니다."
        : category === "builtin" ? "Claude가 업무를 처리할 때 사용하는 기본 도구예요. 목록에 표시돼도 실행 권한을 보장하지 않습니다."
        : "설치된 참고 자료예요. 직접 호출하는 스킬이 아니며, Claude가 필요할 때 내용을 참고할 수 있어요.",
        "capability-secondary-note"));
    }
    const cards = el("div", null, "capability-grid");
    for (const row of rows) {
      const card = el("article", null, "capability-card");
      const header = el("div", null, "capability-card-header");
      header.append(el("h4", row.displayName || row.name, "capability-name"));
      const live = data?.runtime?.state === "live";
      const fromRuntime = origin === "runtime" || row._runtime;
      let badge = fromRuntime ? live ? "연결에서 확인" : "마지막 연결 정보" : "설치에서 확인";
      if (data?.status === "demo") badge = "체험용 가상 항목";
      header.append(el("span", badge, "capability-badge"));
      card.append(header);
      card.append(el("p", row.description || "제공된 설명이 없어요.", "capability-description"));
      const metadata = el("div", null, "capability-metadata");
      if (row.displayName && row.displayName !== row.name) metadata.append(el("code", row.name));
      if (row.invocation && row.userInvocable !== false && !["internal", "reference"].includes(row.category)) {
        metadata.append(el("code", "/" + invocationKey(row)));
      }
      const namespace = row.pluginNamespace || invocationKey(row).split(":").slice(0, -1).join(":");
      if (namespace) metadata.append(el("span", `플러그인: ${namespace}`));
      if (row.source) metadata.append(el("span", `출처: ${sources[row.source] || row.source}`));
      if (row.scope) metadata.append(el("span", `범위: ${scopeNames[row.scope] || row.scope}`));
      if (row.originLabel) metadata.append(el("span", row.originLabel));
      if (row.scopeLabel) metadata.append(el("span", row.scopeLabel));
      if (row._duplicateOrigin && row.candidateId) metadata.append(el("span", `설치 항목 구분: ${row.candidateId.split(":").pop().slice(0, 8)}`));
      if (kind === "tools") {
        const labels = {builtin:"Claude 기본 도구", harness:"외부 제공 도구", mcp:"연결 서버 도구", unknown:"종류 미확인"};
        metadata.append(el("span", `${labels[row.kind] || labels.unknown}${row.server ? " · " + row.server : ""}`));
        metadata.append(el("span", row.descriptionSource === "runtime" ? "설명: 연결에서 제공"
          : row.descriptionSource === "reference" ? "설명: Claude 공식 도구 안내" : "설명: 미제공"));
        const evidence = {runtime:"연결 메타데이터", reference:"공식 도구명 대조", "qualified-name":"연결 서버 이름 형식", unknown:"미확인"};
        metadata.append(el("span", `분류 근거: ${evidence[row.classificationSource] || "미확인"}`));
        if (row.referenceUrl === "https://code.claude.com/docs/en/tools-reference") {
          const link = el("a", "공식 도구 안내"); link.href = row.referenceUrl;
          link.target = "_blank"; link.rel = "noopener noreferrer"; metadata.append(link);
        }
      }
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
      if (data?.schemaVersion === 2) {
        const rows = filtered(inventory.runtime); visibleCount += rows.length;
        addSection("연결 보고 목록", rows, "runtime", "runtime");
      }
    } else if (kind === "tools" && data?.schemaVersion === 2) {
      for (const [type, label] of [["harness", "외부 제공 도구"], ["mcp", "연결 서버 도구"], ["unknown", "종류 미확인 도구"], ["builtin", "Claude 기본 도구"]]) {
        const rows = filtered((current.items || []).filter(row => (row.kind || "unknown") === type));
        visibleCount += rows.length; addSection(label, rows, "runtime", type);
      }
    } else {
      const rows = filtered(current.items || []); visibleCount = rows.length;
      const label = data?.status === "demo" ? "가상 예시" : data?.runtime?.state === "live" ? "현재 연결에서 확인" : "마지막 연결에서 확인";
      addSection(label, rows, "runtime");
    }
    const empty = $("capabilities-empty");
    empty.hidden = visibleCount > 0;
    empty.textContent = loading && !data ? "기능 목록을 불러오고 있어요." : query ? "검색어와 일치하는 항목이 없어요. 이름이나 설명으로 다시 찾아보세요."
      : scope === "folder" && !selectedFolder && data?.schemaVersion !== 1 ? emptyFolderNotice()
      : !active && data?.schemaVersion !== 2 ? "먼저 새 업무를 만들거나 최근 업무를 선택해 주세요."
      : kind === "skills" && installedAvailable() ? installedPartial()
        ? "설치 목록의 일부를 확인하지 못했어요. 현재 확인된 일반 스킬은 없지만 전체가 0개라는 뜻은 아닙니다."
        : "확인한 설치 목록에 일반 스킬이 없어요."
      : current.reported ? `CLI가 보고한 ${names[kind]} 목록이 비어 있어요.`
      : data?.schemaVersion === 2 && data?.status === "installed-only" ? "이 범위의 업무 연결 보고가 없어요. 설치 정보와 실제 연결 목록은 별도로 확인합니다."
      : beforeConnection() ? `선택한 업무의 Claude 연결이 준비되면 ${names[kind]} 목록을 확인할 수 있어요. 위의 ‘연결하고 목록 확인’을 눌러 주세요.`
      : `현재 Claude 연결에서 ${names[kind]} 목록을 전달받지 못했어요. 항목이 없다는 뜻은 아닙니다.`;
    const notes = [...(data?.warnings || [])];
    if (kind === "skills" && data?.installed?.diagnostics?.summary && !["ready", "metadata_read"].includes(data.installed.diagnostics.code)) notes.push(data.installed.diagnostics.summary);
    if (current.limited) notes.push("목록이 길어 일부 항목만 표시합니다.");
    if (kind === "skills" && data?.installed?.limited) notes.push("설치 정보는 확인 범위 내의 일부 항목입니다.");
    if (data && !current.reported && active && !beforeConnection() && !(kind === "skills" && data.installed?.state === "discovered")) {
      notes.push(`현재 Claude 연결에서 ${names[kind]} 목록을 제공하지 않았어요. 항목이 없다는 뜻은 아닙니다.`);
    }
    $("capabilities-warning").textContent = notes.join(" ");
    $("capabilities-warning").hidden = notes.length === 0;
    const installedNotice = !installedAvailable() && data?.installed?.notice;
    $("capabilities-installed-note").textContent = kind === "skills"
      ? installedNotice ? `${installedNotice} 이 화면에서는 목록만 조회합니다.`
        : data?.schemaVersion !== 1 ? "설치 후보와 연결 보고를 별도로 표시합니다. 설치 발견은 실행 가능 여부를 뜻하지 않습니다. 이 화면에서는 목록만 조회합니다."
        : "설치 정보와 연결에서 확인한 스킬을 함께 보여드려요. 설치만 확인한 스킬은 실제 연결에 로드되지 않을 수 있어요. 이 화면에서는 목록만 조회합니다."
      : kind === "commands" ? "원본 Claude CLI 전용 명령이 포함될 수 있어요. 이 화면에서는 목록만 확인합니다."
      : "목록에 표시돼도 실행 권한이나 성공을 보장하지 않습니다. 실제 사용 시 기존 승인과 회사 정책을 따릅니다.";
    if (kind === "skills" && data?.installed?.context?.actualCliContextVerified === false) {
      const roots = {explicit:"앱에 지정된", environment:"앱 환경에서 지정된", default:"사용자 기본"};
      $("capabilities-installed-note").textContent += ` ${roots[data.installed.context.configRootSource] || "서버에서 확인한"} 설정 기준이며 실제 Claude 실행 환경과의 설정 일치는 미확인입니다.`;
    }
    const summary = $("capabilities-summary");
    summary.replaceChildren(); summary.hidden = !data;
    const model = data?.runtime?.model;
    const source = data?.runtime?.source;
    if (source) summary.append(el("span", `연결 기준 · ${source.title || source.sessionId} · ${source.workspace || ""}`, "capability-summary-item"));
    if (model) summary.append(el("span", `모델 · ${model}`, "capability-summary-item"));
    if (kind === "skills") {
      if (data?.schemaVersion === 2) {
        if (installedAvailable()) summary.append(el("span", installedPartial()
          ? `확인된 일반 설치 후보 ${inventory.skills.length}개 · 일부 미확인`
          : `일반 설치 후보 ${inventory.skills.length}개`, "capability-summary-item"));
        if (current.reported) summary.append(el("span", `연결 보고 ${current.items.length}개`, "capability-summary-item"));
        summary.append(el("span", installedAvailable()
          ? "일반 설치 스킬 수예요. 내부 보조·참고 자료와 연결 보고는 별도로 표시합니다."
          : current.reported ? "설치 후보 수를 확인하지 못해 연결 보고 수를 표시합니다. 미확인은 0개라는 뜻이 아닙니다."
          : "설치 후보와 연결 보고 수를 확인하지 못했습니다. 미확인은 0개라는 뜻이 아닙니다.", "capability-count-note"));
      } else if (installedAvailable() || current.reported) {
        summary.append(el("span", `일반 스킬 ${inventory.skills.length}개`, "capability-summary-item"));
        for (const [type, label] of [["internal", "내부 보조"], ["reference", "참고 자료"]]) {
          if (inventory[type].length) summary.append(el("span", `${label} ${inventory[type].length}개`, "capability-summary-item"));
        }
      }
      if (data?.schemaVersion !== 2) summary.append(el("span", "스킬 숫자는 설치·연결 목록의 중복을 합친 일반 스킬 수예요. 내부 보조와 참고 자료는 아래에 따로 표시해요.", "capability-count-note"));
    } else {
      summary.append(el("span", "숫자는 선택한 업무의 Claude 연결이 보고한 항목 수예요. ‘CLI 미제공’은 0개라는 뜻이 아닙니다. 도구 목록은 첫 실제 요청 후 제공될 수 있어요.", "capability-count-note"));
    }
  }
  async function refresh() {
    clearTimeout(refreshTimer);
    syncFolders();
    const ticket = ++generation, key = contextKey();
    if (controller) controller.abort();
    controller = new AbortController(); loading = true;
    $("capabilities-refresh").disabled = true; statusLine();
    if (scope === "folder" && !selectedFolder) {
      data = null; loading = false; $("capabilities-refresh").disabled = false; render(); return;
    }
    try {
      let query = `?scope=${scope}`;
      if (scope === "folder") query += `&workspace=${encodeURIComponent(selectedFolder)}` + (selectedSession ? `&id=${encodeURIComponent(selectedSession.id)}` : "");
      else if (commonSession) query += `&id=${encodeURIComponent(commonSession.id)}`;
      const response = await api("/api/capabilities" + query, undefined, controller.signal);
      if (!opened || ticket !== generation || contextKey() !== key) return;
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
    globalThis.WorkspaceSessionImport?.render();
    $("capabilities-view").hidden = false;
    $("capabilities-open").setAttribute("aria-current", "page");
    $("home-button").setAttribute("aria-current", "false");
    $("chat-title").textContent = "스킬·도구";
    $("work-area").scrollTop = 0;
    syncFolders(); data = null; loading = true; render(); refresh();
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
  async function prepareCatalog() {
    if(preparing||!runtimeSession()||appClosed)return;
    const context={key:contextKey(),id:runtimeSession().id};preparing=context;statusLine();
    const current=()=>opened&&!appClosed&&contextKey()===context.key;
    try {
      const item=await api(`/api/session?id=${encodeURIComponent(context.id)}`);
      if(!current())return;
      if(!item.trusted){
        const accepted=await confirmAction({title:"이 업무 폴더의 Claude 연결",message:`${item.workspace}\n이 폴더의 Claude 설정·후크·MCP를 실행해 목록을 확인합니다. AI 질문은 보내지 않습니다.`,confirmLabel:"이 폴더 연결"});
        if(!accepted||!current())return;
        await api('/api/trust',{id:context.id,trusted:true});
        if(!current())return;
      }
      if(["starting","running","approval","question"].includes(item.state)){
        await refresh();toast("현재 업무의 연결 목록을 갱신했어요. 진행 중인 요청은 유지합니다.");return;
      }
      const response=await api('/api/connect',{id:context.id});
      if(!current())return;
      sessions=sessions.map(row=>row.id===context.id?{...row,connectionState:"live"}:row);
      if(active?.id===context.id){active.trusted=true;active.connection=response.connection;renderConnection(response.connection);}
      await refresh();
      if(current())toast("연결 목록을 확인했어요. 도구 목록은 CLI가 첫 실제 요청에서 제공하는 경우 이후에 표시됩니다.");
    }catch(err){if(current())toast(err.message);}
    finally{if(preparing===context){preparing=null;if(opened)statusLine();}}
  }
  function contextChanged(reload = false) {
    if (!opened) return;
    const previous = contextKey();
    syncFolders();
    if (contextKey() !== previous) { data = null; render(); refresh(); return; }
    statusLine();
    if (reload && active?.id === runtimeSession()?.id) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 100); }
  }
  $("capabilities-scope-select").onchange = () => {
    scope = $("capabilities-scope-select").value === "folder" ? "folder" : "common";
    syncFolders(scope === "folder");
    data = null; render(); refresh();
  };
  $("capabilities-folder").onchange = () => {
    if (!opened) return;
    if (scope === "common") {
      const item = sessionFolders().flatMap(folder => folder.sessions).find(row => row.id === $("capabilities-folder").value);
      if (!item) return syncConnections();
      commonSession = {id:item.id, title:item.title, workspace:item.workspace};
      commonExplicit = true;
    } else selectFolder($("capabilities-folder").value);
    data = null; render(); return refresh();
  };
  $("capabilities-open").onclick = open;
  $("capabilities-back").onclick = () => { close(); $("capabilities-open").focus(); };
  $("capabilities-refresh").onclick = refresh;
  $("capabilities-connect").onclick = prepareCatalog;
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
