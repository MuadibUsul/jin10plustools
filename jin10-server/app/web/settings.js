import { $, node, api, login, displayTime, copyDate, download } from '/workspace.js';

let data, dirty = false;
const providers = { jev: ['日报选稿 · JEV', '栏目分类与重要度评分'], deepseek: ['固定快讯 · DeepSeek', '在规则稿基础上生成可选的 AI 表达'], chat2api: ['固定快讯 · chat2api', '通过本机 chat2api 节点使用 ChatGPT 网页版；留空则使用服务器节点密钥'] };
function message(text, error = false) { const target = $('#settings-message'); target.textContent = text; target.className = error ? 'error' : 'success'; }
function field(label, input) { const row = node('label', label, 'field'); row.append(input); return row; }
async function load() {
  data = await api('/api/settings');
  $('#live-url').value = data.live_url || ''; $('#live-url').readOnly = !data.can_edit;
  $('#live-channel-fields').replaceChildren();
  for (const category of data.live_catalog.categories) {
    const group = node('details'); group.append(node('summary', category.label));
    for (const channel of data.live_catalog.channels.filter(c => c.category === category.id && c.id !== 'custom')) {
      const input = node('input'); input.type = 'url'; input.maxLength = 2048; input.dataset.liveId = channel.id;
      input.value = data.live_urls[channel.id] || ''; input.placeholder = channel.url || '待补充获准使用的播放器地址'; input.readOnly = !data.can_edit;
      group.append(field(channel.name, input), node('p', channel.note || '已接入官方公开播放器', 'muted'));
    }
    $('#live-channel-fields').append(group);
  }
  $('#note-fields').replaceChildren();
  for (const category of data.categories) {
    const input = node('textarea'); input.id = `notes-${category.id}`; input.value = category.notes; input.maxLength = 2000; input.rows = 4; input.readOnly = !data.can_edit;
    const group = node('details'); group.open = category.id === 'volume'; group.append(node('summary', category.label), field('默认注释', input));
    $('#note-fields').append(group);
  }
  $('#note-history').replaceChildren();
  for (const entry of [...data.note_revisions].reverse()) {
    const detail = node('details'); detail.append(node('summary', `第 ${entry.number} 次修改 · ${displayTime(entry.at)}`));
    for (const category of data.categories) if (entry.after[category.id] !== entry.before[category.id]) {
      detail.append(node('h3', category.label), node('p', entry.after[category.id], 'note-history-text'));
    }
    $('#note-history').append(detail);
  }
  if (!data.note_revisions.length) $('#note-history').append(node('p', '暂无默认注释修改记录', 'muted'));
  $('#schedule-enabled').checked = data.schedule_enabled; $('#schedule-enabled').disabled = !data.can_admin;
  $('#ai-fields').replaceChildren();
  $('#ai-provider').value = data.ai_provider || 'deepseek';
  $('#ai-provider').disabled = !data.can_admin;
  for (const [name, [label, description]] of Object.entries(providers)) {
    const saved = data.ai[name], card = node('div', undefined, 'ai-setting');
    card.append(node('h3', label), node('p', description, 'muted'));
    card.append(node('p', `${saved.configured ? '已配置' : '未配置'}${saved.source === 'server' ? ' · 使用服务器 Key' : saved.source === 'team' ? ' · 使用团队 Key' : ''}${saved.enabled ? '' : ' · 已停用'}`, saved.configured && saved.enabled ? 'success' : 'muted'));
    if (data.can_admin) {
      const key = node('input'); key.type = 'password'; key.autocomplete = 'new-password'; key.id = `key-${name}`; key.maxLength = 2048; key.placeholder = '输入新的 Key；留空保留';
      const model = node('input'); model.id = `model-${name}`; model.value = saved.model; model.maxLength = 100; model.placeholder = name === 'deepseek' ? '例如 deepseek-chat' : name === 'chat2api' ? '例如 gpt-4o-mini' : 'jev-latest';
      card.append(field('API Key', key), field('模型名称', model));
      const enabled = node('input'); enabled.type = 'checkbox'; enabled.id = `enabled-${name}`; enabled.checked = saved.enabled;
      const enableLabel = node('label', ' 启用此 AI 接入', 'check-row'); enableLabel.prepend(enabled); card.append(enableLabel);
      if (saved.source === 'team') {
        const reset = node('input'); reset.type = 'checkbox'; reset.id = `reset-${name}`;
        const resetLabel = node('label', ' 移除团队 Key，恢复服务器配置', 'check-row'); resetLabel.prepend(reset); card.append(resetLabel);
      }
    } else card.append(node('p', `模型：${saved.model || '未指定'}`, 'muted'));
    $('#ai-fields').append(card);
  }
  for (const hint of document.querySelectorAll('.admin-hint')) hint.textContent = data.can_admin ? '' : '仅管理员可修改此项。';
  $('#save-settings').hidden = !data.can_edit;
  $('#settings-layout').hidden = false;
  $('#nav-database').hidden = false;
  $('#nav-chat2api').hidden = !data.can_admin;
  showPanel(initialPanel());
  dirty = false;
}
// —— 设置分区导航 ——
const sections = { team: '#team-panel', notes: '#notes-panel', ai: '#ai-panel', live: '#live-panel' };
const standalone = { database: '#database-panel', feedback: '#feedback-panel', chat2api: '#chat2api-panel' };
let panel = 'team', exportData = null, exportPreset = '3m', currentUser = null;

