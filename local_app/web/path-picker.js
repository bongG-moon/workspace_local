"use strict";

// An app-styled chooser. Browsing never attaches, trusts, reads, or sends a file.
globalThis.WorkspacePathPicker = (() => {
  let view = null, dialog = null, ui = null, serial = 0, searchTimer = null;
  const pathKey = path => String(path).replace(/\\/g, "/").toLocaleLowerCase();
  const node = (tag, text, cls) => el(tag, text, cls);
  function button(text, cls, action) { const n=node("button",text,cls);n.type="button";n.onclick=action;return n; }
  function build() {
    if(dialog)return;
    dialog=node("dialog",null,"path-picker");dialog.id="path-picker-dialog";dialog.setAttribute("aria-labelledby","path-picker-title");
    const head=node("div",null,"path-picker-head"),heading=node("div");
    heading.append(node("span","내 PC의 자료","eyebrow"));
    const title=node("h2");title.id="path-picker-title";heading.append(title);
    const close=button("×","icon-button",()=>finish([]));close.setAttribute("aria-label","선택 창 닫기");head.append(heading,close);
    const layout=node("div",null,"path-picker-layout"),sidebar=node("nav",null,"path-picker-shortcuts");sidebar.setAttribute("aria-label","자주 쓰는 위치");
    const main=node("section",null,"path-picker-main"),pathForm=node("form",null,"path-picker-location"),up=button("↑","quiet-button",()=>load(view?.parent));up.setAttribute("aria-label","상위 폴더로 이동");
    const path=node("input");path.type="text";path.autocomplete="off";path.spellcheck=false;path.placeholder="폴더 경로 입력";path.setAttribute("aria-label","폴더 전체 경로");
    const go=button("이동","quiet-button",()=>navigateInput());pathForm.onsubmit=event=>{event.preventDefault();navigateInput();};pathForm.append(up,path,go);
    const crumbs=node("nav",null,"path-picker-crumbs");crumbs.setAttribute("aria-label","현재 폴더 위치");
    const search=node("input");search.type="search";search.placeholder="이 폴더 안에서 이름 검색";search.maxLength=160;search.setAttribute("aria-label","현재 폴더 검색");
    search.oninput=()=>{clearTimeout(searchTimer);const owner=view;searchTimer=setTimeout(()=>{if(owner===view&&owner?.path)load(owner.path,{search:true});},180);};
    const message=node("p",null,"path-picker-message");message.setAttribute("role","status");message.setAttribute("aria-live","polite");
    const list=node("div",null,"path-picker-list");list.setAttribute("aria-label","폴더와 파일");
    list.onkeydown=event=>{
      if(!["ArrowDown","ArrowUp","Home","End"].includes(event.key))return;
      const rows=[...list.children].filter(row=>row.tagName==="BUTTON"&&!row.disabled);if(!rows.length)return;
      const index=rows.indexOf(document.activeElement),next=event.key==="Home"?0:event.key==="End"?rows.length-1:Math.max(0,Math.min(rows.length-1,index+(event.key==="ArrowDown"?1:-1)));
      event.preventDefault();rows[next].focus();rows[next].scrollIntoView({block:"nearest"});
    };
    main.append(pathForm,crumbs,search,message,list);layout.append(sidebar,main);
    const footer=node("footer",null,"path-picker-footer"),selection=node("div",null,"path-picker-selection"),summary=node("p"),chips=node("div",null,"path-picker-chips");selection.append(summary,chips);
    const actions=node("div",null,"path-picker-actions"),native=button("Windows 탐색기로 선택","text-button",nativePicker);native.title="네트워크나 특수 위치는 Windows 선택 창에서 찾을 수 있어요.";
    const cancel=button("취소","quiet-button",()=>finish([])),apply=button("선택","send-button",()=>applySelection());actions.append(native,cancel,apply);footer.append(selection,actions);
    dialog.append(head,layout,footer);document.body.append(dialog);
    dialog.oncancel=event=>{event.preventDefault();finish([]);};dialog.onclose=()=>{if(view)finish([]);};
    ui={title,path,up,go,crumbs,sidebar,search,message,list,summary,chips,native,cancel,apply};
  }
  function note(text,error=false){ui.message.textContent=text;ui.message.classList.toggle("is-error",error);}
  function renderSelection(){
    if(!view)return;
    const files=view.kind==="files";ui.summary.textContent=files?`새로 선택한 자료 ${view.selected.size}개 · ${view.remaining}개까지 추가 가능`:(view.path?`선택할 폴더: ${basename(view.path)}`:"사용할 폴더를 열어 주세요.");
    ui.chips.replaceChildren();
    if(files)for(const [key,path]of view.selected){const chip=button(basename(path)+" ×","path-picker-chip",()=>{view.selected.delete(key);renderRows();renderSelection();});chip.title=path;chip.setAttribute("aria-label",basename(path)+" 선택 취소");ui.chips.append(chip);}
    ui.apply.textContent=files?(view.selected.size?`${view.selected.size}개 자료 추가`:"자료 추가"):"이 폴더 선택";
    ui.apply.disabled=!!view.loading||!!view.nativePending||!!view.validating||(!files?!view.directoryReady:view.selected.size===0);
  }
  function sizeLabel(bytes){return bytes>=1048576?(bytes/1048576).toFixed(1)+" MB":bytes>=1024?Math.ceil(bytes/1024)+" KB":bytes+" B";}
  function renderRows(){
    if(!view)return;ui.list.replaceChildren();
    for(const entry of view.entries){
      const folder=entry.kind==="folder",key=pathKey(entry.path),selected=view.selected.has(key),exists=view.existing.has(key);
      const row=button(null,"path-picker-row",()=>{if(folder){void load(entry.path);return;}if(exists)return;if(view.selected.has(key))view.selected.delete(key);else if(view.selected.size<view.remaining)view.selected.set(key,entry.path);else{note("자료는 기존 첨부를 포함해 한 번에 12개까지 추가할 수 있어요.",true);return;}const focusPath=entry.path;renderRows();renderSelection();[...ui.list.children].find(item=>item.dataset.path===focusPath)?.focus();});
      row.dataset.path=entry.path;row.title=entry.path;row.classList.toggle("is-selected",selected);row.disabled=exists&&!folder;
      if(!folder)row.setAttribute("aria-pressed",String(selected));
      const icon=node("span",folder?"":entry.name.split(".").pop().slice(0,4).toUpperCase(),folder?"path-picker-folder-icon":"path-picker-file-icon");icon.setAttribute("aria-hidden","true");
      row.append(icon,node("span",entry.name,"path-picker-name"),node("span",folder?"열기 ›":exists?"이미 추가됨":selected?"선택됨 ✓":sizeLabel(entry.size),"path-picker-meta"));ui.list.append(row);
    }
  }
  function renderLocations(data){
    ui.sidebar.replaceChildren(node("span","자주 쓰는 위치","path-picker-section-label"));
    for(const item of data.shortcuts){const n=button(item.name,"path-picker-shortcut",()=>load(item.path));n.title=item.path;n.classList.toggle("is-current",pathKey(item.path)===pathKey(data.path));ui.sidebar.append(n);}
    ui.crumbs.replaceChildren();
    for(const item of data.breadcrumbs){const n=button(item.name,"text-button",()=>load(item.path));n.title=item.path;if(item.path===data.path)n.setAttribute("aria-current","location");ui.crumbs.append(n);}
  }
  function navigateInput(){const value=ui.path.value.trim().replace(/^"|"$/g,"");if(value)void load(value);else note("폴더의 전체 경로를 입력해 주세요.",true);}
  async function load(path,{initial=false,search=false}={}){
    if(!view||view.nativePending||view.validating)return;
    const owner=view,ticket=++serial;owner.controller?.abort();owner.controller=new AbortController();owner.loading=true;owner.directoryReady=false;
    clearTimeout(searchTimer);if(!search)ui.search.value="";const query=search?ui.search.value:"";
    ui.list.replaceChildren();ui.list.setAttribute("aria-busy","true");ui.up.disabled=ui.go.disabled=true;note("폴더를 불러오고 있어요…");renderSelection();
    try{
      const data=await api("/api/browse-paths",{kind:owner.kind,path:path||undefined,query,initial},owner.controller.signal);
      if(view!==owner||ticket!==serial)return;
      owner.path=data.path;owner.parent=data.parent;owner.entries=data.entries;owner.directoryReady=true;ui.path.value=data.path;renderLocations(data);renderRows();
      note(data.limited?"폴더가 커서 일부 항목만 표시해요. 이름을 검색하거나 Windows 탐색기로 선택해 주세요.":!data.entries.length?(query?"이 이름의 항목을 찾지 못했어요.":owner.kind==="folder"?"하위 폴더가 없어요. 현재 폴더를 선택할 수 있어요.":"첨부할 문서·이미지·소스·압축·EXE 파일이 없어요."):owner.kind==="folder"?"폴더를 열고 아래에서 ‘이 폴더 선택’을 눌러 주세요.":"문서·이미지·소스·압축·EXE 파일을 선택하세요. 여러 폴더에서 담을 수 있어요.");
    }catch(error){if(view!==owner||ticket!==serial)return;if(error.name!=="AbortError")note(error.message,true);}
    finally{if(view===owner&&ticket===serial){owner.loading=false;ui.list.setAttribute("aria-busy","false");ui.go.disabled=false;ui.up.disabled=!owner.parent;renderSelection();}}
  }
  async function nativePicker(){
    if(!view||view.nativePending||view.validating)return;const owner=view;owner.nativePending=true;owner.controller?.abort();++serial;owner.loading=false;ui.native.disabled=true;renderSelection();note("Windows 선택 창이 열렸어요. 그 창에서 선택을 마쳐 주세요.");
    try{const result=await api("/api/pick",{kind:owner.kind,initialDirectory:owner.path||undefined});if(view!==owner)return;
      if(result.paths?.length){if(owner.kind==="folder"){await applySelection(result.paths.slice(0,1));return;}const paths=new Map(owner.selected);for(const path of result.paths)if(!owner.existing.has(pathKey(path)))paths.set(pathKey(path),path);
        if(paths.size>owner.remaining){note("자료는 기존 첨부를 포함해 한 번에 12개까지 추가할 수 있어요.",true);return;}owner.selected=paths;renderRows();note("선택한 자료를 확인하고 ‘자료 추가’를 눌러 주세요.");
      }else note("Windows 선택을 취소했어요. 이 창에서 계속 고를 수 있어요.");
    }catch(error){if(view===owner)note(error.message,true);}finally{if(view===owner){owner.nativePending=false;ui.native.disabled=false;ui.go.disabled=false;ui.up.disabled=!owner.parent;ui.list.setAttribute("aria-busy","false");renderSelection();}}
  }
  async function applySelection(nativePaths){
    if(!view||view.validating||(!nativePaths&&ui.apply.disabled))return;const owner=view;
    const paths=nativePaths||(owner.kind==="folder"?[owner.path]:[...owner.selected.values()]);owner.validating=true;ui.native.disabled=true;renderSelection();note("선택한 항목을 확인하고 있어요…");
    try{const result=await api("/api/browse-paths",{action:"select",kind:owner.kind,paths});if(view!==owner)return;
      if(!Array.isArray(result.paths)||result.paths.length!==paths.length)throw new Error("선택한 항목을 확인하지 못했어요. 다시 선택해 주세요.");finish(result.paths);
    }catch(error){if(view===owner)note(error.message,true);}finally{if(view===owner){owner.validating=false;ui.native.disabled=false;renderSelection();}}
  }
  function finish(paths){
    if(!view)return;const old=view;view=null;++serial;clearTimeout(searchTimer);old.controller?.abort();if(dialog.open)dialog.close();
    // Invalidate late list/native responses before returning to the caller.
    old.resolve({paths});const focus=old.focus;if(focus?.isConnected&&!focus.disabled)focus.focus();
  }
  function open({kind,initialDirectory,existingPaths=[]}={}){
    if(!["folder","files"].includes(kind))return Promise.reject(new Error("선택할 항목을 확인해 주세요."));
    if(view)return Promise.reject(new Error("이미 열린 선택 창을 먼저 닫아 주세요."));build();
    const existing=new Set(existingPaths.map(pathKey));
    const result=new Promise(resolve=>{view={kind,resolve,focus:document.activeElement,existing,remaining:Math.max(0,12-existing.size),selected:new Map(),entries:[],path:null,parent:null,directoryReady:false,loading:false,nativePending:false};});
    ui.title.textContent=kind==="folder"?"업무 폴더 선택":"자료 추가";ui.path.value=initialDirectory||"";ui.search.value="";ui.native.disabled=false;ui.chips.replaceChildren();ui.sidebar.replaceChildren();ui.crumbs.replaceChildren();showDialog(dialog.id);void load(initialDirectory,{initial:true});ui.path.focus();return result;
  }
  return {open};
})();
