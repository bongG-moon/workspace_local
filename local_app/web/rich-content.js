"use strict";

// Presentation only. Model text is never HTML, and displayed code is never executed.
globalThis.WorkspaceRichContent = (() => {
  const LIMITS = {text:100000, rows:100, columns:30, images:4, imageRequests:2, records:120, imageData:11200000};
  const make = (tag, text, className) => {const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(className)n.className=className;return n;};
  const button = (text, title) => {const n=make("button",text,"rich-action");n.type="button";n.title=title||text;return n;};
  const nameOf = path => String(path).split(/[\\/]/).pop() || "파일";
  const sessionIsCurrent = sid => !sid || typeof active === "undefined" || active?.id === sid;
  let generation=0, requests=0, observer=null, images=[], queue=[], mounted=[];
  function localPath(raw, workspace="") {
    let path=String(raw||"").trim().replace(/^<|>$/g,"");
    // No network shares, URLs, data URIs, or device paths can initiate preview reads.
    if(!path || path.length>4096 || /[\u0000-\u001f]/.test(path) || /^(?:\\\\|\/\/)/.test(path))return null;
    if(/^[a-z][a-z\d+.-]*:/i.test(path)&&! /^[a-z]:[\\/]/i.test(path))return null;
    if(!/^(?:[a-z]:[\\/]|\/)/i.test(path))path=workspace?String(workspace).replace(/[\\/]$/,"")+"/"+path:path;
    return path;
  }
  function safeUrl(raw) {try {const url=new URL(raw);return ["https:","http:"].includes(url.protocol)?url.href:null;}catch(_){return null;}}
  function context(options={}) {return {...options,sessionId:options.sessionId||(typeof active!=="undefined"?active?.id:null),workspace:options.workspace||(typeof active!=="undefined"?active?.workspace:"")||""};}
  function openFile(path, options) {
    if(!sessionIsCurrent(options.sessionId))return;
    if(options.onOpenFile)return options.onOpenFile(path,options.sessionId);
    if(typeof preview==="function")return preview(path);
  }
  async function copy(text, control) {
    const original=control.textContent;
    try {
      if(!globalThis.navigator?.clipboard?.writeText)throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);control.textContent="복사됨";
    } catch (_) {control.textContent="복사 실패";control.title="텍스트를 선택해 Ctrl+C로 복사해 주세요.";}
    setTimeout(()=>{if(control.isConnected)control.textContent=original;},1800);
  }
  function appendInline(parent, source, options={}, depth=0) {
    const text=String(source||"");if(depth>2){parent.append(make("span",text));return;}
    const pattern=/(`+)([^\n]*?)\1|\*\*([^*\n]+)\*\*|(!?)\[([^\]\n]*)\]\((<[^>\n]+>|[^)\n]+)\)/g;
    let offset=0;
    for(const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(offset,match.index)));offset=match.index+match[0].length;
      if(match[1]){parent.append(make("code",match[2],"rich-inline-code"));continue;}
      if(match[3]){const strong=make("strong");appendInline(strong,match[3],options,depth+1);parent.append(strong);continue;}
      const destination=match[6].replace(/^<|>$/g,""), remote=safeUrl(destination);
      if(match[4]) {
        if(remote){const link=make("a",match[5]||"외부 이미지 보기","rich-external-image");link.href=remote;link.target="_blank";link.rel="noopener noreferrer";link.title="클릭하면 외부 브라우저에서 엽니다";parent.append(link);}
        else {const path=localPath(destination,options.workspace);if(path)image(parent,path,match[5],options);else parent.append(document.createTextNode(match[0]));}
      } else if(remote) {const link=make("a",match[5]||destination);link.href=remote;link.target="_blank";link.rel="noopener noreferrer";parent.append(link);}
      else {const path=localPath(destination,options.workspace);if(path&&options.sessionId){const link=button(match[5]||nameOf(path),path);link.className="rich-file-link";link.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{});parent.append(link);}else parent.append(document.createTextNode(match[0]));}
    }
    parent.append(document.createTextNode(text.slice(offset)));
  }
  const KEYWORDS = new Set(("as async await break case catch class const continue def del elif else except export extends false finally for from function if import in instanceof interface let new None null of pass raise return select SELECT from FROM where WHERE join JOIN on ON group GROUP by BY order ORDER insert INSERT into INTO update UPDATE set SET delete DELETE create CREATE table TABLE try true True False type typeof var void while with yield and or not print echo then fi do done param Write-Output").split(" "));
  function syntax(parent, text, language, budget={remaining:2000}) {
    if(!/^(python|py|javascript|js|typescript|ts|jsx|tsx|json|bash|sh|shell|powershell|ps1|sql)$/i.test(language)){parent.textContent=text;return;}
    const shell=/^(python|py|bash|sh|shell|powershell|ps1)$/i.test(language);
    const sql=/^sql$/i.test(language);
    const pattern=/("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\/[^\n]*|#[^\n]*|--[^\n]*|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][\w-]*\b)/g;
    let at=0,count=0;
    for(const match of text.matchAll(pattern)) {
      if(++count>2000||budget.remaining<=0)break;
      budget.remaining--;
      parent.append(document.createTextNode(text.slice(at,match.index)));at=match.index+match[0].length;
      const value=match[0];let kind="";
      if(/^['"`]/.test(value))kind="string";
      else if(value.startsWith("//") || (shell&&value.startsWith("#")) || (sql&&value.startsWith("--")))kind="comment";
      else if(/^\d/.test(value))kind="number";else if(KEYWORDS.has(value))kind="keyword";
      parent.append(kind?make("span",value,"rich-token-"+kind):document.createTextNode(value));
    }
    parent.append(document.createTextNode(text.slice(at)));
  }
  function code(parent, value, language="", options={}) {
    const source=String(value??""), text=source.slice(0,LIMITS.text), lang=String(language||"").trim().split(/\s/)[0].slice(0,24);
    const card=make("section",null,"rich-code"), header=make("div",null,"rich-content-bar"), actions=make("div",null,"rich-content-actions");
    header.append(make("span",options.label||lang||"코드","rich-language"));
    const copyButton=button("복사","코드 복사"), fold=button("접기","코드 접기"), pre=make("pre"), content=make("code");
    copyButton.onclick=()=>copy(text,copyButton);syntax(content,text,lang,options.highlightBudget);pre.append(content);
    let collapsed=!!options.collapsed;const update=()=>{pre.hidden=collapsed;fold.textContent=collapsed?"코드 보기":"접기";fold.setAttribute("aria-expanded",String(!collapsed));};
    fold.onclick=()=>{collapsed=!collapsed;update();};update();actions.append(copyButton,fold);header.append(actions);card.append(header,pre);
    if(source.length>LIMITS.text)card.append(make("p","긴 코드의 앞부분만 표시했습니다. 복사는 표시된 부분에 적용됩니다.","rich-limit"));
    parent.append(card);return card;
  }
  function splitCells(line) {
    let text=String(line).trim();if(text.startsWith("|"))text=text.slice(1);if(text.endsWith("|")&&!text.endsWith("\\|"))text=text.slice(0,-1);
    const result=[];let cell="",ticks=0;
    for(let i=0;i<text.length;i++) {
      const c=text[i];if(c==="\\"&&text[i+1]==="|"){cell+="|";i++;continue;}
      if(c==="`"){let length=1;while(text[i+length]==="`")length++;ticks=ticks===length?0:(ticks||length);cell+="`".repeat(length);i+=length-1;continue;}
      if(c==="|"&&!ticks){result.push(cell.trim());cell="";}else cell+=c;
    }
    result.push(cell.trim());return result;
  }
  function table(parent, data={}, options={}) {
    const allColumns=Array.isArray(data.columns)?data.columns:[], allRows=Array.isArray(data.rows)?data.rows:[];
    const columns=allColumns.slice(0,LIMITS.columns).map(value=>String(value??"")), rows=allRows.slice(0,LIMITS.rows).map(row=>columns.map((_,i)=>String((Array.isArray(row)?row[i]:null)??"")));
    const card=make("section",null,"rich-table"), header=make("div",null,"rich-content-bar"), copyButton=button("표 복사","화면에 표시된 표를 탭 구분 형식으로 복사");
    const truncated=!!data.truncated||allRows.length>LIMITS.rows||allColumns.length>LIMITS.columns;
    header.append(make("span",data.caption||`표 · ${rows.length}행${truncated?" 미리보기":""}`,"rich-language"),copyButton);
    // TSV keeps embedded separators/newlines inside quoted cells for spreadsheet paste.
    const tsvCell=value=>/[\t\r\n"]/.test(value)?'"'+value.replace(/"/g,'""')+'"':value;
    copyButton.onclick=()=>copy([columns,...rows].map(row=>row.map(tsvCell).join("\t")).join("\n"),copyButton);
    const scroll=make("div",null,"rich-table-scroll"), grid=make("table"), thead=make("thead"), head=make("tr"), tbody=make("tbody");
    scroll.tabIndex=0;scroll.setAttribute("role","region");scroll.setAttribute("aria-label",data.caption||"표. 가로로 스크롤할 수 있습니다");
    for(const label of columns){const th=make("th");th.setAttribute("scope","col");if(options.markdown)appendInline(th,label,options);else th.textContent=label;head.append(th);}
    thead.append(head);grid.append(thead);
    for(const row of rows){const tr=make("tr");for(const value of row){const td=make("td");if(options.markdown)appendInline(td,value,options);else td.textContent=value;tr.append(td);}tbody.append(tr);}
    grid.append(tbody);scroll.append(grid);card.append(header,scroll);
    if(truncated)card.append(make("p",`최대 ${LIMITS.rows}행 · ${LIMITS.columns}열까지 표시합니다. 복사는 표시된 범위에 적용됩니다.`,"rich-limit"));
    parent.append(card);return card;
  }
  function release(record) {if(record.img){record.img.removeAttribute("src");record.img.remove();record.img=null;}mounted=mounted.filter(item=>item!==record);record.loaded=false;record.status.textContent="미리보기 열기";}
  function prune() {
    // Cards are often built in a detached article and attached after render().
    // Do not drop their observer while the same article adds its file chips.
    for(const record of images){record.seenConnected ||= record.node.isConnected;if(record.seenConnected&&!record.node.isConnected){release(record);observer?.unobserve(record.node);}}
    images=images.filter(record=>!record.seenConnected||record.node.isConnected);queue=queue.filter(record=>!record.seenConnected||record.node.isConnected);
  }
  function current(record) {return record.epoch===generation&&record.node.isConnected&&sessionIsCurrent(record.options.sessionId);}
  function ensureObserver() {
    if(observer||!globalThis.IntersectionObserver)return;
    observer=new IntersectionObserver(entries=>{
      for(const entry of entries){const record=images.find(row=>row.node===entry.target);if(!record)continue;record.seenConnected ||= record.node.isConnected;record.visible=entry.isIntersecting;if(record.visible)enqueue(record);else if(record.loaded)release(record);}
    },{rootMargin:"120px 0px"});
  }
  function enqueue(record) {if(!current(record)||record.pending||record.loaded||record.failed)return;if(!queue.includes(record))queue.push(record);pump();}
  function pump() {
    while(requests<LIMITS.imageRequests&&queue.length){const record=queue.shift();if(!current(record)||!record.visible)continue;record.pending=true;requests++;loadImage(record).finally(()=>{record.pending=false;requests--;pump();});}
  }
  async function loadImage(record) {
    try {
      const result=await api(`/api/preview?id=${encodeURIComponent(record.options.sessionId)}&path=${encodeURIComponent(record.path)}&thumbnail=1`);
      if(!current(record)||!record.visible)return;
      if(result.kind!=="image"||typeof result.data!=="string"||result.data.length>LIMITS.imageData||!/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+$/.test(result.data))throw new Error("no inline preview");
      while(mounted.length>=LIMITS.images) {const oldest=mounted.find(item=>!item.visible)||mounted[0];release(oldest);}
      const img=make("img");img.alt=record.alt||nameOf(record.path);img.decoding="async";img.loading="lazy";img.src=result.data;
      img.onerror=()=>{release(record);record.failed=true;record.status.textContent="미리보기 열기";};
      img.onload=()=>{if(img.naturalWidth*img.naturalHeight>16000000){release(record);record.failed=true;record.status.textContent="파일 열어 보기";}};
      record.media.append(img);record.img=img;record.loaded=true;mounted.push(record);record.status.textContent="확대해서 보기";
    } catch (_) {if(current(record)){record.failed=true;record.status.textContent="파일 열어 보기";}}
  }
  function image(parent,path,alt,options) {
    if(!options.sessionId||images.length>=LIMITS.records){fileChip(parent,path,options);return;}
    const node=button("",`이미지 확대: ${alt||nameOf(path)}`);node.className="rich-image";const media=make("span",null,"rich-image-media"),label=make("span",null,"rich-image-label"),status=make("small","미리보기 준비 중");
    label.append(make("span",alt||nameOf(path)),status);node.append(media,label);node.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{status.textContent="파일을 열 수 없습니다";});parent.append(node);
    const record={node,path,alt,options,label,media,status,epoch:generation,seenConnected:node.isConnected,visible:!globalThis.IntersectionObserver,loaded:false,pending:false,failed:false,img:null};images.push(record);ensureObserver();
    if(observer)observer.observe(node);else setTimeout(()=>enqueue(record),0);
  }
  function fileChip(parent,path,options) {const chip=button(nameOf(path),path);chip.className="rich-file-chip";chip.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{chip.title="파일을 열 수 없습니다";});parent.append(chip);}
  function files(parent,paths,options={}) {
    options=context(options);prune();const wrap=make("div",null,"rich-files"),unique=[...new Set((paths||[]).map(item=>typeof item==="string"?item:item?.path).filter(Boolean))];
    for(const raw of unique.slice(0,24)){const path=localPath(raw,options.workspace);if(!path)continue;if(/\.(?:png|jpe?g|webp)$/i.test(path))image(wrap,path,"",options);else fileChip(wrap,path,options);}
    if(unique.length>24)wrap.append(make("span",`그 외 ${unique.length-24}개 파일`,"rich-limit"));parent.append(wrap);return wrap;
  }
  function render(parent, source, options={}) {
    options=context(options);parent.replaceChildren();prune();const full=String(source??""),bounded=full.slice(0,LIMITS.text),lines=bounded.split(/\r?\n/);let i=0,blocks=0;
    // Compact rendering is bounded by structure as well as bytes. Pathological
    // tiny fences/cells must not create thousands of controls or syntax spans.
    if(lines.length>1200||(bounded.match(/\||`|\*\*|\[/g)||[]).length>6000||lines.filter(line=>/^\s*(?:`{3,}|~{3,})/.test(line)).length>80) {
      code(parent,full,"text",{label:"긴 메시지"});parent.append(make("p","내용이 길어 서식 없이 표시합니다.","rich-limit"));return true;
    }
    const highlightBudget={remaining:6000};
    while(i<lines.length) {
      const line=lines[i];if(!line.trim()){i++;continue;}
      if(++blocks>200){code(parent,lines.slice(i).join("\n"),"text",{label:"이어서 보기"});parent.append(make("p","나머지 내용은 서식 없이 표시합니다.","rich-limit"));break;}
      const fence=line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if(fence){const block=[],closing=new RegExp("^\\s*"+fence[1][0]+"{"+fence[1].length+",}\\s*$");i++;while(i<lines.length&&!closing.test(lines[i]))block.push(lines[i++]);if(i<lines.length)i++;code(parent,block.join("\n"),fence[2].trim(),{highlightBudget});continue;}
      if(i+1<lines.length&&line.includes("|")&&splitCells(lines[i+1]).every(cell=>/^:?-{3,}:?$/.test(cell))) {
        const columns=splitCells(line),rows=[];let total=0;i+=2;
        while(i<lines.length&&lines[i].includes("|")&&lines[i].trim()){if(rows.length<LIMITS.rows)rows.push(splitCells(lines[i]));total++;i++;}
        table(parent,{columns,rows,truncated:total>LIMITS.rows},{...options,markdown:true});continue;
      }
      const heading=line.match(/^(#{1,6})\s+(.+)$/);if(heading){const h=make("h"+Math.min(heading[1].length+2,6));appendInline(h,heading[2],options);parent.append(h);i++;continue;}
      if(/^\s*(?:[-*+] |\d+[.)] )/.test(line)){const ordered=/^\s*\d/.test(line),list=make(ordered?"ol":"ul");while(i<lines.length&&/^\s*(?:[-*+] |\d+[.)] )/.test(lines[i])){const li=make("li");appendInline(li,lines[i++].replace(/^\s*(?:[-*+]|\d+[.)])\s+/,""),options);list.append(li);}parent.append(list);continue;}
      if(/^\s*>/.test(line)){const quote=make("blockquote");appendInline(quote,line.replace(/^\s*>\s?/,""),options);parent.append(quote);i++;continue;}
      if(/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)){parent.append(make("hr"));i++;continue;}
      const p=make("p");appendInline(p,line,options);parent.append(p);i++;
    }
    if(full.length>LIMITS.text)parent.append(make("p","긴 메시지의 앞부분만 표시합니다.","rich-limit"));return true;
  }
  function reset() {generation++;queue=[];observer?.disconnect();observer=null;for(const record of images)release(record);images=[];mounted=[];}
  return {render,code,table,files,reset,dispose:reset,limits:{...LIMITS}};
})();
