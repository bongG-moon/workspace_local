"use strict";

// Result visibility is independent of execution and desktop notifications.
// One visible result observer; no polling, transcript copies or per-token work.
globalThis.WorkspaceResultReceipts = (() => {
  const known=new Map(), pending=new Map(), retryAfter=new Map(), LIMIT=500;
  const area=$("work-area"), conversation=$("conversation");
  let viewId=null, viewGeneration=-1, rendered=null, target=null, dirty=true;
  let frame=null, dwell=null, observer=null, resize=null, observed=null;
  function normalize(value){
    if(!value||!Number.isSafeInteger(value.revision)||value.revision<1||
      !Number.isSafeInteger(value.seenRevision)||value.seenRevision<0||value.seenRevision>value.revision||
      typeof value.runId!=="string"||!value.runId||value.runId.length>64)return null;
    return {revision:value.revision,seenRevision:value.seenRevision,runId:value.runId};
  }
  const unread=item=>{const value=merge(known.get(item?.id),item?.resultReceipt);return !!value&&value.seenRevision<value.revision;};
  const equal=(a,b)=>a?.revision===b?.revision&&a?.seenRevision===b?.seenRevision&&a?.runId===b?.runId;
  function merge(a,b){
    a=normalize(a);b=normalize(b);if(!a)return b;if(!b)return a;
    const latest=b.revision>a.revision?b:a;
    return {...latest,seenRevision:Math.max(a.seenRevision,b.seenRevision)};
  }
  function remember(id,value){
    if(!value)return;
    known.delete(id);known.set(id,value);
    while(known.size>LIMIT)known.delete(known.keys().next().value);
  }
  function mergeSession(item){
    if(!item?.id)return item;
    const receipt=merge(known.get(item.id),item.resultReceipt);
    if(receipt){item.resultReceipt=receipt;remember(item.id,receipt);}
    return item;
  }
  function apply(id,value,{render=true,row=null}={}){
    if(typeof id!=="string")return false;
    row ||= sessions.find(item=>item.id===id);
    const current=active?.id===id?active:null;
    const receipt=merge(merge(merge(known.get(id),row?.resultReceipt),current?.resultReceipt),value);
    if(!receipt)return false;
    let changed=false;
    for(const item of [row,current])if(item&&!equal(item.resultReceipt,receipt)){item.resultReceipt={...receipt};changed=true;}
    remember(id,receipt);
    if(changed&&render)renderSessions();
    if(current)schedule();
    return changed;
  }
  function cancelDwell(){if(dwell!==null)clearTimeout(dwell);dwell=null;}
  function reset(){
    const checkpoint={id:viewId,receipt:rendered?{...rendered}:null};
    viewId=null;viewGeneration=-1;rendered=null;target=null;dirty=true;
    cancelDwell();if(frame!==null)globalThis.cancelAnimationFrame?.(frame);frame=null;
    observer?.disconnect();resize?.disconnect();observed=null;
    return checkpoint;
  }
  function viewReady(){
    const snapshot=normalize(active?.resultReceipt);
    reset();if(!active)return;
    viewId=active.id;viewGeneration=selectionGeneration;rendered=snapshot;
    apply(viewId,snapshot);schedule();
  }
  function resume(checkpoint){
    // A failed navigation retains the old DOM. Attention may have learned a
    // newer completion meanwhile, so never treat its metadata as rendered.
    reset();if(!active)return;
    viewId=active.id;viewGeneration=selectionGeneration;
    rendered=checkpoint?.id===viewId?normalize(checkpoint.receipt):null;
    schedule();
  }
  function completed(value){
    if(!active)return;
    const receipt=normalize(value);
    apply(active.id,receipt);
    // Attention metadata can arrive before the actual final answer. Only a
    // restored transcript or consumed terminal event can arm this result.
    if(viewId===active.id&&viewGeneration===selectionGeneration&&receipt&&(!rendered||receipt.revision>=rendered.revision)){
      rendered=receipt;dirty=true;cancelDwell();schedule();
    }
  }
  function contextVisible(){
    return !appClosed&&!!active&&active.id===viewId&&selectionGeneration===viewGeneration&&
      document.visibilityState==="visible"&&document.hasFocus?.()===true&&
      !$("task-view").hidden&&!conversation.hidden&&!secondaryViewOpen()&&
      !document.querySelector("dialog[open]")&&!globalThis.WorkspaceUpgrade?.isLocked?.();
  }
  function findTarget(){
    if(!dirty&&target?.isConnected)return target;
    dirty=false;target=null;
    if(!rendered)return null;
    let footer=null;
    for(const node of conversation.children){
      if(node.dataset?.runId!==rendered.runId)continue;
      if(node.classList.contains("message")&&node.classList.contains("assistant")&&!node.classList.contains("streaming"))target=node;
      if(node.classList.contains("request-timing")&&node.dataset.state==="done")footer=node;
    }
    // Some valid requests complete with tool output only. Their completed
    // duration footer is visible evidence, never a still-running user bubble.
    target ||= footer;
    return target;
  }
  function eligible(){
    const receipt=normalize(active?.resultReceipt);
    return contextVisible()&&rendered&&receipt&&rendered.revision>receipt.seenRevision&&
      !pending.has(viewId)&&Date.now()>=(retryAfter.get(viewId)||0);
  }
  function inView(node){
    if(!node?.isConnected||!node.getBoundingClientRect)return false;
    const box=node.getBoundingClientRect(),clip=area.getBoundingClientRect();
    const top=Math.max(box.top,clip.top,0),bottom=Math.min(box.bottom,clip.bottom,globalThis.innerHeight||Infinity);
    const left=Math.max(box.left,clip.left,0),right=Math.min(box.right,clip.right,globalThis.innerWidth||Infinity);
    if(bottom-top<Math.min(24,box.height)||right-left<Math.min(24,box.width)||box.height<=0||box.width<=0)return false;
    const hit=document.elementFromPoint?.((left+right)/2,(top+bottom)/2);
    return !document.elementFromPoint||!!hit&&(hit===node||node.contains(hit));
  }
  function watch(node){
    if(node===observed)return;
    observer?.disconnect();resize?.disconnect();observed=node;
    if(!node)return;
    if(globalThis.IntersectionObserver){observer ||= new IntersectionObserver(schedule,{root:area,threshold:[0,.1,1]});observer.observe(node);}
    if(globalThis.ResizeObserver){resize ||= new ResizeObserver(schedule);resize.observe(area);resize.observe(node);}
  }
  async function acknowledge(id,revision){
    const request={revision};pending.set(id,request);
    try{
      const reply=await api("/api/session/result-read",{id,revision});
      const receipt=normalize(reply?.resultReceipt);
      if(reply?.id!==id||!receipt||receipt.seenRevision<revision)throw new Error("Invalid result receipt");
      retryAfter.delete(id);apply(id,receipt);
    }catch(_){
      // Keep the unread marker on a failed save. Retry only on an existing
      // visibility/attention change after backoff, never add a polling loop.
      retryAfter.set(id,Date.now()+5000);
      while(retryAfter.size>LIMIT)retryAfter.delete(retryAfter.keys().next().value);
    }finally{if(pending.get(id)===request)pending.delete(id);schedule();}
  }
  function check(){
    frame=null;
    if(!eligible()){cancelDwell();watch(null);return;}
    const node=findTarget();watch(node);
    if(!inView(node)){cancelDwell();return;}
    if(dwell!==null)return;
    const id=viewId,generation=viewGeneration,revision=rendered.revision;
    dwell=setTimeout(()=>{
      dwell=null;
      if(viewId===id&&viewGeneration===generation&&rendered?.revision===revision&&eligible()&&inView(node))void acknowledge(id,revision);
    },300);
  }
  function schedule(){if(frame===null)frame=requestAnimationFrame(check);}
  function changed({content=false}={}){if(content)dirty=true;schedule();}
  area.addEventListener("scroll",schedule,{passive:true});
  globalThis.addEventListener?.("focus",schedule);
  globalThis.addEventListener?.("blur",()=>{cancelDwell();schedule();});
  globalThis.addEventListener?.("resize",schedule,{passive:true});
  document.addEventListener("visibilitychange",()=>{cancelDwell();schedule();});
  // Native dialogs do not bubble close events; capture handles all dialogs
  // without attaching an observer to the continuously streaming transcript.
  document.addEventListener("close",schedule,true);
  document.addEventListener("toggle",schedule,true);
  globalThis.addEventListener?.("pagehide",reset);
  for(const item of sessions)mergeSession(item);
  return {normalize,unread,mergeSession,apply,viewReady,completed,changed,reset,resume};
})();
