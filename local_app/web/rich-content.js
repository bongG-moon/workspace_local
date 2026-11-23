"use strict";

// Presentation only. Model text is never HTML, and displayed code is never executed.
globalThis.WorkspaceRichContent = (() => {
  const LIMITS = {text:100000, rows:100, columns:30, images:4, imageRequests:2, records:120, imageData:11200000};
  const make = (tag, text, className) => {const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(className)n.className=className;return n;};
  const linkIcon = name => {
    const create=tag=>document.createElementNS("http://www.w3.org/2000/svg",tag);
    const icon=create("svg"),use=create("use");icon.setAttribute("class","rich-link-icon");icon.setAttribute("aria-hidden","true");icon.setAttribute("focusable","false");use.setAttribute("href",`#i-${name}`);icon.append(use);return icon;
  };
  const button = (text, title) => {const n=make("button",text,"rich-action");n.type="button";n.title=title||text;return n;};
  const nameOf = path => String(path).split(/[\\/]/).pop() || "파일";
  const sessionIsCurrent = sid => !sid || typeof active === "undefined" || active?.id === sid;
  let generation=0, requests=0, observer=null, images=[], queue=[], mounted=[];
  const pendingImages=new Set();
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
    const text=String(source||"");if(depth>2){parent.append(make("span",text));return text;}
    const pattern=/(`+)([^\n]*?)\1|\*\*([^*\n]+)\*\*|(!?)\[([^\]\n]*)\]\((<[^>\n]+>|[^)\n]+)\)/g;
    // Project copy text during the same parse that creates the displayed nodes.
    // Do not render a second hidden tree: images can own observers and requests.
    const plain=[];let offset=0;
    for(const match of text.matchAll(pattern)) {
      const prefix=text.slice(offset,match.index);parent.append(document.createTextNode(prefix));plain.push(prefix);offset=match.index+match[0].length;
      if(match[1]){parent.append(make("code",match[2],"rich-inline-code"));plain.push(match[2]);continue;}
      if(match[3]){const strong=make("strong");plain.push(appendInline(strong,match[3],options,depth+1));parent.append(strong);continue;}
      const destination=match[6].replace(/^<|>$/g,""), remote=safeUrl(destination);
      if(match[4]) {
        if(remote){const label=match[5]||"외부 이미지 보기",link=make("a",null,"rich-external-image");link.append(linkIcon("globe"),document.createTextNode(label));link.href=remote;link.target="_blank";link.rel="noopener noreferrer";link.title="클릭하면 외부 브라우저에서 엽니다";parent.append(link);plain.push(label);}
        else {const path=localPath(destination,options.workspace);if(path)plain.push(image(parent,path,match[5],options));else {parent.append(document.createTextNode(match[0]));plain.push(match[0]);}}
      } else if(remote) {const label=match[5]||destination,link=make("a",label);link.href=remote;link.target="_blank";link.rel="noopener noreferrer";parent.append(link);plain.push(label);}
      else {const path=localPath(destination,options.workspace);if(path&&options.sessionId){const label=match[5]||nameOf(path),link=button(label,path);link.className="rich-file-link";link.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{});parent.append(link);plain.push(label);}else {parent.append(document.createTextNode(match[0]));plain.push(match[0]);}}
    }
    const suffix=text.slice(offset);parent.append(document.createTextNode(suffix));plain.push(suffix);return plain.join("");
  }
  const KEYWORDS = new Set(("as async await break case catch class const continue def del elif else except export extends false finally for from function if import in instanceof interface let new None null of pass raise return select SELECT from FROM where WHERE join JOIN on ON group GROUP by BY order ORDER insert INSERT into INTO update UPDATE set SET delete DELETE create CREATE table TABLE try true True False type typeof var void while with yield and or not print echo then fi do done param Write-Output").split(" "));
  function syntax(parent, text, language, budget={remaining:2000}, prefix="") {
    const lang=String(language||"").toLowerCase();
    if(!/^(html|htm|xml|svg|css|scss|python|py|javascript|js|typescript|ts|jsx|tsx|json|bash|sh|shell|powershell|ps1|sql)$/.test(lang)){parent.textContent=text;return;}
    // Scan a finite prefix for page context without creating hidden DOM. Only
    // visible color spans consume the existing per-block/per-message budget.
    const source=prefix+text, visible=prefix.length;
    let at=visible,count=0;
    const emit=(start,end,kind)=>{
      if(end<=visible)return true;
      if(count>=2000||budget.remaining<=0)return false;
      start=Math.max(start,visible);
      if(start>at)parent.append(document.createTextNode(source.slice(at,start)));
      parent.append(make("span",source.slice(start,end),"rich-token-"+kind));
      at=end;count++;budget.remaining--;return true;
    };
    const matchAt=(pattern,index)=>{pattern.lastIndex=index;return pattern.exec(source);};
    const word=/[A-Za-z_$][\w$-]*/y,number=/(?:0[xX][\da-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?)/y;
    const nextNonspace=index=>{while(index<source.length&&/\s/.test(source[index]))index++;return index;};
    const quoted=(index,end,delimiter,multiline=false,doubled=false,escape=true)=>{
      let cursor=index+delimiter.length;
      while(cursor<end){
        if(escape&&source[cursor]==="\\"){cursor=Math.min(end,cursor+2);continue;}
        if(source.startsWith(delimiter,cursor)){
          if(doubled&&source.startsWith(delimiter,cursor+delimiter.length)){cursor+=delimiter.length*2;continue;}
          return cursor+delimiter.length;
        }
        if(!multiline&&/[\r\n]/.test(source[cursor]))return cursor;
        cursor++;
      }
      return end;
    };
    const through=(needle,index,end)=>{const found=source.indexOf(needle,index);return found<0||found>=end?end:found+needle.length;};
    function program(start,end,kind){
      const python=/^(python|py)$/.test(kind),shell=/^(bash|sh|shell|powershell|ps1)$/.test(kind),sql=kind==="sql";
      const js=/^(javascript|js|typescript|ts|jsx|tsx|json)$/.test(kind);
      for(let index=start;index<end;){
        const ch=source[index];let finish=index+1,token="",match;
        if((js||sql)&&source.startsWith("/*",index)){finish=through("*/",index+2,end);token="comment";}
        else if((js&&source.startsWith("//",index))||((python||shell)&&ch==="#")||(sql&&source.startsWith("--",index))){const line=source.indexOf("\n",index);finish=line<0||line>=end?end:line;token="comment";}
        else if((kind==="powershell"||kind==="ps1")&&source.startsWith("<#",index)){finish=through("#>",index+2,end);token="comment";}
        else if(ch==='"'||ch==="'"||((js||shell)&&ch==='`')){
          const triple=python&&source.startsWith(ch.repeat(3),index),delimiter=triple?ch.repeat(3):ch;
          finish=quoted(index,end,delimiter,triple||ch==='`'||sql,sql);token="string";
        }else if((match=matchAt(number,index))){finish=Math.min(end,index+match[0].length);token="number";}
        else if((match=matchAt(word,index))){
          finish=Math.min(end,index+match[0].length);const value=match[0],next=nextNonspace(finish);
          if(KEYWORDS.has(value)||(sql&&KEYWORDS.has(value.toUpperCase())))token="keyword";
          else if(source[next]==="(")token="function";
          else if(js&&source[next]===":")token="property";
        }else if(/[=+*\/<>!&|?:%~^\-]/.test(ch))token="operator";
        if(token&&!emit(index,finish,token))return false;
        index=Math.max(index+1,finish);
      }
      return true;
    }
    function stylesheet(start,end){
      let value=false,depth=0;
      for(let index=start;index<end;){
        const ch=source[index];let finish=index+1,token="",match;
        if(source.startsWith("/*",index)){finish=through("*/",index+2,end);token="comment";}
        else if(lang==="scss"&&source.startsWith("//",index)){const line=source.indexOf("\n",index);finish=line<0||line>=end?end:line;token="comment";}
        else if(ch==='"'||ch==="'"){finish=quoted(index,end,ch,true);token="string";}
        else if(ch==="{"){depth++;value=false;}
        else if(ch==="}"){depth=Math.max(0,depth-1);value=false;}
        else if(ch===";"){value=false;}
        else if(ch===":"&&depth>0&&value){token="operator";}
        else if(ch==="@"&&(match=matchAt(word,index+1))){finish=index+1+match[0].length;token="keyword";}
        else if((ch==="."||ch==="#")&&!value&&(match=matchAt(word,index+1))){finish=index+1+match[0].length;token="selector";}
        else if(ch==="#"&&value&&(match=matchAt(/[\da-fA-F]{3,8}\b/y,index+1))){finish=index+1+match[0].length;token="number";}
        else if((match=matchAt(number,index))){
          finish=Math.min(end,index+match[0].length);
          const unit=matchAt(/(?:%|[A-Za-z]+)/y,finish);if(unit)finish=Math.min(end,finish+unit[0].length);
          token="number";
        }else if((match=matchAt(/(?:--|\$)?[A-Za-z_][\w-]*/y,index))){
          finish=Math.min(end,index+match[0].length);const next=nextNonspace(finish);
          if(depth>0&&!value&&source[next]===":"){token="property";value=true;}
          else if(source[next]==="(")token="function";
          else token=value?(match[0].startsWith("--")?"property":"keyword"):"selector";
        }
        if(token&&!emit(index,finish,token))return false;
        index=Math.max(index+1,finish);
      }
      return true;
    }
    function markup(){
      const html=lang==="html"||lang==="htm";
      for(let index=0;index<source.length;){
        if(source.startsWith("<!--",index)){const finish=through("-->",index+4,source.length);if(!emit(index,finish,"comment"))return;index=finish;continue;}
        if(source.startsWith("<![CDATA[",index)){index=through("]]>",index+9,source.length);continue;}
        if(source[index]!=="<"){index++;continue;}
        const head=matchAt(/<\/?[A-Za-z][\w:.-]*|<![A-Za-z]+|<\?[A-Za-z][\w:.-]*/y,index);
        if(!head){index++;continue;}
        const tag=head[0],name=tag.replace(/^<\/?/,'').toLowerCase(),closing=tag.startsWith("</");
        if(!emit(index,index+tag.length,"tag"))return;index+=tag.length;
        let closed=false,selfClosed=false,expectValue=false;
        while(index<source.length){
          if(source[index]===">"||source.startsWith("/>",index)||source.startsWith("?>",index)){
            const width=source[index]===">"?1:2;selfClosed=width===2;closed=true;
            if(!emit(index,index+width,"tag"))return;index+=width;break;
          }
          let finish=index+1,kind="",match;
          if(source[index]==='"'||source[index]==="'"){finish=quoted(index,source.length,source[index],true,false,false);kind="string";expectValue=false;}
          else if(source[index]==="="){kind="operator";expectValue=true;}
          else if((match=matchAt(/[^\s=<>"'`/]+/y,index))){finish=index+match[0].length;kind=expectValue?"string":"attribute";expectValue=false;}
          if(kind&&!emit(index,finish,kind))return;
          index=Math.max(index+1,finish);
        }
        if(html&&closed&&!closing&&!selfClosed&&(name==="style"||name==="script")){
          const boundary=new RegExp("</"+name+"(?=[\\s/>])","ig");boundary.lastIndex=index;
          const close=boundary.exec(source),end=close?close.index:source.length;
          if(!(name==="style"?stylesheet(index,end):program(index,end,"javascript")))return;
          index=end;
        }
      }
    }
    if(/^(html|htm|xml|svg)$/.test(lang))markup();
    else if(/^(css|scss)$/.test(lang))stylesheet(0,source.length);
    else program(0,source.length,lang);
    if(at<source.length)parent.append(document.createTextNode(source.slice(at)));
  }
  function code(parent, value, language="", options={}) {
    const source=String(value??""), text=source.slice(0,LIMITS.text), lang=String(language||"").trim().split(/\s/)[0].slice(0,24);
    const card=make("section",null,"rich-code"), header=make("div",null,"rich-content-bar"), actions=make("div",null,"rich-content-actions");
    header.append(make("span",options.label||lang||"코드","rich-language"));
    const copyButton=button("복사","코드 복사"), fold=button("접기","코드 접기"), pre=make("pre"), content=make("code");
    copyButton.onclick=()=>copy(text,copyButton);
    const pager = options.lineNumbers ? numberedSource(pre, content, text, lang) : null;
    if (!options.lineNumbers) { syntax(content,text,lang,options.highlightBudget); pre.append(content); }
    let collapsed=!!options.collapsed;const update=()=>{pre.hidden=collapsed;if(pager)pager.hidden=collapsed;fold.textContent=collapsed?"코드 보기":"접기";fold.setAttribute("aria-expanded",String(!collapsed));};
    fold.onclick=()=>{collapsed=!collapsed;update();};update();actions.append(copyButton,fold);header.append(actions);card.append(header,pre);
    if (pager) card.append(pager);
    if(source.length>LIMITS.text)card.append(make("p","긴 코드의 앞부분만 표시했습니다. 복사는 표시된 부분에 적용됩니다.","rich-limit"));
    parent.append(card);return card;
  }
  function numberedSource(pre, content, text, lang) {
    const pageSize = 500;
    const lines = text.replace(/\r\n?/g,"\n").split("\n");
    const headings = new Set();
    if (lang === "markdown") {
      let fence = "";
      for (const [i,line] of lines.entries()) {
        const mark = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
        if (mark) {
          if (!fence) fence = mark[1];
          else if (!mark[2].trim() && fence[0] === mark[1][0] && mark[1].length >= fence.length) fence = "";
        } else if (!fence && /^ {0,3}#{1,6}(?:\s|$)/.test(line)) headings.add(i);
      }
    }
    const numbers = make("span", null, "source-line-numbers");
    numbers.setAttribute("aria-hidden","true");
    pre.className = "rich-numbered-source";
    pre.tabIndex = 0;
    pre.setAttribute("role","region");
    pre.setAttribute("aria-label","파일 원문");
    pre.append(numbers, content);
    let start = 0;
    const paged = lines.length > pageSize;
    const pager = paged ? make("div", null, "source-pages") : null;
    const status = paged ? make("span") : null;
    const previous = paged ? button("이전","이전 500줄 보기") : null;
    const next = paged ? button("다음","다음 500줄 보기") : null;
    function drawPage() {
      const rows = lines.slice(start,start + pageSize);
      const end = start + rows.length;
      numbers.setAttribute("data-numbers",rows.map((_,i) => start + i + 1).join("\n"));
      content.replaceChildren();
      if (headings.size) {
        let plain = [];
        rows.forEach((line,i) => {
          const fragment = line + (i + 1 < rows.length ? "\n" : "");
          if (headings.has(start+i)) {
            if (plain.length) content.append(document.createTextNode(plain.join("")));
            plain = [];
            content.append(make("span", fragment, "source-markdown-heading"));
          } else plain.push(fragment);
        });
        if (plain.length) content.append(document.createTextNode(plain.join("")));
      } else syntax(content,rows.join("\n"),lang,undefined,start?lines.slice(0,start).join("\n")+"\n":"");
      pre.scrollTop = 0;
      pre.scrollLeft = 0;
      if (paged) {
        status.textContent = `${start + 1}–${end} / ${lines.length}줄`;
        previous.disabled = start === 0;
        next.disabled = end === lines.length;
      }
    }
    if (paged) {
      status.setAttribute("aria-live","polite");
      previous.onclick = () => { start = Math.max(0,start-pageSize); drawPage(); if(previous.disabled)next.focus(); };
      next.onclick = () => { start += pageSize; drawPage(); if(next.disabled)previous.focus(); };
      pager.append(status,previous,next);
    }
    drawPage();
    return pager;
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
  function readChart(source) {
    if (source.length > 24000) return null;
    let data;
    try { data = JSON.parse(source); } catch { return null; }
    const label = (value, max) => typeof value === "string" && value.trim().length > 0 &&
      value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
    if (!data || !["bar", "line"].includes(data.type) || !label(data.title, 120) ||
        !Array.isArray(data.labels) || data.labels.length < (data.type === "line" ? 2 : 1) ||
        data.labels.length > 100 || !data.labels.every(item => label(item, 64)) ||
        !Array.isArray(data.series) || !data.series.length || data.series.length > 4 ||
        !data.series.every(item => item && label(item.name, 64) && Array.isArray(item.values) &&
          item.values.length === data.labels.length && item.values.every(Number.isFinite)) ||
        (data.unit !== undefined && data.unit !== "" && !label(data.unit, 24)) ||
        (data.source !== undefined && data.source !== "" && !label(data.source, 160))) return null;
    return data;
  }

  function chartPlot(data) {
    const values = data.series.flatMap(item => item.values);
    const bar = data.type === "bar";
    let min = Math.min(...values), max = Math.max(...values);
    if (bar || min === max) {
      min = Math.min(0, min);
      max = Math.max(0, max);
      if (max === min) max = 1;
    }
    // Scale before subtracting to handle finite numbers at opposite extremes.
    const scale = Math.max(Math.abs(min), Math.abs(max));
    const low = min / scale, high = max / scale;
    const width = 800, top = 5, bottom = 143;
    const y = value => bottom - ((value / scale - low) / (high - low)) * (bottom - top);
    const svg = (tag, attrs) => {
      const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, String(value));
      return item;
    };
    const plot = make("div", null, "answer-chart-plot");
    const axis = make("div", null, "answer-chart-axis");
    const canvas = svg("svg", {viewBox:"0 0 800 148", preserveAspectRatio:"none", "aria-hidden":"true", focusable:"false"});
    const midpoint = max / 2 + min / 2;
    const ticks = midpoint > min && midpoint < max ? [max, midpoint, min] : [max, min];
    const numbers = new Intl.NumberFormat("ko-KR", {maximumSignificantDigits:5});
    const tickText = value => Math.abs(value) >= 1000000 || (value !== 0 && Math.abs(value) < 0.001)
      ? value.toExponential(2) : numbers.format(value);
    const distinguishTicks = new Set(ticks.map(tickText)).size !== ticks.length;
    for (const value of ticks) {
      axis.append(make("span", distinguishTicks ? String(value) : tickText(value)));
      canvas.append(svg("line", {x1:0, x2:width, y1:y(value), y2:y(value), class:"answer-chart-grid"}));
    }
    if (min < 0 && max > 0)
      canvas.append(svg("line", {x1:0, x2:width, y1:y(0), y2:y(0), class:"answer-chart-zero"}));
    const count = data.labels.length, slot = width / count;
    const x = i => bar ? (i + 0.5) * slot : 5 + i * (width - 10) / (count - 1);
    const barWidth = slot * 0.72 / data.series.length;
    for (const [index, series] of data.series.entries()) {
      const group = svg("g", {class:"answer-series-" + index});
      if (bar) {
        for (const [i, value] of series.values.entries()) {
          const mark = svg("rect", {x:x(i) - 0.36 * slot + index * barWidth, y:Math.min(y(0), y(value)),
            width:barWidth * 0.92, height:Math.abs(y(value) - y(0)), class:"answer-chart-bar"});
          const hint = svg("title", {});
          hint.textContent = data.labels[i] + " · " + series.name + " " + value + (data.unit || "");
          mark.append(hint);
          group.append(mark);
        }
      } else {
        const points = series.values.map((value, i) => x(i) + "," + y(value)).join(" ");
        group.append(svg("polyline", {points, class:"answer-chart-line"}));
        if (count <= 12) {
          for (const [i, value] of series.values.entries()) {
            const point = svg("g", {});
            const hint = svg("title", {});
            hint.textContent = data.labels[i] + " · " + series.name + " " + value + (data.unit || "");
            point.append(hint,
              svg("circle", {cx:x(i), cy:y(value), r:6, fill:"transparent"}),
              svg("circle", {cx:x(i), cy:y(value), r:1.4, class:"answer-chart-dot"}));
            group.append(point);
          }
        }
      }
      canvas.append(group);
    }
    const xAxis = make("div", null, "answer-chart-x");
    const middle = Math.floor((count - 1) / 2);
    const labelIndices = count <= 7 ? data.labels.map((_, i) => i) : [0, middle, count - 1];
    for (const i of labelIndices) {
      const tick = make("span", data.labels[i]); tick.title = data.labels[i];
      // Anchor labels at the same category centers as their marks.
      tick.style.left = (100 * x(i) / width) + "%";
      if (x(i) < width * 0.12) tick.className = "first";
      if (x(i) > width * 0.88) tick.className = "last";
      if (count > 4 && i !== 0 && i !== middle && i !== count - 1) tick.className = "extra";
      tick.style.maxWidth = Math.min(24, 90 / labelIndices.length) + "%";
      xAxis.append(tick);
    }
    plot.append(axis, canvas, xAxis);
    return plot;
  }

  function chartFigure(data, options, expanded = false) {
    const figure = make("figure", null, "answer-chart");
    const heading = make("figcaption", null, "answer-chart-heading");
    if (!expanded) {
      heading.append(make("strong", data.title));
      if (globalThis.WorkspaceResultExplorer?.openCustom) {
        const enlarge = button("넓게 보기", data.title + " 넓게 보기");
        enlarge.onclick = () => {
          if (sessionIsCurrent(options.sessionId)) WorkspaceResultExplorer.openCustom({
            title:data.title, node:chartFigure(data, options, true), expanded:true, opener:enlarge
          });
        };
        heading.append(enlarge);
      }
      figure.append(heading);
    }
    const legend = make("div", null, "answer-chart-legend");
    for (const [index, series] of data.series.entries()) {
      const key = make("span", series.name, "answer-series-" + index);
      legend.append(key);
    }
    if (data.unit) legend.append(make("small", "단위 " + data.unit));
    const details = make("details", null, "answer-chart-values");
    details.append(make("summary", "데이터 보기"));
    // Most history entries stay collapsed. Build their data grids on demand.
    let populated = false;
    details.ontoggle = () => {
      if (!details.open || populated) return;
      populated = true;
      table(details, {
        columns:["항목", ...data.series.map(item => item.name)],
        rows:data.labels.map((name, i) => [name, ...data.series.map(item => item.values[i])]),
        caption:data.unit ? "표시 단위 " + data.unit : data.title
      });
    };
    figure.append(legend, chartPlot(data));
    if (data.source) figure.append(make("div", "출처  " + data.source, "answer-chart-source"));
    figure.append(details);
    return figure;
  }

  // Scan only newly completed lines. Raw text stays on the article for final
  // rendering, history, and interrupted replies. Ordinary deltas still append
  // to a single text node in the shared chat renderer.
  const chartStreams = new WeakMap();
  function streamChart(body, source, offset) {
    let state = chartStreams.get(body);
    if (!state || state.length !== offset) {
      state = {lineStart:0, fence:"", spans:[], length:0};
      chartStreams.set(body, state);
      offset = 0;
    }
    let changed = false;
    for (let end = source.indexOf("\n", offset); end !== -1; end = source.indexOf("\n", end + 1)) {
      const start = state.lineStart;
      state.lineStart = end + 1;
      const match = /^\s*(`{3,}|~{3,})(.*)$/.exec(source.slice(start, end).trimEnd());
      if (!match) continue;
      if (!state.fence) {
        state.fence = match[1];
        if (match[2].trim() === "ax-chart" && state.spans.length < 4) {
          state.spans.push({start,end:null});
          changed = true;
        }
      } else if (!match[2] && match[1][0] === state.fence[0] && match[1].length >= state.fence.length) {
        state.fence = "";
        const span = state.spans.at(-1);
        if (span && span.end === null) { span.end = end + 1; changed = true; }
      }
    }
    state.length = source.length;
    if (!state.spans.length) return false;
    const node = body.firstChild;
    if (!changed && node?.nodeType === 3) {
      if (state.spans.at(-1).end !== null) node.appendData(source.slice(offset));
      return true;
    }
    let cursor = 0, visible = "";
    for (const span of state.spans) {
      visible += source.slice(cursor, span.start) + "그래프 준비 중…\n";
      cursor = span.end ?? source.length;
    }
    body.textContent = visible + source.slice(cursor);
    return true;
  }
  function finishChartStream(body, source, options) {
    if (chartStreams.get(body)?.spans.length) render(body, source, {...options, role:"assistant"});
  }
  function table(parent, data={}, options={}) {
    const allColumns=Array.isArray(data.columns)?data.columns:[], allRows=Array.isArray(data.rows)?data.rows:[];
    const columns=allColumns.slice(0,LIMITS.columns).map(value=>String(value??"")), rows=allRows.slice(0,LIMITS.rows).map(row=>columns.map((_,i)=>String((Array.isArray(row)?row[i]:null)??"")));
    const card=make("section",null,"rich-table"), header=make("div",null,"rich-content-bar"), copyButton=button("표 복사","화면에 표시된 표를 탭 구분 형식으로 복사");
    const truncated=!!data.truncated||allRows.length>LIMITS.rows||allColumns.length>LIMITS.columns;
    header.append(make("span",data.caption||`표 · ${rows.length}행${truncated?" 미리보기":""}`,"rich-language"),copyButton);
    // TSV keeps embedded separators/newlines inside quoted cells for spreadsheet paste.
    const tsvCell=value=>/[\t\r\n"]/.test(value)?'"'+value.replace(/"/g,'""')+'"':value;
    const copyColumns=[],copyRows=[];
    copyButton.onclick=()=>copy([copyColumns,...copyRows].map(row=>row.map(tsvCell).join("\t")).join("\n"),copyButton);
    const scroll=make("div",null,"rich-table-scroll"), grid=make("table"), thead=make("thead"), head=make("tr"), tbody=make("tbody");
    scroll.tabIndex=0;scroll.setAttribute("role","region");scroll.setAttribute("aria-label",data.caption||"표. 가로로 스크롤할 수 있습니다");
    for(const label of columns){const th=make("th");th.setAttribute("scope","col");if(options.markdown)copyColumns.push(appendInline(th,label,options));else {th.textContent=label;copyColumns.push(label);}head.append(th);}
    thead.append(head);grid.append(thead);
    for(const row of rows){const tr=make("tr"),plainRow=[];for(const value of row){const td=make("td");if(options.markdown)plainRow.push(appendInline(td,value,options));else {td.textContent=value;plainRow.push(value);}tr.append(td);}copyRows.push(plainRow);tbody.append(tr);}
    grid.append(tbody);scroll.append(grid);card.append(header,scroll);
    if(truncated)card.append(make("p",`최대 ${LIMITS.rows}행 · ${LIMITS.columns}열까지 표시합니다. 복사는 표시된 범위에 적용됩니다.`,"rich-limit"));
    parent.append(card);return card;
  }
  function release(record) {if(record.img){record.img.removeAttribute("src");record.img.remove();record.img=null;}mounted=mounted.filter(item=>item!==record);record.loaded=false;record.status.textContent="미리보기 열기";}
  function prune() {
    // Cards are often built in a detached article and attached after render().
    // Do not drop their observer while the same article adds its file chips.
    for(const record of images){record.seenConnected ||= record.node.isConnected;if(record.seenConnected&&!record.node.isConnected){record.controller?.abort();release(record);observer?.unobserve(record.node);}}
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
    while(requests<LIMITS.imageRequests&&queue.length){const record=queue.shift();if(!current(record)||!record.visible)continue;record.pending=true;record.controller=new AbortController();pendingImages.add(record);requests++;loadImage(record).finally(()=>{record.pending=false;record.controller=null;pendingImages.delete(record);requests--;pump();});}
  }
  async function loadImage(record) {
    try {
      const result=await api(`/api/preview?id=${encodeURIComponent(record.options.sessionId)}&path=${encodeURIComponent(record.path)}&thumbnail=1`,undefined,record.controller.signal);
      if(!current(record)||!record.visible)return;
      if(result.kind!=="image"||typeof result.data!=="string"||result.data.length>LIMITS.imageData||!/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+$/.test(result.data))throw new Error("no inline preview");
      while(mounted.length>=LIMITS.images) {const oldest=mounted.find(item=>!item.visible)||mounted[0];release(oldest);}
      const img=make("img");img.alt=record.alt||nameOf(record.path);img.decoding="async";img.loading="lazy";img.src=result.data;
      img.onerror=()=>{release(record);record.failed=true;record.status.textContent="미리보기 열기";};
      img.onload=()=>{if(img.naturalWidth*img.naturalHeight>16000000){release(record);record.failed=true;record.status.textContent="파일 열어 보기";}};
      record.media.append(img);record.img=img;record.loaded=true;mounted.push(record);record.status.textContent="확대해서 보기";
    } catch (err) {if(current(record)&&err.name!=="AbortError"){record.failed=true;record.status.textContent="파일 열어 보기";}}
  }
  function image(parent,path,alt,options) {
    if(!options.sessionId||images.length>=LIMITS.records){fileChip(parent,path,options);return nameOf(path);}
    const node=button("",`이미지 확대: ${alt||nameOf(path)}`);node.className="rich-image";const media=make("span",null,"rich-image-media"),label=make("span",null,"rich-image-label"),status=make("small","미리보기 준비 중");
    label.append(make("span",alt||nameOf(path)),status);node.append(media,label);node.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{status.textContent="파일을 열 수 없습니다";});parent.append(node);
    const record={node,path,alt,options,label,media,status,epoch:generation,seenConnected:node.isConnected,visible:!globalThis.IntersectionObserver,loaded:false,pending:false,failed:false,img:null};images.push(record);ensureObserver();
    if(observer)observer.observe(node);else setTimeout(()=>enqueue(record),0);
    // Status text changes as the image loads; table copy uses its stable label.
    return alt||nameOf(path);
  }
  function fileChip(parent,path,options) {const chip=button(nameOf(path),path);chip.className="rich-file-chip";chip.replaceChildren(linkIcon("doc"),document.createTextNode(nameOf(path)));chip.onclick=()=>Promise.resolve(openFile(path,options)).catch(()=>{chip.title="파일을 열 수 없습니다";});parent.append(chip);}
  function files(parent,paths,options={}) {
    options=context(options);prune();const wrap=make("div",null,"rich-files"),unique=[...new Set((paths||[]).map(item=>typeof item==="string"?item:item?.path).filter(Boolean))];
    for(const raw of unique.slice(0,24)){
      const path=localPath(raw,options.workspace);if(!path)continue;
      const action=options.fileAction?.(path),row=action?make("div",null,"rich-file-with-action"):wrap;
      if(/\.(?:png|jpe?g|webp)$/i.test(path))image(row,path,"",options);else fileChip(row,path,options);
      if(action){row.append(action);wrap.append(row);}
    }
    if(unique.length>24)wrap.append(make("span",`그 외 ${unique.length-24}개 파일`,"rich-limit"));parent.append(wrap);return wrap;
  }
  function render(parent, source, options={}) {
    chartStreams.delete(parent);
    options=context(options);parent.replaceChildren();prune();const full=String(source??""),bounded=full.slice(0,LIMITS.text),lines=bounded.split(/\r?\n/);let i=0,blocks=0;
    // Compact rendering is bounded by structure as well as bytes. Pathological
    // tiny fences/cells must not create thousands of controls or syntax spans.
    if(lines.length>1200||(bounded.match(/\||`|\*\*|\[/g)||[]).length>6000||lines.filter(line=>/^\s*(?:`{3,}|~{3,})/.test(line)).length>80) {
      code(parent,full,"text",{label:"긴 메시지"});parent.append(make("p","내용이 길어 서식 없이 표시합니다.","rich-limit"));return true;
    }
    const highlightBudget={remaining:6000};
    let charts = 0;
    while(i<lines.length) {
      const line=lines[i];if(!line.trim()){i++;continue;}
      if(++blocks>200){code(parent,lines.slice(i).join("\n"),"text",{label:"이어서 보기"});parent.append(make("p","나머지 내용은 서식 없이 표시합니다.","rich-limit"));break;}
      const fence=line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const block = [], lang = fence[2].trim();
        const closing = new RegExp("^\\s*" + fence[1][0] + "{" + fence[1].length + ",}\\s*$");
        i++;
        while (i < lines.length && !closing.test(lines[i])) block.push(lines[i++]);
        const complete = i < lines.length;
        if (complete) i++;
        const source = block.join("\n");
        const chart = options.role === "assistant" && lang === "ax-chart" && complete && charts < 4 && readChart(source);
        if (chart) { parent.append(chartFigure(chart, options)); charts++; }
        else code(parent, source, lang, {highlightBudget});
        continue;
      }
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
  function reset() {generation++;queue=[];for(const record of pendingImages)record.controller?.abort();observer?.disconnect();observer=null;for(const record of images)release(record);images=[];mounted=[];}
  return {render,code,table,files,streamChart,finishChartStream,reset,dispose:reset,limits:{...LIMITS}};
})();
