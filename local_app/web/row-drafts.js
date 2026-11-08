"use strict";

// Insert the server's exact quoted values; a delayed click must never target another task's draft.
globalThis.WorkspaceRowDrafts = (() => {
  const pending = new Map();
  const notices = new Map();
  let chipOwner = null, chips = [];
  function parseRange(raw) {
    const newline = raw.indexOf("\n");
    if (newline < 0 || !raw.startsWith("〔표 인용 ")) return null;
    const header = /^〔표 인용 (\d+)〕 (.+) · 데이터 [\d,]+행 · [\d,]+열$/.exec(raw.slice(0,newline));
    if (!header) return null;
    try {
      const data = JSON.parse(raw.slice(newline + 1)), columns = data.열, rows = data.행;
      if (!Array.isArray(columns) || !columns.length || columns.length>30 || !Array.isArray(rows) || !rows.length || rows.length>100 ||
          !columns.every(c=>Array.isArray(c) && Number.isInteger(c[0]) && typeof c[1]==="string") ||
          !rows.every(r=>Array.isArray(r) && Number.isInteger(r[0]) && Array.isArray(r[1]) && r[1].length===columns.length && r[1].every(value=>typeof value==="string"))) return null;
      const dates = columns.findIndex((_,i)=>rows.every(r=>/^\d{4}-\d{2}-\d{2}$/.test(r[1][i])));
      const sequential = rows.every((r,i)=>!i || r[0]===rows[i-1][0]+1);
      let scope = sequential ? (rows.length===1 ? rows[0][0] : rows[0][0]+"–"+rows.at(-1)[0])+"행" : rows.length+"개 행";
      const dateScope = dates>=0 && sequential && rows.every((r,i)=>!i || r[1][dates]>rows[i-1][1][dates]);
      if (dateScope)
        scope = rows[0][1][dates] + (rows.length>1 ? " ~ "+rows.at(-1)[1][dates] : "");
      const names = columns.filter((_,i)=>!dateScope || i!==dates || columns.length===1).map(c=>c[1]);
      return {number:Number(header[1]),raw,columns,rows,label:[header[2].split(/[\\/]/).pop(),scope,
        names.slice(0,2).join(", ")+(names.length>2?" 외 "+(names.length-2)+"열":"")].join(" · ")};
    } catch { return null; }
  }
  function separate(text) {
    const ranges = [];
    while (text.includes("〔표 인용 ") && ranges.length<8) {
      const boundary = text.lastIndexOf("\n\n");
      const range = parseRange(text.slice(boundary<0 ? 0 : boundary+2));
      if (!range) break;
      ranges.unshift(range);
      text = boundary<0 ? "" : text.slice(0,boundary);
    }
    return {text,ranges};
  }
  function chipElement(chip, remove) {
    const wrap=el("span",null,"table-citation-chip"), details=el("details"), summary=el("summary",chip.label);
    details.append(summary);
    details.ontoggle=()=>{
      if (!details.open || details.children.length>1) return;
      const body=el("div",null,"citation-values");
      for (const [row,values] of chip.rows)
        body.append(el("div",row+"행  "+values.map((v,i)=>chip.columns[i][1]+"  "+v).join("  |  ")));
      details.append(body);
    };
    wrap.append(details);
    if(remove){
      const button=el("button","×");
      button.type="button"; button.setAttribute("aria-label","범위 제거 "+chip.label);
      button.onclick=()=>{if(!sending && !appClosed && !pendingSelection) remove();};
      wrap.append(button);
    }
    return wrap;
  }
  function drawChips() {
    const host=$("table-citation-chips"); if(!host) return;
    host.replaceChildren();
    for(const chip of chips) host.append(chipElement(chip,()=>{
      chips=chips.filter(other=>other!==chip); drawChips(); saveDraft(); $("prompt").focus();
    }));
    host.hidden=!chips.length;
  }
  function compose(text) {
    if (!chips.length || chipOwner!==active?.id) return text;
    return [...(text ? [text] : []),...chips.map(chip=>chip.raw)].join("\n\n");
  }
  function restore(text) {
    const parsed=separate(text);
    chipOwner=active?.id; chips=parsed.ranges; drawChips(); return parsed.text;
  }
  function inputText(input) { return input === $("prompt") ? compose(input.value) : input.value; }
  function renderSent(body,text) {
    const parsed=separate(text);
    if (!parsed.ranges.length) return false;
    if (parsed.text) renderBoundedMessageText(body,parsed.text);
    const list=el("div",null,"sent-table-citations");
    parsed.ranges.forEach(chip=>list.append(chipElement(chip)));
    body.append(list);return true;
  }
  function quoteBlock(text, number) {
    const marker = "〔표 인용 " + number + "〕 ";
    const start = text.startsWith(marker) ? 0 : text.indexOf("\n" + marker) + 1;
    if (!text.startsWith(marker, start)) return null;
    const headerEnd = text.indexOf("\n", start);
    if (headerEnd < 0) return null;
    const following = text.slice(headerEnd + 1).split("\n", 1)[0];
    if (!following || following.startsWith("〔표 인용 ")) return null;
    const end = headerEnd + 1 + following.length;
    return {start, end, text:text.slice(start, end)};
  }

  function clearNotice(input) {
    const notice = notices.get(input);
    notice?.node.remove();
    notices.delete(input);
  }
  function syncNotice(input) {
    const notice = notices.get(input);
    if (notice && (!notice.current() || !quoteBlock(inputText(input), notice.number))) clearNotice(input);
  }
  function showStale(err, sessionId, input = $("prompt"), ownsInput = () => true) {
    if (err?.code !== "stale_table_reference" || !err.canReview || !Number.isSafeInteger(err.referenceId)) return false;
    const generation = selectionGeneration, number = err.referenceId;
    const current = () => currentFileLocation(sessionId, generation) && ownsInput();
    if (!current() || !quoteBlock(inputText(input), number)) return false;
    clearNotice(input);
    const node = el("div", null, "row-repair-notice");
    const message = el("span", "표 인용 " + number + "의 원본이 바뀌었어요.");
    message.setAttribute("role", "status");
    const button = el("button", "최신 값으로 교체", "text-button");
    button.type = "button";
    const notice = {node, number, current};
    notices.set(input, notice);
    node.append(message, button); input.before(node);
    button.onclick = async () => {
      if (!current() || button.disabled) return;
      const old = quoteBlock(inputText(input), number);
      if (!old) return syncNotice(input);
      const valid = () => current() && notices.get(input) === notice && quoteBlock(inputText(input), number)?.text === old.text;
      button.disabled = true;
      try {
        const source = await api("/api/table-reference", {id:sessionId, action:"review", referenceId:number});
        if (valid()) await preview(source.path, {view:"table", opener:button, referenceReview:{sessionId, number, old:old.text, input, valid, legacy:!source.previousRows}});
      } catch (failure) {
        if (current() && notices.get(input) === notice) message.textContent = failure.message;
      } finally { button.disabled = false; }
    };
    return true;
  }
  // Replace only this receipt, retaining the other ranges and the user's question.
  // Recheck after the network reply so other edits and sessions keep their own text.
  async function replace(review, {version, rows, columns, rowNumber, isCurrent}, status) {
    const {input, number, sessionId} = review;
    if (!isCurrent() || !review.valid() || input.disabled || input.readOnly) {
      if (isCurrent()) status.textContent = "초안의 인용이 달라졌어요. 최신 표를 다시 열어 주세요.";
      return false;
    }
    try {
      const selection=review.legacy ? {rowNumber} : {rows,columns};
      const result = await api("/api/table-reference", {id:sessionId, action:"replace", referenceId:number, version, ...selection});
      if (!isCurrent() || !review.valid() || input.disabled || input.readOnly || result.replaces !== number || !result.text) return false;
      const before = inputText(input), block = quoteBlock(before, number);
      if (!block || block.text !== review.old) return false;
      if (before.length - block.text.length + result.text.length > 32000) {
        status.textContent = "요청이 길어서 교체할 수 없어요. 내용을 줄여 주세요."; return false;
      }
      if (input === $("prompt") && chips.some(c=>c.number===number)) {
        const replacement = parseRange(result.text);
        if (!replacement) return false;
        chips = chips.filter(chip=>chip.number!==replacement.number || chip.number===number)
          .map(chip=>chip.number===number ? replacement : chip);
        drawChips();
      } else input.setRangeText(result.text, block.start, block.end, "end");
      clearNotice(input);
      input.dispatchEvent(new Event("input", {bubbles:true}));
      if (input === $("prompt")) saveDraft();
      return true;
    } catch (err) {
      if (isCurrent()) status.textContent = err.message;
      return false;
    }
  }
  async function add({sessionId, path, version, rows, columns, isCurrent = () => true}) {
    if (appClosed || active?.id !== sessionId || isGeneralChat() || sending || !isCurrent()) return false;
    const ticket = selectionGeneration;
    const key = JSON.stringify([sessionId, path, version, rows, columns]);
    if (pending.get(key) === ticket) return false;
    pending.set(key, ticket);
    try {
      const result = await api("/api/table-reference", {id:sessionId, path, version, rows, columns});
      if (!currentFileLocation(sessionId,ticket) || sending || !isCurrent()) return false;
      const input = $("prompt");
      if (input.disabled || input.readOnly || !result.text) return false;
      const chip=parseRange(result.text);
      if(!chip)throw Error("선택한 범위를 확인하지 못했어요.");
      if(chipOwner!==sessionId){chips=[];chipOwner=sessionId;}
      if(chips.some(item=>item.number===chip.number))return true;
      const previous=compose(input.value);
      const receiptIds=new Set(Array.from(previous.matchAll(/^〔표 인용 (\d+)〕 /gm),m=>m[1]));
      receiptIds.add(String(chip.number));
      if(receiptIds.size>8 || previous.length+chip.raw.length+(previous?2:0)>32000) {
        toast("한 요청에 담을 수 있는 범위를 넘었어요. 선택을 줄이거나 기존 인용을 제거해 주세요.");return false;
      }
      chips.push(chip);drawChips();saveDraft();
      return true;
    } catch (err) {
      if (currentFileLocation(sessionId,ticket)) toast(err.message);
      return false;
    } finally {
      if (pending.get(key) === ticket) pending.delete(key);
    }
  }
  function hasSelection() { return chips.length>0 && chipOwner===active?.id; }
  return {add, replace, showStale, syncNotice, clearNotice, compose, restore, renderSent, hasSelection};
})();
