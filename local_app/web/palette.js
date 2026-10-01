"use strict";

// A local navigation index: no file scans, conversation copies, polling, or AI requests.
globalThis.WorkspacePalette = (() => {
  const dialog=$("palette-dialog"),input=$("palette-search"),list=$("palette-items");
  const MAX_TASKS=500,MAX_RESULTS=24;
  let results=[],selected=-1,returnFocus=null,composing=false,renderKey="",running=false;
  const normalized=value=>String(value||"").normalize("NFKC").toLocaleLowerCase().trim();
  const hasTask=()=>!!active&&!appClosed;
  const controlsReady=()=>hasTask()&&!busyStates.has(active.state)&&!sending&&!choiceSubmission&&!modelChanging&&!permissionChanging&&!effortChanging&&!connectionPreparing;
  const activate=async id=>{const button=$(id);if(button&&!button.disabled&&typeof button.onclick==="function")return button.onclick();};
  const taskView=()=>globalThis.WorkspaceCapabilities?.close();
  const control=async kind=>{taskView();$("prompt").focus();return globalThis.WorkspaceInlineControls?.open(kind);};
  const commands=[
    {id:"new",label:"새 업무 시작",detail:"이름과 저장할 폴더 정하기",keywords:"new task 폴더",glyph:"＋",run:()=>activate("new-chat")},
    {id:"inbox",label:"통합 알림함",detail:"완료·응답 대기·확인할 일",keywords:"notification inbox 승인 알림",glyph:"◉",run:()=>globalThis.WorkspaceDesktop?.open()},
    {id:"tasks",label:"전체 업무 보기",detail:"업무 이름이나 저장 위치로 찾기",keywords:"tasks search 검색",glyph:"≡",run:()=>activate("tasks-open")},
    {id:"skills",label:"스킬·도구 보기",detail:"현재 Claude의 공통·폴더별 기능",keywords:"skills tools mcp 명령",glyph:"◇",run:()=>globalThis.WorkspaceCapabilities?.open()},
    {id:"results",label:"이번 결과 보기",detail:"현재 업무에서 만든 결과물",keywords:"results files 파일",glyph:"▤",enabled:hasTask,run:()=>{taskView();setPanel("results");globalThis.WorkspaceLayout?.openInspector();}},
    {id:"changes",label:"파일 변경 비교",detail:"현재 업무의 요청 전후 변경 내용",keywords:"diff changes 비교 수정",glyph:"±",enabled:()=>hasTask()&&typeof globalThis.WorkspaceProductivityActions?.openChanges==="function",run:()=>globalThis.WorkspaceProductivityActions?.openChanges()},
    {id:"branch",label:"대화 분기 만들기",detail:"현재 대화를 이어갈 별도 업무",keywords:"branch fork 분기",glyph:"⑂",enabled:()=>hasTask()&&typeof globalThis.WorkspaceProductivityActions?.openBranch==="function"&&(globalThis.WorkspaceProductivityActions?.canBranch?.()??true),run:()=>globalThis.WorkspaceProductivityActions?.openBranch()},
    {id:"model",label:"모델 바꾸기",detail:"현재 연결이 제공하는 모델",keywords:"model Claude 모델",glyph:"◈",enabled:controlsReady,run:()=>control("model")},
    {id:"effort",label:"Effort 바꾸기",detail:"현재 모델의 사고 수준",keywords:"effort 사고 수준",glyph:"◌",enabled:controlsReady,run:()=>control("effort")},
    {id:"permission",label:"승인 모드 바꾸기",detail:"현재 연결의 실행 승인 방식",keywords:"permission mode auto plan 승인",glyph:"✓",enabled:controlsReady,run:()=>control("permission")},
    {id:"queue",label:"이어 할 일과 예약",detail:"대기 요청·예약 확인",keywords:"queue schedule 일정 스케줄",glyph:"◷",enabled:hasTask,run:()=>activate("workflow-open")},
    {id:"import",label:"이전 Claude 대화 불러오기",detail:"기존 CLI 대화에서 이어가기",keywords:"resume import session 이전 세션",glyph:"↶",run:()=>activate("import-open")},
    {id:"settings",label:"설정 열기",detail:"연결·PC 알림·앱 설정",keywords:"settings config",glyph:"⚙",run:()=>activate("settings-open")},
    {id:"help",label:"사용 도움말",detail:"시작 방법과 단축키",keywords:"help guide 사용법",glyph:"?",run:()=>activate("help")}
  ];
  function restoreFocus(node){const owner=node?.closest?.("dialog");if(node?.isConnected&&!node.disabled&&(!owner||owner.open))node.focus();}
  function close(restore=true){
    const target=returnFocus;returnFocus=null;dialog.close();
    if(restore)restoreFocus(target);
  }
  function choose(index){
    if(index<0||index>=results.length||results[index].disabled)return;
    selected=index;
    for(let i=0;i<list.children.length;i++)list.children[i].setAttribute("aria-selected",String(i===selected));
    input.setAttribute("aria-activedescendant",`palette-option-${selected}`);
    list.children[selected]?.scrollIntoView?.({block:"nearest"});
  }
  function move(step){
    if(!results.some(row=>!row.disabled))return;
    let index=selected;
    for(let i=0;i<results.length;i++){index=(index+step+results.length)%results.length;if(!results[index].disabled){choose(index);break;}}
  }
  async function execute(index,expectedId){
    const row=results[index];if(!dialog.open||!row||row.disabled||running||appClosed)return;
    if(expectedId&&row.id!==expectedId)return;
    // A queued keyboard/click event cannot apply an action to a newly selected task.
    if(row.sessionContext!==undefined&&row.sessionContext!==active?.id){refresh();return;}
    if(row.enabled&&!row.enabled()){refresh();return;}
    const focus=returnFocus;running=true;
    globalThis.WorkspaceLayout?.dismissOverlays?.({focus:false});close(false);
    try{await row.run();}
    catch(error){toast(error.message||"선택한 기능을 열지 못했어요.");restoreFocus(focus);}
    finally{running=false;}
  }
  function refresh(){
    if(!dialog.open)return;
    const query=normalized(input.value),terms=query.split(/\s+/).filter(Boolean);
    const matches=row=>terms.every(term=>normalized(`${row.label} ${row.detail} ${row.keywords||""}`).includes(term));
    const available=commands.map(row=>({...row,disabled:!!row.enabled&&!row.enabled(),...(row.enabled?{sessionContext:active?.id}:{}),category:"기능"})).filter(matches);
    const metadata=sessions.slice(0,MAX_TASKS),tasks=[];
    for(const task of metadata){
      if(!task||typeof task.id!=="string")continue;
      const row={id:"task:"+task.id,label:task.title||"업무",detail:task.workspace||"",keywords:task.pinned?"고정 pinned":"",glyph:task.pinned?"⌖":"▱",category:"업무",current:task.id===active?.id,run:()=>selectSession(task.id)};
      if(matches(row)){tasks.push(row);if(tasks.length===MAX_RESULTS)break;}
    }
    const next=query?[...tasks,...available].slice(0,MAX_RESULTS):[...available,...tasks.slice(0,8)].slice(0,MAX_RESULTS);
    const key=JSON.stringify([query,next.map(({id,label,detail,disabled,current,sessionContext})=>[id,label,detail,disabled,current,sessionContext])]);
    if(key===renderKey)return;renderKey=key;
    const previous=results[selected]?.id;results=next;selected=-1;list.replaceChildren();
    results.forEach((row,index)=>{
      const option=el("div",null,"palette-option");option.id=`palette-option-${index}`;option.setAttribute("role","option");option.setAttribute("aria-selected","false");
      option.setAttribute("aria-disabled",String(!!row.disabled));option.dataset.commandId=row.id;
      const icon=el("span",row.glyph,"palette-glyph"),body=el("span",null,"palette-option-body"),title=el("strong",row.label),detail=el("small",row.detail);icon.setAttribute("aria-hidden","true");
      body.append(title,detail);option.append(icon,body,el("span",row.disabled?"지금 사용 불가":row.current?"현재 업무":row.category,"palette-category"));
      option.onmousedown=event=>event.preventDefault();option.onmousemove=()=>{if(!row.disabled&&selected!==index)choose(index);};option.onclick=()=>execute(index,row.id);list.append(option);
    });
    const keep=results.findIndex(row=>row.id===previous&&!row.disabled);choose(keep>=0?keep:results.findIndex(row=>!row.disabled));
    if(selected<0)input.removeAttribute?.("aria-activedescendant");
    $("palette-empty").hidden=results.length>0;
    $("palette-status").textContent=results.length?`${results.length}개 항목${sessions.length>MAX_TASKS?" · 최근 500개 업무에서 검색":""}`:"일치하는 업무나 기능이 없어요. 다른 이름으로 찾아보세요.";
  }
  function open(){
    if(appClosed||running)return false;
    if(dialog.open){close();return true;}
    if(modalStack.some(item=>item.open&&item!==dialog))return false;
    globalThis.WorkspaceComposer?.close();globalThis.WorkspaceInlineControls?.close();
    // A palette command may open controls in the main area. Dismiss temporary
    // panels first, and keep a visible return target if the palette is canceled.
    globalThis.WorkspaceLayout?.dismissOverlays?.();
    returnFocus=document.activeElement;input.value="";composing=false;renderKey="";
    showDialog("palette-dialog");refresh();input.focus();return true;
  }
  function keydown(event){
    if(event.defaultPrevented||event.isComposing||event.keyCode===229||composing)return false;
    if(normalized(event.key)==="p"&&(event.ctrlKey||event.metaKey)&&!event.altKey&&event.shiftKey){
      if(event.repeat){event.preventDefault();return true;}
      if(open()){event.preventDefault();event.stopPropagation?.();return true;}return false;
    }
    if(!dialog.open||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return false;
    if(event.key==="Escape"){event.preventDefault();event.stopPropagation?.();close();return true;}
    if(document.activeElement!==input)return false;
    if(event.key==="ArrowDown"||event.key==="ArrowUp"){event.preventDefault();move(event.key==="ArrowDown"?1:-1);return true;}
    if(event.key==="Enter"){event.preventDefault();execute(selected);return true;}
    return false;
  }
  input.oninput=()=>{if(!composing)refresh();};input.oncompositionstart=()=>{composing=true;};input.oncompositionend=()=>{composing=false;refresh();};
  dialog.oncancel=event=>{event.preventDefault();close();};
  dialog.onclose=()=>{results=[];selected=-1;renderKey="";list.replaceChildren();input.removeAttribute?.("aria-activedescendant");};
  $("palette-open").onclick=open;$("palette-close").onclick=()=>close();
  document.addEventListener("keydown",keydown);
  return {open,close,refresh,keydown};
})();
