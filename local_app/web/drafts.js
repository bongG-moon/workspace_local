"use strict";

// One debounced writer, no interval or worker. Only changed task rows cross HTTP.
globalThis.WorkspaceDraftPersistence=(()=>{
  const dirty=new Set(),touched=new Set(),revisions=new Map(),saved=new Map();
  let map,hooks,ready=false,restoring=false,timer=null,deadline=null,flight=null,lastError="",savedSelected="home";
  const clone=value=>value?{text:value.text||"",attachments:[...(value.attachments||[])]}:null;
  function message(value){
    const node=document.getElementById("draft-save-status");
    if(node){node.textContent=value;node.hidden=!value;}
    if(value&&value!==lastError)hooks?.notify?.(value);
    lastError=value;
  }
  function schedule(){
    if(!ready||!dirty.size)return;
    clearTimeout(timer);timer=setTimeout(()=>{void flush(false).catch(()=>{});},800);
    if(!deadline)deadline=setTimeout(()=>{void flush(false).catch(()=>{});},5000);
  }
  function changed(id){if(restoring)return;touched.add(id);dirty.add(id);schedule();}
  class DraftMap extends Map{
    set(id,value){super.set(id,clone(value));changed(id);return this;}
    delete(id){const existed=super.delete(id);changed(id);return existed;}
    clear(){for(const id of this.keys())this.delete(id);}
  }
  function body(id){
    const stash=hooks.stashes().find(row=>row.id===id);
    return {draft:clone(map.get(id)),stash:stash?{text:stash.text,attachments:[...stash.attachments],selectionStart:stash.selectionStart,selectionEnd:stash.selectionEnd}:null};
  }
  async function drain(){
    for(const id of [...dirty]){
      const value=body(id),signature=JSON.stringify(value);
      dirty.delete(id);
      if(saved.get(id)===signature&&savedSelected===(hooks.selected()||"home"))continue;
      try{
        const selected=hooks.selected()||"home";
        const result=await hooks.api("/api/drafts",{id,revision:revisions.get(id)||0,...value,selectedId:selected});
        if(!Number.isInteger(result.revision))throw Error("초안 저장을 확인하지 못했어요. 입력은 화면에 유지됩니다.");
        revisions.set(id,result.revision);saved.set(id,signature);savedSelected=selected;message("");
      }catch(error){dirty.add(id);message(error.message||"초안을 저장하지 못했어요. 입력은 화면에 유지됩니다.");throw error;}
    }
  }
  async function flush(settle=true){
    clearTimeout(timer);clearTimeout(deadline);timer=deadline=null;
    if(!ready){if(touched.size)throw Error("초안 복원을 확인 중이에요. 잠시 후 다시 시도해 주세요.");return true;}
    if(flight){await flight;if(dirty.size&&settle)return flush();return true;}
    flight=drain();try{await flight;}finally{flight=null;}
    if(dirty.size){if(settle)return flush();schedule();}
    return true;
  }
  async function load(){
    try{
      const result=await hooks.api("/api/drafts");
      if(!Array.isArray(result.entries))throw Error("저장된 초안을 확인하지 못했어요.");
      restoring=true;savedSelected=result.selectedId||"home";
      const stashes=[];
      for(const row of result.entries){
        revisions.set(row.id,row.revision);saved.set(row.id,JSON.stringify({draft:row.draft,stash:row.stash}));
        if(touched.has(row.id))continue;
        // Keep empty entries: they cancel stale recovery snapshots.
        map.set(row.id,row.draft||{text:"",attachments:[]});
        if(row.stash)stashes.push({id:row.id,...row.stash});
      }
      hooks.restoreStashes(stashes);ready=true;message("");
      return {selectedId:result.selectedId,untouched:!touched.size};
    }catch(error){message(error.message||"초안을 복원하지 못했어요. 기존 저장 내용은 유지됩니다.");return null;}
    finally{restoring=false;schedule();}
  }
  function attach(value){
    hooks=value;
    document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="hidden")void flush().catch(()=>{});});
    globalThis.addEventListener?.("pagehide",()=>{void flush().catch(()=>{});});
  }
  function allowStashRestore(id){
    if(restoring||!ready||!saved.has(id))return true;
    if(!JSON.parse(saved.get(id)).stash)return false;
    // A locally cleared stash wins even before its debounced write completes.
    return !touched.has(id)||hooks.stashes().some(row=>row.id===id);
  }
  const keepForRecovery=(id,draft)=>!!draft?.text||!!draft?.attachments?.length||dirty.has(id)||!ready;
  return {createMap:()=>map=new DraftMap(),attach,load,flush,changed,allowStashRestore,keepForRecovery,
    hasPending:()=>dirty.size>0||!!flight,isReady:()=>ready};
})();
