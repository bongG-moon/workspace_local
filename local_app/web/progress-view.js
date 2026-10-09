"use strict";

// One lazy reader for reports actually recorded by the CLI. It never runs tools
// or treats output as HTML, and it does not request history while collapsed.
globalThis.WorkspaceProgressView = (() => {
  const PAGE=50,MAX_ROWS=250,MAX_TEXT=65536,PREVIEW=1400;
  const labels={assistant:"설명",tool_input:"도구 입력",tool_result:"도구 결과",tool_progress:"도구 진행",task:"추가 작업",hook:"자동 처리",status:"상태",result:"결과",error:"오류",report:"보고",notice:"안내"};
  const clean=(value,limit)=>typeof value==="string"?value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,"").slice(0,limit):"";
  const node=(tag,text,className)=>{const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;};
  let hooks=null,host=null,detail=null,summary=null,count=null,disclosure=null,body=null,view=null;
  let sessionId=null,currentRun=null,filter=null,metadata={count:0,lastSeq:0,revision:0,truncated:false,available:false};
  let expanded=false,generation=0,controller=null,pending=null,timer=null,dirty=false,rows=[],cards=new Map();
  let nextBefore=null,hasMore=false,pageTruncated=false,olderWindow=false,newPending=false,failure="",pageNotice="",loaded=false;

  function normalizeMetadata(value){return {count:Number.isSafeInteger(value?.count)&&value.count>=0?value.count:0,
    lastSeq:Number.isSafeInteger(value?.lastSeq)&&value.lastSeq>=0?value.lastSeq:0,
    revision:Number.isSafeInteger(value?.revision)&&value.revision>=0?value.revision:0,
    truncated:value?.truncated===true,available:value?.available===true};}
  function updateSummary(){
    if(!host)return;host.hidden=!sessionId;
    updateDisclosure();
    count.textContent=filter?"선택한 요청":metadata.count?`${metadata.count}개 기록`:"";
    const opener=document.getElementById("progress-open");if(opener)opener.disabled=!sessionId;
  }
  function updateDisclosure(){
    const action=detail?.open?"접기":"펼치기";
    if(disclosure)disclosure.textContent=action;
    summary?.setAttribute("aria-label",`${filter?"선택한 요청의 진행 내용":"업무의 진행 내용"} ${action}`);
  }
  function cancel(){generation++;controller?.abort();controller=null;pending=null;clearTimeout(timer);timer=null;dirty=false;}
  function release(){rows=[];cards.clear();nextBefore=null;hasMore=false;pageTruncated=false;olderWindow=false;newPending=false;failure="";pageNotice="";loaded=false;view=null;body?.replaceChildren();}
  function close(){cancel();expanded=false;if(detail)detail.open=false;updateDisclosure();release();}
  function reset(id=null,value=null,runId=null){close();sessionId=clean(id,160)||null;currentRun=clean(runId,160)||null;filter=null;metadata=normalizeMetadata(value);updateSummary();}
  function isBottom(){return !view||view.scroller.scrollHeight-view.scroller.scrollTop-view.scroller.clientHeight<72;}
  function revealWithinWorkArea(){
    if(globalThis.WorkspaceCapabilities?.isOpen())return;
    const area=document.getElementById("work-area");if(!area||!host)return;
    const frame=area.getBoundingClientRect(),panel=host.getBoundingClientRect();
    const top=frame.top+(area.clientTop||0),height=area.clientHeight,bottom=top+height;
    let delta=0;
    if(panel.bottom-panel.top>height||panel.top<top)delta=panel.top-top;
    else if(panel.bottom>bottom)delta=panel.bottom-bottom;
    if(delta)area.scrollTop=Math.max(0,area.scrollTop+delta);
  }
  function timeText(value){
    const date=new Date(typeof value==="number"?value*1000:value);
    return Number.isFinite(date.getTime())?date.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"";
  }
  function record(value){
    if(!value||!Number.isSafeInteger(value.seq)||value.seq<1)return null;
    const raw=typeof value.text==="string"?value.text:"";
    return {seq:value.seq,runId:clean(value.runId,160),time:value.time,kind:clean(value.kind,60),
      title:clean(value.title,300),text:clean(raw,MAX_TEXT),tool:clean(value.tool,160),
      parentToolUseId:clean(value.parentToolUseId,160),truncated:value.truncated===true||raw.length>MAX_TEXT};
  }
  function page(value){
    if(!value||!Array.isArray(value.records)||value.records.length>PAGE||typeof value.hasMore!=="boolean")throw Error("진행 기록의 응답을 확인하지 못했어요.");
    const result=[],seen=new Set();
    for(const item of value.records){const row=record(item);if(!row||seen.has(row.seq)||filter&&row.runId!==filter)throw Error("진행 기록의 응답을 확인하지 못했어요.");seen.add(row.seq);result.push(row);}
    result.sort((left,right)=>left.seq-right.seq);
    const before=value.nextBefore;
    if(value.hasMore&&(!result.length||!Number.isSafeInteger(before)||before<1||before!==result[0].seq))throw Error("이전 진행 기록의 위치를 확인하지 못했어요.");
    return {records:result,hasMore:value.hasMore,nextBefore:value.hasMore?before:null,truncated:value.truncated===true};
  }
  function makeRow(row,showAll=false){
    const item=node("li",undefined,"progress-record"),heading=node("div",undefined,"progress-record-heading");item.dataset.seq=String(row.seq);item.dataset.kind=row.kind;
    heading.append(node("span",labels[row.kind]||"진행 보고","progress-kind"),node("strong",row.title||row.tool||"진행 내용"),node("time",timeText(row.time)));
    item.append(heading);
    if(row.tool)item.append(node("span",row.tool,"progress-tool"));
    if(row.parentToolUseId)item.append(node("small","추가 작업자의 보고","progress-child"));
    if(row.text){
      const content=node("pre",showAll?row.text:row.text.slice(0,PREVIEW),"progress-record-text");content.tabIndex=0;item.append(content);
      if(row.text.length>PREVIEW){
        const toggle=node("button",showAll?"내용 접기":"내용 더 보기","text-button progress-text-toggle");toggle.type="button";toggle.setAttribute("aria-expanded",String(showAll));
        toggle.onclick=()=>{const open=toggle.getAttribute("aria-expanded")==="true";content.textContent=open?row.text.slice(0,PREVIEW):row.text;toggle.textContent=open?"내용 더 보기":"내용 접기";toggle.setAttribute("aria-expanded",String(!open));};item.append(toggle);
      }
    }
    if(row.truncated)item.append(node("small","긴 내용은 저장된 범위까지만 표시합니다.","progress-truncated"));
    return item;
  }
  function renderRows(mode){
    if(!view)return;const scroller=view.scroller,top=scroller.scrollTop,height=scroller.scrollHeight;
    const activeElement=document.activeElement,activeCard=[...cards.values()].find(card=>card.element.contains?.(activeElement));
    const focusSelector=activeElement?.classList?.contains("progress-text-toggle")?".progress-text-toggle":activeElement?.classList?.contains("progress-record-text")?".progress-record-text":null;
    const rect=scroller.getBoundingClientRect?.(),anchor=rect?[...view.list.children].find(element=>element.getBoundingClientRect().bottom>rect.top):null;
    const anchorTop=anchor?.getBoundingClientRect().top;
    const alive=new Set(rows.map(row=>row.seq));for(const key of cards.keys())if(!alive.has(key))cards.delete(key);
    const elements=rows.map(row=>{
      const previous=cards.get(row.seq),signature=JSON.stringify(row);
      if(!previous||previous.signature!==signature){const showAll=previous?.element.querySelector(".progress-text-toggle")?.getAttribute("aria-expanded")==="true";cards.set(row.seq,{seq:row.seq,signature,element:makeRow(row,showAll)});}
      return cards.get(row.seq).element;
    });
    for(const child of [...view.list.children])if(!elements.includes(child))child.remove();
    elements.forEach((element,index)=>{if(view.list.children[index]!==element)view.list.insertBefore(element,view.list.children[index]||null);});
    if(mode==="older")scroller.scrollTop=anchor?.isConnected?top+anchor.getBoundingClientRect().top-anchorTop:top+scroller.scrollHeight-height;
    else if(mode==="latest")scroller.scrollTop=scroller.scrollHeight;
    else scroller.scrollTop=top;
    if(activeCard&&focusSelector){const replacement=cards.get(activeCard.seq)?.element;if(replacement&&replacement!==activeCard.element)replacement.querySelector(focusSelector)?.focus({preventScroll:true});}
    renderState();
  }
  function renderState(){
    if(!view)return;
    view.older.hidden=!hasMore;view.older.disabled=!!pending;view.latest.disabled=!!pending;
    view.latest.textContent=newPending?"새 진행 내용 보기":"최신 내용 보기";
    view.latest.hidden=!rows.length&&!newPending;
    view.all.hidden=!filter;view.current.hidden=!currentRun||filter===currentRun;
    view.scope.textContent=filter?"선택한 요청의 기록":"이 업무의 전체 기록";
    view.note.hidden=!(metadata.truncated||pageTruncated||olderWindow||pageNotice);
    view.note.textContent=[pageNotice,olderWindow?"이전 기록을 보고 있어요. 최신 내용은 위 버튼에서 확인할 수 있습니다.":"",metadata.truncated||pageTruncated?"저장 한도를 넘은 이전 기록 또는 긴 내용의 일부가 생략되어 있습니다.":""].filter(Boolean).join(" ");
    const legacy=loaded&&!rows.length&&!failure?hooks.legacy?.(sessionId,filter):null;
    view.legacy.replaceChildren();
    if(Array.isArray(legacy)&&legacy.length){
      view.legacy.append(node("p","이전 버전에 저장된 도구 요약입니다. 자세한 입력과 결과 원문은 이 기록에 포함되지 않습니다.","progress-note"));
      const list=node("ol",undefined,"progress-records");
      const states={requested:"요청됨",running:"실행 중",completed:"결과 수신",error:"오류",interrupted:"결과 미확인"};
      for(const row of legacy.slice(-80)){
        const item=node("li",undefined,"progress-record progress-legacy-record"),heading=node("div",undefined,"progress-record-heading");
        heading.append(node("span","도구 요약","progress-kind"),node("strong",clean(row.action,100)||clean(row.tool,160)||"도구 사용"),node("span",states[row.state]||"기록됨","progress-tool"));item.append(heading);
        if(row.tool)item.append(node("span",clean(row.tool,160),"progress-tool"));
        if(row.target)item.append(node("pre",clean(row.target,240),"progress-record-text"));
        if(row.parentToolUseId)item.append(node("small","추가 작업자의 활동","progress-child"));list.append(item);
      }
      view.legacy.append(list);
    }
    view.state.textContent=failure||(!rows.length?(pending?"진행 내용을 불러오고 있어요.":legacy?.length||pageNotice?"":filter?"이 요청에 저장된 상세 진행 기록이 아직 없습니다.":"저장된 상세 진행 기록이 아직 없습니다. 이전 버전에서 수행한 내용은 복원되지 않습니다."):pending?"진행 내용을 확인하고 있어요.":"");
    view.retry.hidden=!failure;view.retry.disabled=!!pending;
  }
  function mount(){
    if(view)return;
    const toolbar=node("div",undefined,"progress-toolbar"),scope=node("span",undefined,"progress-scope");
    const button=text=>{const result=node("button",text,"text-button");result.type="button";return result;};
    const all=button("전체 기록"),current=button("이번 요청"),latest=button("최신 내용 보기"),older=button("이전 기록 50개 보기"),retry=button("다시 확인");
    toolbar.append(scope,all,current,latest);const note=node("p",undefined,"progress-note"),state=node("p",undefined,"progress-state");state.setAttribute("role","status");
    const scroller=node("div",undefined,"progress-scroll"),list=node("ol",undefined,"progress-records"),legacy=node("div",undefined,"progress-legacy");scroller.tabIndex=0;scroller.setAttribute("aria-label","시간순 진행 내용");scroller.append(list,legacy);
    view={toolbar,scope,all,current,latest,older,retry,note,state,scroller,list,legacy};body.append(toolbar,note,state,retry,older,scroller);
    all.onclick=()=>open();current.onclick=()=>open(currentRun);latest.onclick=()=>{olderWindow=false;newPending=false;void load("latest",true);};
    older.onclick=()=>void load("older");retry.onclick=()=>void load("latest",true);renderState();
  }
  async function load(mode="refresh",reveal=false){
    if(!expanded||!sessionId||!hooks||pending)return;
    if(mode==="older"&&(!hasMore||!nextBefore))return;
    // Reading older output never jumps to the tail when a new event arrives.
    if(mode==="refresh"&&(olderWindow||!isBottom())){newPending=true;renderState();return;}
    const id=sessionId,ticket=generation,run=filter,before=mode==="older"?nextBefore:null;
    const request=new AbortController();controller=request;pending=request;dirty=false;failure="";renderState();
    let query=`/api/progress?id=${encodeURIComponent(id)}&limit=${PAGE}`;if(run)query+=`&runId=${encodeURIComponent(run)}`;if(before!==null)query+=`&before=${before}`;
    try{
      const value=await hooks.api(query,request.signal);
      if(ticket!==generation||sessionId!==id||filter!==run||!expanded||request.signal.aborted)return;
      const result=page(value);
      loaded=true;
      if(before!==null&&result.records.some(row=>row.seq>=before))throw Error("이전 진행 기록의 위치를 확인하지 못했어요.");
      if(value.progress){acceptMetadata(value.progress);updateSummary();}
      pageNotice=clean(value.notice,1000);
      // The user may start reading while the asynchronous request is in flight.
      if(mode==="refresh"&&!isBottom()){newPending=true;renderState();return;}
      if(mode==="older"){
        const merged=new Map([...result.records,...rows].map(row=>[row.seq,row]));rows=[...merged.values()].sort((a,b)=>a.seq-b.seq);
        olderWindow=true;if(rows.length>MAX_ROWS)rows=rows.slice(0,MAX_ROWS);
      }else{rows=result.records;olderWindow=false;newPending=false;}
      nextBefore=result.nextBefore;hasMore=result.hasMore;pageTruncated=result.truncated;renderRows(mode==="older"?"older":"latest");
      if(reveal)requestAnimationFrame(()=>{if(ticket===generation&&expanded&&sessionId===id&&filter===run)revealWithinWorkArea();});
    }catch(error){if(ticket===generation&&expanded&&!request.signal.aborted){failure=clean(error.message,500)||"진행 내용을 불러오지 못했어요.";renderState();}}
    finally{if(pending===request){pending=null;controller=null;renderState();if(dirty)schedule();}}
  }
  function schedule(){if(!expanded||timer!==null)return;if(pending){dirty=true;return;}timer=setTimeout(()=>{timer=null;void load();},200);}
  function acceptMetadata(value){
    if(!value||typeof value!=="object")return false;const next=normalizeMetadata(value);
    if(next.revision<metadata.revision||next.revision===metadata.revision&&next.lastSeq<metadata.lastSeq)return false;
    const advanced=next.lastSeq>metadata.lastSeq||next.revision>metadata.revision;metadata=next;return advanced;
  }
  function changed(value){const advanced=acceptMetadata(value);updateSummary();renderState();if(advanced&&expanded)schedule();}
  function activate(){if(globalThis.WorkspaceCapabilities?.isOpen()){close();return;}if(expanded||!sessionId)return;globalThis.WorkspaceStream?.pause();expanded=true;mount();void load("latest",true);}
  function open(runId=null){
    if(!sessionId||!detail||globalThis.WorkspaceCapabilities?.isOpen())return false;const next=clean(runId,160)||null;
    if(filter!==next){close();filter=next;}detail.open=true;updateSummary();activate();summary.focus({preventScroll:true});revealWithinWorkArea();return true;
  }
  function attach(value){
    hooks=value;host=document.getElementById("progress-view");if(!host)return;
    detail=node("details",undefined,"progress-detail");summary=node("summary",undefined,"progress-summary");count=node("span",undefined,"progress-count");body=node("div",undefined,"progress-body");
    disclosure=node("span","펼치기","disclosure-action");
    summary.append(node("span","≋","progress-symbol"),node("span","진행 내용","progress-title"),count,disclosure);summary.firstChild.setAttribute("aria-hidden","true");detail.append(summary,body);host.replaceChildren(detail);
    detail.ontoggle=()=>{updateDisclosure();if(detail.open)activate();else if(expanded)close();};
    const opener=document.getElementById("progress-open");if(opener)opener.onclick=()=>open();updateSummary();
  }
  return {attach,reset,open,close,metadata:changed,currentRun:value=>{currentRun=clean(value,160)||null;renderState();}};
})();