function initialPanel() {
  for (const name of ['feedback', 'chat2api', 'database', 'notes', 'ai', 'live']) {
    if (location.hash === `#${name}`) return name;
  }
  return 'team';
}
function showPanel(name) {
  if (name === 'chat2api' && !data?.can_admin) name = 'team';
  if (!(name in sections) && !(name in standalone)) name = 'team';
  panel = name;
  $('#settings-form').hidden = !(name in sections);
  for (const [key, selector] of Object.entries(sections)) $(selector).hidden = key !== name;
  for (const [key, selector] of Object.entries(standalone)) $(selector).hidden = key !== name;
  for (const link of document.querySelectorAll('.settings-nav a')) {
    const active = link.dataset.panel === name;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  if (name === 'database') openExport();
  if (name === 'feedback') openFeedback();
  if (name === 'chat2api') openChat2api();
}
for (const link of document.querySelectorAll('.settings-nav a')) link.addEventListener('click', (event) => {
  event.preventDefault(); showPanel(link.dataset.panel); history.replaceState(null, '', link.getAttribute('href'));
});
window.addEventListener('hashchange', () => showPanel(initialPanel()));

// —— 数据库导出 ——
const isoDay = (value) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
function presetRange(preset) {
  const now = new Date();
  if (preset === 'all') return { start: '', end: '' };
  if (preset === '3m') { const from = new Date(now); from.setMonth(from.getMonth() - 3); return { start: isoDay(from), end: isoDay(now) }; }
  if (preset === 'year') return { start: isoDay(new Date(now.getFullYear(), 0, 1)), end: isoDay(now) };
  if (preset === '1y') { const from = new Date(now); from.setFullYear(from.getFullYear() - 1); return { start: isoDay(from), end: isoDay(now) }; }
  return { start: $('#export-start').value, end: $('#export-end').value };
}
function currentRange() { return exportPreset === 'custom' ? presetRange('custom') : presetRange(exportPreset); }
function exportStatus(text, error = false) { const target = $('#export-status'); target.textContent = text; target.className = error ? 'error' : 'muted'; }
function updateExportHint() {
  const { start, end } = currentRange();
  const target = $('#export-range');
  if (!start && !end) { target.textContent = '范围：全部已存'; return; }
  if (start && end) { target.textContent = `范围：${copyDate(start)} 至 ${copyDate(end)}`; return; }
  target.textContent = start ? `范围：${copyDate(start)} 起` : `范围：截至 ${copyDate(end)}`;
}
function updateDatasetNote() {
  const item = exportData?.datasets.find((entry) => entry.key === $('#export-dataset').value);
  $('#export-dataset-note').textContent = item ? `${item.description} 筛选字段：${item.date_label}。` : '';
}
function exportQuery() {
  const { start, end } = currentRange();
  const params = new URLSearchParams();
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  return params;
}
function checkedRange() {
  const { start, end } = currentRange();
  if (start && end && start > end) throw new Error('开始日期不能晚于结束日期');
  return { start, end };
}
async function openExport() {
  if (exportData) return;
  try {
    exportData = await api('/api/export/datasets');
    const select = $('#export-dataset');
    select.replaceChildren(...exportData.datasets.map((item) => {
      const option = node('option', `${item.label} · 按${item.date_label}`);
      option.value = item.key; return option;
    }));
    const list = node('div');
    for (const item of exportData.datasets) {
      const block = node('div', undefined, 'export-dataset-item');
      block.append(node('strong', item.label), node('p', `${item.description}（筛选字段：${item.date_label}；${item.columns.length} 列）`, 'muted'));
      list.append(block);
    }
    $('#export-datasets').replaceChildren(list);
    select.onchange = () => { updateDatasetNote(); exportStatus(''); };
    $('#export-format').onchange = () => exportStatus('');
    for (const button of document.querySelectorAll('#export-presets button')) button.onclick = () => {
      exportPreset = button.dataset.range;
      for (const other of document.querySelectorAll('#export-presets button')) other.setAttribute('aria-pressed', String(other === button));
      $('#export-custom').hidden = exportPreset !== 'custom';
      if (exportPreset === 'custom' && !$('#export-start').value) { const range = presetRange('3m'); $('#export-start').value = range.start; $('#export-end').value = range.end; }
      updateExportHint(); exportStatus('');
    };
    $('#export-start').oninput = $('#export-end').oninput = () => { updateExportHint(); exportStatus(''); };
    $('#export-count').onclick = async () => {
      const button = $('#export-count');
      button.disabled = true; exportStatus('正在统计…');
      try {
        checkedRange();
        const result = await api(`/api/export/${select.value}/count?${exportQuery()}`);
        const limited = result.count > result.max_rows ? `，超过单次上限 ${result.max_rows} 行，请缩小时间范围` : '';
        exportStatus(`当前范围共 ${result.count} 行${limited}。`);
      } catch (error) { exportStatus(error.message, true); }
      finally { button.disabled = false; }
    };
    $('#export-download').onclick = async () => {
      const button = $('#export-download');
      button.disabled = true; exportStatus('正在统计并生成文件…');
      try {
        const { start, end } = checkedRange();
        const key = select.value, fmt = $('#export-format').value;
        const count = await api(`/api/export/${key}/count?${exportQuery()}`);
        if (count.count > count.max_rows) throw new Error(`本次 ${count.count} 行，超过单次上限 ${count.max_rows} 行，请缩小时间范围`);
        const params = exportQuery(); params.set('format', fmt);
        await download(`/api/export/${key}?${params}`);
        exportStatus(`已开始下载：${start ? copyDate(start) : '最早'} 至 ${end ? copyDate(end) : '现在'}，共 ${count.count} 行。`);
      } catch (error) { exportStatus(error.message, true); }
      finally { button.disabled = false; }
    };
    updateDatasetNote(); updateExportHint();
  } catch (error) { exportStatus(error.message, true); }
}

// —— 反馈 ——
function feedbackStatus(text, error = false) { const target = $('#feedback-status'); target.textContent = text; target.className = error ? 'error' : text ? 'success' : 'muted'; }
function feedbackItem(item) {
  const box = node('article', undefined, 'feedback-item');
  const head = node('div', undefined, 'feedback-item-head');
  head.append(node('strong', item.title));
  const meta = [item.name, item.account && item.account !== item.name ? `账号：${item.account}` : '',
    displayTime(item.created_at), item.mine ? '我提交的' : ''].filter(Boolean).join(' · ');
  head.append(node('span', meta, 'muted'));
  box.append(head, node('p', item.content, 'feedback-item-body'));
  if (item.has_image) {
    const view = node('button', '查看示意图'); view.type = 'button';
    view.onclick = async () => {
      view.disabled = true;
      try {
        const res = await api(`/api/feedback/${item.id}/image`, { raw: true });
        const url = URL.createObjectURL(await res.blob());
        const img = node('img'); img.src = url; img.alt = `${item.title} 示意图`; img.className = 'feedback-image';
        img.onload = () => URL.revokeObjectURL(url);
        view.replaceWith(img);
      } catch (error) { view.disabled = false; feedbackStatus(`示意图读取失败：${error.message}`, true); }
    };
    box.append(view);
  }
  return box;
}
async function loadFeedback() {
  const target = $('#feedback-list');
  try {
    const data = await api('/api/feedback');
    if (!data.items.length) { target.replaceChildren(node('p', '还没有反馈，欢迎提交第一条。', 'muted')); return; }
    target.replaceChildren(...data.items.map(feedbackItem));
  } catch (error) { target.replaceChildren(node('p', `读取反馈失败：${error.message}`, 'error')); }
}
async function openFeedback() {
  const name = $('#feedback-name');
  if (!name.value && currentUser?.user?.name) name.value = currentUser.user.name;
  await loadFeedback();
}
$('#feedback-form').onsubmit = async (event) => {
  event.preventDefault();
  const button = $('#feedback-submit');
  const title = $('#feedback-title-input').value.trim();
  const content = $('#feedback-content').value.trim();
  const name = $('#feedback-name').value.trim();
  const file = $('#feedback-image').files[0];
  if (!title) { feedbackStatus('请填写标题。', true); return; }
  if (!content) { feedbackStatus('请填写反馈内容。', true); return; }
  if (!name) { feedbackStatus('请填写提交人姓名。', true); return; }
  if (file && file.size > 5 * 1024 * 1024) { feedbackStatus('示意图超过 5MB，请压缩后再提交。', true); return; }
  const body = new FormData();
  body.append('title', title); body.append('content', content); body.append('name', name);
  if (file) body.append('image', file);
  button.disabled = true; feedbackStatus('正在提交…');
  try {
    const token = localStorage.getItem('plus.token');
    const res = await fetch('/api/feedback', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body });
    if (!res.ok) {
      let detail = `提交失败 (${res.status})`;
      try { const data = await res.json(); if (typeof data.detail === 'string') detail = data.detail; } catch { /* not json */ }
      throw new Error(detail);
    }
    $('#feedback-title-input').value = ''; $('#feedback-content').value = ''; $('#feedback-image').value = '';
    feedbackStatus('已提交，感谢反馈。');
    await loadFeedback();
  } catch (error) { feedbackStatus(error.message, true); }
  finally { button.disabled = false; }
};

