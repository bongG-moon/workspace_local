// Loaded only by the generated benchmark fixture, never by the application.
const motionWait = ms => new Promise(resolve => setTimeout(resolve, ms));
const motionAssert = (value, message) => { if (!value) throw Error(message); };
const motionDialog = document.querySelector('#motion-dialog');
const motionTask = document.querySelector('#task-view');
const motionOverride = document.createElement('style');
document.head.append(motionOverride);
function motionEnabled(enabled) {
  motionOverride.textContent = enabled ? '' : 'dialog[open],#task-view,#welcome,#source-panel,#results-panel,#capabilities-view{animation:none!important}';
}
async function motionChecks() {
  motionEnabled(true);
  motionDialog.showModal(); document.querySelector('#dialog-editor').focus();
  motionAssert(getComputedStyle(motionDialog).animationDuration === '0.14s', 'Popup duration');
  motionAssert(motionDialog.getAnimations().length === 1, 'Popup did not animate');
  motionAssert(document.activeElement.id === 'dialog-editor', 'Input unavailable during entry');
  motionDialog.close();
  motionAssert(!motionDialog.open, 'Close waited for animation');
  motionTask.dataset.motionStep = 'a';
  motionAssert(getComputedStyle(motionTask).animationDuration === '0.12s', 'View duration');
  // Simulate a streaming text update; navigation animation must not restart.
  await motionWait(180);
  motionTask.querySelector('p').append(' streaming update');
  motionAssert(motionTask.getAnimations().length === 0, 'Text update restarted entry');
  motionTask.dataset.motionStep = 'b';
  motionAssert(motionTask.getAnimations().length === 1, 'Task navigation did not animate');
  for (let n = 0; n < 100; n++) {
    motionTask.dataset.motionStep = n % 2 ? 'a' : 'b';
    getComputedStyle(motionTask).animationName;
    motionAssert(motionTask.getAnimations().length <= 1, 'Navigation animations accumulated');
    motionDialog.showModal(); motionDialog.close();
  }
  await motionWait(200);
  motionAssert(document.getAnimations().length === 0, 'Finished animation retained');
  return { popupMs: 140, viewMs: 120, immediateFocus: true, immediateClose: true,
    streamingDoesNotAnimate: true, rapidSwitches: 100, remainingAnimations: 0 };
}
async function motionBatch(enabled, cycles) {
  motionEnabled(enabled); await motionWait(200);
  const gaps = [], longTasks = []; let frame, previous, running = true;
  const observe = new PerformanceObserver(list => longTasks.push(...list.getEntries().map(e => e.duration)));
  observe.observe({type:'longtask'});
  function tick(time) { if (previous !== undefined) gaps.push(time - previous); previous = time;
    if (running) frame = requestAnimationFrame(tick); }
  frame = requestAnimationFrame(tick);
  const start = performance.now();
  for (let n = 0; n < cycles; n++) {
    motionTask.dataset.motionStep = n % 2 ? 'a' : 'b';
    document.querySelector('#source-panel').hidden = !!(n % 2);
    document.querySelector('#results-panel').hidden = !(n % 2);
    motionDialog.showModal(); document.querySelector('#dialog-editor').focus();
    await motionWait(180); motionDialog.close();
  }
  await motionWait(200); running = false; cancelAnimationFrame(frame); observe.disconnect();
  gaps.sort((a,b) => a-b);
  motionAssert(gaps.length > cycles * 3, 'Offscreen rendering was throttled; timing not usable');
  return {cycles, elapsedMs:performance.now()-start, frames:gaps.length,
    medianFrameMs:gaps[Math.floor(gaps.length*.5)], p95FrameMs:gaps[Math.floor(gaps.length*.95)],
    framesOver34Ms:gaps.filter(n=>n>34).length, longTasks:longTasks.length,
    longestTaskMs:Math.max(0,...longTasks), remainingAnimations:document.getAnimations().length};
}
async function motionReducedCheck() {
  motionEnabled(true); motionTask.dataset.motionStep = 'b'; motionDialog.showModal();
  motionAssert(matchMedia('(prefers-reduced-motion:reduce)').matches, 'Reduced motion not emulated');
  motionAssert(getComputedStyle(motionDialog).animationName === 'none', 'Reduced popup still animates');
  motionAssert(getComputedStyle(motionTask).animationName === 'none', 'Reduced view still animates');
  motionAssert(document.getAnimations().length === 0, 'Reduced motion retained animations');
  motionDialog.close(); return {enabled:true, remainingAnimations:0};
}
