"use strict";

// A single active detail view owns its DOM, focus return and asynchronous work.
globalThis.WorkspaceResultExplorer = (() => {
  const get = id => document.getElementById(id);
  const shell = get("result-explorer"), dock = get("inspector-panel"), dialog = get("explorer-dialog");
  const host = get("explorer-content"), title = get("explorer-title"), expand = get("explorer-expand");
  const back = get("explorer-back"), popup = get("explorer-popup");
  const narrow = globalThis.matchMedia?.("(max-width: 1100px)");
  const button = (label, cls = "quiet-button") => {
    const item = document.createElement("button");
    item.type = "button"; item.textContent = label; item.className = cls;
    return item;
  };
  let current = null;
  // Keep only display choices for recent files in this window. Rows and DOM
  // references belong to the open view and are released when it closes.
  const recentTables = new Map();
  const maxRecentTables = 24;

  function canDock() {
    return !!shell && !narrow?.matches && !globalThis.WorkspaceLayout?.snapshot?.().miniMode &&
      !document.querySelector(".app.chat-mode,.app.catalog-open") &&
      !document.querySelector('dialog[open]:not(#explorer-dialog)');
  }
  function updateViewActions() {
    expand.hidden = !!current?.modal;
    if (!popup) return;
    popup.hidden = !!current?.modal && !canDock();
    popup.textContent = current?.modal ? "탐색창으로 보기" : "팝업으로 보기";
    popup.setAttribute("aria-expanded", String(!!current?.modal));
  }
  function moveShell(parent, before = null) {
    // State-preserving moves keep an HTML preview's embedded document alive.
    if (typeof parent.moveBefore === "function") parent.moveBefore(shell, before);
    else parent.insertBefore(shell, before);
  }
  function scrollPositions() {
    const positions = [shell, ...shell.querySelectorAll("*")].filter(node => node.scrollTop || node.scrollLeft);
    return positions.map(node => [node, node.scrollTop, node.scrollLeft]);
  }
  function restorePositions(positions) {
    for (const [node, top, left] of positions) {
      node.scrollTop = top;
      node.scrollLeft = left;
    }
  }
  function setPopup(value) {
    if (!current || current.modal === value || (!value && !canDock())) return;
    const positions = scrollPositions();
    const view = current;
    if (value) {
      view.expanded = expand.getAttribute("aria-pressed") === "true";
      dock.classList.remove("exploring-result");
      dock.closest(".app").classList.remove("result-explorer-expanded");
      view.modal = true;
      view.popup = true;
      moveShell(dialog);
      showDialog("explorer-dialog");
    } else {
      view.modal = false;
      dialog.close();
      if (!globalThis.WorkspaceLayout?.snapshot?.().inspectorVisible) globalThis.WorkspaceLayout?.openInspector();
      dock.classList.add("exploring-result");
      moveShell(dock, dock.querySelector(".inspector-foot"));
      setExpanded(view.expanded);
    }
    updateViewActions();
    restorePositions(positions);
    popup?.focus({preventScroll:true});
  }
  function dismiss() {
    if (current?.modal && current.popup && canDock()) setPopup(false);
    else close({focus:true});
  }
  function setExpanded(value) {
    if (!current || current.modal) return;
    dock.closest(".app").classList.toggle("result-explorer-expanded", value);
    expand.setAttribute("aria-pressed", String(value));
    expand.textContent = value ? "기본 너비" : "넓게 보기";
  }
  function close({focus = false} = {}) {
    const view = current;
    if (!view) return;
    current = null;
    view.rememberTable?.();
    view.inputController.abort();
    view.onClose?.();
    host.replaceChildren();
    shell.hidden = true;
    dock.classList.remove("exploring-result");
    dock.closest(".app").classList.remove("result-explorer-expanded");
    if (dialog.open) dialog.close();
    if (shell.parentElement !== dock) moveShell(dock, dock.querySelector(".inspector-foot"));
    if (view.wasDocked) dock.scrollTop = view.scroll;
    if (focus) {
      const previous = view.opener;
      const target = previous?.isConnected && !previous.disabled && !previous.closest("[hidden],[inert]") ?
        previous : dock.querySelector('[role="tab"][aria-selected="true"]');
      if (target?.isConnected && !target.closest("[hidden],[inert]")) target.focus({preventScroll:true});
    }
  }
  function openCustom({title: name, location, node, onClose, opener = document.activeElement, expanded = false, popup: requestedPopup = false}) {
    close();
    if (typeof rememberInspectorPanel === "function") rememberInspectorPanel();
    const modal = requestedPopup || !canDock();
    const view = {onClose, opener, modal, wasDocked:!modal, popup:requestedPopup, expanded,
      scroll:dock.scrollTop, inputController:new AbortController()};
    current = view;
    if (modal) dialog.append(shell);
    else {
      if (!globalThis.WorkspaceLayout?.snapshot?.().inspectorVisible) globalThis.WorkspaceLayout?.openInspector();
      dock.classList.add("exploring-result");
      dock.scrollTop = 0;
    }
    title.textContent = name; title.title = name;
    const directory = get("explorer-location");
    directory.textContent = directory.title = location || ""; directory.hidden = !location;
    host.replaceChildren(node); shell.hidden = false;
    updateViewActions();
    setExpanded(expanded);
    if (modal) showDialog("explorer-dialog");
    title.focus({preventScroll:true});
    return () => { if (current === view) close(); };
  }
  if (shell && dock && dialog) {
    back.onclick = () => close({focus:true});
    expand.onclick = () => setExpanded(expand.getAttribute("aria-pressed") !== "true");
    if (popup) popup.onclick = () => setPopup(!current?.modal);
    shell.addEventListener("keydown", event => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
      event.preventDefault(); event.stopPropagation(); dismiss();
    });
    dialog.oncancel = event => { event.preventDefault(); dismiss(); };
    dialog.onclose = () => { if (!dialog.open && current?.modal) close({focus:true}); };
    get("close-materials")?.addEventListener("click", () => { if (current && !current.modal) close(); });
    // When a desktop preview crosses the overlay breakpoint, return to its list.
    narrow?.addEventListener?.("change", () => {
      if (current && !current.modal) close();
      else updateViewActions();
    });
  }

  function numericCell(raw) {
    const text = String(raw ?? "").trim();
    if (!text) return null;
    // Preserve units and formatted identifiers. Mixed units are not comparable.
    const match = /^([+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?(?:e[+-]?\d+)?)\s*(%|원|개|건|명|회|초|분|시간|kg|mm|cm|ms)?$/i.exec(text);
    if (!match) return false;
    const value = Number(match[1].replace(/,/g, ""));
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) return false;
    return {value, unit:match[2] || ""};
  }
  function isoTime(raw) {
    const value = String(raw ?? "").trim();
    const match = /^(\d{4}-\d{2}-\d{2})(?:T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,3})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?$/.exec(value);
    if (!match) return null;
    const day = Date.parse(match[1] + "T00:00:00Z");
    // Date.parse normalizes dates such as February 30. Reject those labels.
    if (!Number.isFinite(day) || new Date(day).toISOString().slice(0,10) !== match[1]) return null;
    const timestamp = match[1] === value ? day : Date.parse(value);
    return Number.isFinite(timestamp) ? timestamp : null;
  }
  function chartData(columns, rows) {
    const series = [];
    for (const [index, name] of columns.entries()) {
      const heading = name.trim().replace(/([a-z])([A-Z])/g,"$1 $2");
      if (/(?:^|[\s_.-])(?:id|uuid|code|no\.?|number|zip|serial|sku|lot|year|month|day)$|(?:코드|번호|아이디|사번|우편번호|연도|년도|^순번|^월|^일)$/i.test(heading) ||
          /^(?:설비|라인|공정|차종|품목|제품|호기|line|equipment|station|machine|category|product)$/i.test(heading)) continue;
      const cells = rows.map(row => numericCell(row[index]));
      if (cells.includes(false)) continue;
      const numbers = cells.filter(cell => cell !== null);
      if (numbers.length < 2 || new Set(numbers.map(cell => cell.unit)).size !== 1) continue;
      series.push({index, name, cells, unit:numbers[0].unit});
    }
    const category = columns.findIndex((_, i) => !series.some(item => item.index === i) &&
      rows.some(row => String(row[i] ?? "").trim()));
    const dates = [];
    for (const index of columns.keys()) {
      if (rows.length < 2 || series.some(item => item.index === index)) continue;
      if (new Set(rows.map(row => String(row[index] ?? "").trim().length === 10)).size !== 1) continue;
      const times = rows.map(row => isoTime(row[index]));
      if (times.every((time,i) => time !== null && (i === 0 || time > times[i-1]))) dates.push({index,times});
    }
    return {series, category, timeline:dates.length === 1 ? dates[0] : null};
  }
  function element(tag, text, cls) {
    const child = document.createElement(tag);
    if (text != null) child.textContent = text;
    if (cls) child.className = cls;
    return child;
  }
  function previewScope(data, rowCount, columnCount) {
    // Totals are known only when the parser finished counting the file. Actual
    // visible counts also respect the client's rendering bounds.
    const knownRows = Number.isSafeInteger(data.rowCount) && data.rowCount >= rowCount;
    const knownColumns = Number.isSafeInteger(data.columnCount) && data.columnCount >= columnCount;
    const partialRows = data.rowsTruncated === true || (knownRows && data.rowCount > rowCount);
    const partialColumns = data.columnsTruncated === true || (knownColumns && data.columnCount > columnCount);
    const full = knownRows && knownColumns && !partialRows && !partialColumns;
    function dimension(known, total, shown, partial, unit) {
      if (!known) return `${partial ? "앞" : "표시된"} ${shown}${unit}`;
      return partial ? `전체 ${total}${unit} 중 앞 ${shown}${unit}` : `전체 ${total}${unit}`;
    }
    const rowRange = dimension(knownRows,data.rowCount,rowCount,partialRows,"행");
    const columnRange = dimension(knownColumns,data.columnCount,columnCount,partialColumns,"열");
    return {full, summary:full ? `전체 ${rowCount}행 ${columnCount}열` : rowRange + ", " + columnRange,
      rowLabel:partialRows ? `앞 ${rowCount}행` : knownRows ? `전체 ${rowCount}행` : `표시된 ${rowCount}행`};
  }
  function svgNode(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    return node;
  }
  function drawTimeline(pane, chosen, timeline, columns, rows, rowLabel) {
    const {times,index:dateColumn} = timeline;
    const points = chosen.cells.filter(Boolean);
    const low = Math.min(...points.map(cell => cell.value)), high = Math.max(...points.map(cell => cell.value));
    const height = 136, width = 360, padding = 8;
    const axis = element("p",`가로축 ${columns[dateColumn]}, 세로축 ${chosen.name}${chosen.unit ? " ("+chosen.unit+")" : ""}, 지점당 원본 1행`, "explorer-axis");
    const y = value => low === high ? height/2 : padding + (1 - (value - low)/(high - low)) * (height - 2*padding);
    const x = row => padding + (times[row] - times[0])/(times[times.length-1]-times[0]) * (width - 2*padding);
    const graph = svgNode("svg",{viewBox:`0 0 ${width} ${height}`,class:"explorer-time-chart",role:"group",tabindex:"0",
      "aria-label":`${chosen.name} 시간 그래프. 드래그로 구간을 선택하거나 값 목록을 사용하세요.`});
    for (const value of new Set([low, high])) {
      graph.append(svgNode("line",{x1:padding,x2:width-padding,y1:y(value),y2:y(value),class:"explorer-gridline"}));
    }
    // A missing observation breaks the line, even when the next date is close.
    let path = "", continuing = false;
    const dots = [], details = element("details",null,"explorer-values"), list = element("ul");
    details.append(element("summary",`값과 원본 행 ${points.length}개`),list);
    chosen.cells.forEach((cell,row) => {
      if (!cell) { continuing = false; return; }
      const px = x(row), py = y(cell.value);
      path += (continuing ? " L " : " M ") + px + " " + py;
      continuing = true;
      const description = `${rows[row][dateColumn]}, ${rows[row][chosen.index]}, 원본 ${row+1}행`;
      const dot = svgNode("circle",{cx:px,cy:py,r:3.8,class:"explorer-dot"});
      const tip = svgNode("title",{}); tip.textContent = description; dot.append(tip);
      dot.setAttribute("data-chart-row",String(row)); dots.push(dot);
      const item = element("li"), link = button(description,"text-button");
      link.setAttribute("data-chart-row",String(row)); item.append(link); list.append(item);
    });
    graph.append(svgNode("path",{d:path,class:"explorer-series-line"}),...dots);
    const endpoints = element("div",null,"explorer-chart-scale");
    endpoints.append(element("span",rows[0][dateColumn]),element("span",rows[rows.length-1][dateColumn]));
    const scale = element("p",`${rowLabel}의 최솟값 ${low}${chosen.unit}, 최댓값 ${high}${chosen.unit}` +
      (points.length < rows.length ? `, 빈 값 ${rows.length-points.length}개 제외` : ""), "explorer-data-size");
    const plot = element("div",null,"explorer-plot"), labels = element("div",null,"explorer-y-labels");
    labels.setAttribute("aria-hidden","true");
    labels.append(element("span",String(high)+chosen.unit));
    if (low !== high) labels.append(element("span",String(low)+chosen.unit));
    if (low === high) labels.classList.add("constant");
    plot.append(labels,graph);
    pane.append(axis,plot,endpoints,scale,details);
  }
  function showTable(content, data, {view, sessionId, path, reviewingReference = false, legacyRowReview = false, addRow, current: isCurrent}) {
    const columns = data.columns.slice(0, 30), rows = data.rows.slice(0, 100);
    const regular = !data.chartStructure || data.chartStructure === "regular";
    const {series, category, timeline} = regular ? chartData(columns, rows) : {series:[],category:-1,timeline:null};
    const tabs = element("div", null, "explorer-view-tabs");
    tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "데이터 보기 방식");
    const tableTab = button("원본 표"), chartTab = button("그래프");
    tabs.append(tableTab, chartTab);
    tabs.hidden = !series.length;
    const scope = previewScope(data, rows.length, columns.length);
    const count = element("div",null,"explorer-preview-scope");
    count.textContent = scope.summary;
    const pane = element("div", null, "explorer-data-pane");
    const toolbar = element("div",null,"explorer-range-tools");
    const rangeCount = element("span",null,"explorer-range-count");
    rangeCount.setAttribute("role","status");
    const action = button(reviewingReference ? "이 범위의 최신 값으로 교체" : "이 범위로 질문","quiet-button explorer-range-action");
    action.disabled = true;
    toolbar.append(rangeCount,action);
    content.append(tabs, count, pane, toolbar);
    const validVersion = /^[a-f\d]{64}$/.test(data.version);
    const filePath = typeof path === "string" && /^(?:[a-z]:[\\/]|[\\/]{2})/i.test(path)
      ? path.replaceAll("\\","/") : path;
    const key = !reviewingReference && sessionId && filePath ? JSON.stringify([sessionId,filePath]) : null;
    const last = key && validVersion ? recentTables.get(key) : null;
    const saved = last?.version === data.version && validVersion ? last : null;
    if (key) recentTables.delete(key);
    const ranges = {table:saved?.tableRange || null,chart:saved?.chartRange || null};
    let metric = saved?.metric ?? 0, page = saved?.page ?? 0;
    let sortColumn = saved?.sortColumn ?? -1, direction = saved?.direction ?? 1, display = null;
    let rowOrder = [], marks = [], tabStop = null, drag = null, submitting = false;
    const selectable = !!(validVersion && addRow && rows.length && columns.length);
    toolbar.hidden = true;
    const scrollTop = {table:saved?.tableTop || 0, chart:saved?.chartTop || 0};
    let tableLeft = saved?.tableLeft || 0, tableTop = saved?.innerTableTop || 0;
    function rememberScroll() {
      if (!display) return;
      scrollTop[display] = content.scrollTop;
      const scroll = pane.querySelector(".rich-table-scroll");
      if (scroll) { tableLeft = scroll.scrollLeft; tableTop = scroll.scrollTop; }
    }
    if (current && key && validVersion) {
      current.rememberTable = () => {
        rememberScroll();
        cancelDrag(true);
        recentTables.set(key, {version:data.version, tableRange:ranges.table, chartRange:ranges.chart, metric, page, sortColumn, direction,
          tableTop:scrollTop.table, chartTop:scrollTop.chart, tableLeft, innerTableTop:tableTop});
        if (recentTables.size > maxRecentTables) recentTables.delete(recentTables.keys().next().value);
      };
    }
    function paintRange() {
      const range = ranges[display], selectedRows = new Set(range?.rows), selectedColumns = new Set(range?.columns);
      for (const mark of marks) {
        const on = selectedRows.has(mark.row) && (display === "chart" || selectedColumns.has(mark.col));
        if (mark.on === on) continue;
        mark.on = on;
        if (display === "chart") {
          mark.node.dataset.selected = String(on);
          (mark.node.matches("button") ? mark.node : mark.node.querySelector("button"))?.setAttribute("aria-pressed",String(on));
        } else mark.node.setAttribute("aria-selected",String(on));
      }
      const summary = range ? `선택 ${range.rows.length}행 × ${range.columns.length}열` : "";
      if (rangeCount.textContent !== summary) rangeCount.textContent = summary;
      toolbar.hidden = !selectable || !range;
      action.disabled = !range || submitting;
    }
    function indexMarks() {
      if (!selectable) return;
      const attr = display === "table" ? "td[data-column]" : "[data-chart-row]";
      marks = Array.from(pane.querySelectorAll(attr),node => ({node,
        row:Number(node.dataset.sourceRow ?? node.dataset.chartRow),col:Number(node.dataset.column)}));
      if (display === "table" && marks.length) {
        const end = ranges.table?.end;
        tabStop = marks.find(m => m.row === end?.row && m.col === end?.col)?.node || marks[0].node;
        tabStop.tabIndex = 0;
      }
      paintRange();
    }
    function setRange(point, extend = false) {
      if (!point || !isCurrent() || !selectable) return;
      const previous = ranges[display];
      const anchor = extend && previous && !legacyRowReview ? previous.anchor : point;
      const orderKey = display === "table" ? `${sortColumn}:${direction}` : String(metric);
      if (previous?.anchor.row === anchor.row && previous.anchor.col === anchor.col &&
          previous.end.row === point.row && previous.end.col === point.col && previous.orderKey === orderKey) return;
      const a = rowOrder.indexOf(anchor.row), b = rowOrder.indexOf(point.row);
      if (a < 0 || b < 0) return;
      let selectedColumns;
      if (legacyRowReview) selectedColumns = columns.map((_,i) => i);
      else if (display === "chart") {
        const label = timeline?.index ?? category;
        selectedColumns = [label,series[metric].index].filter(i => i >= 0).sort((a,b) => a-b);
      } else {
        const left = Math.min(anchor.col,point.col), width = Math.abs(anchor.col-point.col)+1;
        selectedColumns = Array.from({length:width},(_,i) => left+i);
      }
      ranges[display] = {anchor,end:point,orderKey,rows:rowOrder.slice(Math.min(a,b),Math.max(a,b)+1),columns:selectedColumns};
      paintRange();
    }
    function selectedPoint(node) {
      if (!node || !pane.contains(node)) return null;
      const cell = node.closest(display === "table" ? "td[data-column]" : "[data-chart-row]");
      if (!cell) return null;
      return {row:Number(cell.dataset.sourceRow ?? cell.dataset.chartRow),
        col:display === "chart" ? series[metric].index : Number(cell.dataset.column)};
    }
    function pointAt(x,y) {
      const graph = pane.querySelector(".explorer-time-chart");
      if (display === "chart" && graph && drag?.timeline) {
        const matrix = graph.getScreenCTM();
        if (!matrix?.a) return null;
        const coordinate = (x-matrix.e)/matrix.a;
        const closest = marks.filter(m => m.node.tagName.toLowerCase() === "circle").reduce((best,m) =>
          Math.abs(m.node.cx.baseVal.value-coordinate) < Math.abs(best.node.cx.baseVal.value-coordinate) ? m : best);
        return {row:closest.row,col:series[metric].index};
      }
      const rect = drag?.region.getBoundingClientRect();
      if (rect) {
        x = Math.max(rect.left+2,Math.min(rect.right-2,x)); y = Math.max(rect.top+2,Math.min(rect.bottom-2,y));
        if (display === "table") y = Math.max(y, Math.min(rect.bottom-2, pane.querySelector("thead").getBoundingClientRect().bottom+1));
      }
      if (drag?.bars) {
        // Include the space between bar rows and the first/last row at the
        // ends of the plot. Only rows on the displayed page are hit targets.
        let nearest = null, distance = Infinity;
        for (const mark of marks) {
          const bounds = mark.node.getBoundingClientRect();
          const gap = Math.max(0,bounds.top-y,y-bounds.bottom);
          if (gap < distance) { nearest=mark; distance=gap; }
          if (gap === 0) break;
        }
        return nearest ? {row:nearest.row,col:series[metric].index} : null;
      }
      return selectedPoint(document.elementFromPoint(x,y));
    }
    function focusCell(node) {
      if (tabStop !== node) {
        if (tabStop) tabStop.tabIndex = -1;
        tabStop = node; node.tabIndex = 0;
      }
      node.focus({preventScroll:true});
    }
    function cancelDrag(restore = false) {
      if (!drag) return;
      const old = drag; drag = null;
      if (old.frame) cancelAnimationFrame(old.frame);
      if (pane.hasPointerCapture(old.id)) pane.releasePointerCapture(old.id);
      if (restore) { ranges[display] = old.previous; paintRange(); }
    }
    function adjustDrag() {
      if (!drag || !isCurrent() || !pane.isConnected) { cancelDrag(true); return; }
      drag.frame = 0;
      const {region,x,y} = drag, rect = region.getBoundingClientRect();
      const boundedSpeed = value => Math.max(-18,Math.min(18,value));
      const speed = (v,low,high) => v<low+14 ? boundedSpeed(v-low-14) : v>high-14 ? boundedSpeed(v-high+14) : 0;
      const dx = drag.timeline ? 0 : speed(x,rect.left,rect.right);
      const dy = drag.timeline ? 0 : speed(y,rect.top,rect.bottom);
      const lastX=region.scrollLeft, lastY=region.scrollTop;
      if (dx || dy) region.scrollBy(dx,dy);
      setRange(pointAt(x,y), true);
      if (drag && (region.scrollLeft!==lastX || region.scrollTop!==lastY)) drag.frame=requestAnimationFrame(adjustDrag);
    }
    pane.onpointerdown = event => {
      if (!selectable || !isCurrent() || !event.isPrimary || event.button !== 0) return;
      const timelinePlot = event.target.closest(".explorer-time-chart");
      const point = selectedPoint(event.target);
      if (!point && !timelinePlot) return;
      const region = display === "table" ? pane.querySelector(".rich-table-scroll") : content;
      drag = {region,timeline:!!timelinePlot,bars:!!event.target.closest(".explorer-bars"),id:event.pointerId,previous:ranges[display],
        startX:event.clientX,startY:event.clientY,x:event.clientX,y:event.clientY,frame:0};
      setRange(point || pointAt(event.clientX,event.clientY),event.shiftKey);
      if (display === "table") focusCell(event.target.closest("td"));
      else {
        const target = timelinePlot || event.target.closest("button") || event.target.closest("[data-chart-row]")?.querySelector("button");
        target?.focus({preventScroll:true});
      }
      pane.setPointerCapture(event.pointerId);
      if (event.pointerType !== "touch") event.preventDefault();
    };
    pane.onpointermove = event => {
      if (drag?.id !== event.pointerId) return;
      if (drag.x===event.clientX && drag.y===event.clientY) return;
      drag.x = event.clientX; drag.y = event.clientY;
      if (!drag.frame) drag.frame=requestAnimationFrame(adjustDrag);
    };
    pane.onpointerup = event => {
      if (drag?.id !== event.pointerId) return;
      if (Math.abs(event.clientX-drag.startX)+Math.abs(event.clientY-drag.startY)>1) setRange(pointAt(event.clientX,event.clientY),true);
      cancelDrag();
    };
    pane.onpointercancel = pane.onlostpointercapture = event => { if (drag?.id === event.pointerId) cancelDrag(true); };
    pane.onclick = event => {
      if (event.detail === 0 || !event.isTrusted) setRange(selectedPoint(event.target),event.shiftKey);
    };
    pane.onkeydown = event => {
      if (event.isComposing || !isCurrent()) return;
      const plot = event.target.closest(".explorer-time-chart");
      const p = selectedPoint(event.target) || (plot && (ranges.chart?.end || {row:rowOrder[0],col:series[metric].index}));
      if (!p) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();setRange(p,event.shiftKey);return;
      }
      if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End"].includes(event.key)) return;
      event.preventDefault();
      let pos=rowOrder.indexOf(p.row),col=p.col;
      if (display === "chart") {
        if (event.key === "Home") pos=0;
        else if (event.key === "End") pos=rowOrder.length-1;
        else pos+=["ArrowLeft","ArrowUp"].includes(event.key)?-1:1;
      } else {
        if(event.key==="ArrowUp") pos--; if(event.key==="ArrowDown") pos++;
        if(event.key==="ArrowLeft") col--; if(event.key==="ArrowRight") col++;
        if(event.key==="Home") { col=0; if(event.ctrlKey) pos=0; }
        if(event.key==="End") { col=columns.length-1; if(event.ctrlKey) pos=rowOrder.length-1; }
      }
      pos=Math.max(0,Math.min(rowOrder.length-1,pos));col=Math.max(0,Math.min(columns.length-1,col));
      if(event.shiftKey && !ranges[display]) setRange(p);
      const next={row:rowOrder[pos],col};setRange(next,event.shiftKey);
      if (display === "chart" && !timeline && Math.floor(pos/12)!==page) { page=Math.floor(pos/12);drawChart(); }
      const node = plot || (display === "table" ? pane.querySelector(`td[data-source-row="${next.row}"][data-column="${col}"]`) :
        pane.querySelector(`button[data-chart-row="${next.row}"], [data-chart-row="${next.row}"] button`));
      if (node) { if(display==="table") focusCell(node);else node.focus({preventScroll:true});node.scrollIntoView({block:"nearest",inline:"nearest"}); }
    };
    if (selectable) content.addEventListener("keydown", function clear(event) {
      if (!action.isConnected) { content.removeEventListener("keydown",clear); return; }
      if (event.key !== "Escape" || event.isComposing || (!drag && !ranges[display])) return;
      event.preventDefault();event.stopPropagation();
      if (drag) cancelDrag(true); else { ranges[display]=null;paintRange(); }
    },{signal:current?.inputController.signal});
    action.onclick=async () => {
      const range=ranges[display];
      if (!isCurrent() || !range || submitting) return;
      const mode=display, order=rowOrder;
      const stillSelected=() => isCurrent() && display===mode && rowOrder===order && ranges[mode]===range && action.isConnected;
      const selectedRows=rowOrder.filter(i=>range.rows.includes(i)).map(i=>i+1);
      submitting=true;paintRange();action.setAttribute("aria-busy","true");
      try { await addRow({version:data.version,rows:selectedRows,columns:range.columns.map(i=>i+1),
        rowNumber:selectedRows[0],isCurrent:stillSelected}); }
      finally { submitting=false;action.removeAttribute("aria-busy");if(action.isConnected)paintRange(); }
    };
    function drawChart() {
      pane.replaceChildren();
      const chosen = series[metric], picker = element("label", "그래프 지표", "explorer-metric");
      const select = element("select");
      for (const [i, item] of series.entries()) {
        const option = element("option", `${item.name} (${item.index+1}열)`); option.value = String(i); select.append(option);
      }
      select.setAttribute("aria-label","그래프 지표 선택");
      select.title = `${chosen.name} (${chosen.index+1}열)`;
      select.value = String(metric);
      select.onchange = () => { cancelDrag(); metric = Number(select.value); page = 0; ranges.chart = null; drawChart(); pane.querySelector("select")?.focus(); };
      picker.append(select); pane.append(picker);
      const points = chosen.cells.flatMap((cell, index) => cell ? [{...cell, index}] : []);
      rowOrder = points.map(point => point.index);
      if (timeline) { drawTimeline(pane,chosen,timeline,columns,rows,scope.rowLabel); indexMarks(); return; }
      const low = Math.min(0, ...points.map(point => point.value));
      const high = Math.max(0, ...points.map(point => point.value));
      const magnitude = Math.max(Math.abs(low), high) || 1;
      // Normalize before division so even subnormal finite numbers have a finite scale.
      const minimum = low / magnitude, range = (high / magnitude - minimum) || 1;
      const position = value => ((value / magnitude - minimum) / range) * 100;
      const axis = element("p", `가로축 ${chosen.name}${chosen.unit ? " ("+chosen.unit+")" : ""}, 세로축 ${category < 0 ? "데이터 행" : columns[category]}, 막대당 원본 1행`, "explorer-axis");
      pane.append(axis);
      const chart = element("ol", null, "explorer-bars");
      chart.setAttribute("aria-label", chosen.name + " 가로 막대그래프");
      const pageSize = 12, start = page * pageSize;
      for (const point of points.slice(start, start + pageSize)) {
        const raw = String(rows[point.index][chosen.index]);
        const categoryText = category < 0 ? "" : String(rows[point.index][category] ?? "").trim();
        const label = categoryText || `${point.index + 1}행`;
        const row = element("li", null, "explorer-bar-row"), header = button("","explorer-bar-heading");
        row.dataset.chartRow=String(point.index);
        header.setAttribute("aria-label",`${label} ${raw}, 원본 ${point.index+1}행 선택`);
        header.title = `원본 ${point.index+1}행 선택`;
        const name = element("span", label), value = element("strong", raw);
        name.title = label; value.title = raw; header.append(name, value);
        const track = element("div", null, "explorer-bar-track"), bar = element("span", null, "explorer-bar-fill");
        track.setAttribute("aria-hidden", "true");
        const zero = position(0), end = position(point.value);
        track.style.setProperty("--chart-zero", zero + "%");
        bar.style.left = Math.min(zero, end) + "%"; bar.style.width = Math.abs(end - zero) + "%";
        if (point.value < 0) bar.classList.add("is-negative");
        track.append(bar); row.append(header, track); chart.append(row);
      }
      pane.append(chart);
      const scale = element("div", null, "explorer-chart-scale");
      scale.append(element("span",String(low)+chosen.unit), element("span",String(high)+chosen.unit)); pane.append(scale);
      const note = element("p", `${scope.rowLabel}의 수치 ${points.length}개 중 ${start+1}–${Math.min(start+pageSize,points.length)}번째` + (points.length < rows.length ? `, 빈 값 ${rows.length-points.length}개 제외` : ""), "explorer-data-size");
      pane.append(note);
      if (points.length > pageSize) {
        const nav = element("div", null, "explorer-chart-pages"), prev = button("이전"), next = button("다음");
        prev.disabled = page === 0; next.disabled = start + pageSize >= points.length;
        function move(delta) {
          page += delta; drawChart();
          const choices = pane.querySelectorAll(".explorer-chart-pages button");
          const preferred = choices[delta < 0 ? 0 : 1];
          (preferred.disabled ? choices[delta < 0 ? 1 : 0] : preferred).focus();
        }
        prev.onclick = () => move(-1); next.onclick = () => move(1);
        nav.append(prev,next); pane.append(nav);
      }
      indexMarks();
    }
    function drawTable() {
      if (pane.querySelector(".rich-table-scroll")) rememberScroll();
      pane.replaceChildren();
      const order = rows.map((_,i) => i);
      if (sortColumn !== -1) {
        const numbers = series.find(item => item.index === sortColumn)?.cells;
        const collator = new Intl.Collator("ko",{numeric:true});
        order.sort((a,b) => {
          if (numbers && (!numbers[a] || !numbers[b])) return Number(!numbers[a]) - Number(!numbers[b]) || a-b;
          const diff = numbers ? numbers[a].value - numbers[b].value : collator.compare(rows[a][sortColumn],rows[b][sortColumn]);
          return direction * diff || a-b;
        });
      }
      const card = WorkspaceRichContent.table(pane, {columns, rows:order.map(index => rows[index]),
        caption:scope.summary});
      rowOrder=order;
      for (const [i,th] of Array.from(card.querySelectorAll("thead th")).entries()) {
        if (series.some(item => item.index === i)) th.className = "explorer-number";
        const sorted = i === sortColumn;
        th.setAttribute("aria-sort",sorted ? (direction === 1 ? "ascending" : "descending") : "none");
        const sort = button(columns[i] + (sorted ? (direction === 1 ? " ↑" : " ↓") : ""), "explorer-sort");
        sort.title = sorted && direction === -1 ? "원본 순서로 복원" : (sorted ? "내림차순으로 정렬" : "오름차순으로 정렬");
        sort.setAttribute("aria-label",columns[i] + " " + sort.title);
        sort.onclick = () => {
          if (sorted && direction === -1) sortColumn = -1;
          else { sortColumn = i; direction = sorted ? -1 : 1; }
          drawTable(); pane.querySelectorAll(".explorer-sort")[i]?.focus({preventScroll:true});
        };
        th.replaceChildren(sort);
      }
      const scroll = card.querySelector(".rich-table-scroll");
      const bodyRows = Array.from(card.querySelectorAll("tbody tr"));
      for (const [pos,tr] of bodyRows.entries()) {
        tr.dataset.sourceRow = String(order[pos]);
        for (const item of series) tr.cells[item.index].className = "explorer-number";
      }
      if (selectable) {
        const grid=card.querySelector("table");
        grid.setAttribute("role","grid");grid.setAttribute("aria-multiselectable","true");
        grid.setAttribute("aria-label",legacyRowReview ? "교체할 원본 행 선택" : "데이터 범위 선택. 방향키로 이동하고 Shift와 함께 눌러 확장하세요.");
        bodyRows.forEach((tr,pos) => Array.from(tr.cells).forEach((td,col) => {
          td.dataset.sourceRow=String(order[pos]); td.dataset.column=String(col);
          td.tabIndex=-1; td.setAttribute("role","gridcell");
          td.title=`원본 ${order[pos]+1}행, ${columns[col]}`;
        }));
      }
      indexMarks();
      scroll.scrollLeft = tableLeft; scroll.scrollTop = tableTop;
    }
    function switchView(chart) {
      const next = chart ? "chart" : "table";
      if (display === next) return;
      rememberScroll();
      cancelDrag();display = next;
      chartTab.setAttribute("aria-pressed",String(chart)); tableTab.setAttribute("aria-pressed",String(!chart));
      count.hidden = !chart;
      if (chart) drawChart(); else drawTable();
      content.scrollTop = scrollTop[next];
    }
    chartTab.onclick = () => switchView(true); tableTab.onclick = () => switchView(false);
    switchView(view === "chart" && series.length > 0);
  }
  return {openCustom, close, canDock, isOpen:() => !!current, isPopup:() => !!current?.modal, showTable};
})();
