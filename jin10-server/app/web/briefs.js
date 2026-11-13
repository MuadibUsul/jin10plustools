import { $, node, api, login, download, copy, copyDate, displayTime, toast } from '/workspace.js';
import '/pull-cord.js';
import { computerShell } from '/retro-computer.js';
const labels = { queued: '等待执行', running: '生成中', waiting: '等待数据更新', succeeded: '已生成', failed: '生成失败', skipped: '已跳过' };
let me, config, items = [], dataStates = [], selected = null, current = null, dirty = false;
let dataStatusDay = null, dataStatusAt = 0, refreshRequest = 0, actionBusy = false;
let briefFilter = 'all';
const blobs = new Set();
let imageKey = null, pendingImageKey = null, imageRequest = 0;
const pendingCategories = new Set();
const computeCategories = new Set(['token_cost', 'gpu_rental']);
let manualCategory = null, manualBusy = false;
const active = (job) => ['queued', 'running', 'waiting'].includes(job?.status);
const processSteps = ['准备任务', '正在取数', '正在制图', '整理文案', '保存稿件', '生成完成'];
const channelNames = { volume: '成交额', turnover: '换手率', valuation: '估值', liquidity: '流动性', oil_etf: '原油ETF', us_barometer: '美股晴雨表', us_structure: '美股结构', token_cost: 'AI算力CPI', gpu_rental: 'AI景气度指标' };
const terminalSeen = new Map(), terminalPending = new Set();
let terminalDay = null, polling = false;
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
$('#day').value = today(); $('#day').max = today();
function message(text, error = false) { $('#message').textContent = text; $('#message').className = error ? 'error' : 'success'; }
async function safe(action) { try { await action(); } catch (e) { message(e.message, true); toast(e.message, 'error'); } }
function canEdit() { return me.user.role !== 'viewer'; }
function visibleWarnings(version) {
  return (version?.warnings || []).filter(text => !text.startsWith('历史覆盖尚未完整核验'));
}

