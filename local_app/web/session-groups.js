"use strict";

globalThis.WorkspaceSessionGroups = (() => {
  let saving=false, editContext=null;
  const data=()=>boot.sessionGroups||{groups:[],assignments:{},revision:0,warning:null};
  const groupFor=id=>data().assignments[id]||null;
  const label=id=>data().groups.find(group=>group.id===groupFor(id))?.name||"일반 목록";
  const locked=()=>saving||sessionOrderSaving||!!sessionDragId||appClosed||!!data().warning;
  function feedback(id,text){$(id).textContent=text||"";$(id).hidden=!text;}
  function controls(){
    for(const id of ["task-group-create","task-group-save","task-group-delete"])$(id).disabled=locked();
  }
  async function mutate(change){
    if(locked())return null;
    const origin=document.activeElement,header=origin?.closest?.(".task-group-header");
    const focus=header?{groupId:header.dataset.groupId,selector:origin.classList.contains("task-group-menu")?".task-group-menu":".task-group-toggle"}:null;
    saving=true;controls();renderSessions();
    try{
      const result=await api("/api/session-groups",{...change,revision:data().revision});
      boot.sessionGroups=result.sessionGroups;return result;
    }catch(err){
      if(err.code==="session_groups_changed"){
        try{boot.sessionGroups=await api("/api/session-groups");}catch{}
      }
      throw err;
    }finally{
      const current=document.activeElement,restore=focus&&(!current||current===document.body||!current.isConnected||current.closest?.(".task-group-header")?.dataset.groupId===focus.groupId);
      saving=false;controls();renderSessions();
      if(restore)[...$("sessions").children].find(row=>row.dataset.groupId===focus.groupId)?.querySelector(focus.selector)?.focus({preventScroll:true});
    }
  }
  function openEdit(groupId=null){
    if(locked())return;
    const group=data().groups.find(row=>row.id===groupId);
    if(groupId&&!group)return;
    editContext={groupId};$("task-group-title").textContent=group?"폴더 이름 바꾸기":"업무 폴더 만들기";
    $("task-group-name").value=group?.name||"";$("task-group-save").textContent=group?"이름 저장":"폴더 만들기";
    $("task-group-delete").hidden=!group;feedback("task-group-error","");controls();
    showDialog("task-group-dialog");$("task-group-name").focus();$("task-group-name").select?.();
  }
  async function move(id,groupId){
    if(groupFor(id)===(groupId||null)||!sessions.some(row=>row.id===id)||locked())return false;
    try{
      const result=await mutate({action:"move",id,groupId:groupId||null});
      if(!result)return false;
      toast(`‘${sessions.find(row=>row.id===id)?.title||"업무"}’을 ${groupId?`‘${label(id)}’ 폴더로`:"폴더 밖으로"} 옮겼어요.`);return true;
    }catch(err){toast(err.message);return false;}
  }
  function header(group,members,query){
    const expanded=!!query||!group.collapsed;
    const row=el("div",null,"task-group-header"+(members.some(item=>item.id===active?.id)?" has-active":""));
    row.dataset.groupId=group.id;
    const toggle=el("button",null,"task-group-toggle");toggle.type="button";toggle.disabled=locked();
    toggle.setAttribute("aria-label",`${group.name} · 업무 ${members.length}개${query?" · 검색 해제":expanded?" · 접기":" · 펼치기"}`);
    toggle.title=query?"검색 중에는 폴더를 펼쳐 보여요. 누르면 검색을 해제합니다.":expanded?"폴더 접기":"폴더 펼치기";
    toggle.setAttribute("aria-expanded",String(expanded));
    const chevron=el("span",null,"task-group-chevron"),folder=el("span",null,"task-folder-icon");
    chevron.setAttribute("aria-hidden","true");folder.setAttribute("aria-hidden","true");
    toggle.append(chevron,folder,el("span",group.name,"task-group-name"),el("span",String(members.length),"task-group-count"));
    toggle.onclick=async()=>{
      // Search reveals matches without overwriting the saved collapse preference.
      if(query){$("session-search").value="";renderSessions();return;}
      try{await mutate({action:"collapse",groupId:group.id,collapsed:!group.collapsed});}catch(err){toast(err.message);}
    };
    row.append(toggle);
    const menu=el("button","···","task-group-menu");menu.type="button";menu.disabled=locked();
    menu.title="폴더 이름 변경·삭제";menu.setAttribute("aria-label",`${group.name} 폴더 설정`);menu.onclick=()=>openEdit(group.id);row.append(menu);
    return row;
  }
  function render(items,createRow,query){
    controls();const container=$("sessions"),state=data();
    if(state.warning)container.append(el("p",state.warning,"sidebar-empty"));
    if(!state.groups.length){for(const item of items)container.append(createRow(item));return false;}
    const buckets=new Map([[null,[]],...state.groups.map(group=>[group.id,[]])]);
    for(const item of items)(buckets.get(groupFor(item.id))||buckets.get(null)).push(item);
    for(const group of state.groups){
      const members=buckets.get(group.id);
      if(query&&!members.length&&!group.name.toLocaleLowerCase().includes(query))continue;
      container.append(header(group,members,query));
      if(group.collapsed&&!query)continue;
      for(const item of members){const row=createRow(item);row.classList.add("grouped-session");row.dataset.groupId=group.id;container.append(row);}
      if(!members.length){const empty=el("p",query?"일치하는 업무가 없어요":"여기로 업무를 끌어 넣으세요","task-group-empty");empty.dataset.groupId=group.id;container.append(empty);}
      const tail=el("div",null,"task-group-tail");tail.dataset.groupId=group.id;tail.setAttribute("aria-hidden","true");container.append(tail);
    }
    for(const item of buckets.get(null)){const row=createRow(item);row.dataset.groupId="";container.append(row);}
    if(query&&!items.length&&!container.children.length)container.append(el("p","찾는 업무나 폴더가 없어요","sidebar-empty"));
    const exit=el("div",null,"session-root-drop");exit.dataset.groupId="";exit.setAttribute("aria-hidden","true");container.append(exit);
    return true;
  }
  function dropTarget(container,hit,y,sourceId){
    if(saving||sessionOrderSaving||appClosed||data().warning||!sessions.some(item=>item.id===sourceId))return null;
    const children=[...container.children],marked=row=>row&&typeof row.dataset?.groupId==="string";
    let row=hit;
    while(row&&row!==container&&row.parentElement!==container)row=row.parentElement;
    if(!marked(row)){
      row=null;let previous=null;
      // Include indentation and the small gaps between a folder's child rows.
      // Never let the gap between two different folders select either by accident.
      for(const candidate of children){
        const bounds=candidate.getBoundingClientRect();
        if(y>=bounds.top&&y<=bounds.bottom&&marked(candidate)){row=candidate;break;}
        if(y<bounds.top){if(marked(previous)&&marked(candidate)&&previous.dataset.groupId===candidate.dataset.groupId)row=previous;break;}
        previous=candidate;
      }
    }
    if(!marked(row)||groupFor(sourceId)===(row.dataset.groupId||null))return null;
    const groupId=row.dataset.groupId||null;
    const folderHeader=groupId?children.find(child=>child.classList.contains("task-group-header")&&child.dataset.groupId===groupId):null;
    return {row,header:folderHeader,kind:"folder",groupId};
  }
  $("task-group-create").onclick=()=>openEdit();
  for(const id of ["task-group-close","task-group-cancel"])$(id).onclick=()=>$("task-group-dialog").close();
  $("task-group-dialog").addEventListener("close",()=>{if(!$("task-group-dialog").open)editContext=null;});
  $("task-group-name").onkeydown=event=>{if(event.key==="Enter"&&(event.isComposing||event.keyCode===229))event.preventDefault();};
  $("task-group-form").onsubmit=async event=>{
    event.preventDefault();if(!editContext||locked())return;
    const context=editContext,name=$("task-group-name").value.trim();
    if(!name){feedback("task-group-error","폴더 이름을 입력해 주세요.");return;}
    feedback("task-group-error","");
    try{
      const result=await mutate(context.groupId?{action:"rename",groupId:context.groupId,name}:{action:"create",name});
      if(result&&editContext===context)$("task-group-dialog").close();
    }catch(err){if(editContext===context)feedback("task-group-error",err.message);}
  };
  $("task-group-delete").onclick=async()=>{
    const context=editContext,group=data().groups.find(row=>row.id===context?.groupId);if(!group||locked())return;
    if(!await confirmAction({title:"업무 폴더를 삭제할까요?",message:`‘${group.name}’ 폴더만 삭제합니다. 안에 있던 업무는 일반 목록에 그대로 남으며, 대화와 실제 작업 파일은 유지됩니다.`,confirmLabel:"폴더만 삭제"}))return;
    if(editContext!==context||appClosed)return;
    try{const result=await mutate({action:"delete",groupId:group.id});if(result&&editContext===context)$("task-group-dialog").close();}
    catch(err){if(editContext===context)feedback("task-group-error",err.message);}
  };
  return {render,groupFor,label,openEdit,move,dropTarget,isSaving:()=>saving,
    matches:(item,query)=>!!groupFor(item.id)&&label(item.id).toLocaleLowerCase().includes(query)};
})();
