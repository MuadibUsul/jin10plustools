import { $, node, api, login, download, copy, copyDate, displayTime, toast } from '/workspace.js';
import '/pull-cord.js';
import { computerShell } from '/retro-computer.js';
const labels = { queued: '等待执行', running: '生成中', waiting: '等待数据更新', succeeded: '已生成', failed: '生成失败', skipped: '已跳过' };
const aiLabels = { not_configured: '未配置', disabled: '已关闭', pending: '待处理', ok: '已生成', failed: '生成失败', invalid_response: '结果未通过校验', timeout: '请求超时' };
const aiName = (info) => info?.provider === 'chat2api' ? 'chat2api' : 'DeepSeek';
let me, config, items = [], selected = null, current = null, dirty = false;
const blobs = new Set();
let imageKey = null, pendingImageKey = null, imageRequest = 0;
const skillCategories = new Set(['volume', 'turnover', 'valuation', 'liquidity', 'oil_etf']);
let skillData = null;
const pendingCategories = new Set();
const active = (job) => ['queued', 'running', 'waiting'].includes(job?.status);
const processSteps = ['准备任务', '正在取数', '正在制图', '整理文案', '保存稿件', '生成完成'];
const channelNames = { volume: '成交额', turnover: '换手率', valuation: '估值', liquidity: '流动性', oil_etf: '原油ETF', us_barometer: '美股晴雨表', us_structure: '美股结构' };
const terminalSeen = new Map(), terminalPending = new Set();
let terminalDay = null, polling = false;
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
$('#day').value = today(); $('#day').max = today();
function message(text, error = false) { $('#message').textContent = text; $('#message').className = error ? 'error' : 'success'; }
async function safe(action) { try { await action(); } catch (e) { message(e.message, true); toast(e.message, 'error'); } }
function canEdit() { return me.user.role !== 'viewer'; }

