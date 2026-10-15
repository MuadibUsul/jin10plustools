import { $, node, api, login, download, copy } from '/workspace.js';
const labels = { queued: '等待执行', running: '生成中', waiting: '等待数据更新', succeeded: '已生成', failed: '生成失败', skipped: '已跳过' };
const aiLabels = { not_configured: '未配置', disabled: '已关闭', pending: '待处理', ok: '已生成', failed: '生成失败', invalid_response: '结果未通过校验', timeout: '请求超时' };
let me, config, items = [], selected = null, current = null, dirty = false;
const blobs = new Set();
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
$('#day').value = today();
function message(text, error = false) { $('#message').textContent = text; $('#message').className = error ? 'error' : 'success'; }
async function safe(action) { try { await action(); } catch (e) { message(e.message, true); } }
function canEdit() { return me.user.role !== 'viewer'; }
function chosen() { return selected?.versions.find((v) => v.number === Number($('#version').value)); }
function displayed() { return chosen(); }
async function refresh() {
  const data = await api(`/api/briefs/issues?date=${$('#day').value}`); items = data.items; renderCards();
  if (selected && !dirty) { const fresh = items.find((x) => x.id === selected.id); if (fresh) await open(fresh, Number($('#version').value)); }
}
function renderCards() {
  $('#cards').replaceChildren();
  for (const category of config.categories) {
    const item = items.find((x) => x.category === category.id); const card = node('article', undefined, 'brief-card');
    card.append(node('h2', category.label));
    const job = item?.jobs[0], version = item?.versions[0];
    const state = item?.confirmed_version ? `已确认 v${item.confirmed_version}` : version ? '待核对' : '未生成';
    card.append(node('span', state, item?.confirmed_version ? 'badge ready' : 'badge'));
    card.append(node('h3', version?.title || '等待本期数据与成品'));
    card.append(node('p', version ? `数据日 ${version.data_date}` : '尚无有效数据', 'muted'));
    if (job) card.append(node('p', `${labels[job.status] || job.status}${job.error ? '：' + job.error : ''}`, job.status === 'failed' ? 'error' : 'muted'));
    if (version?.warnings.length) card.append(node('p', '有来源或口径说明，请核对', 'warning'));
    const controls = node('div', undefined, 'toolbar');
    if (item?.versions.length) { const view = node('button', '查看与取稿'); view.onclick = () => safe(() => open(item)); controls.append(view); }
    if (canEdit()) { const run = node('button', '生成稿件'); run.onclick = () => safe(() => runJob(category.id, 'produce')); controls.append(run); }
    card.append(controls); $('#cards').append(card);
  }
}
async function runJob(category, action) {
  const result = await api('/api/briefs/jobs', { method: 'POST', json: { category, action, issue_date: $('#day').value } });
  message(`任务已加入队列：${labels[result.status] || result.status}。不必重复点击。`); await refresh();
}
function clearImages() { for (const url of blobs) URL.revokeObjectURL(url); blobs.clear(); $('#images').replaceChildren(); }
async function open(item, number) {
  if (dirty && !window.confirm('有未保存的修改，确认离开？')) return;
  selected = item; dirty = false;
  $('#detail').hidden = false; $('#detail-title').textContent = item.label;
  const select = $('#version'); select.replaceChildren();
  for (const v of item.versions) { const option = node('option', `v${v.number} · ${v.kind === 'edited' ? '人工修改' : '生成稿'}${item.confirmed_version === v.number ? ' · 已确认' : ''}`); option.value = v.number; select.append(option); }
  select.value = String(number || item.versions[0].number); await showVersion();
}
async function showVersion() {
  const v = chosen(); if (!v) return; current = v;
  $('#title').value = v.title; $('#body').value = v.dynamic_body;
  $('#title').readOnly = $('#body').readOnly = !canEdit();
  $('#fixed').textContent = v.fixed_notes || (selected.category === 'oil_etf' ? '点击查看完整持仓数据，捕捉油价之外的资金变化。' : '');
  $('#detail-status').textContent = `数据日 ${v.data_date} · DeepSeek：${aiLabels[v.deepseek.status] || '未完成'}`;
  $('#warnings').replaceChildren(...v.warnings.map((s) => node('p', s)));
  $('#ack-row').hidden = !v.warnings.length; $('#ack').checked = false;
  $('#facts').textContent = JSON.stringify(v.facts, null, 2);
  for (const id of ['save', 'confirm', 'unconfirm', 'rerender', 'retry-ai', 'refetch']) $(`#${id}`).hidden = !canEdit();
  $('#save').disabled = v.number !== selected.version;
  $('#confirm').disabled = v.number !== selected.version || !v.valid;
  $('#use-ai').hidden = !canEdit() || v.deepseek.status !== 'ok';
  clearImages();
  if (!v.images.length) $('#images').append(node('p', '本栏目无需配图', 'muted'));
  for (const file of v.images) {
    try { const res = await api(file.url, { raw: true }); if (current !== v) return; const url = URL.createObjectURL(await res.blob()); blobs.add(url); const image = node('img'); image.src = url; image.alt = file.name; const button = node('button', '下载图片'); button.onclick = () => safe(() => download(file.url, file.name)); $('#images').append(image, button); }
    catch (e) { $('#images').append(node('p', e.message, 'error')); }
  }
  dirty = false;
}
async function act(path, json) { await api(`/api/briefs/issues/${selected.id}/${path}`, { method: 'POST', json }); dirty = false; const fresh = await api(`/api/briefs/issues/${selected.id}`); await open(fresh); await refresh(); }
$('#refresh').onclick = () => safe(refresh);
$('#day').onchange = () => safe(async () => { if (dirty && !confirm('放弃未保存的修改？')) { $('#day').value = selected.issue_date; return; } selected = null; dirty = false; $('#detail').hidden = true; await refresh(); });
$('#version').onchange = () => safe(async () => { if (dirty && !confirm('放弃未保存的修改？')) { $('#version').value = current.number; return; } await showVersion(); });
$('#title').oninput = $('#body').oninput = () => { dirty = true; };
$('#close-detail').onclick = () => { if (dirty && !confirm('放弃未保存的修改？')) return; dirty = false; selected = null; $('#detail').hidden = true; clearImages(); };
$('#save').onclick = () => safe(() => act('revisions', { base_version: current.number, title: $('#title').value, dynamic_body: $('#body').value }));
$('#confirm').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改，再确认版本'); await act('confirm', { version: current.number, acknowledge_warnings: $('#ack').checked }); message('此版本已确认为正式取稿版本。'); });
$('#unconfirm').onclick = () => safe(() => act('unconfirm'));
$('#copy-title').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.title); message('标题已复制。'); });
$('#copy-body').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改后取稿'); await copy(current.body); message('完整正文已复制。'); });
$('#export').onclick = () => safe(async () => { if (dirty) throw new Error('请先保存修改'); await download(`/api/briefs/issues/${selected.id}/export?version=${current.number}`, `${selected.label}-${selected.issue_date}-v${current.number}.zip`); });
$('#use-ai').onclick = () => { $('#title').value = current.deepseek.title; $('#body').value = current.deepseek.body; dirty = true; message('已采用DeepSeek表达，请保存并核对。'); };
for (const [id, action] of [['rerender', 'render'], ['retry-ai', 'ai'], ['refetch', 'refresh']]) $(`#${id}`).onclick = () => safe(async () => { if (!confirm(action === 'refresh' ? '重新请求数据源并生成新版本？现有稿件会保留。' : '创建新版本？现有人工修改和确认版本会保留。')) return; await runJob(selected.category, action); });
$('#schedule').onclick = () => safe(async () => { if (!confirm(config.schedule_enabled ? '关闭本团队自动生产？' : '启用工作日08:00—10:00自动生产？来源差异仍需编辑核对。')) return; await api('/api/briefs/config', { method: 'PUT', json: { enabled: !config.schedule_enabled } }); config.schedule_enabled = !config.schedule_enabled; $('#schedule').textContent = config.schedule_enabled ? '关闭自动排期' : '启用自动排期'; });
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
await safe(async () => { me = await login(); config = await api('/api/briefs/categories'); $('#schedule').hidden = !config.can_configure; $('#schedule').textContent = config.schedule_enabled ? '关闭自动排期' : '启用自动排期'; await refresh(); });
setInterval(() => { if (me && !document.hidden && !dirty) safe(refresh); }, 30000);
