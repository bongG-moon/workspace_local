"use strict";
globalThis.WorkspaceLlmUsage = (() => {
  let api, timer, pending = false, stopped = false, current;
  const $ = id => document.getElementById(id);
  const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(value);
  const reasons = {key_required:'LLM Key 확인이 필요합니다.', not_configured:'사용량 연결 설정이 필요합니다.',
    no_teams:'조회할 팀을 확인해 주세요.'};
  function render(value) {
    current = value;
    const u = value.usage, error = value.error, state = $('llm-usage-state');
    const message = error ? reasons[error] || '사용량을 조회하지 못했어요.' : value.loading ? '사용량 확인 중…' : '';
    $('llm-usage').dataset.level = u?.percent >= 95 ? 'danger' : u?.percent >= 80 ? 'warning' : 'normal';
    $('llm-usage-percent').textContent = u ? u.percent === null ? '한도 미설정' : `${u.percent}%` : '—';
    $('llm-usage-fill').style.width = `${Math.max(0, Math.min(100, u?.percent || 0))}%`;
    $('llm-usage-track').setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, u?.percent || 0))));
    $('llm-usage-track').setAttribute('aria-valuetext', u ? `${u.percent ?? '한도 미설정'}${u.percent === null ? '' : '%'} · 팀 예산 기준` : message);
    $('llm-usage-amount').textContent = u ? `${money(u.spend)} / ${money(u.max)}` : message;
    $('llm-usage-remaining').textContent = u ? `${money(u.remaining)} 남음` : '';
    $('llm-usage-demo').hidden = !u?.demo;
    state.textContent = message || (u?.percent >= 95 ? '사용 한도에 가까워졌어요.' : u?.percent >= 80 ? '사용량이 80%를 넘었어요.' : '');
    state.hidden = !state.textContent;
    $('llm-usage-detail-amount').textContent = u ? `${money(u.spend)} / ${money(u.max)}` : '—';
    $('llm-usage-detail-remaining').textContent = u ? money(u.remaining) : '—';
    let reset = '제공되지 않음';
    if (u?.resetAt) {
      // The internal budget service reports a local Korean reset time when
      // the ISO value has no offset. Do not silently reinterpret it as UTC.
      const raw = /(?:Z|[+-]\d\d:\d\d)$/.test(u.resetAt) ? u.resetAt : u.resetAt + '+09:00';
      const date = new Date(raw);
      if (Number.isFinite(+date)) reset = date.toLocaleString('ko-KR', {timeZone:'Asia/Seoul',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) + ' KST';
    }
    $('llm-usage-reset').textContent = reset;
    $('llm-usage-updated').textContent = u ? `${error ? '마지막 확인' : '확인'} ${new Date(u.updatedAt * 1000).toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit'})}${u.demo ? ' · 예시 데이터' : ''}` : message;
    const select = $('llm-usage-team');
    if (u && JSON.stringify(u.teams) !== select.dataset.rows) {
      select.replaceChildren(...u.teams.map(row => {const o=document.createElement('option');o.value=row.id;o.textContent=row.name;return o;}));
      select.dataset.rows = JSON.stringify(u.teams);
    }
    if (u) select.value = u.team;
    select.disabled = value.loading || !u;
    $('llm-usage-refresh').disabled = value.loading;
    $('llm-usage-detail-state').textContent = message;
  }
  async function refresh(data) {
    if (!api || stopped || pending || document.hidden) return;
    pending = true; clearTimeout(timer);
    try {render(await api('/api/llm-usage', data));}
    catch (_) {render({...current, error:'unavailable', loading:false});}
    finally {pending=false;if(!stopped)timer=setTimeout(()=>refresh(),current?.loading?1000:300000);}
  }
  function start(hooks) {
    if (api) return;
    api = hooks.api;
    const toggle=$('llm-usage-toggle'), panel=$('llm-usage-details');
    toggle.onclick=()=>{const open=panel.hidden;panel.hidden=!open;toggle.setAttribute('aria-expanded',String(open));if(open)panel.querySelector('select').focus();};
    const close=()=>{panel.hidden=true;toggle.setAttribute('aria-expanded','false');};
    $('llm-usage-close').onclick=()=>{close();toggle.focus();};
    document.addEventListener('pointerdown',e=>{if(!panel.hidden&&!$('llm-usage').contains(e.target))close();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){close();toggle.focus();}});
    $('llm-usage-refresh').onclick=()=>refresh({refresh:true});
    $('llm-usage-team').onchange=e=>refresh({team:e.target.value});
    document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
    window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);},{once:true});
    timer=setTimeout(()=>refresh(),1200);
  }
  return {start};
})();