const detailDialog = $('#detail-dialog');
function showDetail() {
  if (!detailDialog || detailDialog.open) return;
  if (detailDialog.showModal) detailDialog.showModal();
  else detailDialog.setAttribute('open', '');
}
function clearDetailState() { dirty = false; selected = null; skillData = null; clearImages(); closeSkillPreview(); }
function hideDetail() {
  if (!detailDialog || !detailDialog.open) return;
  if (detailDialog.close) { detailDialog.close(); return; }
  detailDialog.removeAttribute('open'); clearDetailState();
}
detailDialog?.addEventListener('cancel', (event) => {
  if (dirty && !window.confirm('有未保存的修改，确认关闭？')) event.preventDefault();
});
detailDialog?.addEventListener('close', clearDetailState);
function chosen() { return selected?.versions.find((v) => v.number === Number($('#version').value)); }
function displayed() { return chosen(); }
async function refresh() {
  const day = $('#day').value;
  const data = await api(`/api/briefs/issues?date=${day}`);
  if (day !== $('#day').value) return;
  items = data.items; renderCards();
  if (selected && !dirty) {
    const fresh = items.find((x) => x.id === selected.id);
    if (!fresh?.versions.length) hideDetail();
    else if (fresh.version !== selected.version || fresh.confirmed_version !== selected.confirmed_version) {
      await open(fresh, Number($('#version').value));
    }
  }
}
function renderCards() {
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
    const busy = active(job) || pendingCategories.has(category.id);
    card.setAttribute('aria-busy', String(busy));
    const indicator = card.querySelector('.job-indicator');
    const indicatorClass = busy ? 'job-indicator spinning' : `job-indicator ${job?.status || ''}`;
    if (indicator.className !== indicatorClass) indicator.className = indicatorClass;
    card.querySelector('.process-button').setAttribute('aria-label', `${category.label}生成过程：${busy ? '正在执行或等待' : labels[job?.status] || '暂无任务'}`);
    const body = card.querySelector('.brief-card-body');
    const signature = JSON.stringify([item, pendingCategories.has(category.id)]);
    if (body.dataset.signature === signature) continue;
    body.dataset.signature = signature; body.replaceChildren();
    const state = item?.confirmed_version
      ? (version?.number > item.confirmed_version
        ? '稿件已更新'
        : '已确认')
      : version ? '已生成' : '未生成';
    body.append(node('span', state, item?.confirmed_version ? 'badge ready' : 'badge'));
    body.append(node('h3', version?.title || '等待本期数据与成品'));
    body.append(node('p', version ? `数据日 ${copyDate(version.data_date)}` : '尚无有效数据', 'muted'));
    if (job) body.append(node('p', `${job.status === 'failed' && version ? '最近一次任务失败；已有预览图可查看' : labels[job.status] || job.status}${job.error ? '：' + job.error : ''}`, job.status === 'failed' ? 'error' : 'muted'));
    if (version?.warnings.length) body.append(node('p', '有来源或口径说明，请核对', 'warning'));
    if (version?.deepseek && !['not_configured','disabled'].includes(version.deepseek.status)) body.append(node('p', version.deepseek.status === 'ok' ? `${aiName(version.deepseek)}已完成，可在编辑区采用` : `规则稿已保存 · ${aiName(version.deepseek)}未完成，可补跑`, version.deepseek.status === 'ok' ? 'success' : 'warning'));
    const controls = node('div', undefined, 'toolbar');
    if (item?.versions.length) { const view = node('button', '查看与取稿'); view.onclick = () => safe(() => open(item)); controls.append(view); }
    if (canEdit()) { const run = node('button', busy ? '任务执行中' : '生成稿件'); run.disabled = busy; run.onclick = () => safe(() => runJob(category.id, 'produce')); controls.append(run); }
    body.append(controls);
  }
  renderProcess();
}
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
    const ai = item?.versions[0]?.deepseek?.status;
    const aiFailed = ai && !['ok','not_configured','disabled'].includes(ai);
    const step = Math.max(1, Math.min(6, Number(job.progress?.step) || 1)), signature = `${job.status}:${step}:${ai || ''}`;
    if (terminalSeen.get(job.id) === signature) continue;
    terminalSeen.set(job.id, signature);
    const phase = category.id === 'oil_etf' && step === 3 ? '核对持仓' : processSteps[step - 1];
    const text = job.status === 'running' ? phase : { queued: '任务已排队', waiting: '等待数据更新', succeeded: aiFailed ? '规则稿已保存，AI未完成' : ai === 'ok' ? '稿件与AI文案已完成' : '稿件已保存', failed: '生成未完成', skipped: '任务已停止' }[job.status] || '等待任务状态';
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
async function runJob(category, action) {
  if (pendingCategories.has(category)) return;
  pendingCategories.add(category); renderCards();
  try {
  const result = await api('/api/briefs/jobs', { method: 'POST', json: { category, action, issue_date: $('#day').value } });
  message(`任务已加入队列：${labels[result.status] || result.status}。不必重复点击。`); toast('任务已加入队列。'); await refresh();
  } finally { pendingCategories.delete(category); renderCards(); }
}
function clearImages() {
  imageRequest++; imageKey = pendingImageKey = null; current = null;
  for (const url of blobs) URL.revokeObjectURL(url);
  blobs.clear(); $('#images').replaceChildren();
}
async function open(item, number) {
  if (dirty && !window.confirm('有未保存的修改，确认离开？')) return;
  selected = item; dirty = false;
  showDetail(); $('#detail-title').textContent = item.label;
  const select = $('#version'); select.replaceChildren();
  for (const v of item.versions) { const saved = displayTime(v.created_at); const option = node('option', `${saved} · ${v.kind === 'edited' ? '保存修改' : '生成稿件'}${item.confirmed_version === v.number ? ' · 已确认' : ''}`); option.value = v.number; select.append(option); }
  select.value = String(item.versions.some((v) => v.number === number) ? number : item.versions[0].number); await showVersion();
}
async function showVersion() {
  const v = chosen(); if (!v) return; current = v;
  $('#title').value = v.title; $('#body').value = v.dynamic_body;
  $('#title').readOnly = $('#body').readOnly = !canEdit();
  $('#fixed').value = v.fixed_notes || '';
  $('#fixed').readOnly = !canEdit();
  $('#notes-label').textContent = v.notes_placement === 'image' ? '注释（保存后显示在图片底部）' : '注释（自动加入完整正文）';
  $('#detail-status').textContent = `数据日 ${copyDate(v.data_date)}${v.deepseek.status === 'not_configured' || v.deepseek.status === 'disabled' ? '' : ` · ${aiName(v.deepseek)}：${aiLabels[v.deepseek.status] || '未完成'}`}`;
  const warnings = [...(v.warnings || [])];
  if (v.deepseek.stale) warnings.push('AI候选基于生成时的正文，当前正文已修改；请先补跑AI再采用。');
  if (v.deepseek.untracked) warnings.push('AI候选来源未记录，请补跑AI后再采用。');
  $('#warnings').replaceChildren(...warnings.map((s) => node('p', s)));
  $('#facts').textContent = JSON.stringify(v.facts, null, 2);
  for (const id of ['save', 'confirm', 'unconfirm', 'rerender', 'retry-ai', 'refetch']) $(`#${id}`).hidden = !canEdit();
  $('#save').disabled = v.number !== selected.version;
  $('#confirm').disabled = v.number !== selected.version || !v.valid;
  $('#use-ai').hidden = !canEdit() || v.deepseek.status !== 'ok' || v.deepseek.stale === true || v.deepseek.untracked === true || v.number !== selected.version;
  $('#skill-preview').hidden = !skillCategories.has(selected.category);
  $('#skill-preview').disabled = false;
  $('#unconfirm').disabled = !selected.confirmed_version;
  dirty = false;
  const key = JSON.stringify([selected.id, v.number, v.images]);
  if (key === pendingImageKey) return;
  const request = ++imageRequest;
  pendingImageKey = null;
  if (key === imageKey) return;
  pendingImageKey = key;
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
      button.onclick = () => safe(async () => { await download(file.url); toast('图片已开始下载。'); }); children.push(image, button);
    }
    const oldBlobs = [...blobs];
    $('#images').replaceChildren(...children);
    blobs.clear(); for (const url of nextBlobs) blobs.add(url);
    nextBlobs.clear(); imageKey = key;
    for (const url of oldBlobs) URL.revokeObjectURL(url);
  } catch (e) {
    if (request === imageRequest) message(`图片加载失败：${e.message}；原预览保留，请刷新重试。`, true);
  } finally {
    for (const url of nextBlobs) URL.revokeObjectURL(url);
    if (request === imageRequest) pendingImageKey = null;
  }
}
async function showSkillPreview() {
  const v = chosen();
  if (!v || !selected || !skillCategories.has(selected.category)) return;
  const button = $('#skill-preview'); button.disabled = true;
  message('正在请求 AI 润色 Skill 文案…');
  try {
    const data = await api(`/api/briefs/issues/${selected.id}/skill-preview?version=${v.number}`);
    skillData = data;
    const ai = data.ai || {};
    const aiLabel = ai.status === 'ok' ? `${ai.provider || 'AI'} 已润色` : '生产规则稿（AI 未完成）';
    $('#skill-status').textContent = `${copyDate(data.data_date)} · ${data.skill_id}@${data.skill_version} · ${aiLabel}`;
    $('#skill-title').value = data.title || '';
    $('#skill-body').value = data.dynamic_body || '';
    $('#skill-note').value = data.fixed_note || '';
    const warnings = [...(data.warnings || []), ...((data.ai || {}).warnings || [])];
    $('#skill-warnings').replaceChildren(...warnings.map((text) => node('p', text)));
    $('#skill-adopt').hidden = !canEdit();
    const dialog = $('#skill-dialog');
    if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); }
  } finally { button.disabled = false; }
}
function closeSkillPreview() {
  const dialog = $('#skill-dialog');
  if (!dialog?.open) return;
  if (dialog.close) dialog.close(); else dialog.removeAttribute('open');
}
async function act(path, json) { await api(`/api/briefs/issues/${selected.id}/${path}`, { method: 'POST', json }); dirty = false; const fresh = await api(`/api/briefs/issues/${selected.id}`); await open(fresh); await refresh(); }
$('#refresh').onclick = () => safe(refresh);
$('#day').onchange = () => safe(async () => { if (dirty && !confirm('放弃未保存的修改？')) { $('#day').value = selected.issue_date; return; } hideDetail(); await refresh(); });
$('#version').onchange = () => safe(async () => { if (dirty && !confirm('放弃未保存的修改？')) { $('#version').value = current.number; return; } await showVersion(); });
$('#title').oninput = $('#body').oninput = $('#fixed').oninput = () => { dirty = true; };
$('#close-detail').onclick = () => { if (dirty && !confirm('放弃未保存的修改？')) return; hideDetail(); };
$('#save').onclick = () => { const button = $('#save'); if (button.disabled) return; button.disabled = true; safe(async () => { await act('revisions', { base_version: current.number, title: $('#title').value, dynamic_body: $('#body').value, fixed_notes: $('#fixed').value }); toast('修改已保存。'); }).finally(() => { button.disabled = false; }); };
$('#confirm').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改，再确认版本'); await act('confirm', { version: current.number }); message('此版本已确认为正式取稿版本。'); toast('已确认此版本。'); });
$('#unconfirm').onclick = () => safe(async () => { await act('unconfirm'); toast('已撤回确认。'); });
$('#copy-title').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.title); message('标题已复制。'); toast('标题已复制。'); });
$('#copy-body').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.body); message('完整正文已复制。'); toast('完整正文已复制。'); });
$('#use-ai').onclick = () => { $('#title').value = current.deepseek.title; $('#body').value = current.deepseek.body; dirty = true; message(`已采用${aiName(current.deepseek)}表达，请保存并核对。`); toast('已采用AI表达，请保存。'); };
$('#skill-preview').onclick = () => safe(showSkillPreview);
$('#close-skill').onclick = closeSkillPreview;
$('#skill-copy-title').onclick = () => safe(async () => { await copy($('#skill-title').value); message('Skill 标题已复制。'); toast('Skill 标题已复制。'); });
$('#skill-copy-body').onclick = () => safe(async () => { await copy($('#skill-body').value); message('Skill 正文已复制。'); toast('Skill 正文已复制。'); });
$('#skill-adopt').onclick = () => {
  if (!skillData) return;
  if (dirty && !window.confirm('采用 Skill 文案会覆盖当前未保存的标题和正文，继续？')) return;
  $('#title').value = skillData.title || '';
  $('#body').value = skillData.dynamic_body || '';
  dirty = true; closeSkillPreview();
  message('已采用 Skill 文案，请核对并保存。'); toast('已采用 Skill 文案，请保存。');
};
for (const [id, action] of [['rerender', 'render'], ['retry-ai', 'ai'], ['refetch', 'refresh']]) $(`#${id}`).onclick = () => safe(async () => { if (!confirm(action === 'refresh' ? '重新请求数据源并生成新版本？现有稿件会保留。' : '创建新版本？现有人工修改和确认版本会保留。')) return; await runJob(selected.category, action); });
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
await safe(async () => { me = await login(); config = await api('/api/briefs/categories'); await refresh(); });
setInterval(() => { if (me && !document.hidden && !dirty) safe(refresh); }, 30000);
setInterval(() => { pollJobs().catch(() => {}); }, 2000);
