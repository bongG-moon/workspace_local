"use strict";

// Input-only Claude defaults. Completion, approval controls, Enter, and interrupt
// handling have priority; terminal/process shortcuts are deliberately not remapped.
globalThis.WorkspaceInputKeys = (() => {
  const HISTORY_LIMIT = 100, KILL_LIMIT = 20, KILL_CHARS = 64000;
  const graphemes = typeof Intl?.Segmenter === "function" ? new Intl.Segmenter(undefined, {granularity:"grapheme"}) : null;
  const words = typeof Intl?.Segmenter === "function" ? new Intl.Segmenter(undefined, {granularity:"word"}) : null;
  let hooks = null, input = null, contextId, composing = false, changing = false;
  let browsing = null, killed = [], yank = null, measure = null;
  function copyDraft(value) {
    return {text:String(value?.text || ""), attachments:Array.isArray(value?.attachments) ? [...value.attachments] : []};
  }
  function reset() { browsing = null; yank = null; }
  function context() {
    const value = hooks?.context?.() || {};
    if (value.id !== contextId) { contextId = value.id; reset(); killed = []; }
    return value;
  }
  function edited() { if (!changing) reset(); }
  function startComposition() { composing = true; reset(); }
  function endComposition() { composing = false; }
  function attach(options) {
    if (input) {
      input.removeEventListener?.("input", edited);
      input.removeEventListener?.("compositionstart", startComposition);
      input.removeEventListener?.("compositionend", endComposition);
    }
    hooks = options; input = options?.input || null; contextId = undefined;
    composing = false; reset(); killed = [];
    input?.addEventListener?.("input", edited);
    input?.addEventListener?.("compositionstart", startComposition);
    input?.addEventListener?.("compositionend", endComposition);
  }
  function caret(start, end = start) { input.setSelectionRange(start, end); }
  function boundaries(text) {
    const points = [0];
    if (graphemes) for (const part of graphemes.segment(text)) points.push(part.index + part.segment.length);
    else { let offset = 0; for (const char of text) points.push(offset += char.length); }
    return points;
  }
  function floorBoundary(text, index) { let result = 0; for (const point of boundaries(text)) { if (point > index) break; result = point; } return result; }
  function ceilBoundary(text, index) { return boundaries(text).find(point => point >= index) ?? text.length; }
  function lineStart(text, index) { return index <= 0 ? 0 : text.lastIndexOf("\n", index - 1) + 1; }
  function lineEnd(text, index) { const end = text.indexOf("\n", index); return end < 0 ? text.length : end; }
  function unwrapped(text) {
    // Textareas expose no visual-row caret API. Only use logical rows for the
    // ordinary arrows when text measurement confirms that no row is soft-wrapped.
    // Unknown layout, tabs, or a font still loading retain native arrow behavior.
    if (text.includes("\t") || typeof globalThis.getComputedStyle !== "function" || document.fonts?.status === "loading") return false;
    try {
      const style = globalThis.getComputedStyle(input);
      if (style.writingMode && style.writingMode !== "horizontal-tb") return false;
      if (style.textTransform && style.textTransform !== "none") return false;
      const number = value => value === "normal" || value === "" ? 0 : Number.parseFloat(value);
      const left = number(style.paddingLeft), right = number(style.paddingRight);
      const spacing = number(style.letterSpacing), wordSpacing = number(style.wordSpacing);
      const size = number(style.fontSize), width = input.clientWidth - left - right - 2;
      if (![left, right, spacing, wordSpacing, size, width].every(Number.isFinite) || size <= 0 || width <= 0) return false;
      if (!measure) measure = (typeof OffscreenCanvas === "function" ? new OffscreenCanvas(1, 1) : document.createElement("canvas")).getContext("2d");
      if (!measure) return false;
      measure.font = style.font || `${style.fontStyle || "normal"} ${style.fontWeight || "normal"} ${style.fontSize} ${style.fontFamily}`;
      return text.split("\n").every(line => {
        const measured = measure.measureText(line).width + Math.max(0, spacing) * [...line].length + Math.max(0, wordSpacing) * (line.match(/ /g)?.length || 0);
        return Number.isFinite(measured) && measured <= width;
      });
    } catch (_) { return false; }
  }
  function draftFingerprint() {
    // Attachment changes can bypass the text input event (picker/drop/remove).
    // Compare values, not array identities, before replacing a recalled draft.
    try { return JSON.stringify(copyDraft(hooks?.capture?.() || {text:input.value})); }
    catch (_) { return null; }
  }
  function wordRanges(text) {
    const ranges = [];
    const parts = words ? words.segment(text) : [{index:0, segment:text}];
    // Even a locale segment containing underscores or dots must retain CLI word
    // boundaries, while Intl handles CJK words and graphemes where available.
    for (const part of parts) for (const match of part.segment.matchAll(/[\p{L}\p{N}\p{M}]+/gu)) {
      const start = part.index + match.index; ranges.push([start, start + match[0].length]);
    }
    return ranges;
  }
  function previousWord(text, index) {
    const ranges = wordRanges(text);
    for (let i = ranges.length - 1; i >= 0; i--) if (ranges[i][0] < index) return ranges[i][0];
    return 0;
  }
  function nextWord(text, index) {
    for (const range of wordRanges(text)) if (range[1] > index) return range[1];
    return text.length;
  }
  function remember(text) {
    if (!text) return;
    // Never retain part of a killed fragment: pasting a truncated path is unsafe.
    if (text.length > KILL_CHARS) { killed = []; return; }
    killed.unshift(text);
    let size = 0;
    killed = killed.filter((value, index) => index < KILL_LIMIT && (size += value.length) <= KILL_CHARS);
  }
  function replace(start, end, value, {kill = false, retainYank = false} = {}) {
    const before = input.value;
    start = floorBoundary(before, start); end = ceilBoundary(before, end);
    if (kill) remember(before.slice(start, end));
    changing = true;
    try {
      caret(start, end);
      let handled = false;
      try {
        if (typeof document.execCommand === "function") handled = document.execCommand("insertText", false, value);
      } catch (_) { /* Restricted browser hosts can reject the native undo path. */ }
      const expected = before.slice(0, start) + value + before.slice(end);
      if (!handled || input.value !== expected) {
        // A host returning false after actually applying the edit must not apply it twice.
        if (input.value === before) input.setRangeText(value, start, end, "end");
      }
      if (input.value === expected) caret(start + value.length);
      browsing = null;
      if (!retainYank) yank = null;
      hooks?.edited?.();
      return input.value === expected;
    } finally { changing = false; }
  }
  function history(direction) {
    const current = context();
    if (browsing && (browsing.observed === null || draftFingerprint() !== browsing.observed)) reset();
    if (!browsing) {
      if (direction > 0) return false;
      const rows = (Array.isArray(current.history) ? current.history : []).filter(row => typeof row?.text === "string" && (row.text || row.files?.length)).slice(-HISTORY_LIMIT);
      if (!rows.length) return false;
      browsing = {rows, index:rows.length, draft:copyDraft(hooks?.capture?.() || {text:input.value}),
        start:input.selectionStart, end:input.selectionEnd};
    }
    const state = browsing;
    const index = Math.max(0, Math.min(state.rows.length, state.index + direction));
    if (index === state.index) return true;
    const atDraft = index === state.rows.length;
    const row = state.rows[index];
    const draft = atDraft ? copyDraft(state.draft) : {text:row.text, attachments:Array.isArray(row.files) ? [...row.files] : []};
    changing = true;
    try {
      hooks.restore(draft);
      // App draft restoration also resets editor state for ordinary task changes.
      // Retain this navigation transaction after that synchronous shared hook.
      browsing = state;
      state.index = index;
      state.observed = draftFingerprint();
      if (atDraft) caret(state.start, state.end);
      else caret(direction < 0 ? 0 : draft.text.length);
      yank = null;
    } finally { changing = false; }
    if (atDraft) browsing = null;
    return true;
  }
  function vertical(direction) {
    const text = input.value, position = input.selectionStart;
    const start = lineStart(text, position), end = lineEnd(text, position);
    if ((direction < 0 && start === 0) || (direction > 0 && end === text.length)) return history(direction);
    const column = boundaries(text.slice(start, position)).length - 1;
    const targetStart = direction < 0 ? lineStart(text, start - 1) : end + 1;
    const targetEnd = lineEnd(text, targetStart);
    const points = boundaries(text.slice(targetStart, targetEnd));
    caret(targetStart + points[Math.min(column, points.length - 1)]); yank = null;
    return true;
  }
  function keydown(event) {
    if (!input || event.defaultPrevented || event.isComposing || event.keyCode === 229 || composing || input.readOnly || input.disabled
        || (event.target && event.target !== input) || event.metaKey || (event.ctrlKey && event.altKey)) return false;
    context();
    const key = event.key.toLowerCase(), text = input.value, start = input.selectionStart, end = input.selectionEnd;
    if (!Number.isInteger(start) || !Number.isInteger(end)) return false;
    const control = event.ctrlKey && !event.altKey, alt = event.altKey && !event.ctrlKey;
    const undo = control && (key === "_" || (event.shiftKey && key === "-"));
    if (event.shiftKey && !undo) return false;
    const take = action => { event.preventDefault(); action(); return true; };
    if (undo) return take(() => {
      reset(); changing = true;
      try { document.execCommand?.("undo"); hooks?.edited?.(); } finally { changing = false; }
    });
    if (!control && !alt && start === end && ["arrowup", "arrowdown"].includes(key)) {
      const up = key === "arrowup";
      const absoluteEdge = up ? start === 0 : end === text.length;
      const logicalEdge = up ? lineStart(text, start) === 0 : lineEnd(text, end) === text.length;
      if (absoluteEdge || (logicalEdge && unwrapped(text))) {
        if (!history(up ? -1 : 1)) return false;
        event.preventDefault(); return true;
      }
    }
    if (control && ["p", "n"].includes(key)) {
      if (start !== end) return false;
      // Native arrows keep visual-row behavior. Ctrl+P/N use logical rows because
      // textarea does not expose its wrapped visual-line caret positions.
      vertical(key === "p" ? -1 : 1); event.preventDefault(); return true;
    }
    if (control && ["a", "e"].includes(key)) return take(() => { caret(key === "a" ? lineStart(text, start) : lineEnd(text, end)); yank = null; });
    if (control && key === "f") return take(() => {
      caret(start !== end ? ceilBoundary(text, end) : boundaries(text).find(point => point > end) ?? text.length); yank = null;
    });
    if (control && key === "h") return take(() => {
      replace(start === end ? floorBoundary(text, Math.max(0, start - 1)) : start, end, "");
    });
    if (alt && ["b", "f"].includes(key)) return take(() => { caret(key === "b" ? previousWord(text, start) : nextWord(text, end)); yank = null; });
    if ((control && ["u", "k", "w", "backspace"].includes(key)) || (alt && key === "d")) return take(() => {
      let from = start, to = end;
      if (start === end) {
        if (key === "u") { from = lineStart(text, start); if (from === start && start > 0) from = start - 1; }
        else if (key === "k") { to = lineEnd(text, end); if (to === end && end < text.length) to++; }
        else if (key === "w") from = start - (text.slice(0, start).match(/\S+\s*$/u)?.[0].length || text.slice(0, start).match(/\s+$/u)?.[0].length || 0);
        else if (key === "backspace") from = previousWord(text, start);
        else to = nextWord(text, end);
      }
      replace(from, to, "", {kill:true});
    });
    if (control && key === "y") return take(() => {
      if (!killed.length) return;
      const value = killed[0], from = floorBoundary(text, start);
      if (replace(start, end, value, {retainYank:true})) yank = {start:from, end:from + value.length, index:0, text:input.value};
    });
    if (alt && key === "y") return take(() => {
      if (!yank || killed.length < 2 || yank.text !== text || start !== end || end !== yank.end) return;
      const previous = yank, index = (previous.index + 1) % killed.length, value = killed[index];
      if (replace(previous.start, previous.end, value, {retainYank:true})) yank = {start:previous.start, end:previous.start + value.length, index, text:input.value};
    });
    if (!["shift", "control", "alt"].includes(key)) yank = null;
    return false;
  }
  return {attach, keydown, reset};
})();
