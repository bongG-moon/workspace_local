"use strict";

globalThis.WorkspaceAttachments = (() => {
  const zone = $("composer"), note = $("attachment-drop-status"), copies = new Map();
  const mime = "application/x-company-workspace-file";
  let drag = null, serial = 0, uploading = false, depth = 0;
  const pathKey = value => String(value).replace(/\\/g,"/").toLowerCase();
  const merge = (before, incoming) => { const seen = new Set(); return [...before,...incoming].filter(path => {const key=pathKey(path);if(seen.has(key))return false;seen.add(key);return true;}).slice(0,12); };
  const locked = () => sending || !!choiceSubmission || appClosed || uploading;
  function renderNote() {
    note.hidden = !uploading && !attachments.some(path=>copies.has(pathKey(path)));
    note.textContent = uploading ? "앱 관리 공간에 파일 복사본을 저장하고 있습니다. 원본은 변경하지 않습니다."
      : "끌어온 파일은 앱 관리 복사본입니다. 첨부를 취소해도 원본과 복사본은 삭제되지 않습니다.";
  }
  function attach(paths, context) {
    if (appClosed) return;
    if (context.id === (active?.id || null) && context.generation === selectionGeneration) {
      attachments = merge(attachments, paths); renderAttachments(); saveDraft();
    } else if (context.id) {
      const draft = drafts.get(context.id) || {text:"",attachments:[]};
      drafts.set(context.id, {...draft,attachments:merge(draft.attachments || [],paths)});
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
    const response = await fetch(`/api/attachments/upload?id=${encodeURIComponent(context.id)}`, {
      method:"POST",headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/octet-stream","X-File-Name":encodeURIComponent(file.name)},body:file
    });
    let value;try {value=await response.json();}catch (_) {throw new Error("파일 복사 결과를 확인하지 못했습니다. 자료 목록을 확인해 주세요.");}
    if (!response.ok) throw new Error(value.error || "파일을 복사하지 못했습니다.");
    if (typeof value.path !== "string" || value.copied !== true) throw new Error("파일 복사 응답을 확인하지 못했습니다.");
    copies.set(pathKey(value.path),{name:value.name||file.name,size:value.size});
    return value.path;
  }
  async function drop(event) {
    if (!accepts(event)) return;
    event.preventDefault(); depth=0;zone.classList.remove("drag-over");
    if (locked()) return toast("자료 전송이 끝난 뒤 다시 추가해 주세요.");
    const context = {id:active?.id||null,generation:selectionGeneration};
    if (!context.id) {toast("먼저 새 업무를 만들거나 기존 업무를 선택해 주세요.");return chooseFolder();}
    const key = event.dataTransfer.getData(mime);
    if (key) {
      if (!drag || key !== drag.key || drag.sessionId !== context.id || drag.generation !== context.generation) return toast("현재 업무의 자료를 다시 끌어 주세요.");
      if (!attachments.some(path=>pathKey(path)===pathKey(drag.path)) && attachments.length>=12) return toast("자료는 한 번에 12개까지 추가할 수 있습니다.");
      attach([drag.path],context);drag=null;toast("원본 파일을 자료에 추가했습니다.");return;
    }
    const directory = [...(event.dataTransfer.items || [])].some(item => {
      try {return item.kind === "file" && item.webkitGetAsEntry?.()?.isDirectory === true;} catch (_) {return false;}
    });
    if (directory) return toast("폴더 대신 파일을 끌어 주세요. 업무 폴더는 ‘저장 위치 선택’에서 지정할 수 있습니다.");
    const files = [...(event.dataTransfer.files || [])];
    if (!files.length) return toast("폴더 대신 파일을 끌어 주세요. 경로로 추가할 수도 있습니다.");
    if (attachments.length + files.length > 12) return toast("자료는 한 번에 12개까지 추가할 수 있습니다.");
    if (files.some(file=>file.size > 50*1024*1024)) return toast("끌어 놓는 파일은 각각 50MB 이하여야 합니다. 큰 파일은 ‘자료 추가’로 원본을 선택해 주세요.");
    uploading=true;note.hidden=false;note.textContent="앱 관리 공간에 파일 복사본을 저장하고 있습니다. 원본은 변경하지 않습니다.";setStatus(active.state);
    let completed=0;
    try {
      for (const file of files) {
        if (appClosed) break;
        const path=await upload(file,context);attach([path],context);completed++;
      }
      if (completed) toast(`${completed}개 파일의 복사본을 원래 업무의 자료에 추가했습니다.`);
    } catch (err) {toast(`${err.message}${completed ? ` ${completed}개 파일은 추가됐습니다.` : ""}`);}
    finally {uploading=false;setStatus(active?.state||"idle");renderNote();if(context.id===active?.id)renderAttachments();}
  }
  zone.addEventListener?.("dragenter",event=>{if(accepts(event)){event.preventDefault();depth++;zone.classList.add("drag-over");}});
  zone.addEventListener?.("dragover",event=>{if(accepts(event)){event.preventDefault();event.dataTransfer.dropEffect=locked()?"none":"copy";}});
  zone.addEventListener?.("dragleave",()=>{depth=Math.max(0,depth-1);if(!depth)zone.classList.remove("drag-over");});
  zone.addEventListener?.("drop",drop);
  document.addEventListener?.("dragover",event=>{if(accepts(event))event.preventDefault();});
  document.addEventListener?.("drop",event=>{if(accepts(event)&&!zone.contains?.(event.target)){event.preventDefault();toast("파일을 아래 입력창에 끌어 놓아 주세요.");}});
  return {drop,makeDraggable,renderNote,isUploading:()=>uploading,isCopy:path=>copies.has(pathKey(path)),attach};
})();