// —— chat2api 登录设置 ——
let chat2apiExpiryAt = null, chat2apiExpiryKnown = true, chat2apiExpiryTimer = null;
function chat2apiStatus(text, error = false) { const target = $('#chat2api-state'); target.textContent = text; target.className = error ? 'error' : 'muted'; }
function chat2apiMessage(text, error = false) { const target = $('#chat2api-message'); target.textContent = text; target.className = error ? 'error' : 'muted'; }
function formatRemaining(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86400), hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  const parts = [];
  if (days) parts.push(`${days} 天`);
  if (days || hours) parts.push(`${hours} 小时`);
  parts.push(`${minutes} 分`, `${seconds} 秒`);
  return parts.join(' ');
}
function renderChat2apiExpiry() {
  const target = $('#chat2api-expiry');
  if (!target) return;
  if (!chat2apiExpiryKnown) { target.textContent = '无法读取 Token 到期时间（节点数据目录未挂载）。'; target.className = 'muted'; return; }
  if (!chat2apiExpiryAt) { target.textContent = '当前没有带到期时间的 AccessToken（仅有 RefreshToken 或尚未添加）。'; target.className = 'muted'; return; }
  const left = chat2apiExpiryAt - Date.now();
  const when = displayTime(new Date(chat2apiExpiryAt).toISOString());
  if (left <= 0) { target.textContent = `最近一个 AccessToken 已于 ${when} 到期，请重新粘贴整段 JSON 上传。`; target.className = 'error'; return; }
  target.textContent = `最近一个 AccessToken 将于 ${when} 到期，剩余 ${formatRemaining(left)}。`;
  target.className = left < 24 * 3600 * 1000 ? 'warning' : 'muted';
}
async function loadChat2api() {
  chat2apiStatus('正在读取节点状态…');
  try {
    const info = await api('/api/chat2api');
    chat2apiExpiryKnown = info.expiry_available !== false;
    chat2apiExpiryAt = info.earliest_expiry ? Date.parse(info.earliest_expiry) : null;
    renderChat2apiExpiry();
    if (!chat2apiExpiryTimer) chat2apiExpiryTimer = setInterval(renderChat2apiExpiry, 1000);
    if (!info.configured) { chat2apiStatus('尚未配置 chat2api 节点（缺少 PLUS_CHAT2API_PREFIX）。', true); return; }
    chat2apiStatus(info.online ? `节点在线 · 当前可用 Tokens：${info.tokens_count ?? '未知'}` : '节点离线，暂时无法读取 Token 数。', !info.online);
  } catch (error) { chat2apiStatus(`读取节点状态失败：${error.message}`, true); }
}
async function openChat2api() {
  if (!data?.can_admin) return;
  await loadChat2api();
}
$('#chat2api-upload').onclick = async () => {
  const text = $('#chat2api-tokens').value.trim();
  if (!text) { chat2apiMessage('请先粘贴至少一个 Token。', true); return; }
  const button = $('#chat2api-upload'); button.disabled = true; chat2apiMessage('正在上传…');
  try {
    const result = await api('/api/chat2api/tokens', { method: 'POST', json: { text } });
    $('#chat2api-tokens').value = ''; $('#chat2api-error-list').hidden = true;
    chat2apiMessage(`已识别并上传 ${result.extracted ?? '?'} 个 Token，当前可用 Tokens：${result.tokens_count ?? '未知'}。`);
    await loadChat2api();
  } catch (error) { chat2apiMessage(error.message, true); }
  finally { button.disabled = false; }
};
$('#chat2api-errors').onclick = async () => {
  const button = $('#chat2api-errors'); button.disabled = true;
  try {
    const { error_tokens: tokens } = await api('/api/chat2api/errors');
    const box = $('#chat2api-error-list');
    box.textContent = tokens.length ? tokens.join('\n') : '没有错误 Token。';
    box.hidden = false;
  } catch (error) { chat2apiMessage(error.message, true); }
  finally { button.disabled = false; }
};
$('#chat2api-clear').onclick = async () => {
  if (!confirm('清空所有已上传和错误的 Token？此操作不可撤销。')) return;
  const button = $('#chat2api-clear'); button.disabled = true; chat2apiMessage('正在清空…');
  try {
    const result = await api('/api/chat2api/clear', { method: 'POST' });
    $('#chat2api-error-list').hidden = true;
    chat2apiMessage(`已清空，当前可用 Tokens：${result.tokens_count ?? 0}。`);
    await loadChat2api();
  } catch (error) { chat2apiMessage(error.message, true); }
  finally { button.disabled = false; }
};

