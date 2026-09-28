// One standalone, script-free reader; authored Markdown remains modular.
// node scripts/build-manuals.mjs --modules <approved node_modules> [--check]
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const i=process.argv.indexOf('--modules');
if(i<0||!process.argv[i+1])throw Error('Provide approved --modules; no dependencies are downloaded.');
const req=createRequire(path.join(path.resolve(process.argv[i+1]),'_manual_builder.cjs'));
const {marked}=await import(pathToFileURL(req.resolve('marked')).href);
const output='Company-Agent-사용자-안내서.html';
const books=[
  {id:'onboarding',label:'01 · 시작하기',source:'ONBOARDING_COURSE.md',legacy:'Company-Agent-Onboarding.html'},
  {id:'basics',label:'02 · Claude Code 기본 사용법',source:'CLAUDE_CODE_BASICS.md'},
  {id:'usage',label:'03 · 업무별 사용법',source:'USER_GUIDE.md',legacy:'Company-Agent-사용자-안내서.html'},
  {id:'handbook',label:'04 · 기억·스킬·하네스 이해',source:'COMPANY_AGENT_HANDBOOK.md',legacy:'Company-Agent-Handbook.html'},
  {id:'commands',label:'05 · Claude Code 명령어·단축키',source:'CLAUDE_CODE_COMMANDS.md',legacy:'Claude-Code-필수-사용법.html'},
  {id:'design',label:'06 · 디자인 용어 참고',source:'DESIGN_TERMS.md'},
];
const esc=x=>x.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fontRoot=path.join(root,'scripts/assets/manual-font');
const font=fs.readFileSync(path.join(fontRoot,'NotoSansKR-guide.woff'));
const fontManifest=JSON.parse(fs.readFileSync(path.join(fontRoot,'manifest.json'),'utf8'));
const fontLicense=fs.readFileSync(path.join(fontRoot,'OFL.txt'),'utf8');
if(createHash('sha256').update(font).digest('hex')!==fontManifest.sha256)throw Error('Manual font hash mismatch');
const csp="default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src 'none'; script-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'";
const style=`
@font-face{font-family:'Noto Sans KR';font-style:normal;font-weight:400 700;font-display:swap;src:url(data:font/woff;base64,${font.toString('base64')}) format('woff')}
:root{color-scheme:light;--ink:#253b35;--muted:#5d7069;--line:#dce6df;--accent:#17664f;--soft:#f0f5f1;--paper:#fff;--type:'Noto Sans KR','Malgun Gothic',sans-serif}
*{box-sizing:border-box}html{scroll-padding-top:28px}body{margin:0;background:#f4f6f2;color:var(--ink);font:400 16px/1.85 var(--type);word-break:keep-all;overflow-wrap:anywhere;-webkit-font-smoothing:antialiased}::selection{background:#c5e6d6;color:#153c2f}
a{color:var(--accent);text-underline-offset:4px;text-decoration-thickness:1px}a:hover{color:#103f31}a:focus-visible,[tabindex]:focus-visible,summary:focus-visible{outline:3px solid #b37c30;outline-offset:4px}.skip{position:absolute;left:16px;top:-80px}.skip:focus{top:10px;background:white;padding:12px;z-index:5}
.layout{max-width:1320px;margin:auto;padding:32px 28px 72px;display:grid;grid-template-columns:244px minmax(0,1fr);gap:42px}aside{position:sticky;top:28px;align-self:start;max-height:calc(100vh - 56px);overflow:auto;padding-right:12px;scrollbar-width:thin;scrollbar-color:#c6d7ce transparent}.brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:var(--ink);font-size:20px;letter-spacing:-.6px;font-weight:700}.brand-mark{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:#174d3b;color:white;font-size:14px;letter-spacing:0;flex:none}.brand-note{font-size:13px;color:var(--muted);margin:12px 0 28px}.toc-caption{font-size:11px;letter-spacing:.08em;font-weight:600;color:var(--muted);margin-bottom:10px}.toc a{display:block;font-size:13px;line-height:1.65;padding:8px 12px;text-decoration:none;border-radius:7px}.toc a:hover{background:#e6eee7;color:#123f2f}.toc details{border-bottom:1px solid var(--line);padding:4px 0}.toc summary{cursor:pointer;font-size:13px;line-height:1.55;font-weight:600;padding:12px 8px;border-radius:8px}.toc summary:hover{background:#e6eee7}.toc details[open]>summary{color:#15543d}.toc details a{margin:0 0 2px 12px;border-left:1px solid #d4e0d7;border-radius:0 6px 6px 0}.reader-tools{font-size:12px;color:var(--muted);line-height:2;padding:18px 8px 0}.mobile-nav{display:none}
main{min-width:0}.hero,.book{background:var(--paper);border:1px solid var(--line);border-radius:20px;padding:38px 42px;margin-bottom:28px}.hero{background:#153f32;color:#f6faf7;border-color:#153f32;padding:40px 42px 32px}.eyebrow{font-size:12px;letter-spacing:.03em;font-weight:500;color:#c4dbcf}.hero h1{font-size:36px;line-height:1.4;letter-spacing:-1.4px;margin:16px 0}.hero .lead{font-size:16px;line-height:1.85;color:#dce9e0;max-width:37em}.paths{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:28px 0 22px}.paths a{display:block;padding:18px 16px 16px;min-width:0;border:1px solid #cfdfd3;border-radius:12px;background:#f7faf6;color:#234d3a;text-decoration:none;transition:background .15s}.paths a:hover{background:#e9f3e9;border-color:#90b79f}.paths .path-label{display:block;color:#526c5b;font-size:11px;font-weight:500;margin-bottom:10px}.paths b{display:block;font-size:15px;font-weight:600;margin-bottom:6px}.paths .path-desc{display:block;font-size:12px;line-height:1.75;color:#4e6659}.notice{font-size:12px;color:#c8dbce;margin:0;line-height:1.8}
.book-heading{display:flex;align-items:flex-start;gap:14px;margin-bottom:24px}.book-number{flex:none;display:grid;place-items:center;width:34px;height:34px;border:1px solid #d2e2d7;border-radius:10px;font-size:13px;font-weight:600;color:#286044;background:#f2f7f2;margin-top:4px}h1{font-size:32px;line-height:1.45}h2{font-size:26px;font-weight:700;line-height:1.5;letter-spacing:-.8px;margin:0}h3{font-size:22px;font-weight:700;line-height:1.55;letter-spacing:-.5px;border-top:1px solid var(--line);padding-top:30px;margin:44px 0 20px}h4{font-size:18px;font-weight:600;line-height:1.65;margin:28px 0 12px}h5{font-size:16px;font-weight:600;margin:24px 0 12px}h2,h3,h4,h5{scroll-margin-top:30px}p{margin:14px 0 18px}strong{font-weight:600}ul,ol{padding-left:24px;margin:16px 0 24px}li{padding-left:3px;margin:10px 0}li::marker{color:#5a8065}.back{border-top:1px solid var(--line);padding-top:22px;margin:36px 0 0;font-size:12px}.back a{text-decoration:none;color:var(--muted)}.callout{padding:16px 18px;border-radius:9px;border-left:3px solid #80a98d;background:#f1f6f1;font-size:14px}.callout.caution{background:#fbf7ed;border-color:#c4a56b}.callout strong{display:inline;color:#244b35}.callout.caution strong{color:#705523}
code{font-family:var(--type);font-size:.9em;background:#edf2ef;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere;box-decoration-break:clone;-webkit-box-decoration-break:clone}h2 code,h3 code,h4 code{font-size:inherit}.example{margin:22px 0 24px;border:1px solid #d4e2d9;border-radius:12px;overflow:hidden;break-inside:avoid}.example-label{display:flex;justify-content:space-between;gap:12px;padding:10px 18px;background:#edf4ee;color:#355e44;font-size:11px;font-weight:500;border-bottom:1px solid #d9e5dc}.example-label span:last-child{color:#5b7262;font-weight:400}pre{margin:0;padding:20px 22px;background:#f8faf7;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2;line-height:1.9}pre code{display:block;padding:0;border:0;background:transparent;border-radius:0;font-size:14px;line-height:1.95;color:#244834;user-select:all;font-weight:400}.table-wrap{margin:22px 0 26px;border:1px solid #d8e3da;border-radius:12px;overflow:hidden}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px;line-height:1.8}td,th{text-align:left;padding:14px 15px;vertical-align:top;border-bottom:1px solid #e0e8e2;overflow-wrap:anywhere}th{background:#edf3ee;font-size:12px;letter-spacing:.01em;font-weight:600;color:#3b5d48}th:first-child{width:25%}td+td,th+th{border-left:1px solid #e4ebe5}tbody tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#fafcf9}.cell-content{min-width:0}small{color:var(--muted);font-size:12px}.standalone{max-width:880px;margin:40px auto}.standalone nav a{display:block;padding:8px 0}
@media(max-width:1050px){.layout{grid-template-columns:214px minmax(0,1fr);gap:26px;padding:24px 22px}.hero,.book{padding:30px}.hero h1{font-size:32px}.paths{gap:8px}.paths a{padding:15px 12px}}
@media(max-width:860px){.layout{display:block;max-width:800px;padding:20px}aside{display:none}.mobile-nav{display:block;margin-bottom:20px;border:1px solid var(--line);border-radius:12px;background:white;padding:0 18px}.mobile-nav>summary{cursor:pointer;display:flex;justify-content:space-between;gap:16px;list-style:none;padding:16px 0;font-size:13px;font-weight:600}.mobile-nav>summary::after{content:'+';color:var(--accent);font-size:18px;line-height:1.2}.mobile-nav[open]>summary::after{content:'−'}.mobile-nav>summary::-webkit-details-marker{display:none}.mobile-nav .toc{max-height:55vh;overflow:auto;padding-bottom:14px}.hero,.book{padding:30px}.hero h1{font-size:32px}.reader-tools{display:none}}
@media(max-width:600px){body{font-size:15px;line-height:1.9}.layout{padding:14px 12px 40px}.mobile-nav{margin-bottom:14px}.hero,.book{padding:24px 20px;border-radius:15px;margin-bottom:20px}.hero h1{font-size:28px;letter-spacing:-.9px}.hero .lead{font-size:14px}.eyebrow{font-size:11px}.paths{grid-template-columns:1fr;gap:9px;margin:24px 0 18px}.paths a{padding:15px 17px;display:grid;grid-template-columns:1fr auto;column-gap:12px;align-items:center}.paths .path-label{display:none}.paths b{margin:0;font-size:14px}.paths .path-desc{font-size:11px;max-width:12em;text-align:right}.book-heading{gap:10px;margin-bottom:22px}.book-number{width:28px;height:28px;border-radius:8px;font-size:11px}h2{font-size:22px;letter-spacing:-.6px}h3{font-size:20px;line-height:1.55;margin-top:36px;padding-top:26px}h4{font-size:17px}.example-label{padding:10px 14px;font-size:10px;gap:8px}pre{padding:16px}pre code{font-size:13px}.callout{padding:14px;font-size:13px}ul,ol{padding-left:20px}.table-wrap{border:0;border-radius:0;overflow:visible}table,tbody{display:block;width:100%}thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}tbody tr{display:block;border:1px solid #d8e3da;border-radius:10px;margin-bottom:12px;background:#fff!important;overflow:hidden}td{display:grid;grid-template-columns:minmax(72px,28%) minmax(0,1fr);gap:12px;padding:12px 14px;border:0;border-bottom:1px solid #e5ece6;font-size:13px}td+td{border-left:0}td::before{content:attr(data-label);font-size:11px;font-weight:600;color:#526c59;line-height:1.8}td:first-child{background:#f1f6f0}td code{font-size:.92em}.standalone{margin:14px 12px}}
@media print{body{background:white;font-size:10pt}.layout{display:block;padding:0}aside,.mobile-nav,.skip,.back,.paths{display:none}.hero,.book{border:0;padding:0;background:white;color:#20372b}.hero .eyebrow,.hero .lead,.hero .notice{color:#3b5547}.hero h1{font-size:22pt}.book-heading{break-after:avoid}h2{font-size:18pt}h3{font-size:14pt;break-after:avoid}h4,h5{break-after:avoid}.book{break-before:page}table{display:table;font-size:9pt;table-layout:auto}thead{display:table-header-group;position:static;width:auto;height:auto;clip-path:none}tbody{display:table-row-group}tbody tr{display:table-row;break-inside:avoid;border:0}td{display:table-cell;font-size:9pt;padding:8px;gap:0}td::before{display:none}.table-wrap{overflow:visible}.example{break-inside:auto}pre{padding:14px}pre code{font-size:9pt}.example-label{font-size:8pt}.callout{font-size:9pt}a{color:inherit}@page{size:A4;margin:18mm}}
`;
const responsiveRefinements=`@media(max-width:600px){.hero h1 span{display:block}.paths a{display:block;padding:14px 17px}.paths b{margin-bottom:4px}.paths .path-desc{max-width:none;text-align:left}}`;
const head=(title,metadata='')=>`<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${metadata}<meta http-equiv="Content-Security-Policy" content="${csp}"><title>${esc(title)}</title><!-- Embedded Noto Sans KR subset.\n${fontLicense.replaceAll('--','—')}\n--><style>${style}${responsiveRefinements}</style></head>`;
const decodeEntities=text=>text.replace(/&(amp|lt|gt|quot|#39|#\d+|#x[0-9a-f]+);/gi,(_,entity)=>{
  const known={amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"};
  return known[entity]??String.fromCodePoint(entity.startsWith('#x')?parseInt(entity.slice(2),16):Number(entity.slice(1)));
});
function responsiveTable(table){
  const labels=[...table.matchAll(/<th(?:\s[^>]*)?>([\s\S]*?)<\/th>/g)].map(m=>decodeEntities(m[1].replace(/<[^>]*>/g,'')));
  let column=0;
  return '<div class="table-wrap" tabindex="0" role="region" aria-label="안내 표">'+
    table.replace('<table>','<table role="table">').replace(/<th(\s[^>]*)?>/g,(_,attrs)=>'<th scope="col"'+(attrs||'')+'>')
      .replace(/<tr>/g,()=>{column=0;return '<tr role="row">';})
      .replace(/<td(\s[^>]*)?>([\s\S]*?)<\/td>/g,(_,attrs,content)=>
        '<td role="cell" data-label="'+esc(labels[column++%labels.length]||'내용')+'"'+(attrs||'')+'><span class="cell-content">'+content+'</span></td>')+
    '</div>';
}
function assertFontCoverage(html){
  const visible=decodeEntities(html.replace(/<style>[\s\S]*?<\/style>|<!--[\s\S]*?-->|<[^>]+>/g,''));
  const available=new Set(fontManifest.characters);
  const missing=[...new Set([...visible].filter(c=>!(/\s/.test(c))&&!available.has(c)))];
  if(missing.length)throw Error('Refresh manual font subset for: '+missing.join(''));
}
function emit(name,html,folders=['docs','company-agent-plugin/resources/manuals']){
  if(name.endsWith('.html'))assertFontCoverage(html);
  for(const folder of folders){
    const dest=path.join(root,folder,name);
    if(process.argv.includes('--check')){
      if(!fs.existsSync(dest)||fs.readFileSync(dest,'utf8')!==html)throw Error('Manual needs rebuilding: '+folder+'/'+name);
    }else{fs.mkdirSync(path.dirname(dest),{recursive:true});fs.writeFileSync(dest,html,'utf8');}
  }
}
// Ship the same readable sources after installation, without adding them to
// skills, hooks or model context. Preserve source bytes, including line endings.
const installedFolders=['company-agent-plugin/resources/manuals'];
emit('README.md',fs.readFileSync(path.join(root,'docs/README.md'),'utf8'),installedFolders);
for(const book of books){
  const raw=fs.readFileSync(path.join(root,'docs',book.source),'utf8');
  emit(book.source,raw,installedFolders);
  const text=raw.replace(/\r\n/g,'\n');
  book.title=text.match(/^# (.+)$/m)[1];
  book.headings=[...text.matchAll(/^## (.+)$/gm)].map(x=>x[1]);
  book.sha=createHash('sha256').update(text).digest('hex');
  let n=0,body=marked.parse(text,{gfm:true});
  body=body.replace(/<h([1-5])>(.*?)<\/h\1>/g,(_,level,t)=>{
    if(level==='1')return `<div class="book-heading"><span class="book-number" aria-hidden="true">${String(books.indexOf(book)+1).padStart(2,'0')}</span><h2 id="${book.id}">${t}</h2></div>`;
    const id=level==='2'?` id="${book.id}-section-${++n}"`:'';
    return `<h${Number(level)+1}${id}>${t}</h${Number(level)+1}>`;
  }).replace(/<table>[\s\S]*?<\/table>/g,responsiveTable)
    .replace(/<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g,(_,attrs,content)=>
      `<div class="example"><div class="example-label"><span>${attrs.includes('language-text')?'요청 예문':'입력 예시'}</span><span>선택 후 Ctrl+C로 복사</span></div><pre><code${attrs}>${content}</code></pre></div>`)
    .replace(/<p><strong>(성공 확인:|막혔을 때:)/g,(_,label)=>`<p class="callout${label==='막혔을 때:'?' caution':''}"><strong>${label}`)
    .replace(/<a href="([^"/:#]+\.(?:md|html))">([\s\S]*?)<\/a>/g,(_,file,t)=>{
      const dest=books.find(b=>b.source===file||b.legacy===file);
      if(dest)return `<a href="#${dest.id}">${t}</a>`;
      throw Error('Link to a document outside the unified guide: '+file);
    });
  if(n!==book.headings.length||/<(?:script|iframe|object|img)\b|\son\w+=/i.test(body))throw Error('Unexpected active content in '+book.source);
  book.body=body;
}
const toc=(expanded=false)=>books.map(b=>`<details${expanded&&b.id==='onboarding'?' open':''}><summary>${esc(b.label)}</summary><a href="#${b.id}">이 안내부터 보기</a>${b.headings.map((h,i)=>`<a href="#${b.id}-section-${i+1}">${esc(h)}</a>`).join('')}</details>`).join('');
const metadata=books.map(b=>`<meta name="source-sha256" data-file="${b.source}" content="${b.sha}">`).join('');
const html=head('Company Agent 사용자 안내서',metadata)+`<body>
<a class="skip" href="#content">본문으로 이동</a>
<div class="layout">
  <aside>
    <a class="brand" href="#start"><span class="brand-mark" aria-hidden="true">CA</span>Company Agent</a>
    <p class="brand-note">시작하기부터 업무 활용까지</p>
    <div class="toc-caption">사용 가이드 · 목차</div>
    <nav class="toc" aria-label="목차">${toc(true)}</nav>
    <p class="reader-tools">페이지 찾기 <b>Ctrl+F</b><br>인쇄·PDF 저장 <b>Ctrl+P</b></p>
  </aside>
  <main id="content">
    <details class="mobile-nav"><summary>Company Agent · 목차</summary><nav class="toc" aria-label="모바일 목차">${toc()}</nav></details>
    <header class="hero" id="start">
      <span class="eyebrow">처음 사용하는 분을 위한 안내</span>
      <h1><span>Company Agent</span> 사용자 안내서</h1>
      <p class="lead">파일이 없어도, 명령어를 몰라도.<br>작은 가상 업무 하나부터 시작해 보세요.</p>
      <div class="paths">
        <a href="#onboarding-section-2"><span class="path-label">01 · 첫 연습</span><b>처음이라면</b><span class="path-desc">가상 자료 만들기 → 실제 읽기</span></a>
        <a href="#usage"><span class="path-label">02 · 업무 활용</span><b>바로 일하려면</b><span class="path-desc">보고서·회의·비교 요청 예문</span></a>
        <a href="#commands"><span class="path-label">03 · 빠른 참조</span><b>명령어가 궁금하면</b><span class="path-desc">입력·중단·압축·다시 시작</span></a>
      </div>
      <p class="notice">회사 자료나 참고 PPT는 필요 없습니다.<br>예문을 선택해 Ctrl+C로 복사하고, 결과를 확인하며 따라 해보세요.</p>
    </header>
    ${books.map(b=>`<section class="book" aria-labelledby="${b.id}">${b.body}<p class="back"><a href="#start">처음 안내로 돌아가기 ↑</a></p></section>`).join('')}
  </main>
</div></body></html>\n`;
// One complete reader; keep bookmarks previously shared for this filename.
const manual=html.replace(/(<h3 id="usage-section-(\d+)">)/g,'$1<span id="section-$2" aria-hidden="true"></span>');
emit(output,manual);
for(const reader of [manual]){
  const ids=[...reader.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  if(new Set(ids).size!==ids.length)throw Error('Duplicate guide anchors');
  for(const [,id]of reader.matchAll(/href="#([^"]+)"/g))if(!ids.includes(id))throw Error('Missing guide anchor '+id);
}
console.log(`${output}: ${books.length} parts, ${books.reduce((n,b)=>n+b.headings.length,0)} chapters, ${Buffer.byteLength(manual)} bytes; one HTML reader; ${books.length+1} Markdown sources`);
