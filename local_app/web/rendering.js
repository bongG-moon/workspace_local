"use strict";

// Coalesce transport fragments without changing their order or delaying a final message.
globalThis.WorkspaceStream = (() => {
  const area = $("work-area"), latest = $("latest-response");
  const pending = new Map();
  let timer = null, frame = null, scrollFrame = null, epoch = 0, following = true;
  const nearBottom = () => area.scrollHeight - area.scrollTop - area.clientHeight < 100;
  const visible = () => !!active && !globalThis.WorkspaceCapabilities?.isOpen();
  function badge() { latest.hidden = !visible() || following; }
  function changed() {
    badge();
    if (scrollFrame !== null) return;
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
    if (frame !== null) { globalThis.cancelAnimationFrame?.(frame); frame = null; }
    for (const entry of pending.values()) if (entry.sessionId === active?.id && entry.epoch === epoch) entry.apply(entry.data);
    pending.clear(); changed();
  }
  function enqueue(data, apply) {
    const key = streamKey(data), previous = pending.get(key);
    if (previous) previous.data.text += data.text;
    else pending.set(key, {data:{...data}, apply, sessionId:active?.id, epoch});
    if (timer !== null || frame !== null) return;
    const ticket = epoch;
    timer = setTimeout(() => {
      timer = null;
      frame = requestAnimationFrame(() => { frame = null; if (ticket === epoch) flush(); });
    }, 40);
  }
  function finish(data) { pending.delete(streamKey(data)); }
  function reset() {
    epoch++; pending.clear(); clearTimeout(timer); timer = null;
    if (frame !== null) globalThis.cancelAnimationFrame?.(frame);
    if (scrollFrame !== null) globalThis.cancelAnimationFrame?.(scrollFrame);
    frame = scrollFrame = null; following = true; latest.hidden = true;
  }
  function jump() { following = true; area.scrollTop = area.scrollHeight; badge(); }
  area.addEventListener?.("scroll", () => { following = nearBottom(); badge(); }, {passive:true});
  // Cancel queued following as soon as the user starts reading upward.
  area.addEventListener?.("wheel", event => { if (event.deltaY < 0) { following = false; badge(); } }, {passive:true});
  area.addEventListener?.("keydown", event => { if (["ArrowUp","PageUp","Home"].includes(event.key)) { following = false; badge(); } });
  latest.onclick = jump;
  return {enqueue, flush, finish, reset, changed, jump, isFollowing:() => following};
})();
