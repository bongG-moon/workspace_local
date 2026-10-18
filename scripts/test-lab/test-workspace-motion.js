// Loaded only by the generated benchmark fixture, never by the application.
const motionWait = ms => new Promise(resolve => setTimeout(resolve, ms));
const motionAssert = (value, message) => { if (!value) throw Error(message); };
const motionDialog = document.querySelector('#motion-dialog');
const motionTask = document.querySelector('#task-view');
const motionOverride = document.createElement('style');
document.head.append(motionOverride);
function motionEnabled(enabled) {
  const noDialogTransition = 'dialog,dialog::backdrop{transition:none!important}dialog{opacity:1;transform:none}dialog::backdrop{opacity:1!important}';
  motionOverride.textContent = enabled === 'previous' ? noDialogTransition +
    'dialog[open]{animation:motion-previous-dialog 300ms cubic-bezier(.2,.8,.2,1)!important}#task-view,#welcome,#capabilities-view,#source-panel,#results-panel{animation-duration:300ms!important;animation-name:motion-previous-view!important}#task-view[data-motion-step=b],#welcome[data-motion-step=b]{animation-name:motion-previous-view-alt!important}@keyframes motion-previous-dialog{from{opacity:.45;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}@keyframes motion-previous-view-alt{from{opacity:.65}to{opacity:1}}@keyframes motion-previous-view{from{opacity:.65}to{opacity:1}}' : enabled ? '' : noDialogTransition + 'dialog[open],#task-view,#welcome,#source-panel,#results-panel,#capabilities-view{animation:none!important}';
}
async function motionChecks() {
  motionEnabled(true); await motionWait(400);
  motionDialog.showModal(); document.querySelector('#dialog-editor').focus();
  motionAssert(document.activeElement.id === 'dialog-editor', 'Input unavailable during entry');
  await motionWait(70);
  const entryOpacity = Number(getComputedStyle(motionDialog).opacity);
  motionAssert(entryOpacity > 0 && entryOpacity < 1, 'Popup entry not visibly fading');
  await motionWait(400);
  motionAssert(Number(getComputedStyle(motionDialog).opacity) === 1, 'Popup did not settle');
  motionDialog.close();
  motionAssert(!motionDialog.open, 'Close waited for animation');
  await motionWait(70);
  const exitOpacity = Number(getComputedStyle(motionDialog).opacity);
  motionAssert(exitOpacity > 0 && exitOpacity < 1, 'Popup exit not visibly fading');
  motionAssert(getComputedStyle(motionDialog).pointerEvents === 'none', 'Closed popup intercepts input');
  await motionWait(400);
  motionAssert(getComputedStyle(motionDialog).display === 'none', 'Closed popup still displayed');
  motionTask.dataset.motionStep = 'a';
  motionAssert(getComputedStyle(motionTask).animationDuration === '0.3s', 'View duration');
  await motionWait(400);
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
  await motionWait(400);
  motionAssert(document.getAnimations().length === 0, 'Finished animation retained');
  return { popupMs: 300, viewMs: 300, entryOpacity, exitOpacity, immediateFocus: true, immediateClose: true,
    streamingDoesNotAnimate: true, rapidSwitches: 100, remainingAnimations: 0 };
}
async function motionBatch(enabled, cycles) {
  motionEnabled(enabled); await motionWait(400);
  if (enabled === 'previous') {
    motionAssert(getComputedStyle(motionTask).animationDuration === '0.3s', 'Previous baseline duration');
    motionTask.dataset.motionStep = 'b';
    motionAssert(getComputedStyle(motionTask).animationName === 'motion-previous-view-alt', 'Previous baseline restart');
    await motionWait(400);
  }
  const gaps = [], longTasks = []; let frame, previous, running = true;
  const observe = new PerformanceObserver(list => longTasks.push(...list.getEntries().map(e => e.duration)));
  observe.observe({type:'longtask'});
  function tick(time) { if (previous !== undefined) gaps.push(time - previous); previous = time;
    if (running) frame = requestAnimationFrame(tick); }
  frame = requestAnimationFrame(tick);
  const start = performance.now();
  for (let n = 0; n < cycles; n++) {
    motionTask.dataset.motionStep = motionTask.dataset.motionStep === 'a' ? 'b' : 'a';
    document.querySelector('#source-panel').hidden = !!(n % 2);
    document.querySelector('#results-panel').hidden = !(n % 2);
    motionDialog.showModal(); document.querySelector('#dialog-editor').focus();
    await motionWait(340); motionDialog.close(); await motionWait(340);
  }
  await motionWait(400); running = false; cancelAnimationFrame(frame); observe.disconnect();
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