$('#settings-form').oninput = () => { dirty = true; };
$('#reload-settings').onclick = async () => { if (dirty && !confirm('放弃未保存的设置并刷新？')) return; try { exportData = null; await load(); message('设置已刷新。'); } catch (e) { message(e.message, true); } };
$('#settings-form').onsubmit = async (event) => {
  event.preventDefault(); const button = $('#save-settings'); button.disabled = true;
  try {
    const body = { revision: data.revision, live_url: $('#live-url').value.trim(), notes: Object.fromEntries(data.categories.map((c) => [c.id, $(`#notes-${c.id}`).value])) };
    body.live_urls = Object.fromEntries([...document.querySelectorAll('[data-live-id]')].filter(input => input.value.trim()).map(input => [input.dataset.liveId, input.value.trim()]));
    if (data.can_admin) {
      body.schedule_enabled = $('#schedule-enabled').checked;
      body.ai_provider = $('#ai-provider').value;
      body.ai = Object.fromEntries(Object.keys(providers).map((name) => {
        const value = { enabled: $(`#enabled-${name}`).checked };
        const model = $(`#model-${name}`).value.trim(), key = $(`#key-${name}`).value.trim();
        if (model) value.model = model;
        if (key) value.key = key;
        if ($(`#reset-${name}`)?.checked) value.use_server_key = true;
        return [name, value];
      }));
    }
    await api('/api/settings', { method: 'PUT', json: body });
    for (const name of Object.keys(providers)) if ($(`#key-${name}`)) $(`#key-${name}`).value = '';
    dirty = false; await load(); message('设置已保存。后续任务将使用新配置。');
  } catch (e) { message(e.message, true); } finally { button.disabled = false; }
};
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
try { currentUser = await login(); await load(); } catch (e) { message(e.message, true); }
