'use strict';
// Fixed guidance only. Never copy raw CLI/server errors or credentials into the dialog.
globalThis.WorkspaceErrorHelp = (() => {
  const editModelSettings = '기본 모델을 바꾸려면 이 창을 닫고 오류 아래의 ‘Claude 설정 파일 열기’를 누르세요. 메모장에서 현재 연결의 사용자 설정 파일(settings.json)을 수정하고 저장한 뒤, 앱 상단의 ‘Claude Code 재시작’을 누르세요.';
  const launchReasons = {
    profile_parse:'PowerShell이 시작 설정 파일의 문법을 읽지 못했어요.',
    profile_load:'PowerShell이 시작 설정 파일을 불러오다가 멈췄어요.',
    command_not_found:'이 실행 환경에서 Claude 명령을 찾지 못했어요.',
    config_root:'Claude 설정 폴더의 전체 경로를 확인해 주세요.',
    function_changed:'다시 불러온 Claude 함수가 처음 확인한 함수와 달라요.',
    cli_start:'Claude 실행 파일을 시작하지 못했어요.',
    cli_version:'Claude 실행 버전을 확인하지 못했어요.',
    cli_options:'Claude가 앱에 필요한 실행 옵션을 제공하는지 확인하지 못했어요.',
    cli_initialize:'Claude의 첫 연결 응답을 확인하지 못했어요.',
    cli_protocol:'Claude의 연결 응답 형식을 읽지 못했어요.'
  };
  const stages = {launcher_profile:'실행기 프로필',profile_restore:'프로필 불러오기',command_resolution:'명령 찾기',
    function_restore:'함수 확인',process_start:'프로세스 시작',version:'버전 확인',help:'실행 옵션 확인',
    initialize:'첫 연결 응답',protocol:'응답 형식 확인'};
  let shellControls;
  function diagnosticSummary(d, nested=false) {
    if(!d || typeof d!=='object')return '';
    const lines=[], ctx=shellControls?.context()||{};
    if(!nested&&/^\d+\.\d+\.\d+$/.test(ctx.version))lines.push('AX Workspace '+ctx.version);
    if(!nested&&/^[a-f0-9]{64}$/i.test(ctx.build))lines.push('빌드 '+ctx.build.slice(0,12));
    if(!nested&&ctx.root){
      const parts=String(ctx.root).toLowerCase().replaceAll('\\','/').split('/');
      const updated=parts.some((part,i)=>part==='updates'&&parts[i+1]==='versions');
      lines.push('실행 위치  '+(updated?'업데이트 버전 폴더':parts.includes('workspace-runtime')?'설치 실행 폴더':'별도 실행 폴더'));
    }
    if(Object.hasOwn(launchReasons,d.category))lines.push('진단  '+d.category);
    if(Object.hasOwn(stages,d.stage))lines.push('단계  '+stages[d.stage]+' ('+d.stage+')');
    if(/^\d{1,2}\.\d{1,3}(?:\.\d{1,6}){0,2}$/.test(d.shellVersion))lines.push('PowerShell '+d.shellVersion);
    if(['Function','Application','ExternalScript','Alias','Unknown'].includes(d.commandType))lines.push('명령 유형  '+d.commandType);
    if(['exe','cmd','bat','ps1','function'].includes(d.entryKind))lines.push('실행 항목  '+d.entryKind);
    if(d.exceptionType==='ParseException')lines.push('예외 유형  ParseException');
    if(typeof d.loadProfiles==='boolean')lines.push('호출 시 프로필 로드  '+(d.loadProfiles?'예':'아니요'));
    if(Number.isInteger(d.errorLine)&&d.errorLine>0)lines.push('오류 위치  '+d.errorLine+'행');
    if(d.encodingHint==='utf8_without_bom')lines.push('인코딩  같은 파일을 UTF-8로 해석했을 때 문법 검사를 통과했어요.');
    if(!nested&&d.startupProfile?.stage==='launcher_profile'&&Object.hasOwn(launchReasons,d.startupProfile.category)){
      lines.push('','실행기 시작 시 관찰',diagnosticSummary(d.startupProfile,true));
    }
    return lines.join('\n');
  }
  const entries = {
    not_found: ['Claude Code를 찾지 못했어요', '앱이 현재 실행 환경에서 Claude 명령을 찾지 못했어요.', ['Claude Code가 설치되지 않았거나 앱의 실행 경로에서 보이지 않을 수 있어요.', '터미널과 앱의 실행 계정이나 환경이 다를 수 있어요.'], ['Windows 시작 메뉴에서 PowerShell을 열고 claude --version을 입력해 실행되는지 확인하세요.', '명령을 찾지 못하면 회사의 Claude Code 설치 안내를 따르거나 담당자에게 문의하세요.', '터미널에서는 되는데 앱에서만 안 되면 앱의 설정에서 완전히 종료한 뒤 같은 Windows 계정으로 다시 열어 보세요.']],
    unsupported: ['Claude 연결 기능을 확인해 주세요', '현재 Claude Code가 앱에 필요한 연결 옵션을 제공하지 않았어요.', ['Claude Code 버전이 오래됐거나 다른 실행 파일을 사용 중일 수 있어요.'], ['Windows 시작 메뉴에서 PowerShell을 열고 claude --version을 입력해 버전을 확인하세요.', '회사에 정해진 업데이트 방법이 있으면 그 안내를 따르세요. 그렇지 않으면 터미널에서 claude를 실행하고 Claude에게 ‘최신 버전으로 업데이트해 줘’라고 요청하세요. 실행 전 제안된 변경을 확인하세요.', 'Claude가 실행되지 않거나 업데이트가 제한되면 회사 담당자에게 업데이트 방법을 문의하세요.', '업데이트가 완료됐는지 확인한 뒤 앱의 설정에서 완전히 종료하고 다시 열어 연결해 보세요.']],
    auth: ['Claude 인증을 확인해 주세요', 'Claude Code가 인증 실패를 알려왔어요.', ['로그인이 만료됐거나, 앱이 평소 쓰는 터미널과 다른 설정 폴더 또는 실행 파일을 사용하고 있을 수 있어요.'], ['같은 Windows 계정에서 평소 쓰는 PowerShell을 열고 claude를 실행해 간단한 질문에 답변이 나오는지 확인하세요.', '터미널에서 답변이 오면 아래의 ‘터미널과 경로 비교’를 여세요. 터미널에서 사용하는 설정 폴더와 실행 파일을 앱이 찾은 위치와 비교할 수 있어요.', '터미널에서도 인증 오류가 나면 회사의 Claude 로그인 또는 인증 갱신 안내를 따르세요.'], '경로를 비교할 때는 폴더나 파일의 위치만 사용해요.'],
    timeout: ['Claude 연결 준비가 오래 걸려요', 'Claude가 준비 응답을 제시간에 보내지 않았어요.', ['로그인 대기, MCP 연결, 시작 후크 또는 네트워크 지연이 원인일 수 있어요.'], ['같은 Windows 계정에서 PowerShell을 열고 claude를 입력해 정상적으로 시작되는지 확인하세요.', '터미널에서도 오래 걸리면 로그인 안내, 네트워크 및 최근 추가한 MCP 연결을 확인하거나 담당자에게 문의하세요.', '문제를 확인한 뒤 앱의 연결 다시 확인을 누르고 다시 요청하세요.']],
    closed: ['Claude 연결이 종료됐어요', 'Claude 연결이 예상보다 일찍 종료됐어요.', ['인증, 네트워크, CLI 실행 환경이나 보안 프로그램 등 여러 원인이 가능해요.'], ['오류가 나기 전의 안내와 발생 시점을 확인하세요.', '대화와 이번 결과에서 이미 수행된 작업 및 파일 변경을 먼저 확인하세요.', '같은 계정에서 PowerShell을 열고 claude가 정상 실행되는지 확인한 뒤 앱의 연결 다시 확인을 누르세요. 계속되면 오류 내용과 시점을 담당자에게 알려 주세요.']],
    model_rejected: ['모델 변경이 거절됐어요', '연결된 Claude가 모델 변경을 받아들이지 않았어요.', ['모델 이름이나 연결 설정이 맞지 않거나 해당 CLI가 변경을 지원하지 않을 수 있어요.'], ['현재 진행 중인 작업이 끝날 때까지 기다리세요.', '입력창 아래의 모델 버튼을 눌러 회사에서 사용할 수 있는 모델을 고르거나 기존 설정 사용을 선택하세요.', editModelSettings, '계속 거절되면 연결 다시 확인을 누른 뒤 회사 모델 설정과 Claude Code 버전을 확인해 주세요.']],
    model_timeout: ['모델 변경 결과를 확인하지 못했어요', 'Claude의 모델 변경 응답이 제시간에 오지 않았어요.', ['연결이 끊겼거나 CLI 응답이 지연됐을 수 있어요.'], ['계속할 준비가 되면 같은 대화에 필요한 다음 요청을 보내세요. 다시 연결하며 이전 요청은 자동으로 재실행하지 않습니다.', '입력창 아래의 모델 버튼에서 현재 표시된 모델을 확인하세요.', '계속 반복되면 같은 계정의 터미널에서 claude를 실행해 모델 변경이 되는지 확인하거나 담당자에게 문의하세요.']],
    model_permission: ['모델 사용 권한을 확인해 주세요', '현재 선택한 모델을 사용할 권한이 없다고 응답했어요.', ['선택 모델과 KEY 또는 팀에 허용된 모델이 다를 수 있어요.', '모델 목록은 설정된 후보이며, 목록에 있다고 사용 권한이 확인된 것은 아니에요.'], ['다시 요청하기 전에 현재 대화와 이번 결과에서 이미 수행된 작업과 파일 변경을 확인하세요.', '오류 아래의 모델 선택 또는 입력창 아래의 모델 버튼에서 회사가 허용한 모델을 선택한 뒤 필요한 내용만 요청하세요.', '기존 설정 사용은 처음 연결한 모델로 돌아갑니다. 그 모델에도 권한이 없으면 다른 허용 모델을 선택하세요.', editModelSettings, '계속되면 사내 LLM 서비스 담당자에게 팀과 모델 이름을 알려 주고 접근 권한을 확인해 달라고 요청하세요.'], 'KEY나 토큰 값은 공유하지 않아도 됩니다.'],
    model_not_found: ['사용할 모델을 다시 선택해 주세요', '현재 연결에서 요청한 모델을 찾을 수 없거나 지원하지 않는다고 응답했어요.', ['모델 이름이나 Claude 설정의 별칭이 현재 회사 서비스와 맞지 않을 수 있어요.'], ['현재 대화와 이번 결과에서 이미 수행된 작업과 파일 변경을 확인하세요.', '오류 아래의 모델 선택에서 회사가 안내한 모델을 선택하거나 정확한 모델 이름을 입력하세요.', editModelSettings, '필요한 내용만 다시 요청하세요. 모델 변경만으로 이전 요청이 자동 전송되지는 않습니다.'], '목록에 표시되는 모델의 실제 사용 가능 여부는 회사 서비스의 응답으로 확인됩니다.'],
    diagnostic: ['진행 결과를 먼저 확인해 주세요', 'Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요.', ['연결 중단이나 요청 전환 등 여러 상황에서 발생할 수 있어 이 안내만으로 원인을 확정할 수 없어요.'], ['이 창을 닫고 현재 대화에서 마지막 진행 내용과 답변을 확인하세요.', '오른쪽 이번 결과와 업무 폴더에서 이미 만들어지거나 변경된 파일을 확인하세요.', '확인한 결과를 바탕으로 필요한 내용만 같은 대화에 적어 보내세요.'], '이전 요청은 자동으로 다시 실행하지 않습니다.'],
    generic: ['오류 내용을 확인해 주세요', '현재 표시된 오류만으로 원인을 확정하기 어려워요.', ['연결, 도구, 설정 또는 저장 위치 등 여러 원인이 있을 수 있어요.'], ['이 창을 닫고 상단의 오류 문구와 발생 시점을 확인하세요.', '작업 중이었다면 대화와 이번 결과에서 이미 수행된 작업과 파일 변경을 먼저 확인하세요.', '필요한 내용만 다시 요청하고, 반복되면 오류 문구와 발생 시점을 담당자에게 알려 주세요.'], 'KEY나 토큰 값은 전달하지 마세요.']
  };
  function classify(message, code) {
    const s=String(message||'').toLowerCase(), c=String(code||'').toLowerCase();
    if(c.startsWith('launch_')&&Object.hasOwn(launchReasons,c.slice(7)))return 'launch';
    if (c==='model_permission' || /\b(?:team|key|token|user)[^\n]{0,200}\bnot allowed[^\n]{0,100}\bmodel\b/.test(s) || /\b(?:no|not have|does not have|do not have)[^\n]{0,40}\b(?:access|permission)[^\n]{0,80}\bmodel\b/.test(s)) return 'model_permission';
    if (c==='model_not_found') return 'model_not_found';
    if (c==='cli_turn_incomplete'||s.includes('ede_diagnostic')) return 'diagnostic';
    if (c==='model_timeout'||s.includes('모델 변경 응답을 확인하지 못해')) return 'model_timeout';
    if (c==='model_rejected'||s.includes('모델 변경이 거절')) return 'model_rejected';
    if (c==='cli_authentication'||c==='authentication_failed'||s.includes('authentication_failed')||s.includes('인증을 확인하지 못했')||s.includes('failed to authenticate')||s.includes('oauth session expired')||s.includes('invalid authentication credentials')||s.includes('not logged in')) return 'auth';
    if (s.includes('cli 준비 응답이 60초')) return 'timeout';
    if (s.includes('claude code를 찾지 못했')||s.includes('기존 터미널의 claude 실행 명령을 확인하지 못했')) return 'not_found';
    if (s.includes('필요한 대화 연결 기능이 없습니다')) return 'unsupported';
    if (s.includes('cli 연결이 종료되었')||s.includes('cli 연결이 종료됐')) return 'closed';
    return 'generic';
  }
  let current=null;
  const node=id=>document.getElementById(id);
  function update(message, code, owner, diagnostic){
    const category=diagnostic?.category||String(code||'').replace(/^launch_/,'');
    const launch=Object.hasOwn(launchReasons,category);
    const guide=launch?classify(message):null;
    const startup=diagnostic?.startupProfile;
    const observed=startup?.stage==='launcher_profile'&&['profile_parse','profile_load'].includes(startup.category);
    const next=message?{kind:launch?'launch':classify(message,code),guide,owner:owner||null,
      diagnostic:launch?{...diagnostic,category}:observed?{startupProfile:startup}:null}:null;
    const dialog=node('error-help-dialog');
    if(dialog?.open && (!next || !current || next.kind!==current.kind || next.owner!==current.owner || diagnosticSummary(next.diagnostic)!==diagnosticSummary(current.diagnostic)))dialog.close();
    current=next;
  }
  function open(show){
    if(!current)return;
    const d=current.diagnostic;
    const launchCauses={profile_parse:'파일의 문법이나 PowerShell이 문자를 읽는 방식에 따라 시작 설정을 불러오지 못할 수 있어요.',
      config_root:'설정 폴더가 이동했거나 위치를 찾을 수 없어요. 실행 환경 확인을 열고 ‘터미널과 경로 비교’에서 폴더를 다시 선택하거나 ‘자동으로 되돌리기’를 누른 뒤 적용하세요.',
      profile_load:'시작 설정에서 참조하는 파일이나 명령이 이 환경에서 제공되는지 확인하세요.',
      function_changed:'터미널을 열 때마다 다른 함수가 만들어지거나 서로 다른 프로필을 사용했을 수 있어요.',
      command_not_found:'앱과 평소 터미널이 서로 다른 시작 설정이나 실행 경로를 쓸 수 있어요.'};
    const related=d&&(d.category==='cli_options'&&current.guide==='unsupported'?'unsupported':
      d.category==='command_not_found'?'not_found':d.category==='cli_initialize'&&current.guide==='timeout'?'timeout':null);
    const launchHelp=current.kind==='launch'&&[related?entries[related][0]:'Claude 실행 환경을 확인해 주세요',launchReasons[d.category],
      [d.encodingHint==='utf8_without_bom'?'이 파일은 UTF-8로 읽었을 때 문법 검사를 통과했어요. 평소 사용하는 PowerShell에서 점검해 보세요.':launchCauses[d.category]||'사용 중인 Claude 실행 버전과 연결에 필요한 옵션을 확인해 주세요.'],
      ['아래의 실행 환경 확인을 열고, 터미널에서 Claude가 정상 작동하는 PowerShell을 선택해 점검하세요.',
       ...(related?entries[related][3]:[]),
       '도움이 필요하면 진단 요약을 복사해 담당자에게 전달하세요.']];
    const [title,summary,causes,actions,note]=launchHelp||entries[current.kind], dialog=node('error-help-dialog');
    const comparePaths=current.kind==='auth';
    node('error-launch-details').hidden=!d&&!comparePaths;
    node('error-launch-summary').hidden=node('error-launch-copy').hidden=!d;
    node('error-launch-open').textContent=comparePaths?'터미널과 경로 비교':'실행 환경 확인';
    node('error-launch-summary').textContent=diagnosticSummary(d);
    node('error-help-title').textContent=title;node('error-help-summary').textContent=summary;
    const visibleActions = node('recovery-settings-file')?.hidden === false
      ? actions : actions.filter(step => step !== editModelSettings);
    for(const [id,values] of [['error-help-causes',causes],['error-help-actions',visibleActions]]){
      const list=node(id);list.replaceChildren(...values.map(value=>{const li=document.createElement('li');li.textContent=value;return li;}));
    }
    const footer=node('error-help-note');footer.textContent=note||'안내를 확인한 뒤 필요한 작업을 직접 선택해 주세요.';
    if(!dialog.open){show();node('error-help-close').focus({preventScroll:true});}
  }
  function bindShell(controls){
    shellControls=controls;
    const panel=node('claude-shell-panel'), select=node('claude-shell-choice'), status=node('claude-shell-status');
    const check=node('claude-shell-check'), apply=node('claude-shell-apply'), copy=node('claude-shell-copy');
    const report=node('claude-shell-diagnostic');
    const paths=node('claude-shell-paths');
    const configDir=node('claude-config-dir'), executable=node('claude-executable');
    const configPick=node('claude-config-pick'), executablePick=node('claude-executable-pick');
    const configClear=node('claude-config-clear'), executableClear=node('claude-executable-clear');
    let saved=null, busy=false, dialogGeneration=0;
    node('settings-dialog').addEventListener('close',()=>{dialogGeneration++;});
    const pathValue=input=>input.value.trim().replace(/^"([^"]+)"$/,'$1');
    function isEdited(){
      return saved&&(select.value!==saved.choice||pathValue(configDir)!==saved.configDir||pathValue(executable)!==saved.executable);
    }
    const lock=()=>{
      select.disabled=busy||!saved;
      for(const control of [check,configDir,executable,configPick,executablePick,configClear,executableClear])control.disabled=select.disabled;
      configClear.hidden=!configDir.value;
      executableClear.hidden=!executable.value;
      apply.disabled=select.disabled||!isEdited();
    };
    function showSaved(config){
      saved={choice:config.choice,configDir:config.configDir||'',executable:config.executable||''};
      select.value=saved.choice;configDir.value=saved.configDir;executable.value=saved.executable;
      node('claude-config-current').textContent=config.runtime?.configRoot||'아직 확인하지 못했어요.';
      node('claude-executable-current').textContent=config.runtime?.entry||'아직 확인하지 못했어요.';
    }
    function showReport(d){
      report.textContent=diagnosticSummary(d);
      copy.hidden=report.hidden=!report.textContent;
    }
    async function copyReport(value){
      if(!value)return;
      try{await navigator.clipboard.writeText(value);controls.toast('진단 요약을 복사했어요.');}
      catch{controls.toast('복사할 수 없어요. 표시된 진단 내용을 선택해 복사하세요.');}
    }
    panel.ontoggle=async()=>{
      if(!panel.open||busy)return;
      if(controls.context().demo){status.textContent='실행 환경 점검은 실제 앱 연결에서 사용할 수 있어요.';return;}
      busy=true;lock();showReport(null);status.textContent='설정을 불러오고 있어요.';
      try{
        const config=await controls.api('/api/claude-shell');
        for(const option of select.options)option.disabled=config.available?.[option.value]===false;
        showSaved(config);
        status.textContent='선택한 환경에서 Claude 실행과 연결에 필요한 옵션을 확인할 수 있어요.';
      }catch{saved=null;status.textContent='설정을 불러올 수 없어요. 이 항목을 닫았다 다시 열어 주세요.';}
      finally{busy=false;lock();}
    };
    async function probe(checkOnly){
      if(busy||!saved||!checkOnly&&!isEdited())return;
      busy=true;lock();showReport(null);status.textContent='선택한 PowerShell에서 Claude 실행을 확인하고 있어요…';
      try{
        const result=await controls.api('/api/claude-shell',{choice:select.value,checkOnly,configDir:pathValue(configDir),executable:pathValue(executable)});
        showReport(result.diagnostic);
        if(checkOnly){
          status.textContent='Claude 실행과 연결에 필요한 옵션을 확인했어요. 로그인과 모델 권한은 실제 요청에서 확인돼요.';
        }else{
          showSaved(result);
          status.textContent='저장했어요. 다음에 새로 연결할 때부터 이 환경을 사용해요. 진행 중인 업무는 계속됩니다.';
          try{await controls.applied();}catch{status.textContent+=' 앱을 다시 열면 저장한 설정이 표시돼요.';}
        }
        if(result.chatSupported===false)status.textContent+=' 이 버전에서는 일반 대화에 필요한 기능을 확인하지 못했어요.';
      }catch(err){
        status.textContent=err.message||launchReasons[err.diagnostic?.category]||'실행 환경을 확인할 수 없어요.';
        showReport(err.diagnostic);
      }finally{busy=false;lock();}
    }
    function edited(){
      showReport(null);
      status.textContent=isEdited()?'점검 후 적용을 누르면 이후 새 연결에 사용해요.':'현재 사용 중인 설정이에요.';
      lock();
    }
    function bindPathField(input,pick,clear,kind,commandId){
      input.oninput=edited;
      clear.onclick=()=>{input.value='';edited();input.focus();};
      pick.onclick=async()=>{
        if(busy||!saved)return;
        const generation=dialogGeneration;
        busy=true;lock();
        try{
          const result=await controls.api('/api/pick',{kind});
          if(generation!==dialogGeneration||!panel.open||!node('settings-dialog').open||result.status==='cancel')return;
          if(result.status!=='success'||result.paths?.length!==1)throw Error('경로 하나를 선택해 주세요.');
          input.value=result.paths[0];edited();
        }catch(err){if(generation===dialogGeneration)status.textContent=err.message||'선택 창을 열 수 없어요. 경로를 직접 입력해 주세요.';}
        finally{busy=false;lock();}
      };
      node(commandId+'-copy').onclick=async()=>{
        try{await navigator.clipboard.writeText(node(commandId).textContent);controls.toast('명령을 복사했어요. PowerShell에 붙여넣고 Enter를 누르세요.');}
        catch{node('claude-path-commands').open=true;node(commandId).scrollIntoView({block:'nearest'});controls.toast('아래 명령을 직접 선택해 복사하세요.');}
      };
    }
    select.onchange=edited;
    bindPathField(configDir,configPick,configClear,'claude-config','claude-config-command');
    bindPathField(executable,executablePick,executableClear,'claude-executable','claude-executable-command');
    check.onclick=()=>probe(true);apply.onclick=()=>probe(false);
    copy.onclick=()=>copyReport(report.textContent);
    node('error-launch-copy').onclick=()=>copyReport(node('error-launch-summary').textContent);
    node('error-launch-open').onclick=()=>{
      node('error-help-dialog').close();if(!node('settings-dialog').open)controls.openSettings();
      panel.open=true;
      if(current?.kind==='auth'){
        paths.open=true;node('claude-shell-paths-summary').focus({preventScroll:true});
        paths.scrollIntoView({block:'start'});
      }else{panel.querySelector('summary').focus();}
    };
  }
  return {classify,update,open,bindShell};
})();
