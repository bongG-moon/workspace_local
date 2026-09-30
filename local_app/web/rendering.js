"use strict";

// Coalesce transport fragments without changing their order or delaying a final message.
globalThis.WorkspaceStream = (() => {
  const area = $("work-area"), latest = $("latest-response");
  const pending = new Map();
  const TEXT_LIMIT = 100000, PENDING_LIMIT = 100000, PENDING_MESSAGES = 16;
  let timer = null, scrollFrame = null, epoch = 0, following = true, pendingCharacters = 0;
  const nearBottom = () => area.scrollHeight - area.scrollTop - area.clientHeight < 100;
  const visible = () => !!active && !globalThis.WorkspaceCapabilities?.isOpen();
  function badge() { latest.hidden = !visible() || following; }
  function changed() {
    badge();
    if (document.hidden || scrollFrame !== null) return;
    const ticket = epoch, sid = active?.id;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null;
      if (ticket !== epoch || sid !== active?.id || !visible()) return;
      if (following) area.scrollTop = area.scrollHeight;
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
    if (scrollFrame !== null) globalThis.cancelAnimationFrame?.(scrollFrame);
    scrollFrame = null; following = true; latest.hidden = true;
  }
  function jump() { following = true; area.scrollTop = area.scrollHeight; badge(); }
  area.addEventListener?.("scroll", () => { following = nearBottom(); badge(); }, {passive:true});
  // Cancel queued following as soon as the user starts reading upward.
  area.addEventListener?.("wheel", event => { if (event.deltaY < 0) { following = false; badge(); } }, {passive:true});
  area.addEventListener?.("keydown", event => { if (["ArrowUp","PageUp","Home"].includes(event.key)) { following = false; badge(); } });
  document.addEventListener?.("visibilitychange", () => {
    if (scrollFrame !== null) globalThis.cancelAnimationFrame?.(scrollFrame);
    scrollFrame = null;
    if (pending.size) flush(); else changed();
  });
  latest.onclick = jump;
  return {enqueue, flush, finish, reset, changed, jump, isFollowing:() => following};
})();
