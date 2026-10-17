"use strict";

globalThis.WorkspaceAttachments = (() => {
  const zone = $("composer"), note = $("attachment-drop-status"), copies = new Map();
  const mime = "application/x-company-workspace-file";
  let drag = null, serial = 0, uploading = false, depth = 0;
  const pathKey = value => String(value).replace(/\\/g,"/").toLowerCase();
  const currentKind = () => active ? active.kind === "chat" ? "chat" : "work" : globalThis.WorkspaceModes?.isChat() ? "chat" : "work";
  const policyFor = kind => kind === "chat" ? boot.chatAttachmentPolicy : boot.attachmentPolicy;
  const capture = () => ({id:active?.id||null,generation:selectionGeneration,kind:currentKind()});
  const limit = (name, fallback, kind=currentKind()) => {const policy=policyFor(kind);return Number.isSafeInteger(policy?.[name])&&policy[name]>0?policy[name]:fallback;};
  const maxAttachments = kind => limit("maxAttachments",12,kind);
  const merge = (before, incoming, kind) => { const seen = new Set(); return [...before,...incoming].filter(path => {const key=pathKey(path);if(seen.has(key))return false;seen.add(key);return true;}).slice(0,maxAttachments(kind)); };
  const locked = () => sending || !!choiceSubmission || appClosed || uploading;
  function renderNote() {
    note.hidden = !uploading && !attachments.some(path=>copies.has(pathKey(path)));
    note.textContent = uploading ? "앱 관리 공간에 파일 복사본을 저장하고 있습니다. 원본은 변경하지 않습니다."
      : globalThis.WorkspaceModes?.isChat()?"첨부 사본은 이 대화에 보관하며, 대화를 삭제하면 함께 정리합니다. 원본은 유지됩니다.":"끌어온 파일은 앱 관리 복사본입니다. 원본은 유지되며, 미사용 복사본은 설정에서 정리할 수 있습니다.";
  }
  function attach(paths, context) {
    if (appClosed || hiddenSessionIds.has(context.id) || hidingSessionIds.has(context.id)) return;
    if (context.id === (active?.id || null) && context.generation === selectionGeneration) {
      attachments = merge(attachments, paths, context.kind); renderAttachments(); saveDraft();
    } else if (context.id) {
      const draft = drafts.get(context.id) || {text:"",attachments:[]};
      drafts.set(context.id, {...draft,attachments:merge(draft.attachments || [],paths,context.kind)});
    }
  }
  function makeDraggable(node, path, sessionId) {
    node.draggable = true;
    node.setAttribute("aria-description", "입력창으로 끌어 자료에 추가할 수 있습니다.");
    node.ondragstart = event => {
      if (sessionId !== active?.id || locked()) {event.preventDefault();return;}
      drag = {key:String(++serial),path,sessionId,generation:selectionGeneration};
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData(mime, drag.key);
    };
    node.ondragend = () => {drag=null;depth=0;zone.classList.remove("drag-over");};
  }
  const accepts = event => [...(event.dataTransfer?.types || [])].some(type => type === "Files" || type === mime);
  async function upload(file, context) {
    const extensions=policyFor(context.kind)?.extensions,name=String(file.name).toLowerCase();
    if(Array.isArray(extensions)&&!extensions.some(extension=>typeof extension==="string"&&name.endsWith(extension.toLowerCase()))) {
      throw new Error(`‘${file.name}’은 첨부를 지원하지 않는 파일 형식입니다.`);
    }
    // Check metadata before sending bytes so a rejected large body cannot hide
    // the actual type, session, or quota error behind a closed connection.
    const prepared=await api("/api/attachments/prepare",{id:context.id,name:file.name,size:file.size});
    if(appClosed)throw new Error("앱이 종료되어 파일 전송을 시작하지 않았습니다.");
    if(prepared?.ok!==true)throw new Error("파일 전송 준비를 확인하지 못했습니다. 자료 목록을 확인해 주세요.");
    let response;
    try {
      response = await fetch(`/api/attachments/upload?id=${encodeURIComponent(context.id)}`, {
        method:"POST",headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/octet-stream","X-File-Name":encodeURIComponent(file.name)},body:file
      });
    } catch (_) {
      throw new Error("파일 전송 중 앱과 연결이 끊겼습니다. 복사 여부를 확인하지 못했으니 자료 목록을 먼저 확인해 주세요.");
    }
    let value;try {value=await response.json();}catch (_) {throw new Error("파일 복사 결과를 확인하지 못했습니다. 자료 목록을 확인해 주세요.");}
    if (!response.ok) throw new Error(value.error || "파일을 복사하지 못했습니다.");
    if (typeof value.path !== "string" || value.copied !== true) throw new Error("파일 복사 응답을 확인하지 못했습니다.");
    copies.set(pathKey(value.path),{name:value.name||file.name,size:value.size,sessionId:context.id});
    return value.path;
  }
  async function drop(event) {
    if (!accepts(event)) return;
    event.preventDefault(); depth=0;zone.classList.remove("drag-over");
    if (locked()) return toast("자료 전송이 끝난 뒤 다시 추가해 주세요.");
    const context = capture();
    if (!context.id) {if(globalThis.WorkspaceModes?.isChat()){try{if(!await ensureChatSession())return;Object.assign(context,capture());}catch(error){toast(error.message);return;}}else {toast("먼저 새 업무를 만들거나 기존 업무를 선택해 주세요.");return chooseFolder();}}
    const key = event.dataTransfer.getData(mime);
    if (key) {
      if (!drag || key !== drag.key || drag.sessionId !== context.id || drag.generation !== context.generation) return toast("현재 업무의 자료를 다시 끌어 주세요.");
      if (!attachments.some(path=>pathKey(path)===pathKey(drag.path)) && attachments.length>=maxAttachments()) return toast(`자료는 한 번에 ${maxAttachments()}개까지 추가할 수 있습니다.`);
      attach([drag.path],context);drag=null;toast("원본 파일을 자료에 추가했습니다.");return;
    }
    const directory = [...(event.dataTransfer.items || [])].some(item => {
      try {return item.kind === "file" && item.webkitGetAsEntry?.()?.isDirectory === true;} catch (_) {return false;}
    });
    if (directory) return toast("폴더 대신 파일을 끌어 주세요. 업무 폴더는 ‘저장 위치 선택’에서 지정할 수 있습니다.");
    const files = [...(event.dataTransfer.files || [])];
    if (!files.length) return toast("폴더 대신 파일을 끌어 주세요. 경로로 추가할 수도 있습니다.");
    if (attachments.length + files.length > maxAttachments()) return toast(`자료는 한 번에 ${maxAttachments()}개까지 추가할 수 있습니다.`);
    return uploadFiles(files,context);
  }
  async function uploadFiles(files, suppliedContext=null) {
    if(locked()||!files.length)return;
    if(!active&&globalThis.WorkspaceModes?.isChat()){try{if(!await ensureChatSession())return;}catch(error){toast(error.message);return;}}
    const context=suppliedContext||capture();
    if(!context.id)return toast("먼저 대화나 업무를 선택해 주세요.");
    if((context.id===active?.id?attachments:drafts.get(context.id)?.attachments||[]).length+files.length>maxAttachments(context.kind))return toast(`자료는 한 번에 ${maxAttachments(context.kind)}개까지 추가할 수 있습니다.`);
    const maxBytes=limit("maxUploadBytes",50*1024*1024,context.kind);
    if (files.some(file=>file.size > maxBytes)) return toast(`끌어 놓는 파일은 각각 ${Math.floor(maxBytes/1024/1024)}MB 이하여야 합니다. 파일 크기를 줄인 뒤 다시 추가해 주세요.`);
    uploading=true;note.hidden=false;note.textContent="앱 관리 공간에 파일 복사본을 저장하고 있습니다. 원본은 변경하지 않습니다.";setStatus(active?.state||"idle");
    let completed=0;
    try {
      for (const file of files) {
        if (appClosed||hiddenSessionIds.has(context.id)||hidingSessionIds.has(context.id)) break;
        const path=await upload(file,context);if(hiddenSessionIds.has(context.id)||hidingSessionIds.has(context.id))break;attach([path],context);completed++;
      }
      if (completed) toast(`${completed}개 파일의 복사본을 원래 ${context.kind==="chat"?"대화":"업무"}의 자료에 추가했습니다.`);
    } catch (err) {toast(`${err.message}${completed ? ` ${completed}개 파일은 추가됐습니다.` : ""}`);}
    finally {uploading=false;setStatus(active?.state||"idle");renderNote();if(context.id===active?.id)renderAttachments();}
  }
  zone.addEventListener?.("dragenter",event=>{if(accepts(event)){event.preventDefault();depth++;zone.classList.add("drag-over");}});
  zone.addEventListener?.("dragover",event=>{if(accepts(event)){event.preventDefault();event.dataTransfer.dropEffect=locked()?"none":"copy";}});
  zone.addEventListener?.("dragleave",()=>{depth=Math.max(0,depth-1);if(!depth)zone.classList.remove("drag-over");});
  zone.addEventListener?.("drop",drop);
  document.addEventListener?.("dragover",event=>{if(accepts(event))event.preventDefault();});
  document.addEventListener?.("drop",event=>{if(accepts(event)&&!zone.contains?.(event.target)){event.preventDefault();toast("파일을 아래 입력창에 끌어 놓아 주세요.");}});
  const forgetSession=id=>{for(const [path,entry] of copies)if(entry.sessionId===id)copies.delete(path);};
  return {drop,uploadFiles,makeDraggable,renderNote,forgetSession,isUploading:()=>uploading,isCopy:path=>copies.has(pathKey(path)),attach};
})();
