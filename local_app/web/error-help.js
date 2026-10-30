'use strict';
// Fixed guidance only. Never copy raw CLI/server errors or credentials into the dialog.
globalThis.WorkspaceErrorHelp = (() => {
  const entries = {
    not_found: ['Claude Code를 찾지 못했어요', '앱이 현재 실행 환경에서 Claude 명령을 찾지 못했어요.', ['Claude Code가 설치되지 않았거나 앱의 실행 경로에서 보이지 않을 수 있어요.', '터미널과 앱의 실행 계정이나 환경이 다를 수 있어요.'], ['Windows 시작 메뉴에서 PowerShell을 열고 claude --version을 입력해 실행되는지 확인하세요.', '명령을 찾지 못하면 회사의 Claude Code 설치 안내를 따르거나 담당자에게 문의하세요.', '터미널에서는 되는데 앱에서만 안 되면 앱의 설정에서 완전히 종료한 뒤 같은 Windows 계정으로 다시 열어 보세요.']],
    unsupported: ['Claude 연결 기능을 확인해 주세요', '현재 Claude Code가 앱에 필요한 연결 옵션을 제공하지 않았어요.', ['Claude Code 버전이 오래됐거나 다른 실행 파일을 사용 중일 수 있어요.'], ['Windows 시작 메뉴에서 PowerShell을 열고 claude --version을 입력해 버전을 확인하세요.', '회사에 정해진 업데이트 방법이 있으면 그 안내를 따르세요. 그렇지 않으면 터미널에서 claude를 실행하고 Claude에게 ‘최신 버전으로 업데이트해 줘’라고 요청하세요. 실행 전 제안된 변경을 확인하세요.', 'Claude가 실행되지 않거나 업데이트가 제한되면 회사 담당자에게 업데이트 방법을 문의하세요.', '업데이트가 완료됐는지 확인한 뒤 앱의 설정에서 완전히 종료하고 다시 열어 연결해 보세요.']],
    auth: ['Claude 인증을 확인해 주세요', 'Claude Code가 인증 실패를 알려왔어요.', ['인증이 만료됐거나 앱과 터미널의 실행 환경 또는 사용 가능한 모델 설정이 다를 수 있어요.'], ['같은 Windows 계정에서 PowerShell을 열고 claude를 실행해 간단한 질문에 답변이 나오는지 확인하세요.', 'PowerShell에서 cmd /c "claude -p 안녕하세요"도 입력해 답변이 나오는지 확인하세요.', '터미널에서 오류가 나면 모델을 사용할 수 있는지 확인하고, 인증 안내가 나오면 회사의 Claude 로그인 또는 KEY 갱신 안내를 따르세요.', '터미널에서만 정상이면 앱에서 선택한 모델과 Claude 기본 모델을 확인하고 앱을 완전히 종료한 뒤 다시 열어 보세요.', '계속되면 담당자에게 실행 계정과 앱의 실행 위치 및 설정 위치 확인을 요청하세요.'], 'KEY나 토큰 값은 다른 사람에게 전달하지 마세요.'],
    timeout: ['Claude 연결 준비가 오래 걸려요', 'Claude가 준비 응답을 제시간에 보내지 않았어요.', ['로그인 대기, MCP 연결, 시작 후크 또는 네트워크 지연이 원인일 수 있어요.'], ['같은 Windows 계정에서 PowerShell을 열고 claude를 입력해 정상적으로 시작되는지 확인하세요.', '터미널에서도 오래 걸리면 로그인 안내, 네트워크 및 최근 추가한 MCP 연결을 확인하거나 담당자에게 문의하세요.', '문제를 확인한 뒤 앱의 연결 다시 확인을 누르고 다시 요청하세요.']],
    closed: ['Claude 연결이 종료됐어요', 'Claude 연결이 예상보다 일찍 종료됐어요.', ['인증, 네트워크, CLI 실행 환경이나 보안 프로그램 등 여러 원인이 가능해요.'], ['오류가 나기 전의 안내와 발생 시점을 확인하세요.', '대화와 이번 결과에서 이미 수행된 작업 및 파일 변경을 먼저 확인하세요.', '같은 계정에서 PowerShell을 열고 claude가 정상 실행되는지 확인한 뒤 앱의 연결 다시 확인을 누르세요. 계속되면 오류 내용과 시점을 담당자에게 알려 주세요.']],
    model_rejected: ['모델 변경이 거절됐어요', '연결된 Claude가 모델 변경을 받아들이지 않았어요.', ['모델 이름이나 연결 설정이 맞지 않거나 해당 CLI가 변경을 지원하지 않을 수 있어요.'], ['현재 진행 중인 작업이 끝날 때까지 기다리세요.', '입력창 아래의 모델 버튼을 눌러 회사에서 사용할 수 있는 모델을 고르거나 기존 설정 사용을 선택하세요.', '계속 거절되면 연결 다시 확인을 누른 뒤 회사 모델 설정과 Claude Code 버전을 확인해 주세요.']],
    model_timeout: ['모델 변경 결과를 확인하지 못했어요', 'Claude의 모델 변경 응답이 제시간에 오지 않았어요.', ['연결이 끊겼거나 CLI 응답이 지연됐을 수 있어요.'], ['계속할 준비가 되면 같은 대화에 필요한 다음 요청을 보내세요. 다시 연결하며 이전 요청은 자동으로 재실행하지 않습니다.', '입력창 아래의 모델 버튼에서 현재 표시된 모델을 확인하세요.', '계속 반복되면 같은 계정의 터미널에서 claude를 실행해 모델 변경이 되는지 확인하거나 담당자에게 문의하세요.']],
    model_permission: ['모델 사용 권한을 확인해 주세요', '요청한 모델을 현재 연결에서 사용할 권한이 없을 수 있어요.', ['선택 모델과 KEY 또는 팀에 허용된 모델이 다를 수 있어요.', '모델 별칭이 실제 요청에서 다른 이름으로 연결될 수도 있어요.'], ['오류 문구에서 요청한 모델과 사용 가능한 모델 이름을 확인하세요.', '다시 요청하기 전에 현재 대화와 이번 결과에서 이미 수행된 작업과 파일 변경을 확인하세요.', '입력창 아래의 모델 버튼에서 허용된 모델을 선택하거나 기존 설정 사용을 선택한 뒤 필요한 내용만 요청하세요.', '계속되면 사내 LLM 서비스 담당자에게 팀과 모델 이름을 알려 주고 접근 권한을 확인해 달라고 요청하세요.'], 'KEY나 토큰 값은 공유하지 않아도 됩니다.'],
    diagnostic: ['진행 결과를 먼저 확인해 주세요', 'Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요.', ['연결 중단이나 요청 전환 등 여러 상황에서 발생할 수 있어 이 안내만으로 원인을 확정할 수 없어요.'], ['이 창을 닫고 현재 대화에서 마지막 진행 내용과 답변을 확인하세요.', '오른쪽 이번 결과와 업무 폴더에서 이미 만들어지거나 변경된 파일을 확인하세요.', '확인한 결과를 바탕으로 필요한 내용만 같은 대화에 적어 보내세요.'], '이전 요청은 자동으로 다시 실행하지 않습니다.'],
    generic: ['오류 내용을 확인해 주세요', '현재 표시된 오류만으로 원인을 확정하기 어려워요.', ['연결, 도구, 설정 또는 저장 위치 등 여러 원인이 있을 수 있어요.'], ['이 창을 닫고 상단의 오류 문구와 발생 시점을 확인하세요.', '작업 중이었다면 대화와 이번 결과에서 이미 수행된 작업과 파일 변경을 먼저 확인하세요.', '필요한 내용만 다시 요청하고, 반복되면 오류 문구와 발생 시점을 담당자에게 알려 주세요.'], 'KEY나 토큰 값은 전달하지 마세요.']
  };
  function classify(message, code) {
    const s=String(message||'').toLowerCase(), c=String(code||'').toLowerCase();
    if ((s.includes('403') && /team[\s\S]*not allowed[\s\S]*model/.test(s)) || c==='model_permission') return 'model_permission';
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
  function update(message, code, owner){
    const next=message?{kind:classify(message,code),owner:owner||null}:null;
    const dialog=node('error-help-dialog');
    if(dialog?.open && (!next || !current || next.kind!==current.kind || next.owner!==current.owner))dialog.close();
    current=next;
  }
  function open(show){
    if(!current)return;
    const [title,summary,causes,actions,note]=entries[current.kind], dialog=node('error-help-dialog');
    node('error-help-title').textContent=title;node('error-help-summary').textContent=summary;
    for(const [id,values] of [['error-help-causes',causes],['error-help-actions',actions]]){
      const list=node(id);list.replaceChildren(...values.map(value=>{const li=document.createElement('li');li.textContent=value;return li;}));
    }
    const footer=node('error-help-note');footer.textContent=note||'안내를 확인한 뒤 필요한 작업을 직접 선택해 주세요.';
    if(!dialog.open){show();node('error-help-close').focus({preventScroll:true});}
  }
  return {classify,update,open};
})();
