"use strict";
// Source/model/memory text uses text nodes. The trusted map renderer's escaped
// document is isolated in a scriptless, sandboxed iframe.
(() => {
  let view = 'guide', data = {}, sid = null, generation = 0;
  const tabs = {guide:'따라 하기', map:'구성 한눈에', shared:'회사 공통', personal:'개인 전체', project:'이 프로젝트', usage:'사용 현황', checks:'준비·결과 확인'};
  const domains = {
    shared:'회사에서 검토·배포한 지식과 업무 구성 · 읽기 전용',
    personal:'같은 사용자 설치의 여러 프로젝트에서 참고할 기억·스킬·도구',
    project:'현재 프로젝트용 기억·스킬·도구와 폴더 지침 · 나만 사용과 공유 파일 구분'
  };
  const selectedScope=()=>view.startsWith('project-')?'project':view.startsWith('personal-')?'personal':null;
  const content = $('companion-content');
  const labels = {active:'사용 중',draft:'초안',inactive:'사용 안 함',deprecated:'사용 중단',ready:'확인됨',check:'확인 필요',unverified:'미검증',passed:'통과',failed:'실패',cancelled:'중지',blocked:'차단'};
  const message = text => $('companion-message').textContent = text || '';
  const button = (text, fn, cls='quiet-button') => {const b=el('button',text,cls);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn(b);}catch(e){message(e.message);}finally{b.disabled=false;}};return b;};
  const note = text => el('p',text,'companion-note');
  const card = (title, text) => {const c=el('section',null,'companion-card');c.append(el('h3',title));if(text)c.append(note(text));return c;};
  const actions = (...buttons) => {const n=el('div',null,'companion-actions');n.append(...buttons);return n;};
  function field(parent, label, value='', type='textarea') {
    const wrap=el('label',null,'companion-field'), n=el(type==='select'?'select':type==='textarea'?'textarea':'input');
    wrap.append(el('span',label));if(type!=='textarea'&&type!=='select')n.type=type;
    n.value=value;n.setAttribute('aria-label',label);if(type==='textarea'){n.rows=3;n.maxLength=6000;}wrap.append(n);parent.append(wrap);return n;
  }
  function select(parent,label,items,value) {const n=field(parent,label,'','select');for(const [v,t] of items){const o=el('option',t);o.value=v;n.append(o);}n.value=value;return n;}
  function checkbox(parent,label,checked=false){const wrap=el('label',null,'companion-check'),n=el('input');n.type='checkbox';n.checked=checked;wrap.append(n,el('span',label));parent.append(wrap);return n;}
  async function action(value){if(!sid||active?.id!==sid)throw new Error('작업 폴더가 바뀌었습니다. 도우미를 다시 열어 주세요.');return api('/api/companion',{id:sid,...(selectedScope()?{storageScope:selectedScope()}:{}),...value});}
  async function load(){
    const ticket=++generation;
    message('로컬 정보를 확인하고 있어요. AI 호출은 하지 않습니다.');
    const next= sid ? await api('/api/companion?id='+encodeURIComponent(sid)+'&view='+encodeURIComponent(view)) : {course:await api('/api/course')};
    if(ticket!==generation)return;
    data=next;message('');render();
  }
  async function open(which){
    view=({memory:'personal-memory',knowledge:'shared-memory',brief:'project-harness'})[which]||which;sid=active?.trusted?active.id:null;data={};
    if($('settings-dialog')?.open)$('settings-dialog').close();
    showDialog('companion-dialog');content.replaceChildren();
    await load();
  }
  function requireHarness(){if(data.harness)return true;content.append(note(data.unavailable || '먼저 신뢰할 수 있는 작업 폴더를 선택하세요. 기존 Claude 로그인·개인 설정은 그대로 유지합니다.'));return false;}
  async function hydrate(category,item){
    if(typeof item.body==='string')return item;
    return Object.assign(item,await action({action:'detail',category,entryKey:item.entryKey}));
  }
  function bodyDetails(category,item){
    const d=el('details'), body=el('pre');d.append(el('summary','내용·출처 보기'),body);
    d.ontoggle=async()=>{if(!d.open||d.dataset.loading)return;d.dataset.loading='yes';body.textContent='선택한 내용만 읽고 있어요.';
      try{const fresh=await hydrate(category,item);body.textContent=fresh.body;}catch(e){body.textContent=e.message;delete d.dataset.loading;}};
    return d;
  }
  function more(category,list){
    if(!list.nextCursor)return;
    content.append(button('다음 항목 보기',async()=>{
      const ticket=generation, result=await action({action:'list',category,cursor:list.nextCursor});
      if(ticket!==generation)return;
      list.items.push(...result.items);list.nextCursor=result.nextCursor;list.limited=result.limited;
      list.warnings=[...new Set([...(list.warnings||[]),...(result.warnings||[])])];render();
    }));
  }
  async function confirmCompanion(options,trigger){
    const context={sid,view,generation},accepted=await confirmAction({...options,returnFocus:trigger||document.activeElement});
    if(!accepted)return false;
    if(context.sid!==sid||context.view!==view||context.generation!==generation||!$('companion-dialog').open||(sid&&active?.id!==sid)){message('업무 정보가 바뀌었어요. 내용을 다시 확인해 주세요.');return false;}
    return true;
  }
  async function compose(prompt,trigger){
    const task=active?.id,previous=$('prompt').value;
    if(previous.trim()&&!await confirmCompanion({title:'요청을 예문으로 바꿀까요?',message:'작성 중인 요청이 선택한 예문으로 바뀝니다. 첨부한 자료는 그대로 유지돼요.',confirmLabel:'예문으로 바꾸기'},trigger))return;
    if(active?.id!==task||$('prompt').value!==previous||sending||appClosed)return message('작성 중인 요청이 바뀌었어요. 내용을 다시 확인해 주세요.');
    $('companion-dialog').close();$('prompt').value=prompt;$('prompt').focus();toast('입력창에 넣었습니다. 내용을 확인하고 보내기를 눌러 주세요.');
  }
  async function preview(change){
    if(['memory','knowledge'].includes(change.kind)&&!change.storageScope)throw new Error('어디에 저장할까요? 개인 전체 또는 이 프로젝트를 선택해 주세요.');
    const result=await action({action:'plan',data:change});
    const block=card('저장 전 확인','아래 내용만 저장합니다. 실제 다음 업무에 적용됐는지는 별도로 확인해야 합니다.');
    block.append(note(result.preview.storageLabel||'이 프로젝트의 업무 지침'),note('공통 배포·다른 사람과 자동 공유하지 않습니다.'));
    const names={title:'제목',body:'내용',status:'사용 상태',goal:'목표',inputs:'입력 자료',outputs:'결과물',checks:'완료 확인 기준'};
    for(const [k,title]of Object.entries(names))if(result.preview.spec[k])block.append(el('h4',title),el('pre',labels[result.preview.spec[k]]||result.preview.spec[k]));
    if(result.preview.spec.metadata?.workspace_review)block.append(note('출처: '+result.preview.spec.metadata.workspace_review.reference));
    block.append(actions(button('취소',()=>block.remove()),button('확인하고 저장',async()=>{const saved=await action({action:'apply',token:result.token,confirmed:true});if(saved.scope?.resourceScope)view=saved.scope.resourceScope+'-memory';await load();message('저장했습니다. 다음 업무의 적용 여부는 아직 확인하지 않았습니다.');if(saved.path){const d=el('details');d.append(el('summary','저장 위치 확인'),note(saved.path));content.prepend(d);}},'send-button')));
    content.prepend(block);block.scrollIntoView({block:'start'});
  }
  function guide(){
    content.append(note('처음에는 가상 자료 하나로 연습해 보세요. 예문을 입력창에 넣고 보내기를 누르면 시작합니다. 결과를 직접 확인하면서 다음 단계로 이어가세요.'));
    const manuals=el('p');
    for(const [url,title] of [['/manual/guide','사용자 안내서 · 준비물 없이 시작하기']]){const link=el('a',title);link.href=url;link.target='_blank';link.rel='noopener noreferrer';manuals.append(link);}
    content.append(manuals);
    for(const step of data.course?.steps||[]){
      const c=card(step.title,step.concept);c.append(el('pre',step.prompt),note('확인할 것 · '+step.check));
      c.append(actions(button('입력창에 넣기',trigger=>compose(step.prompt,trigger)),...(step.alternativePrompt?[button(step.id==='read'?'파일 없이 텍스트로 연습':'PPT로 연습',trigger=>compose(step.alternativePrompt,trigger))]:[])));
      c.append(note('잘 안될 때 · '+step.recovery));
      if(sid){const check=checkbox(c,'직접 확인했어요 · 업무 성공 자동 판정이 아닙니다',!!data.records?.steps?.[step.id]?.checked);check.onchange=async()=>{check.disabled=true;try{await action({action:'progress',step:step.id,checked:check.checked});}catch(e){check.checked=!check.checked;message(e.message);}finally{check.disabled=false;}};}
      content.append(c);
    }
    if(!sid)content.append(note('확인한 단계를 저장하려면 먼저 작업 폴더를 선택하세요.'));
    const advanced=el('details');advanced.append(el('summary','익숙해진 다음 · 기억과 업무 환경 알아보기'));
    if(data.course?.intro)advanced.append(note(data.course.intro));
    advanced.append(note('회사 공통·개인 전체·이 프로젝트의 자료는 각각 관리됩니다. 기존 파일을 자동으로 이동하거나 공유하지 않습니다.'));
    advanced.append(button('현재 업무 환경 보기',async()=>{view='map';await load();}));content.append(advanced);
  }
  function memoryForm(item=null, version=null){
    const c=card(version?'이전 내용 복원':item?'기억 수정':'짧은 선호 하나 저장','문서 본문·계정 정보 대신 앞으로 필요한 선호만 적으세요.');
    const scope=storageChoice(c,item);
    const title=field(c,'기억 제목',version?.title||item?.title||'','text');title.maxLength=200;
    const body=field(c,'기억할 내용',version?.body||item?.body||'');body.maxLength=2000;
    const kind=select(c,'기억 종류',[['preference','표현 선호'],['work_context','업무 맥락'],['convention','업무 관례']],version?.kind||item?.kind||'preference');
    const status=select(c,'사용 상태',[['active','사용'],['draft','초안'],['inactive','사용 안 함']],item?.status==='deprecated'?'inactive':item?.status||'active');
    c.append(actions(button('취소',()=>c.remove()),button('변경안 확인',()=>preview({kind:'memory',storageScope:scope.value,itemId:item?.id,expectedSha256:item?.sha256,title:title.value,body:body.value,memoryKind:kind.value,status:status.value}),'send-button')));
    content.prepend(c);title.focus();
  }
  function storageChoice(parent,item=null){
    const choices=data.harness.scope.choices||[];
    const n=select(parent,'어디에 저장할까요?', [['','저장 범위를 선택하세요'],...choices.map(x=>[x.id,x.label+(x.available?'':' · 설치 확인 필요')])],item?.storageScope||'');
    for(const option of n.options){const c=choices.find(x=>x.id===option.value);if(c&&!c.available)option.disabled=true;}
    if(item){n.value=item.storageScope||selectedScope();n.disabled=true;parent.append(note('기존 항목은 원래 범위에서 수정합니다. 범위 이동·복사는 별도 요청이 필요합니다.'));}
    else parent.append(note('개인 전체: 여러 프로젝트 / 이 프로젝트: 현재 작업에서만. 회사 공통은 저장 선택지가 아닙니다.'));
    return n;
  }
  function memories(){
    if(!requireHarness())return;
    const mem=data.harness.memory;
    content.append(el('h3','선호·업무 맥락'),note('Company Agent의 내 기억입니다. 다른 사람에게 자동 공유하지 않으며 Claude 자체 자동 기억은 별도 관리합니다.'),button('＋ 내 기억 추가',()=>memoryForm()));
    for(const item of mem.items){
      const source={explicit_workspace_request:'이 화면에서 직접 저장',explicit_user_feedback:'직접 요청',self_learning:'업무 중 학습',automatic_learning:'업무 중 학습'}[item.source]||'기존 저장 기록';
      const c=card(item.title,`${labels[item.status]||item.status} · ${source}`);c.append(bodyDetails('memory',item));
      c.append(actions(button('수정',async()=>memoryForm(await hydrate('memory',item))),button(item.status==='active'?'사용 안 함':'다시 사용',async()=>{const fresh=await hydrate('memory',item);return preview({kind:'memory',storageScope:fresh.storageScope||selectedScope(),itemId:fresh.id,expectedSha256:fresh.sha256,title:fresh.title,body:fresh.body,memoryKind:fresh.kind,status:fresh.status==='active'?'inactive':'active'});}),button('이전 내용 보기',async()=>{
        const result=await action({action:'versions',itemId:item.id}), box=card('이전 내용',result.notice);
        if(!result.versions.length)box.append(note('이전 저장본이 없습니다.'));
        for(const v of result.versions){const detail=el('details');detail.append(el('summary',v.version),el('pre',v.body),button('이 내용으로 복원안 보기',()=>memoryForm(item,v)));box.append(detail);}c.append(box);
      })));
      content.append(c);
    }
    if(!mem.items.length)content.append(note('아직 저장한 개인 기억이 없습니다.'));
    more('memory',mem);
    (mem.warnings||[]).forEach(x=>content.append(note(x)));if(mem.limited)content.append(note('표시 한도에 도달했습니다. 전체 항목이 아닐 수 있습니다.'));
    const learn=data.harness.learning, c=card('업무 중 학습 관리','대상은 내 선호와 제한된 개인 스킬 점검 영역입니다. 공통 자료·프로젝트 CLAUDE.md를 자동 수정하지 않습니다. 끄더라도 기존 자료는 보존합니다.');
    c.append(note(learn.scopeNotice||'현재 설치 범위에서 학습합니다.'),note('업무 피드백 수집 → 업무 완료·검증 → 짧은 선호/스킬 점검 항목 개선 → 다음 사용 결과 확인. 모델 자체 훈련이나 종료 후 백그라운드 작업은 아닙니다.'));
    if(learn.activeInstallation===false){c.append(note('이 범위는 현재 명시적인 저장용입니다. 자동 학습 설정은 '+learn.scopeLabel+'에서 확인하세요.'));content.append(c);return;}
    if(typeof learn.enabled==='boolean')c.append(button(learn.enabled?'자동 학습 끄기':'자동 학습 켜기',async trigger=>{if(!await confirmCompanion({title:learn.enabled?'자동 학습을 끌까요?':'자동 학습을 켤까요?',message:'기존 자료는 보존됩니다. 내 선호와 개인 스킬 점검 영역에 적용돼요.',confirmLabel:learn.enabled?'자동 학습 끄기':'자동 학습 켜기'},trigger))return;await action({action:'learning',enabled:!learn.enabled,confirmed:true});await load();}));
    else c.append(note(learn.notice||'학습 상태 미확인'));
    for(const change of learn.recentChanges||[]){const row=el('div',null,'companion-row');row.append(note(`${change.id} · ${change.status||'기록됨'}`),button('이 변경 되돌리기',async trigger=>{if(!await confirmCompanion({title:'이 학습 변경을 되돌릴까요?',message:'선택한 변경을 되돌립니다. 이후 직접 편집한 내용은 보존합니다.',confirmLabel:'변경 되돌리기',danger:true},trigger))return;await action({action:'rollback',changeId:change.id,confirmed:true});await load();}));c.append(row);}content.append(c);
    for(const candidate of learn.recentCandidates||[]){const d=el('details');d.append(el('summary','학습 후보 · '+candidate.title+' · '+candidate.status),el('pre',candidate.body),note('후보 기록만으로 업무에 적용된 것은 아닙니다.'));c.append(d);}
  }
  function knowledgeForm(item=null){
    const c=card(item?'내 업무 지식 검토·수정':'내 업무 지식 초안 만들기','내 기억에만 저장합니다. 공통 기억 반영이나 외부 전송은 하지 않습니다.');
    const scope=storageChoice(c,item);
    const title=field(c,'지식 제목',item?.title||'','text'), body=field(c,'확인한 업무 지식',item?.body||''), reference=field(c,'출처·확인 근거',item?.review?.reference||'','text');
    const date=field(c,'다음 확인일 (선택)',item?.review?.reviewAfter||'','date');
    const status=select(c,'적용 상태',[['draft','초안 · 업무에 미적용'],['active','검토 후 개인 업무에 사용'],['deprecated','사용 중단']],item?.status||'draft');
    c.append(actions(button('취소',()=>c.remove()),button('저장 내용 확인',()=>preview({kind:'knowledge',storageScope:scope.value,itemId:item?.id,expectedSha256:item?.sha256,title:title.value,body:body.value,reference:reference.value,reviewAfter:date.value,status:status.value}),'send-button')));content.prepend(c);title.focus();
  }
  function knowledge(shared=false){
    if(!requireHarness())return;
    const category=shared?'shared-knowledge':'personal-knowledge';
    content.append(el('h3',shared?'함께 참고하는 업무 지식':'내가 확인한 업무 지식'),note(shared?data.harness.notice:'개인 지식도 내 기억에 포함됩니다. 선호와 달리 출처·확인 근거를 함께 남깁니다.'));
    if(shared&&!data.harness.configured)content.append(note('현재 설치에 공통 지식 저장소가 지정되지 않았습니다. 회사 정책이 없다는 뜻은 아닙니다.'));
    if(!shared)content.append(button('＋ 내 업무 지식 초안',()=>knowledgeForm()));
    const selected=new Set();
    if(!shared)content.append(button('선택한 지식을 공통 기억 검토용으로 내보내기',async trigger=>{if(!selected.size)return message('검토할 내 지식을 선택하세요.');if(!await confirmCompanion({title:'공유 검토용 파일을 만들까요?',message:'선택한 지식을 이 PC의 ZIP 파일로 만듭니다. 외부 전송·회사 반영은 하지 않습니다. 공유 전 민감 내용을 직접 검토해 주세요.',confirmLabel:'검토용 ZIP 만들기'},trigger))return;const r=await action({action:'share',itemIds:[...selected],confirmed:true});message(r.notice+' '+r.path);}));
    for(const item of data.harness.knowledge.items){
      const c=card(item.title,`${shared?'공통 · 읽기 전용':'내 기억'} · ${labels[item.status]||item.status}`);
      const d=bodyDetails(category,item);d.append(note(`출처: ${item.review?.reference||item.source||'미확인'} / 확인: ${item.review?.reviewedAt||'미확인'} / 다음 확인: ${item.review?.reviewAfter||'미지정'}`));c.append(d);
      if(!shared&&item.ownership==='personal'){
        const cb=checkbox(c,'공유 후보에 포함');cb.onchange=()=>cb.checked?selected.add(item.id):selected.delete(item.id);
        if(item.kind==='term'&&!item.extends)c.append(button('검토·수정',async()=>knowledgeForm(await hydrate(category,item))));
      }content.append(c);
    }
    if(!data.harness.knowledge.items.length)content.append(note('표시할 지식이 없습니다. 이 표시는 회사 정책이나 실제 검색 적용 여부를 보장하지 않습니다.'));
    more(category,data.harness.knowledge);
    (data.harness.knowledge.warnings||[]).forEach(x=>content.append(note(x)));
    if(data.harness.knowledge.limited)content.append(note('일부 항목만 표시합니다. 전체 목록이 아닙니다.'));
  }
  function myMemory(){if(!requireHarness())return;memories();knowledge(false);}
  function skillInventory(){
    const inv=data.harness.inventory, c=card('스킬 · 출처별 확인',inv.notice);
    const sources={company:'공통 배포',corporate:'공통 지식 저장소',personal:'내가 만든 개인 자산',user:'내 Claude 설정에 설치',project:'이 폴더·상위 폴더 구성 · 공유 여부는 작성자 확인',plugin:'외부 플러그인 · 원본 별도 관리'};
    for(const item of inv.items){const row=el('details');row.append(el('summary',(item.invocation||item.name)+' · '+(sources[item.source]||'출처 미확인')),note(item.description||'설명 없음'));if(item.explicitOnly)row.append(note('명시적으로 요청할 때만 사용'));c.append(row);}
    if(!inv.items.length)c.append(note('현재 확인한 범위에 스킬이 없습니다. 없는 스킬을 설치하거나 강제로 호출하지 않습니다.'));
    if(inv.conflicts)c.append(note('현재 전체 목록에 이름 겹침 '+inv.conflicts+'건 · 우선 선택을 확인하거나 사용자에게 질문합니다.'));
    if(inv.limited)c.append(note('일부 목록만 확인했습니다. 전체가 아니므로 미표시를 미설치로 단정하지 않습니다.'));
    (inv.warnings||[]).forEach(x=>c.append(note(x)));content.append(c);
  }
  function sharedHarness(){
    if(!requireHarness())return;
    const policy=card('공통 업무 기준 · 읽기 전용','회사·팀이 배포한 절차와 기준입니다. 공통 기억의 참고 지식이나 나의 표현 선호와 구분합니다. 변경은 담당자의 검토·배포가 필요합니다.');
    const rules=data.harness.policy;policy.append(note('조회 상태: '+({available:'확인됨','not-configured':'업무 기준 항목 미설정 · 회사 보안 정책이 없다는 뜻은 아닙니다.',unavailable:'확인 필요 · 없는 것으로 처리하지 않습니다.'}[rules.status]||'미확인')));
    for(const rule of rules.rules||[])policy.append(note((rule.level==='required'?'필수':'기본값')+' · '+rule.text));content.append(policy);
    skillInventory();const h=data.harness.hooks;content.append(card('공통 후크 · 자동 처리',h.notice),note(h.events.join(' · ')||'확인한 후크 없음'));
  }
  function myHarness(){
    if(!requireHarness())return;
    content.append(note('내가 추가하거나 연결한 업무 방식입니다. 내 사용자 설치와 이 폴더 범위를 구분합니다. 공유 프로젝트 파일·외부 플러그인은 개인 소유로 간주하거나 이 화면에서 덮어쓰지 않습니다.'));
    skillInventory();const tools=data.harness.tools,c=card('내가 만든 도구',tools.notice);
    for(const item of tools.items)c.append(note(`${item.name} · ${item.type==='script-tool'?'로컬 계산 도구':'MCP 연결 정의'} · ${({candidate:'시험 전 후보',active:'활성화 기록',validated:'검증 기록'})[item.status]||'상태 미확인'}`));
    if(!tools.items.length)c.append(note(tools.status==='unavailable'?'목록 미확인':'등록된 개인 도구 없음'));
    if(tools.limited)c.append(note('일부 등록 기록만 표시합니다.'));content.append(c);
    const create=card('스킬·도구 만들기','범위와 할 일을 정하면 요청을 입력창에 넣습니다. 자동 전송·생성하지 않습니다.');
    const scope=storageChoice(create),task=field(create,'재사용할 업무');
    create.append(button('제작 요청을 입력창에 넣기',trigger=>{if(!scope.value||!task.value.trim())throw new Error('저장 범위와 재사용할 업무를 입력해 주세요.');return compose(`${scope.value==='personal'?'개인 전체':'이 프로젝트'} 범위에 저장할 스킬 또는 도구를 만들어줘. 할 일: ${task.value.trim()}. 이미 저장 범위를 선택했으므로 다시 묻지 말고 기존 스킬·도구와 겹치는지 확인해줘. 회사 공통 원본은 변경하지 마. 필요한 기능만 만들고 검증해줘.`,trigger);}));content.append(create);
    const manual=el('a','사용자 안내서 · 내 스킬·도구 만들기 실습');manual.href='/manual/guide#onboarding-section-9';manual.target='_blank';manual.rel='noopener noreferrer';content.append(manual);
    if(data.harness.brief){content.append(el('h3','이 폴더에만 적용할 업무 지침'));brief();}
  }
  function brief(){
    if(!requireHarness())return;
    const item=data.harness.brief;content.append(note(item.notice),note(item.path));
    if(item.sha256){const d=el('details');d.append(el('summary','현재 업무 지침'),el('pre',item.body||'(빈 파일)'));content.append(d);if(!item.owned)return content.append(note('기존 또는 직접 수정한 파일입니다. 이 화면에서 덮어쓰지 않습니다.'));}
    const c=card('이 폴더에서 할 일만 짧게','회사 정책·전역 CLAUDE.md·개인 설정은 그대로 둡니다. 저장 후 새 대화에서 적용을 확인하세요.');
    const fields={};for(const[k,label]of Object.entries({goal:'업무 목표',inputs:'입력 자료',outputs:'원하는 결과물',checks:'완료 확인 기준'})){fields[k]=field(c,label);fields[k].maxLength=600;}
    c.append(button('업무 지침 미리보기',()=>preview({kind:'brief',expectedSha256:item.sha256,...Object.fromEntries(Object.entries(fields).map(([k,n])=>[k,n.value]))}),'send-button'));content.append(c);
  }
  function metricsPanel(title, value, tokenKey='tokens'){
    const c=card(title,value.notice);const values=value[tokenKey]||{};
    const grid=el('dl',null,'metric-grid');for(const[k,t]of Object.entries({input_tokens:'입력',output_tokens:'출력',cache_read_input_tokens:'캐시 재사용',cache_creation_input_tokens:'캐시 작성'})){const box=el('div');box.append(el('dt',t),el('dd',values[k]===null||values[k]===undefined?'미제공':values[k].toLocaleString()));grid.append(box);}c.append(grid);
    return c;
  }
  function usage(){
    const t=data.telemetry||{notice:'업무 시작 후 현재 요청의 CLI 보고값을 표시합니다.'};content.append(metricsPanel('현재 요청 · 관찰된 값',t));
    const p=t.phaseMs||{};const times=card('어디에서 기다렸나요?','앱이 관찰한 구간입니다. 모델 추론·후크·도구 내부 시간을 임의로 나누지 않습니다.');
    const fmt=x=>x===undefined||x===null?'미확인':(x/1000).toFixed(1)+'초';
    times.append(note(`전체 경과 ${fmt(t.wallMs)} / 실행 구간 ${fmt((p.starting||0)+(p.running||0))} / 사용자 승인 대기 ${fmt(p.approval)} / 질문 응답 대기 ${fmt(p.question)}`),note('CLI 보고 비용: '+(t.cliReportedCostUsd==null?'미제공 · 사내 단가를 추정하지 않습니다.':'$'+t.cliReportedCostUsd+' (CLI 보고값, 청구액 검증 아님)')),note('관찰한 스킬 호출: '+(t.skills?.join(', ')||'미관찰 · 목록 부재를 뜻하지 않음')));content.append(times);
    if(t.budgetWarning)times.append(note(t.budgetWarning));
    const budget=field(times,'다음 요청부터 토큰 알림 기준 (빈칸은 해제)','','number');budget.min=1;
    times.append(button('이 대화에만 알림 설정',async()=>{const r=await action({action:'budget',tokenAlert:budget.value===''?null:Number(budget.value)});message(r.notice);}));
    const c=card('선택한 기록만 분석','과거 기록은 자동 검색하지 않습니다. 직접 고른 JSONL만 최대 8개 읽고 원문·경로를 분석 결과에 저장하지 않습니다. 현재 요청 값과 합산하지 않습니다.');
    const paths=field(c,'분석할 JSONL 절대 경로 (한 줄에 하나)');
    c.append(button('선택한 로컬 기록 분석',async()=>{const result=await action({action:'usage',paths:paths.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean)});const out=metricsPanel('선택 로그 · 별도 분석',result);out.append(note(`상태: ${result.status} / 같은 범위 재읽기: ${result.sameRangeReads??'미확인'} / 고유 메시지: ${result.uniqueUsageMessages??'미확인'}`),note(`관찰한 도구 오류 ${result.observedToolErrors??'미확인'} / 재시도 ${result.retryCount??'미확인'}`),note(result.costReason));for(const model of result.byModel||[])out.append(metricsPanel('모델 · '+model.model,model));c.append(out);}));if(data.unavailable) c.append(note(data.unavailable));content.append(c);
  }
  function checks(){
    const c=card('준비 상태와 실제 결과는 다릅니다','파일·목록이 있다는 사실만으로 모델 성능이나 업무 성공을 판정하지 않습니다.');
    for(const check of data.harness?.checks||[])c.append(note(check.label+' · '+(labels[check.status]||check.status)+(check.count!==undefined?' ('+check.count+')':'')));
    if(!data.harness)c.append(note(data.unavailable||'작업 폴더 선택 후 확인할 수 있습니다.'));
    if(data.harness){c.append(note(`스킬 이름 충돌: ${data.harness.skillConflicts} / 목록에 표시된 스킬: ${data.harness.skills.length}`));const d=el('details');d.append(el('summary','발견한 스킬 · 실제 실행과 별개'));for(const s of data.harness.skills)d.append(note(`${s.invocation||s.name} · ${s.source} · ${s.description}`));c.append(d);}content.append(c);
    const a=data.assessment||{total:0,counts:{}};content.append(note(`직접 평가한 기록 ${a.total}건 · ${Object.entries(labels).filter(([k])=>['passed','failed','unverified','cancelled','blocked'].includes(k)).map(([k,v])=>v+' '+(a.counts[k]||0)).join(' / ')}`),note(a.notice||'평가하지 않은 업무를 성공으로 계산하지 않습니다.'));
    const workflowLabels={read:'자료 읽기',report:'결과 만들기',revise:'수정',remember:'기억 저장',reuse:'기억 재사용'};
    for(const group of a.groups||[])content.append(note(`${group.demo?'체험':'앱 대화 관찰'} / ${workflowLabels[group.workflow]||group.workflow} / ${group.model||'모델 미확인'} / 평가 시 설치 ${group.coreVersionAtReview||'미확인'} · ${group.total}건 · 수정 횟수를 답한 ${group.repairsReported}건의 수정 합계 ${group.reportedRepairs}회`));
    if(sid)content.append(actions(button('원문 없는 확인 기록 내려받기',async()=>{const r=await action({action:'records-export',confirmed:true});const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='workspace-self-check-'+Date.now()+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}),button('이 폴더의 확인 기록만 비우기',async trigger=>{if(!await confirmCompanion({title:'이 폴더의 확인 기록을 비울까요?',message:'따라 하기와 자기 확인 기록만 비웁니다. 이 기록은 되돌릴 수 없습니다. 대화·기억·결과 파일은 그대로 남습니다.',confirmLabel:'확인 기록 비우기',danger:true},trigger))return;const r=await action({action:'records-clear',confirmed:true});await load();message(r.notice);} )));
    if(!data.telemetry?.requestId)return content.append(note('업무를 한 번 실행하고 결과를 직접 확인하면 평가를 남길 수 있습니다.'));
    const f=card('이번 결과 직접 확인','대화에 “완료”가 나와도 수치·파일을 직접 확인하세요. 실패·중지·미확인도 남길 수 있습니다.');
    const workflow=select(f,'확인한 업무',[['read','자료 읽기'],['report','결과 만들기'],['revise','수정'],['remember','기억 저장'],['reuse','기억 재사용']],'read');
    const status=select(f,'결과 판정',[['unverified','아직 미확인'],['passed','직접 확인 통과'],['failed','실패'],['cancelled','중지'],['blocked','차단']],'unverified');
    const checks={};for(const[k,text]of Object.entries({content:'내용·수치를 원자료와 대조했어요',scope:'확인 범위와 미확인 부분을 구분했어요',artifact:'결과 파일을 열어 직접 확인했어요',noOverwrite:'기존 파일이 보존됐는지 확인했어요'}))checks[k]=checkbox(f,text);
    const repairs=field(f,'내가 요청한 수정 횟수 (미확인이면 빈칸)','','number');repairs.min=0;repairs.max=100;
    f.append(button('내 확인 결과 저장',async()=>{await action({action:'outcome',confirmed:true,requestId:data.telemetry.requestId,workflow:workflow.value,status:status.value,checks:Object.entries(checks).filter(([,n])=>n.checked).map(([k])=>k),repairs:repairs.value===''?null:Number(repairs.value)});await load();},'send-button'));content.append(f);
  }
  function harnessMap(){
    if(!requireHarness())return;
    if(!data.harness.map||typeof data.harness.html!=='string'){content.append(note('설치된 Company Agent를 같은 배포본으로 업데이트하면 구성 지도를 볼 수 있습니다.'));return;}
    content.append(actions(button('구성 지도 HTML 저장',()=>{
      const url=URL.createObjectURL(new Blob([data.harness.html],{type:'text/html;charset=utf-8'}));
      const link=el('a');link.href=url;link.download='company-agent-harness-map-'+Date.now()+'.html';link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);message('현재 스냅샷을 저장합니다. 경로·항목 이름이 포함되므로 공유 전에 확인하세요.');
    })));
    content.append(note('읽기 전용 · AI 호출 없음 · 저장한 HTML에는 경로와 항목 이름이 포함됩니다.'));
    const frame=el('iframe');frame.className='harness-map-frame';frame.title='회사 공통·개인 전체·이 프로젝트 하네스 구성';
    frame.setAttribute('sandbox','');frame.referrerPolicy='no-referrer';frame.srcdoc=data.harness.html;content.append(frame);
  }
  function render(){
    content.replaceChildren();$('companion-tabs').replaceChildren();
    $('companion-dialog').dataset.view=view;
    const area=view.split('-')[0];
    $('companion-scope').textContent=(data.demo?'체험 모드 · 가상 결과 / ':'')+(view==='guide'?'가상 자료로 시작하는 5단계':area==='shared'?'회사 공통 · 읽기 전용':data.harness?data.harness.scope.label+' · '+(active?.workspace||''):'회사 공통 / 개인 전체 / 이 프로젝트');
    if(domains[area])content.append(note(domains[area]));
    if(data.harness&&view!=='map')content.append(note(data.harness.scope.notice));
    for(const[k,title]of Object.entries(tabs)){const b=button(title,async()=>{view=domains[k]?k+'-memory':k;message('');await load();});b.setAttribute('aria-current',k===area?'page':'false');$('companion-tabs').append(b);}
    $('companion-tabs').append(button('새로고침',load));
    if(domains[area]){const sub=actions();for(const[k,label]of [['memory','기억·지식'],['harness','업무 구성']]){const b=button(label,async()=>{view=area+'-'+k;await load();});b.setAttribute('aria-current',view.endsWith('-'+k)?'page':'false');sub.append(b);}content.append(sub);}
    ({guide,map:harnessMap,'personal-memory':myMemory,'project-memory':myMemory,'shared-memory':()=>knowledge(true),'shared-harness':sharedHarness,'personal-harness':myHarness,'project-harness':myHarness,usage,checks}[view])();
    $('companion-dialog').scrollTop=0;
  }
  $('learn-open').onclick=()=>open('guide').catch(e=>message(e.message));
  $('memory-open').onclick=()=>open('memory').catch(e=>message(e.message));
  $('usage-open').onclick=()=>open('usage').catch(e=>message(e.message));
  $('companion-close').onclick=()=>{$('companion-dialog').close();generation++;};
})();
