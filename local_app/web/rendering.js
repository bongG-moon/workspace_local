"use strict";

// Coalesce transport fragments without changing their order or delaying a final message.
globalThis.WorkspaceStream = (() => {
  const area = $("work-area"), latest = $("latest-response");
  const pending = new Map();
  const TEXT_LIMIT = 100000, PENDING_LIMIT = 100000, PENDING_MESSAGES = 16;
  let timer = null, scrollFrame = null, epoch = 0, following = true, pendingCharacters = 0;
  let observer = null, observing = false, suspended = false, readingTop = 0;
  let intentUntil = 0, direction = 0, dragging = false, touchY = null;
  const atBottom = () => area.scrollHeight - area.scrollTop - area.clientHeight <= 4;
  const visible = () => !!active && !suspended && !globalThis.WorkspaceCapabilities?.isOpen();
  function badge() { latest.hidden = !visible() || following; }
  function cancelScroll() {
    if (scrollFrame !== null) globalThis.cancelAnimationFrame?.(scrollFrame);
    scrollFrame = null;
  }
  function clearIntent() { intentUntil = 0; direction = 0; dragging = false; touchY = null; }
  function observeLayout() {
    if (document.hidden || !visible()) {
      observer?.disconnect(); observing = false; cancelScroll(); return;
    }
    if (observing || !globalThis.ResizeObserver) return;
    // One observer for the current task and viewport, not one per message.
    observer ||= new ResizeObserver(changed);
    observer.observe(area);
    const content = $("task-view"); if (content) observer.observe(content);
    observing = true;
  }
  function changed() {
    badge();
    observeLayout();
    if (document.hidden || !visible() || !following || dragging || touchY !== null || scrollFrame !== null) return;
    const ticket = epoch, sid = active?.id;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null;
      if (ticket !== epoch || sid !== active?.id || document.hidden || !visible()) return;
      if (following && !dragging && touchY === null) area.scrollTop = area.scrollHeight;
      badge();
    });
  }
  function flush() {
    clearTimeout(timer); timer = null;
    const entries = [...pending.values()];
    pending.clear(); pendingCharacters = 0;
    for (const entry of entries) if (entry.sessionId === active?.id && entry.epoch === epoch) entry.apply(entry.data);
    changed();
  }
  function enqueue(data, apply) {
    const key = streamKey(data), incoming = String(data.text || "");
    const text = incoming.slice(0, TEXT_LIMIT);
    // A hidden WebView may suspend RAF and heavily throttle timers. Bounded
    // synchronous pressure flushing prevents its transport buffer growing.
    if (pendingCharacters + text.length > PENDING_LIMIT || (!pending.has(key) && pending.size >= PENDING_MESSAGES)) flush();
    const previous = pending.get(key);
    if (previous) {
      const available = TEXT_LIMIT - previous.data.text.length;
      const addition = text.slice(0, available);
      previous.data.text += addition; pendingCharacters += addition.length;
      previous.data.uiTruncated ||= !!data.uiTruncated || incoming.length > available;
    } else {
      pending.set(key, {data:{...data, text, uiTruncated:!!data.uiTruncated || incoming.length > TEXT_LIMIT}, apply, sessionId:active?.id, epoch});
      pendingCharacters += text.length;
    }
    if (timer !== null) return;
    const ticket = epoch;
    timer = setTimeout(() => {
      timer = null;
      // Flush transport data on the existing batch timer. RAF is only a visual
      // scroll optimization, never the owner of delivery or retained text.
      if (ticket === epoch) flush();
    }, 40);
  }
  function finish(data) {
    const key = streamKey(data), entry = pending.get(key);
    if (entry) pendingCharacters -= entry.data.text.length;
    pending.delete(key);
    if (!pending.size) { clearTimeout(timer); timer = null; }
  }
  function reset() {
    epoch++; pending.clear(); pendingCharacters = 0; clearTimeout(timer); timer = null;
    cancelScroll(); observer?.disconnect(); observing = false; clearIntent();
    suspended = false; readingTop = 0; following = true; latest.hidden = true;
  }
  function jump() {
    clearIntent(); following = true;
    if (visible() && !document.hidden) area.scrollTop = area.scrollHeight;
    changed();
  }
  function pause() { clearIntent(); following = false; cancelScroll(); badge(); }
  function suspend() {
    if (!suspended) readingTop = area.scrollTop;
    suspended = true; clearIntent(); observeLayout(); badge();
  }
  function resume() {
    const wasSuspended = suspended; suspended = false;
    if (wasSuspended && !following) area.scrollTop = readingTop;
    changed();
  }
  function nestedInput(target) {
    for (let node = target; node && node !== area; node = node.parentElement || node.parentNode) {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName) || node.isContentEditable) return true;
      if (node.nodeType !== 1 && !node.tagName) continue;
      const style = globalThis.getComputedStyle?.(node);
      if (style && /auto|scroll/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1) return true;
    }
    return false;
  }
  function intent(value) {
    direction = value; intentUntil = Date.now() + 1000;
    if (value < 0) { following = false; cancelScroll(); badge(); }
  }
  area.addEventListener?.("scroll", () => {
    if (!visible() || document.hidden) return;
    // Browser layout/focus/programmatic scrolls do not revoke follow intent.
    if (dragging) following = atBottom();
    else if (Date.now() < intentUntil && direction > 0 && atBottom()) following = true;
    badge();
  }, {passive:true});
  area.addEventListener?.("wheel", event => {
    if (!visible() || nestedInput(event.target) || !event.deltaY || event.ctrlKey) return;
    intent(Math.sign(event.deltaY));
  }, {passive:true});
  area.addEventListener?.("keydown", event => {
    if (!visible() || event.defaultPrevented || nestedInput(event.target) || event.altKey || event.metaKey) return;
    if (["ArrowUp","PageUp","Home"].includes(event.key)) intent(-1);
    else if (["ArrowDown","PageDown","End"].includes(event.key)) intent(1);
    else if (event.key === " " && !/^(BUTTON|A|SUMMARY)$/.test(event.target?.tagName)) intent(event.shiftKey ? -1 : 1);
  });
  area.addEventListener?.("pointerdown", event => {
    if (!visible() || event.button !== 0 || event.target !== area) return;
    const rect = area.getBoundingClientRect?.(); if (!rect) return;
    const edge = rect.left + (area.clientLeft || 0) + area.clientWidth;
    if (event.clientX >= edge && event.clientX <= rect.right && area.scrollHeight > area.clientHeight) {
      pause(); dragging = true;
    }
  }, {passive:true});
  function endDrag() {
    if (!dragging) return;
    dragging = false; following = atBottom(); clearIntent(); changed();
  }
  document.addEventListener?.("pointerup", endDrag, {passive:true});
  document.addEventListener?.("pointercancel", endDrag, {passive:true});
  area.addEventListener?.("touchstart", event => {
    if (visible() && !nestedInput(event.target) && event.touches?.length === 1) touchY = event.touches[0].clientY;
  }, {passive:true});
  area.addEventListener?.("touchmove", event => {
    if (touchY === null || event.touches?.length !== 1) return;
    const next = event.touches[0].clientY, delta = touchY - next; touchY = next;
    if (delta) intent(Math.sign(delta));
  }, {passive:true});
  function endTouch() { if (touchY !== null) { touchY = null; changed(); } }
  area.addEventListener?.("touchend", endTouch, {passive:true});
  area.addEventListener?.("touchcancel", endTouch, {passive:true});
  globalThis.addEventListener?.("resize", changed, {passive:true});
  document.addEventListener?.("visibilitychange", () => {
    cancelScroll(); clearIntent();
    if (pending.size) flush(); else changed();
  });
  latest.onclick = jump;
  return {enqueue, flush, finish, reset, changed, jump, pause, suspend, resume, isFollowing:() => following};
})();
