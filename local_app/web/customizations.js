"use strict";

globalThis.WorkspaceCustomizations = (() => {
  const dialog = $("customizations-dialog"), list = $("customizations-list");
  const pendingApplications = new Set();
  let view = null, sequence = 0, controller = null, changing = false, limit = 100;
  const labels = {on:"사용", "name-only":"이름만 제공", "user-invocable-only":"직접 호출만", off:"사용 안 함"};
  const inheritLabels = {local:"공통·기존 설정 따르기", user:"스킬 기본 설정 따르기"};
  function notice(text) { $("customizations-status").textContent = text; }
  function catalogNotice(current) {
    return (current.data?.notice || "")+(current.data?.limited ? " 일부 설치 정보는 확인하지 못했어요." : "")
      +(current.pendingApply ? " 저장한 변경은 이 연결에 아직 적용하지 않았어요. ‘저장한 설정 적용’으로 재연결할 수 있어요." : "");
  }
  function controls() {
    const scope = view?.scope || "local";
    $("customizations-inherit-title").textContent = inheritLabels[scope];
    $("customizations-inherit-term").textContent = inheritLabels[scope];
    $("customizations-inherit-description").textContent = scope === "local"
      ? "이 프로젝트에서 별도로 지정한 값만 지우고 공통 설정과 기존 프로젝트 설정, 스킬 원래 규칙을 따릅니다. 스킬을 삭제하거나 반드시 켜는 기능은 아니에요."
      : "사용자 공통에서 별도로 지정한 값만 지우고 스킬 원래 규칙을 따릅니다. 프로젝트별 설정이나 회사 정책이 있으면 계속 우선 적용될 수 있어요. 스킬은 삭제하지 않습니다.";
    $("customizations-scope").disabled = changing;
    $("customizations-refresh").disabled = changing;
    $("customizations-apply").hidden = !view?.pendingApply;
    $("customizations-apply").disabled = changing;
    for (const id of ["customizations-skills", "customizations-plugins"]) $(id).disabled = changing;
  }
  function render() {
    list.replaceChildren(); controls();
    if (!view?.data) return;
    const current = view, kind = view.kind;
    for (const type of ["skills", "plugins"]) {
      $("customizations-"+type).setAttribute("aria-pressed", String(type === kind));
    }
    $("customizations-path").textContent = view.data.settingsPath;
    const query = $("customizations-search").value.trim().toLocaleLowerCase();
    const rows = (view.data[kind] || []).filter(row => (row.name+" "+(row.description||"")).toLocaleLowerCase().includes(query));
    if (!rows.length) list.append(el("p", "현재 범위에서 확인된 항목이 없어요.", "composer-note"));
    for (const [index, row] of rows.slice(0,limit).entries()) {
      const card = el("article", null, "customization-row"), text = el("div");
      text.append(el("strong", row.name));
      if (row.description) text.append(el("p", row.description, "composer-note"));
      if (kind === "skills") text.append(el("small", `저장된 스킬 설정: ${labels[row.visibility] || row.visibility} · ${row.source === "user" ? "개인 스킬" : "프로젝트 스킬"}`));
      else text.append(el("small", `저장된 업무 설정: ${row.enabled ? "사용" : "사용 안 함"}`));
      if (row.locked) text.append(el("small", row.reason));
      const select = el("select"); select.setAttribute("aria-label", row.name+" 사용 상태");
      const choices = kind === "skills" ? {inherit:inheritLabels[current.scope], ...labels} : {on:"사용", off:"사용 안 함"};
      for (const [value, label] of Object.entries(choices)) {const option=el("option",label);option.value=value;select.append(option);}
      select.value = kind === "skills" ? row.override || "inherit" : (row.override ?? row.enabled) ? "on" : "off";
      select.disabled = changing || row.locked;
      select.onchange = () => change(current, row, select.value);
      globalThis.WorkspaceAppearance?.enhanceSelect?.(select);
      card.append(text, select);
      if (kind === "skills" && select.value === "inherit" && !row.locked) {
        const hint = el("small", "이 범위의 별도 지정만 해제해요. 스킬은 삭제하지 않습니다.", "customization-state-hint");
        hint.id = "customization-inherit-" + index;
        select.setAttribute("aria-describedby", hint.id);
        card.classList.add("has-state-hint"); card.append(hint);
      }
      list.append(card);
    }
    if(rows.length>limit){const more=el("button",`더 보기 · ${limit}/${rows.length}`,"quiet-button");more.type="button";more.onclick=()=>{limit+=100;render();};list.append(more);}
  }
  async function refresh() {
    if (!view || changing) return;
    const current = view, ticket = ++sequence;
    current.data=null; controls();
    controller?.abort(); controller = new AbortController();
    notice("설치된 스킬과 플러그인을 확인하고 있어요…"); list.replaceChildren();
    try {
      const data = await api(`/api/customizations?id=${encodeURIComponent(current.id)}&scope=${current.scope}`, undefined, controller.signal);
      if (view !== current || ticket !== sequence || !dialog.open) return;
      current.data=data; render();notice(catalogNotice(current));
    } catch (err) {if (view===current && ticket===sequence && err.name!=="AbortError")notice(err.message);}
  }
  async function open(kind="skills", id=active?.id) {
    if(globalThis.WorkspaceModes?.isChat())return;
    if (changing || appClosed) return;
    if (!id) {toast("먼저 설정을 적용할 업무를 선택해 주세요.");return;}
    for(const pendingId of pendingApplications)if(pendingId!==id&&pendingId!==active?.id&&!sessions.some(row=>row.id===pendingId))pendingApplications.delete(pendingId);
    view={id,kind,scope:"local",data:null,pendingApply:pendingApplications.has(id)};limit=100;
    $("customizations-scope").value="local";$("customizations-search").value="";
    const item = [active, ...sessions].find(row=>row?.id===id);
    $("customizations-context").textContent=`${item?.title || "선택한 업무"} · ${item?.workspace || ""}`;
    globalThis.WorkspaceComposer?.close(); globalThis.WorkspaceInlineControls?.close();
    showDialog("customizations-dialog"); await refresh();
  }
  async function change(current, row, value) {
    if (changing || view !== current || !dialog.open) return;
    changing=true;notice("설정을 저장하고 있어요…");render();
    try {
      const item = await api(`/api/session?id=${encodeURIComponent(current.id)}`);
      if (view!==current || !dialog.open) return;
      if (!item.trusted) {notice("이 업무의 폴더 확인을 먼저 완료한 뒤 설정을 변경해 주세요.");return;}
      if (current.scope === "user") {
        const accepted=await confirmAction({title:"사용자 공통 설정 변경",message:"같은 Claude 설정을 사용하는 다른 업무와 터미널에도 영향을 줍니다. 이 범위에 저장할까요?",confirmLabel:"공통 설정에 저장"});
        if (view!==current || !dialog.open) return;
        if (!accepted) {notice("설정을 변경하지 않았어요.");return;}
      }
      const result=await api('/api/customizations',{id:current.id,scope:current.scope,kind:current.kind,
        name:row.name,value:current.kind==="plugins" ? value==="on" : value==="inherit" ? null : value,
        revision:current.data.revision});
      if(result.reconnectRequired===true)pendingApplications.add(current.id);
      if (view!==current || !dialog.open) return;
      current.data=result.catalog; current.pendingApply=pendingApplications.has(current.id); notice(result.notice);
      if(current.pendingApply)await applySaved(current,item);
      globalThis.WorkspaceCapabilities?.contextChanged(true);
    } catch (err) {if(view===current)notice(err.message+" 목록을 다시 확인한 뒤 재시도해 주세요.");}
    finally {changing=false;render();}
  }
  async function applySaved(current,item){
    if(view!==current||!dialog.open||appClosed)return;
    const result=await restartConnection({customizations:{id:current.id,item,scope:current.scope,
      isCurrent:()=>view===current&&dialog.open,close:()=>dialog.close()}});
    if(view!==current||!dialog.open)return;
    if(result?.ok===true)current.pendingApply=false;
    else notice(result?.error||"설정은 저장했어요. 현재 연결에는 아직 적용하지 않았습니다. ‘저장한 설정 적용’으로 재연결하거나, 작업을 마친 뒤 Claude Code를 재시작해 주세요.");
  }
  $("customizations-apply").onclick=async()=>{
    if(changing||!view?.pendingApply||!dialog.open||appClosed)return;
    const current=view;changing=true;render();
    try{const item=await api(`/api/session?id=${encodeURIComponent(current.id)}`);await applySaved(current,item);}
    catch(err){if(view===current)notice("설정은 저장되어 있어요. "+err.message);}
    finally{changing=false;render();}
  };
  async function handleCommand(text) {
    const match=text.trim().match(/^\/(skills|plugin)(?:\s+([\s\S]*))?$/u);
    if(!match)return false;
    if(match[2]){toast(`/${match[1]}로 관리 화면을 열어 항목과 적용 범위를 선택해 주세요. 요청은 전송하지 않았습니다.`);return true;}
    await open(match[1]==="plugin"?"plugins":"skills");
    if(dialog.open && $("prompt").value===text){$("prompt").value="";saveDraft();}
    return true;
  }
  for(const type of ["skills","plugins"])$("customizations-"+type).onclick=()=>{if(view&&!changing){view.kind=type;render();}};
  $("customizations-scope").onchange=()=>{if(view&&!changing){view={...view,scope:$("customizations-scope").value,data:null};void refresh();}};
  $("customizations-search").oninput=render;
  $("customizations-refresh").onclick=refresh;
  $("customizations-close").onclick=()=>dialog.close();
  dialog.addEventListener("close",()=>{if(dialog.open)return;sequence++;controller?.abort();view=null;list.replaceChildren();});
  function connectionRestarted(id){pendingApplications.delete(id);if(view?.id===id){view.pendingApply=false;controls();if(view.data)notice(catalogNotice(view));}}
  return {open,handleCommand,connectionRestarted};
})();