const detailDialog = $('#detail-dialog');
function showDetail() {
  if (!detailDialog || detailDialog.open) return;
  if (detailDialog.showModal) detailDialog.showModal();
  else detailDialog.setAttribute('open', '');
}
function clearDetailState() { dirty = false; selected = current = null; clearImages(); $('#history-exports').hidden = true; }
function hideDetail() {
  if (!detailDialog || !detailDialog.open) return;
  if (detailDialog.close) { detailDialog.close(); return; }
  detailDialog.removeAttribute('open'); clearDetailState();
}
detailDialog?.addEventListener('cancel', (event) => {
  if (actionBusy || (dirty && !window.confirm('有未保存的修改，确认关闭？'))) event.preventDefault();
});
detailDialog?.addEventListener('close', clearDetailState);
function chosen() { return selected?.versions.find((v) => v.number === Number($('#version').value)); }
async function refresh() {
  const request = ++refreshRequest;
  const day = $('#day').value;
  const isCurrent = () => request === refreshRequest && day === $('#day').value;
  const data = await api(`/api/briefs/issues?date=${day}`);
  if (!isCurrent()) return;
  const checkSources = dataStatusDay !== day || Date.now() - dataStatusAt > 60000 || JSON.stringify(data.items) !== JSON.stringify(items);
  if (dataStatusDay !== day) dataStates = [];
  items = data.items; renderCards();
  if (selected && !dirty && !actionBusy) {
    const fresh = items.find((x) => x.id === selected.id);
    if (!fresh?.versions.length) hideDetail();
    else if (fresh.version !== selected.version || fresh.confirmed_version !== selected.confirmed_version) {
      open(fresh, Number($('#version').value));
    }
  }
  if (!checkSources) return;
  const [status, health] = await Promise.allSettled([
    api(`/api/briefs/data-status?date=${day}`),
    canEdit() ? api('/api/briefs/health') : null,
  ]);
  if (!isCurrent()) return;
  if (status.status === 'fulfilled') {
    dataStates = status.value.items || []; dataStatusDay = day; dataStatusAt = Date.now();
    renderCards();
  }
  const messages = (health.value?.alerts || []).map(alert => alert.message);
  if (status.status === 'rejected') messages.push('暂时无法检查来源状态，已保存稿件仍可查看。');
  if (health.status === 'rejected') messages.push('暂时无法检查自动生产状态。');
  const banner = $('#production-health');
  banner.textContent = messages.join('；'); banner.hidden = !messages.length;
}
function renderCards() {
  $('#brief-filters').hidden = false;
  $('[data-brief-filter="review"]').hidden = !canEdit();
  let shown = 0;
  for (const category of config.categories) {
    const item = items.find((x) => x.category === category.id);
    let card = $(`#card-${category.id}`);
    if (!card) {
      card = node('article', undefined, 'brief-card'); card.id = `card-${category.id}`;
      const head = node('div', undefined, 'brief-card-head'), process = node('button', undefined, 'process-button');
      process.type = 'button'; process.append(node('span', '', 'job-indicator'), node('span', '生成过程'));
      process.onclick = () => safe(async () => {
        const job = items.find(x => x.category === category.id)?.jobs[0];
        if (!job) { message('该栏目本期尚无生成记录。'); return; }
        const { showGenerationProcess } = await import('/generation-process.js');
        await showGenerationProcess({ title: category.label, load: () => api(`/api/briefs/jobs/${job.id}/process`) });
      });
      head.append(node('h2', category.label), process); card.append(head, node('div', undefined, 'brief-card-body')); $('#cards').append(card);
    }
    const job = item?.jobs[0], version = item?.versions[0];
    const confirmed = item?.confirmed_version;
    const needsReview = Boolean(version && version.number !== confirmed);
    card.hidden = briefFilter !== 'all' && (briefFilter === 'review' ? !needsReview : !version || needsReview);
    if (!card.hidden) shown++;
    const busy = active(job) || pendingCategories.has(category.id);
    card.setAttribute('aria-busy', String(busy));
    const indicator = card.querySelector('.job-indicator');
    const executing = pendingCategories.has(category.id) || ['queued', 'running'].includes(job?.status);
    const indicatorClass = executing ? 'job-indicator spinning' : `job-indicator ${job?.status || ''}`;
    if (indicator.className !== indicatorClass) indicator.className = indicatorClass;
    const process = card.querySelector('.process-button');
    process.disabled = !job;
    process.setAttribute('aria-label', `${category.label}生成过程：${busy ? '正在执行或等待' : labels[job?.status] || '暂无任务'}`);
    const body = card.querySelector('.brief-card-body');
    const dataState = dataStates.find((x) => x.category === category.id);
    const signature = JSON.stringify([item, dataState, pendingCategories.has(category.id)]);
    if (body.dataset.signature === signature) continue;
    body.dataset.signature = signature; body.replaceChildren();
    const state = version ? needsReview ? '待核对' : '已确认' : '暂无稿件';
    body.append(node('span', state, `badge ${needsReview ? 'brief-badge-review' : version ? 'ready' : ''}`));
    body.append(node('h3', version?.title || (job && busy ? '正在准备本期稿件' : '本期暂无可查看稿件')));
    if (version) {
      const meta = node('dl', undefined, 'brief-card-meta');
      for (const [label, value] of [
        ['数据日', version.data_date ? copyDate(version.data_date) : '未记录'],
        [canEdit() ? '当前稿' : '可查看版本', `第 ${version.number} 版`],
        ['确认稿', confirmed ? `第 ${confirmed} 版` : '尚未确认'],
      ]) {
        const row = node('div');
        row.append(node('dt', label), node('dd', value));
        meta.append(row);
      }
      body.append(meta);
    }
    if (job) body.append(node('p', `${job.status === 'failed' && version ? '最近一次任务失败；已有预览图可查看' : labels[job.status] || job.status}${job.error ? '：' + job.error : ''}`, job.status === 'failed' ? 'error' : 'muted'));
    if (job?.status === 'waiting' && job.next_attempt_at) body.append(node('p', `下次重试：${displayTime(job.next_attempt_at)}`, 'muted'));
    if (visibleWarnings(version).length) body.append(node('p', '有来源或口径说明，请核对', 'warning'));
    if (version && dataState?.report?.current_data_valid && dataState.target_date > version.data_date) {
      body.append(node('p',`来源已更新至 ${copyDate(dataState.target_date)}，请生成并核对新稿`,'warning'));
    }
    if (version?.data_date === dataState?.target_date && version?.data_reliability?.publication_digest && dataState?.report?.publication_digest &&
        (version.data_reliability.publication_digest_version || 1) === (dataState.report.publication_digest_version || 1) &&
        version.data_reliability.publication_digest !== dataState.report.publication_digest) {
      body.append(node('p', '来源数据已修订，请重新生成并核对稿件', 'error'));
    }
    const controls = node('div', undefined, 'toolbar');
    if (version) { const view = node('button', needsReview ? '核对新稿' : '查看确认稿', 'primary'); view.onclick = () => safe(() => open(item)); controls.append(view); }
    if (version && category.id === 'us_barometer') {
      const exports = node('button', '历史数据导出');
      exports.onclick = () => safe(() => open(item, version.number));
      controls.append(exports);
    }
    if (needsReview && item.versions.some((entry) => entry.number === confirmed)) {
      const viewConfirmed = node('button', '查看确认稿');
      viewConfirmed.onclick = () => safe(() => open(item, confirmed));
      controls.append(viewConfirmed);
    }
    if (canEdit()) { const run = node('button', executing ? '任务执行中' : job?.status === 'waiting' ? '立即重试' : '生成稿件', version || executing ? undefined : 'primary'); run.disabled = executing; run.title = computeCategories.has(category.id) ? '读取公开网页展示值并生成稿件' : '自动刷新来源，并处理最接近当前的可用数据'; run.onclick = () => safe(() => runJob(category.id, 'produce')); controls.append(run); }
    if (canEdit() && computeCategories.has(category.id)) { const manual = node('button', '人工填数并生成'); manual.disabled = busy; manual.onclick = () => openManual(category.id); controls.append(manual); }
    body.append(controls);
  }
  const empty = $('#brief-filter-empty');
  empty.hidden = shown > 0;
  empty.textContent = shown ? '' : briefFilter === 'review' ? '当前生产日没有待核对的稿件。' : '当前生产日没有已确认的稿件。';
  renderProcess();
}
$('#brief-filters').addEventListener('click', (event) => {
  const button = event.target.closest('[data-brief-filter]');
  if (!button || briefFilter === button.dataset.briefFilter) return;
  briefFilter = button.dataset.briefFilter;
  for (const control of $('#brief-filters').querySelectorAll('button')) {
    control.setAttribute('aria-pressed', String(control === button));
  }
  renderCards();
});
function renderProcess() {
  let panel = $('#job-process');
  if (!panel) {
    panel = node('aside', undefined, 'retro-panel'); panel.id = 'job-process'; panel.tabIndex = -1; panel.setAttribute('aria-label', '进程动态：所有栏目的实时处理过程');
    const shell = computerShell({ live: true });
    panel = shell.panel;
    const { computer, screen, mouse } = shell;
    mouse.onclick = () => safe(async () => { const { toggleLive } = await import('/computer-live.js'); await toggleLive(screen); });
    if (!window.__plusPullCord?.mount?.(panel)) $('#brief-workspace').append(panel);
    panel.onpointermove = (event) => {
      if (event.pointerType !== 'mouse' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const rect = panel.getBoundingClientRect();
      const x = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1));
      const y = Math.max(-1, Math.min(1, (event.clientY - rect.top) / rect.height * 2 - 1));
      computer.style.setProperty('--tilt-x', `${(-y * 3).toFixed(2)}deg`);
      computer.style.setProperty('--tilt-y', `${(x * 4).toFixed(2)}deg`);
    };
    panel.onpointerleave = () => { computer.style.setProperty('--tilt-x', '0deg'); computer.style.setProperty('--tilt-y', '0deg'); };
    if (window.ResizeObserver) {
      // The computer follows the spare row's size; it never determines card height.
      const last = $('#cards').lastElementChild;
      const observer = new window.ResizeObserver(() => panel.style.setProperty('--dock-height', `${last.getBoundingClientRect().height}px`));
      observer.observe($('#cards')); observer.observe(last);
    }
  }
  const feed = $('#terminal-feed');
  if (terminalDay !== $('#day').value) { terminalDay = $('#day').value; terminalSeen.clear(); terminalPending.clear(); feed.replaceChildren(); }
  const updates = [];
  for (const category of config.categories) {
    const item = items.find((x) => x.category === category.id), job = item?.jobs[0];
    if (pendingCategories.has(category.id) && !terminalPending.has(category.id)) {
      terminalPending.add(category.id); updates.push({ category: category.id, text: '正在提交任务', at: new Date().toISOString(), kind: 'running' });
    } else if (!pendingCategories.has(category.id)) terminalPending.delete(category.id);
    if (!job) continue;
    const step = Math.max(1, Math.min(6, Number(job.progress?.step) || 1)), signature = `${job.status}:${step}`;
    if (terminalSeen.get(job.id) === signature) continue;
    terminalSeen.set(job.id, signature);
    const phase = category.id === 'oil_etf' && step === 3 ? '核对持仓' : processSteps[step - 1];
    const text = job.status === 'running' ? phase : { queued: '任务已排队', waiting: '等待数据更新', succeeded: '稿件已保存', failed: '生成未完成', skipped: '任务已停止' }[job.status] || '等待任务状态';
    const at = job.status === 'running' ? job.progress?.events?.at(-1)?.at || job.updated_at || job.created_at : job.updated_at || job.created_at;
    updates.push({ category: category.id, text, at, kind: job.status, step });
  }
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 28;
  for (const update of updates.sort((a, b) => new Date(a.at) - new Date(b.at))) {
    const row = node('p', undefined, `terminal-line ${update.kind}`); row.dataset.category = update.category; if (update.step) row.dataset.step = update.step;
    const time = node('time', new Date(update.at).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit' })); time.dateTime = update.at;
    row.append(time, node('span', `[${channelNames[update.category]}]`, 'terminal-channel'), node('span', update.text)); feed.append(row);
  }
  while (feed.children.length > 50) feed.firstElementChild.remove();
  while (terminalSeen.size > 100) terminalSeen.delete(terminalSeen.keys().next().value);
  if (updates.length && nearBottom) feed.scrollTop = feed.scrollHeight;
  $('#terminal-empty').hidden = feed.children.length > 0;
  const busy = config.categories.filter((c) => active(items.find((x) => x.category === c.id)?.jobs[0]) || pendingCategories.has(c.id)).length;
  const failed = items.some((x) => ['failed', 'skipped'].includes(x.jobs[0]?.status));
  const completed = items.filter((x) => x.jobs[0]?.status === 'succeeded').length;
  panel.dataset.active = String(busy > 0);
  if (busy) window.dispatchEvent(new window.Event('plus-brief-generation'));
  const state = busy ? `RUNNING ${String(busy).padStart(2, '0')}` : failed ? 'TASK NEEDS REVIEW' : completed ? `SAVED ${String(completed).padStart(2, '0')} / IDLE` : 'STANDBY';
  if ($('#terminal-state').textContent !== state) $('#terminal-state').textContent = state;
}
async function pollJobs() {
  if (polling || !me || document.hidden) return;
  const jobs = items.flatMap((x) => x.jobs.filter(active)); if (!jobs.length) return;
  polling = true; const day = $('#day').value;
  try {
    const results = await Promise.allSettled(jobs.map((job) => api(`/api/briefs/jobs/${job.id}`)));
    if (day !== $('#day').value) return;
    let finished = false;
    for (const result of results) if (result.status === 'fulfilled') {
      const job = result.value, item = items.find((x) => x.jobs.some((old) => old.id === job.id));
      if (item) item.jobs = item.jobs.map((old) => old.id === job.id ? job : old);
      if (!active(job)) finished = true;
    }
    renderCards();
    if (finished && !dirty) {
      try { await refresh(); } catch (e) { message(e.message, true); }
    }
    if (results.some((r) => r.status === 'rejected')) message('过程状态暂时无法更新，正在重连；后台任务仍可能运行。', true);
  } finally { polling = false; }
}
async function runJob(category, action, { issueDate = $('#day').value, expectedVersion } = {}) {
  if (pendingCategories.has(category)) return;
  pendingCategories.add(category); renderCards();
  try {
    const result = await api('/api/briefs/jobs', { method: 'POST', json: {
      category, action, issue_date: issueDate, expected_version: expectedVersion,
    } });
    message(`任务已加入队列：${labels[result.status] || result.status}。不必重复点击。`);
    toast('任务已加入队列。');
    await refresh();
  } finally { pendingCategories.delete(category); renderCards(); }
}
function openManual(category) {
  manualCategory = category;
  const form = $('#manual-form'); form.reset();
  $('#manual-status').textContent = '';
  $('#manual-title').textContent = `${channelNames[category]} · 人工填数`;
  form.elements.data_date.max = $('#day').value;
  $('#manual-token').hidden = category !== 'token_cost';
  $('#manual-gpu').hidden = category !== 'gpu_rental';
  for (const name of ['token_price', 'a100', 'h100', 'b200']) form.elements[name].required =
    category === 'token_cost' ? name === 'token_price' : name !== 'token_price';
  $('#manual-dialog').showModal();
}
const manualDialog = $('#manual-dialog');
function closeManual() { if (!manualBusy) manualDialog.close(); }
manualDialog.addEventListener('cancel', event => { if (manualBusy) event.preventDefault(); });
manualDialog.addEventListener('close', () => { manualCategory = null; });
$('#manual-close').onclick = $('#manual-cancel').onclick = closeManual;
$('#manual-form').onsubmit = async (event) => {
  event.preventDefault();
  if (!manualCategory || pendingCategories.has(manualCategory)) return;
  const category = manualCategory, form = $('#manual-form');
  const fields = category === 'token_cost'
    ? ['token_price', 'token_change_pct']
    : ['a100', 'h100', 'b200', 'a100_change_pct', 'h100_change_pct', 'b200_change_pct'];
  const payload = {category, issue_date: $('#day').value,
    data_date: form.elements.data_date.value, source_note: form.elements.source_note.value};
  for (const name of fields) payload[name] = form.elements[name].value.trim();
  const status = $('#manual-status'), controls = [...form.elements];
  manualBusy = true;
  status.textContent = '正在提交人工数据…'; status.className = 'muted';
  for (const control of controls) control.disabled = true;
  pendingCategories.add(category); renderCards();
  let job;
  try {
    job = await api('/api/briefs/manual-compute', {method:'POST', json:payload});
  } catch (error) {
    status.textContent = error.message; status.className = 'error';
  } finally {
    manualBusy = false;
    for (const control of controls) control.disabled = false;
    pendingCategories.delete(category); renderCards();
  }
  if (!job) return;
  closeManual(); message(`人工数据已加入生成队列：${labels[job.status] || job.status}。`);
  toast('人工数据已提交，正在生成稿件。'); await safe(refresh);
};
function clearImages() {
  imageRequest++; imageKey = pendingImageKey = null;
  for (const url of blobs) URL.revokeObjectURL(url);
  blobs.clear(); $('#images').replaceChildren();
  $('#image-status').textContent = '';
}
async function open(item, number) {
  if (dirty && !window.confirm('有未保存的修改，确认离开？')) return;
  selected = item; dirty = false;
  $('#detail-action-status').textContent = '';
  showDetail(); $('#detail-title').textContent = item.label;
  const select = $('#version'); select.replaceChildren();
  const kinds = { edited: '保存修改', rendered: '重新制图' };
  for (const v of item.versions) { const saved = displayTime(v.created_at); const option = node('option', `v${v.number} · ${saved} · ${kinds[v.kind] || '生成稿件'}${item.confirmed_version === v.number ? ' · 已确认' : ''}`); option.value = v.number; select.append(option); }
  select.value = String(item.versions.some((v) => v.number === number) ? number : item.versions[0].number); await showVersion();
}
async function showVersion() {
  const v = chosen(); if (!v) return; current = v;
  $('#title').value = v.title; $('#body').value = v.dynamic_body;
  $('#fixed').value = v.fixed_notes || '';
  $('#notes-label').textContent = computeCategories.has(selected.category)
    ? '注释（显示在图片页脚，不进正文）'
    : v.notes_placement === 'image' ? '注释（保存后显示在图片底部）' : '注释（自动加入完整正文）';
  const confirmed = selected.confirmed_version === v.number;
  $('#detail-status').textContent = `数据日 ${copyDate(v.data_date)} · ${confirmed ? '已确认 · 正式取稿版本' : '未确认 · 编辑预览'}`;
  if (v.editorial?.status === 'active') $('#detail-status').textContent += ` · ${v.editorial.writer === 'sample_story_v2' ? '样本规范写稿' : '风格模块'} · ${v.editorial.selection_status === 'model_selected' ? 'AI组织表达' : '完整规则回退'}`;
  else if (v.editorial?.reason) $('#detail-status').textContent += ` · 样本风格未采用：${v.editorial.reason}`;
  const warnings = visibleWarnings(v);
  $('#warnings').replaceChildren(...warnings.map((s) => node('p', s)));
  $('#facts').textContent = JSON.stringify(v.facts, null, 2);
  for (const id of ['save', 'confirm', 'unconfirm', 'rerender', 'refetch']) $(`#${id}`).hidden = !canEdit();
  dirty = false; renderHistoryExports(v); updateDetailControls();
  await loadImages(v);
}
function renderHistoryExports(v) {
  const panel = $('#history-exports');
  const actions = $('#history-export-actions');
  actions.replaceChildren();
  panel.hidden = selected?.category !== 'us_barometer' || !v?.data_date || (me.user.role === 'viewer' && selected.confirmed_version !== v.number);
  if (panel.hidden) return;
  $('#history-export-label').textContent = `历史数据导出（CSV）· 第 ${v.number} 版`;
  for (const [metric, label] of [['buffett', '导出巴菲特指数历史数据'], ['erp', '导出风险溢价历史数据']]) {
    const button = node('button', label);
    button.dataset.download = '';
    const url = `/api/briefs/issues/${selected.id}/history/${v.number}/${metric}.csv`;
    button.onclick = () => safe(async () => { await download(url); toast('历史数据已开始下载。'); });
    actions.append(button);
  }
}
function updateDetailControls() {
  const latest = current?.number === selected?.version;
  const editable = !actionBusy && canEdit() && latest;
  for (const id of ['title', 'body', 'fixed']) $(`#${id}`).readOnly = !editable;
  for (const id of ['version', 'close-detail', 'copy-title', 'copy-body']) $(`#${id}`).disabled = actionBusy;
  $('#save').disabled = !editable || !dirty;
  $('#confirm').disabled = !editable || dirty || !current?.valid || current?.number === selected?.confirmed_version;
  $('#unconfirm').disabled = actionBusy || dirty || !selected?.confirmed_version;
  $('#rerender').disabled = !editable || dirty;
  $('#rerender').title = !latest ? '仅可为当前最新版本重新制图' : dirty ? '请先保存修改' : '';
  $('#refetch').disabled = actionBusy || dirty;
  for (const button of $('#images').querySelectorAll('[data-download]')) button.disabled = actionBusy || dirty;
  for (const button of $('#history-export-actions').querySelectorAll('[data-download]')) button.disabled = actionBusy || dirty;
}
async function loadImages(v) {
  const key = JSON.stringify([selected.id, v.number, v.images]);
  if (key === pendingImageKey || key === imageKey) return;
  clearImages();
  const request = imageRequest;
  pendingImageKey = key;
  const status = $('#image-status');
  status.textContent = '正在读取当前版本配图…';
  const nextBlobs = new Set(), children = [];
  try {
    if (!v.images.length) children.push(node('p', '本栏目无需配图', 'muted'));
    for (const file of v.images) {
      const res = await api(file.url, { raw: true }); const blob = await res.blob();
      if (request !== imageRequest) return;
      const url = URL.createObjectURL(blob); nextBlobs.add(url);
      const image = node('img'); image.src = url; image.alt = file.name;
      await image.decode();
      if (request !== imageRequest) return;
      const button = node('button', '下载图片');
      button.dataset.download = '';
      button.disabled = actionBusy || dirty;
      button.onclick = () => safe(async () => { await download(file.url); toast('图片已开始下载。'); }); children.push(image, button);
    }
    $('#images').replaceChildren(...children);
    for (const url of nextBlobs) blobs.add(url);
    nextBlobs.clear(); imageKey = key;
    status.textContent = '';
  } catch (e) {
    if (request === imageRequest) {
      status.textContent = `当前版本图片加载失败：${e.message}`;
      const retry = node('button', '重试当前版本配图');
      retry.onclick = () => loadImages(v);
      $('#images').replaceChildren(retry);
    }
  } finally {
    for (const url of nextBlobs) URL.revokeObjectURL(url);
    if (request === imageRequest) pendingImageKey = null;
  }
}
async function act(path, json) {
  if (actionBusy) return;
  actionBusy = true; updateDetailControls();
  const status = $('#detail-action-status');
  status.className = 'muted';
  status.textContent = { revisions: '正在保存修改…', confirm: '正在确认稿件…', unconfirm: '正在撤回确认…' }[path];
  try {
    const fresh = await api(`/api/briefs/issues/${selected.id}/${path}`, { method: 'POST', json });
    refreshRequest++;
    items = items.map(item => item.id === fresh.id ? fresh : item);
    renderCards();
    dirty = false;
    status.textContent = '';
    open(fresh, path === 'revisions' ? fresh.version : current.number);
    return true;
  } catch (error) {
    status.className = 'error';
    status.textContent = error.message;
    return false;
  } finally {
    actionBusy = false; updateDetailControls();
  }
}
$('#refresh').onclick = () => safe(refresh);
$('#day').onchange = () => safe(async () => {
  if (dirty && !confirm('放弃未保存的修改？')) { $('#day').value = selected.issue_date; return; }
  hideDetail();
  items = [];
  dataStates = [];
  renderCards();
  await refresh();
});
$('#version').onchange = () => safe(async () => { if (dirty && !confirm('放弃未保存的修改？')) { $('#version').value = current.number; return; } await showVersion(); });
$('#title').oninput = $('#body').oninput = $('#fixed').oninput = () => { dirty = true; updateDetailControls(); };
$('#close-detail').onclick = () => { if (actionBusy || (dirty && !confirm('放弃未保存的修改？'))) return; hideDetail(); };
$('#save').onclick = () => safe(async () => {
  if (await act('revisions', { base_version: current.number, title: $('#title').value, dynamic_body: $('#body').value, fixed_notes: $('#fixed').value })) toast('修改已保存。');
});
$('#confirm').onclick = () => safe(async () => { if (await act('confirm', { version: current.number })) toast('此版本已确认为正式取稿版本。'); });
$('#unconfirm').onclick = () => safe(async () => {
  if (confirm('撤回确认后，只读成员将无法取用本期稿件。继续撤回？') && await act('unconfirm')) toast('已撤回确认。');
});
$('#copy-title').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.title); message('标题已复制。'); toast('标题已复制。'); });
$('#copy-body').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.body); message('完整正文已复制。'); toast('完整正文已复制。'); });
for (const [id, action] of [['rerender', 'render'], ['refetch', 'refresh']]) $(`#${id}`).onclick = async () => {
  if (!selected || !current || actionBusy) return;
  const { category, issue_date: issueDate } = selected;
  const expectedVersion = action === 'render' ? current.number : undefined;
  if (!confirm(action === 'refresh' ? '重新请求数据源并生成新版本？现有稿件会保留。' : `基于第 ${expectedVersion} 版重新制图并创建新版本？`)) return;
  const status = $('#detail-action-status');
  status.className = 'muted'; status.textContent = '';
  try { await runJob(category, action, { issueDate, expectedVersion }); }
  catch (error) { status.className = 'error'; status.textContent = error.message; }
};
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
await safe(async () => { me = await login(); config = await api('/api/briefs/categories'); await refresh(); });
setInterval(() => { if (me && !document.hidden && !dirty) safe(refresh); }, 30000);
setInterval(() => { pollJobs().catch(() => {}); }, 2000);
