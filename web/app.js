/* Native browser UI. No runtime dependencies or remote requests. */
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="anygpt-token"]').content;
const number = new Intl.NumberFormat('zh-CN');
const timeFormat = new Intl.DateTimeFormat('zh-CN', {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const dateFormat = new Intl.DateTimeFormat('zh-CN', {month:'2-digit',day:'2-digit'});
const runDateFormat = new Intl.DateTimeFormat('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
let selectedRun = '', payload = null, timer = null, loading = false, again = false;
let rowSignature = '', sessionSignature = '', stopped = false, initialized = false, actionBusy = false, detailId = '';
let followNewRun = false;
const activeStates = new Set(['starting', 'capturing', 'stopping']);
const text = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = text(value);
  if (className) node.className = className;
  return node;
}
function message(value, kind = 'action') {
  $('message').textContent = value || '';
  $('message').hidden = !value;
  $('message').dataset.kind = kind;
}
async function api(path, options = {}) {
  const response = await fetch(path, {cache:'no-store', ...options,
    headers:{'X-AnyGPT-Token':token, ...(options.body ? {'Content-Type':'application/json'} : {})}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function controls() {
  const state = payload?.capture.state || 'idle';
  const active = activeStates.has(state);
  $('start').disabled = actionBusy || active || stopped;
  $('stop').disabled = actionBusy || !active || state === 'stopping' || stopped;
  for (const id of ['port', 'duration', 'thread']) $(id).disabled = active || actionBusy || stopped;
  const labels = {idle:'未开始抓取',starting:'正在启动',capturing:'正在抓取',stopping:'正在停止',stopped:'已停止抓取',failed:'抓取失败'};
  $('state-label').textContent = stopped ? '服务已退出' : labels[state] || '状态未知';
  $('capture-state').className = `capture-state${active ? ' active' : ''}${state === 'failed' ? ' failed' : ''}`;
}
function renderSessions(data) {
  const sessions = [...data.sessions];
  if (data.selected && !sessions.some((item) => item.id === data.selected.id)) sessions.unshift({id:data.selected.id});
  const signature = JSON.stringify(sessions);
  if (signature !== sessionSignature) {
    $('runs').replaceChildren(...sessions.map((item) => {
      const option = element('option', runLabel(item));
      option.value = item.id;
      return option;
    }));
    if (!sessions.length) $('runs').append(element('option', '暂无记录'));
    sessionSignature = signature;
  }
  if (data.selected) {
    if (!followNewRun) selectedRun = data.selected.id;
    $('runs').value = data.selected.id;
  }
}
function runLabel(item) {
  const date = new Date(item.created_at);
  return Number.isNaN(date.getTime()) ? '当前抓取批次' : `${runDateFormat.format(date)} 开始的抓取`;
}
function renderRows() {
  const selected = payload?.selected;
  const source = selected?.rows || [];
  const query = $('search').value;
  const onlyDifferent = $('only-different').checked;
  const rows = source.filter((row) => (!onlyDifferent || row.effort_differs === true) &&
    (!query || [row.thread_name, row.thread_id, row.request_model, row.response_model, row.upstream_request_id].some((value) => String(value || '').includes(query))));
  const signature = JSON.stringify(rows);
  if (signature !== rowSignature) {
    $('rows').replaceChildren(...rows.map((row) => {
      const tr = element('tr');
      const date = new Date(row.captured_at);
      const time = element('td', Number.isNaN(date.getTime()) ? row.captured_at : timeFormat.format(date), 'time-cell');
      time.append(element('small', Number.isNaN(date.getTime()) ? '' : dateFormat.format(date)));
      const conversation = element('td', undefined, 'conversation-cell');
      const name = element('span', row.thread_name || (row.thread_id ? '等待对话名称' : '未提供会话 ID'));
      name.title = row.thread_name || row.thread_id || '';
      conversation.append(name);
      if (!row.thread_name && row.thread_id) conversation.append(element('small', row.thread_id));
      const model = element('td', row.request_model, 'model-cell');
      if (row.response_model && row.response_model !== row.request_model) model.append(element('small', `响应：${row.response_model}`));
      const comparison = element('td');
      comparison.append(element('span', row.requested_effort, 'effort'), element('span', '→', 'arrow'),
        element('span', row.response_effort, `effort${row.effort_differs === true ? ' changed' : ''}`));
      const result = element('td');
      const label = row.effort_differs === true ? '强度不同' : row.effort_differs === false ? '强度一致' : '信息不足';
      result.append(element('span', label, `badge ${row.effort_differs === true ? 'different' : row.effort_differs === null ? 'unknown' : ''}`));
      const last = element('td');
      const button = element('button', '查看 ↗', 'detail-link');
      button.type = 'button'; button.dataset.sample = row.sample;
      button.setAttribute('aria-label', `查看第 ${row.sample} 条请求详情`);
      last.append(button);
      tr.append(time, conversation, model, comparison, element('td', row.reasoning_tokens === null ? '—' : number.format(row.reasoning_tokens)), result, last);
      return tr;
    }));
    rowSignature = signature;
  }
  $('empty').hidden = rows.length > 0;
  const filtered = source.length > 0;
  $('empty-title').textContent = filtered ? '没有符合条件的记录' : '等待第一条请求';
  $('empty-copy').textContent = filtered ? '调整搜索词，或取消“仅看差异”。' : '点击开始抓取，然后正常使用 Codex。请求完成后会出现在这里。';
  $('row-count').textContent = `${rows.length} 条显示${selected?.total > 300 ? ' · 仅展示最近 300 条' : ''}${selected?.more ? ' · 正在读取更多历史记录' : ''}`;
}
function render(data) {
  payload = data;
  if (followNewRun && data.capture.run) {
    selectedRun = data.capture.run;
    followNewRun = false;
    if (data.selected?.id !== selectedRun) { again = true; controls(); return; }
  }
  if (!initialized) {
    const options = data.capture.options;
    $('port').value = data.capture.state === 'idle' ? data.default_port : options?.port || data.default_port;
    $('duration').value = String(options?.seconds || 0);
    $('thread').value = options?.thread_id || '';
    initialized = true;
  }
  renderSessions(data);
  controls();
  const selected = data.selected;
  $('total').textContent = number.format(selected?.total || 0);
  $('different').textContent = number.format(selected?.different || 0);
  $('unknown').textContent = number.format(selected?.unknown || 0);
  $('ratio').textContent = selected?.total ? `占已记录请求 ${Math.round(selected.different / selected.total * 100)}%` : '等待数据';
  const batch = data.sessions.find((item) => item.id === selected?.id);
  $('run-caption').textContent = selected ? `${runLabel(batch || {})} · 对话名称自动更新` : '开始一次抓取，或等待本地记录出现';
  const options = data.capture.options;
  const scope = options?.thread_id ? `仅记录会话 ${options.thread_id}` : '记录所有对话，包括新开的对话';
  const duration = options?.seconds ? `${options.seconds / 60} 分钟后自动停止` : '持续抓取，点击停止才结束';
  const status = selected?.id === data.capture.run ? selected?.status : null;
  const drops = status?.npcap?.dropped || 0;
  $('capture-info').textContent = activeStates.has(data.capture.state) ? `${scope} · ${duration}${drops ? ` · 已丢包 ${number.format(drops)}，部分请求可能不完整` : ''}` : '默认持续记录所有对话，新开对话无需重启。';
  $('export').setAttribute('aria-disabled', selected?.total ? 'false' : 'true');
  if (selected?.total) $('export').href = `/api/export?run=${encodeURIComponent(selected.id)}&token=${encodeURIComponent(token)}`;
  else $('export').removeAttribute('href');
  if (data.capture.error) message(data.capture.error, 'capture');
  else if (['network','capture'].includes($('message').dataset.kind)) message('');
  renderRows();
  $('updated').textContent = `更新于 ${timeFormat.format(new Date())}`;
}
function schedule(delay) {
  clearTimeout(timer);
  if (stopped || document.hidden) return;
  const active = activeStates.has(payload?.capture.state);
  timer = setTimeout(refresh, delay ?? (payload?.selected?.more ? 300 : active ? 2000 : 10000));
}
async function refresh() {
  if (stopped || document.hidden) return;
  if (loading) { again = true; return; }
  loading = true;
  try {
    const run = selectedRun;
    const data = await api(`/api/state${run ? `?run=${encodeURIComponent(run)}` : ''}`);
    if (run === selectedRun) render(data);
    else again = true;
  } catch (error) {
    message(`暂时无法连接本地服务：${error.message}`, 'network');
    $('updated').textContent = '连接中断，可点击刷新重试';
  } finally {
    loading = false;
    if (again) { again = false; schedule(0); } else schedule();
  }
}
async function action(path, body = {}) {
  if (actionBusy) return;
  actionBusy = true; controls(); message('');
  try {
    await api(path, {method:'POST', body:JSON.stringify(body)});
    if (path === '/api/start') { selectedRun = ''; rowSignature = ''; followNewRun = true; }
    await refresh();
  } catch (error) { message(error.message); }
  finally { actionBusy = false; controls(); }
}
$('start').addEventListener('click', () => {
  if (!$('port').reportValidity()) return;
  action('/api/start', {port:Number($('port').value), seconds:Number($('duration').value), thread_id:$('thread').value});
});
$('stop').addEventListener('click', () => action('/api/stop'));
$('runs').addEventListener('change', () => { selectedRun = $('runs').value; rowSignature = ''; refresh(); });
$('search').addEventListener('input', renderRows);
$('only-different').addEventListener('change', renderRows);
$('refresh').addEventListener('click', refresh);
document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) refresh(); });
$('rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-sample]');
  if (!button) return;
  $('detail-content').replaceChildren(element('p', '正在读取详情…', 'muted'));
  $('copy-status').textContent = ''; $('copy-id').disabled = true; $('detail').showModal();
  try {
    const record = await api(`/api/record?run=${encodeURIComponent(selectedRun)}&sample=${encodeURIComponent(button.dataset.sample)}`);
    const comparison = element('div', undefined, 'detail-comparison');
    for (const [label, value] of [['请求强度',record.requested_effort], ['响应报告强度',record.response_effort]]) {
      const block = element('div'); block.append(element('small',label),element('strong',value)); comparison.append(block);
      if (label === '请求强度') comparison.append(element('span','→','arrow'));
    }
    const grid = element('dl', undefined, 'detail-grid');
    const values = [['对话名称',record.thread_name || '尚未读取到名称'], ['请求模型',record.request_model], ['响应模型',record.response_model], ['推理 tokens',record.reasoning_tokens],
      ['完成事件',record.response_completed ? '已收到' : '未收到'], ['HTTP 状态',record.http_status],
      ['上游请求 ID',record.upstream_request_id], ['响应 ID',record.response_id], ['路由响应头',record.routed_channel_id],
      ['会话 ID',record.thread_id], ['轮次 ID',record.turn_id]];
    for (const [label,value] of values) grid.append(element('dt',label),element('dd',value));
    $('detail-content').replaceChildren(comparison,grid);
    detailId = record.upstream_request_id || '';
    $('copy-id').disabled = !detailId;
  } catch (error) { $('detail-content').replaceChildren(element('p',error.message,'muted')); }
});
$('copy-id').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(detailId); $('copy-status').textContent = '已复制'; }
  catch { $('copy-status').textContent = '复制未成功，可以在上方手动选中请求 ID。'; }
});
$('close-detail').addEventListener('click', () => $('detail').close());
$('quit').addEventListener('click', () => $('quit-dialog').showModal());
$('cancel-quit').addEventListener('click', () => $('quit-dialog').close());
$('confirm-quit').addEventListener('click', async () => {
  try {
    await api('/api/quit', {method:'POST', body:'{}'});
    stopped = true; clearTimeout(timer); $('quit-dialog').close(); controls();
    $('quit').disabled = true;
    message('本地服务已退出，抓取已停止。可以关闭此页面。', 'exit');
    $('updated').textContent = '服务已退出';
  } catch (error) { $('quit-dialog').close(); message(error.message); }
});
refresh();
