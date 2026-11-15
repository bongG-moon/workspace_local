const panel = document.querySelector('#web-demo');
const find = selector => panel.querySelector(selector);
const number = new Intl.NumberFormat('ko-KR');
const connected = (selector, label, value = true) => {
  const node = find(selector);
  node.textContent = label;
  node.dataset.connected = String(value);
};

async function start() {
  connected('#demo-js-state', 'JS 연결');
  const styled = getComputedStyle(panel).getPropertyValue('--web-demo-loaded').trim() === '1';
  connected('#demo-css-state', styled ? 'CSS 연결' : 'CSS 미연결', styled);
  const picture = find('.demo-illustration');
  const pictureStatus = () => connected('#demo-image-state', picture.naturalWidth ? '이미지 연결' : '이미지 미연결', picture.naturalWidth > 0);
  picture.addEventListener('load', pictureStatus, {once:true});
  picture.addEventListener('error', pictureStatus, {once:true});
  if (picture.complete) pictureStatus();
  const response = await fetch(new URL('./data.json', import.meta.url), {credentials:'omit'});
  if (!response.ok) throw Error('데이터 파일을 열지 못했습니다.');
  const data = await response.json();
  if (!Array.isArray(data.rows) || data.rows.length !== data.sourceRows) throw Error('데이터 범위를 확인하지 못했습니다.');
  const records = data.rows;
  connected('#demo-data-state', 'JSON 연결');
  find('#demo-mode').textContent = '웹 모드 · 연결 완료';
  find('#demo-mode').classList.add('is-live');
  find('#demo-title').textContent = '범위를 바꾸면 결과도 달라집니다';
  find('#demo-description').textContent = '아래 버튼을 눌러 보세요. 연결된 JavaScript가 로컬 데이터로 최대값과 그래프를 다시 계산합니다.';
  const buttons = [...panel.querySelectorAll('[data-scope]')];
  const labels = {all:'전체 105건', first:'처음 100건', inspect:'검사 공정'};

  function render(scope) {
    const rows = scope === 'first' ? records.slice(0,100) : scope === 'inspect' ? records.filter(row=>row.process==='검사') : records;
    const sorted = rows.slice().sort((a,b)=>b.quantity-a.quantity || a.row-b.row);
    const maximum = sorted[0];
    find('#demo-count').textContent = number.format(rows.length) + '건';
    find('#demo-max').textContent = number.format(maximum.quantity) + '개';
    find('#demo-max-detail').textContent = maximum.id + ' · ' + maximum.process;
    find('#demo-scope').textContent = '선택 범위: ' + labels[scope] + ' / ' + rows.length + '건';
    const bars = find('#demo-bars');
    bars.replaceChildren();
    for (const row of sorted.slice(0,3)) {
      const item = document.createElement('div');item.className='demo-chart-row';
      const name = document.createElement('span');name.textContent=row.id;
      const track = document.createElement('div');track.className='demo-track';
      const fill = document.createElement('div');fill.className='demo-fill';
      fill.style.width=(100*row.quantity/2400).toFixed(2)+'%';track.append(fill);
      const quantity = document.createElement('span');quantity.textContent=number.format(row.quantity)+'개';
      item.append(name,track,quantity);bars.append(item);
    }
    buttons.forEach(button=>button.setAttribute('aria-pressed', String(button.dataset.scope===scope)));
  }
  buttons.forEach(button=>{button.disabled=false;button.addEventListener('click',()=>render(button.dataset.scope));});
  render('all');
}

start().catch(()=>{
  find('#demo-mode').textContent='웹 모드 · 연결 확인 필요';
  find('#demo-description').textContent='같은 폴더의 production-web-demo 자료를 함께 열어 주세요. 기존 보고서는 아래에서 계속 확인할 수 있습니다.';
});
