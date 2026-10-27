"use strict";
globalThis.WorkspaceLlmUsage = (() => {
  let api, timer, pending = false, stopped = false, current, urlEdited = false;
  const $ = id => document.getElementById(id);
  function fitPanel(){
    const panel=$('llm-usage-details');if(!panel||panel.hidden)return;
    const bottom=innerWidth<=700?innerHeight-16:$('llm-usage-toggle').getBoundingClientRect().bottom;
    panel.style.maxHeight=Math.max(180,Math.min(innerHeight-32,bottom-16))+'px';
  }
  const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(value);
  const reasons = {key_required:'LLM Key 확인이 필요합니다.', not_configured:'사용량 연결 설정이 필요합니다.',
    no_teams:'조회할 팀을 확인해 주세요.', no_session:'서버의 연결 세션을 확인하지 못했어요.',
    dns_error:'사용량 서버 주소를 찾지 못했어요. 사내망 연결을 확인해 주세요.',
    connection_error:'사용량 서버에 연결하지 못했어요.', timeout:'사용량 서버의 응답 시간이 초과됐어요.',
    tls_error:'사용량 서버 인증서를 확인하지 못했어요.', proxy_auth_required:'네트워크 프록시 인증이 필요해요.',
    rate_limited:'조회 요청이 많아요. 잠시 뒤 다시 확인해 주세요.', http_error:'사용량 서버가 오류를 반환했어요.',
    redirect_blocked:'서버 주소가 다른 위치로 이동했어요. MCP 주소를 확인해 주세요.',
    server_error:'서버에서 조회를 처리하지 못했어요. KEY의 팀·권한을 확인해 주세요.',
    bad_payload:'서버 응답을 해석하지 못했어요. 연결 진단을 확인해 주세요.'};
  const stages = {configuration:'주소 설정', credentials:'Claude KEY 확인', initialize:'서버 연결 초기화',
    'notifications/initialized':'초기화 완료 알림',list_my_teams:'내 팀 목록 조회',get_team_budget_usage:'팀 사용량 조회',client:'앱 내부 처리'};
  function renderReset(value) {
    const field = $('llm-usage-reset'), note = $('llm-usage-reset-note');
    field.textContent = value ? '시각 확인 필요' : '제공되지 않음';
    field.removeAttribute('title');
    note.hidden = true;
    note.textContent = '';
    if (typeof value !== 'string' || !value.trim()) return;
    const raw = value.trim();
    // Deployment convention: a timestamp without an offset is UTC. This
    // matches the reported 09:00 KST reset, but is not inferred from its hour.
    // Explicit Z/offsets always win. Never add nine hours to an already zoned value.
    const parts = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(Z|[+-]\d{2}:?\d{2})?$/i.exec(raw);
    field.title = `서버 reset_at: ${raw}`;
    if (!parts) return;
    const [, year, month, day, hour, minute, second, zone] = parts;
    const days = new Date(Date.UTC(+year, +month, 0)).getUTCDate();
    if (+month < 1 || +month > 12 || +day < 1 || +day > days || +hour > 23 || +minute > 59 || +(second || 0) > 59) return;
    const iso = raw.replace(' ', 'T').replace(/z$/i, 'Z') + (zone ? '' : 'Z');
    const date = new Date(iso);
    if (!Number.isFinite(+date)) return;
    field.textContent = date.toLocaleString('ko-KR', {timeZone:'Asia/Seoul', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}) + ' KST';
    if (!zone) {
      note.textContent = '서버가 시간대를 제공하지 않아 UTC 기준으로 표시해요.';
      note.hidden = false;
      field.title += ' · 시간대 없음: UTC 기준';
    }
  }
  function scheduleRefresh() {
    if (stopped) return;
    const fallback = current?.loading ? 1000 : current?.error ? 60000 : 300000;
    const delay = current?.loading ? 1000 : Number.isFinite(current?.pollAfterMs) ? current.pollAfterMs : fallback;
    timer = setTimeout(() => refresh(), Math.max(1000, Math.min(300000, delay)));
  }
  function render(value) {
    current = value;
    const error = value.error, failed = !!(error || value.retry), state = $('llm-usage-state');
    // Keep team selection metadata, but never present a cached budget as
    // available balance after a failed observation (including automatic retries).
    const known = value.usage, u = failed ? null : known;
    const unavailable = error==='http_error' && value.diagnostic?.httpStatus===503;
    const message = value.loading ? value.retry ? `자동으로 다시 확인 중 (${value.retry.attempt}/${value.retry.limit})` : '사용량 확인 중…'
      : unavailable ? '사용량 서버가 일시적으로 응답하지 못했어요. 잠시 뒤 자동으로 다시 확인합니다.'
      : error ? reasons[error] || '사용량을 조회하지 못했어요.' : '';
    $('llm-usage').dataset.level = failed ? 'unavailable' : u?.percent >= 95 ? 'danger' : u?.percent >= 80 ? 'warning' : 'normal';
    $('llm-usage-percent').textContent = u ? u.percent === null ? '한도 미설정' : `${u.percent}%` : '—';
    $('llm-usage-fill').style.width = `${Math.max(0, Math.min(100, u?.percent || 0))}%`;
    const track = $('llm-usage-track');
    track.hidden = !u || u.percent === null;
    if (track.hidden) track.removeAttribute('aria-valuenow');
    else track.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, u.percent))));
    track.setAttribute('aria-valuetext', failed ? '사용량 조회 실패' : u ? `${u.percent ?? '한도 미설정'}${u.percent === null ? '' : '%'} · 팀 예산 기준` : message);
    $('llm-usage-amount').textContent = u ? `${money(u.spend)} / ${money(u.max)}` : failed ? '사용량을 조회하지 못했어요.' : message;
    $('llm-usage-remaining').textContent = u ? `${money(u.remaining)} 남음` : '';
    $('llm-usage-demo').hidden = !u?.demo;
    state.textContent = message || (u?.percent >= 95 ? '사용 한도에 가까워졌어요.' : u?.percent >= 80 ? '사용량이 80%를 넘었어요.' : '');
    state.hidden = !state.textContent;
    $('llm-usage-detail-amount').textContent = u ? `${money(u.spend)} / ${money(u.max)}` : failed ? '조회 불가' : '—';
    $('llm-usage-detail-remaining').textContent = u ? money(u.remaining) : failed ? '조회 불가' : '—';
    renderReset(u?.resetAt);
    if (failed) $('llm-usage-reset').textContent = '조회 불가';
    $('llm-usage-updated').textContent = u ? `${error || value.loading ? '마지막 확인' : '확인'} ${new Date(u.updatedAt * 1000).toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit',hourCycle:'h23'})}${error || value.loading ? ' · 이전 조회 값' : ''}${u.demo ? ' · 예시 데이터' : ''}` : value.loading?'조회 중…':error?'조회 실패':'아직 조회하지 않았어요';
    const select = $('llm-usage-team');
    if (known && JSON.stringify(known.teams) !== select.dataset.rows) {
      select.replaceChildren(...known.teams.map(row => {const o=document.createElement('option');o.value=row.id;o.textContent=row.name;return o;}));
      select.dataset.rows = JSON.stringify(known.teams);
    }
    if (known) select.value = known.team;
    select.disabled = value.loading || !known;
    $('llm-usage-refresh').disabled = value.loading;
    $('llm-usage-detail-state').textContent = message;
    const diagnosis=$('llm-usage-diagnostic'), d=value.diagnostic;
    diagnosis.hidden=!error;
    const networkLabel=path=>path==='proxy'?'프록시 경유':path==='direct'?'직접 연결':'';
    diagnosis.textContent=error ? [stages[d?.stage]||'조회 준비',`진단: ${error}`,Number.isInteger(d?.httpStatus)?`HTTP ${d.httpStatus}`:'',networkLabel(d?.networkPath)].filter(Boolean).join(' · ') : '';
    const connection=$('llm-usage-connection');connection.hidden=value.connection?.editable!==true;
    if(!connection.hidden){
      if(!urlEdited){
        $('llm-usage-url').value=value.connection.mcpUrl||'';
        for(const radio of connection.querySelectorAll('[name="llm-usage-network"]'))radio.checked=radio.value===(value.connection.networkMode||'system');
      }
      const route=networkLabel(d?.networkPath||u?.networkPath),routeNote=$('llm-usage-network-state');
      routeNote.hidden=!route;routeNote.textContent=route?'최근 조회 경로: '+route:'';
      $('llm-usage-connect').disabled=value.loading;
      $('llm-usage-connect').textContent=value.loading?'연결 확인 중…':'저장하고 연결 확인';
    }
    fitPanel();
  }
  async function refresh(data) {
    if (!api || stopped || pending || document.hidden) return;
    pending = true; clearTimeout(timer);
    try {render(await api('/api/llm-usage', data));}
    catch (_) {render({...current, error:'unavailable', loading:false, retry:null, diagnostic:null, pollAfterMs:60000});}
    finally {pending=false;scheduleRefresh();}
  }
  function start(hooks) {
    if (api) return;
    api = hooks.api;
    const toggle=$('llm-usage-toggle'), panel=$('llm-usage-details');
    toggle.onclick=()=>{const open=panel.hidden;panel.hidden=!open;toggle.setAttribute('aria-expanded',String(open));fitPanel();if(open)(current?.connection?.editable?$('llm-usage-url'):panel.querySelector('select')).focus();};
    const close=()=>{panel.hidden=true;toggle.setAttribute('aria-expanded','false');};
    $('llm-usage-close').onclick=()=>{close();toggle.focus();};
    document.addEventListener('pointerdown',e=>{if(!panel.hidden&&!$('llm-usage').contains(e.target))close();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){close();toggle.focus();}});
    $('llm-usage-refresh').onclick=()=>refresh({refresh:true});
    $('llm-usage-team').onchange=e=>refresh({team:e.target.value});
    $('llm-usage-url').oninput=()=>{urlEdited=true;};
    for(const radio of document.querySelectorAll('[name="llm-usage-network"]'))radio.onchange=()=>{urlEdited=true;};
    $('llm-usage-connection').onsubmit=async event=>{
      event.preventDefault();if(pending||stopped||current?.loading)return;
      pending=true;clearTimeout(timer);$('llm-usage-connect').disabled=true;
      const message=$('llm-usage-connect-state');message.textContent='';
      try{
        const networkMode=document.querySelector('[name="llm-usage-network"]:checked')?.value||'system';
        const value=await api('/api/llm-usage',{configure:{mcpUrl:$('llm-usage-url').value.trim(),networkMode}});
        urlEdited=false;render(value);message.textContent='연결 설정을 저장했어요. 조회 결과를 확인해 주세요.';
      }catch(error){message.textContent=error.message||'주소를 저장하지 못했어요.';}
      finally{pending=false;$('llm-usage-connect').disabled=!!current?.loading;if(!stopped)timer=setTimeout(()=>refresh(),1000);}
    };
    document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
    window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);},{once:true});
    window.addEventListener('resize',fitPanel);
    timer=setTimeout(()=>refresh(),1200);
  }
  return {start};
})();
