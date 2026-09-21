// PLUS 日报选稿页：待选素材按栏目分组、按综合分排序；勾选后生成 PDF。
// 同源调用 /api，登录令牌只保存在本浏览器。

const SECTIONS = [
  { id: 'macro', label: '宏观消息', quota: 3 },
  { id: 'cn_equity', label: '国内股市动向', quota: 2 },
  { id: 'global_equity', label: '海外股市动向', quota: 2 },
  { id: 'ai', label: 'AI热点', quota: 2 },
  { id: 'research', label: '精选研报', quota: 1 },
  { id: 'charts', label: '精选图表', quota: 1 },
];
const UNSORTED = { id: 'unsorted', label: '待分类', quota: 0 };
const TOKEN_KEY = 'plus.token';
const REFRESH_MS = 30_000;

const $ = (sel) => document.querySelector(sel);
const state = { token: null, me: null, materials: [], picked: new Set(), expanded: new Set(), order: {}, dragId: null, editing: null, editingTitle: null };

// ---------- 通用 ----------

function store(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* 隐私模式等场景不可用，忽略 */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

async function api(path, { method = 'GET', json, raw = false } = {}) {
  const headers = { Authorization: `Bearer ${state.token}` };
  if (json !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: json === undefined ? undefined : JSON.stringify(json) });
  if (res.status === 401) {
    logout();
    throw new Error('登录已失效，请重新登录');
  }
  if (!res.ok) {
    let detail = `请求失败（HTTP ${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch { /* 非 JSON */ }
    throw new Error(detail);
  }
  if (raw) return res;
  return res.status === 204 ? null : res.json();
}

function beijingToday() {
  return new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
}
function fmtTime(iso) {
  if (!iso) return '';
  // 不带时区的时间按 UTC 解析（SQLite 开发环境）
  const utc = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utc).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function sectionOf(m) {
  return SECTIONS.some((s) => s.id === m.category) ? m.category : UNSORTED.id;
}
function pickKey() {
  return `plus.picked.${$('#day').value}`;
}
function orderKey() {
  return `plus.order.${$('#day').value}`;
}

/** 栏目内的素材：拖拽排过序的在前（按拖拽顺序），其余按综合分 */
function sectionItems(sectionId) {
  const items = state.materials.filter((m) => sectionOf(m) === sectionId);
  const order = state.order[sectionId] ?? [];
  const rank = (m) => { const i = order.indexOf(m.id); return i === -1 ? Infinity : i; };
  return items.map((m, i) => ({ m, i })).sort((a, b) => rank(a.m) - rank(b.m) || a.i - b.i).map((x) => x.m);
}
function saveOrder() {
  store(orderKey(), JSON.stringify(state.order));
}
function restoreOrder() {
  try { state.order = JSON.parse(load(orderKey()) || '{}'); } catch { state.order = {}; }
}

/** 拖拽放下：移到 targetSection 的 index 位置；跨栏目时同时修改栏目（锁定，AI 不再覆盖） */
async function dropMaterial(id, targetSection, index) {
  const m = state.materials.find((x) => x.id === id);
  if (!m || targetSection === UNSORTED.id) return;
  const ids = sectionItems(targetSection).map((x) => x.id).filter((x) => x !== id);
  ids.splice(Math.min(index, ids.length), 0, id);
  state.order[targetSection] = ids;
  const from = sectionOf(m);
  if (from !== targetSection) {
    state.order[from] = (state.order[from] ?? []).filter((x) => x !== id);
    saveOrder();
    await moveTo(m, targetSection);
  } else {
    saveOrder();
    render();
  }
}

function clearDropMarks() {
  document.querySelectorAll('.drop-before, .drop-after, .drop-target')
    .forEach((n) => n.classList.remove('drop-before', 'drop-after', 'drop-target'));
}

// ---------- 登录 ----------

async function login(email, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '登录失败');
  state.token = body.access_token;
  store(TOKEN_KEY, state.token);
}

function logout() {
  state.token = null;
  store(TOKEN_KEY, null);
  $('#app-view').hidden = true;
  $('#login-view').hidden = false;
}

// ---------- 数据 ----------

async function refresh() {
  const day = $('#day').value;
  const days = $('#days').value;
  state.materials = await api(`/api/pool?day=${day}&days=${days}`);
  const valid = new Set(state.materials.map((m) => m.id));
  for (const id of [...state.picked]) if (!valid.has(id)) state.picked.delete(id);
  render();
}

const HISTORY_PAGE = 5;
let historyPage = 0;

async function loadHistory(page = historyPage) {
  // 多取一条判断是否还有下一页
  const rows = await api(`/api/issues?limit=${HISTORY_PAGE + 1}&offset=${page * HISTORY_PAGE}`);
  if (!rows.length && page > 0) return loadHistory(page - 1);
  historyPage = page;
  const issues = rows.slice(0, HISTORY_PAGE);
  const hasNext = rows.length > HISTORY_PAGE;
  $('#history-pager').replaceChildren(
    ...(page > 0 || hasNext
      ? [
          el('button', { class: 'link', disabled: page === 0, onclick: () => loadHistory(page - 1) }, '‹ 上一页'),
          el('span', { class: 'muted' }, `第 ${page + 1} 页`),
          el('button', { class: 'link', disabled: !hasNext, onclick: () => loadHistory(page + 1) }, '下一页 ›'),
        ]
      : []),
  );
  const list = $('#history');
  list.replaceChildren(
    ...(issues.length
      ? issues.map((i) =>
          el('li', {},
            el('span', {}, `${i.issue_date} · ${i.page_count} 页`, el('br'), el('span', { class: 'muted' }, fmtTime(i.created_at))),
            el('span', {},
              i.has_pdf ? el('button', { class: 'link', onclick: () => download(i, 'pdf') }, 'PDF') : null,
              el('button', { class: 'link', onclick: () => download(i, 'pptx') }, 'PPT'))))
      : [el('li', { class: 'muted' }, '暂无')]),
  );
}

async function download(issue, format) {
  const res = await api(`/api/issues/${issue.id}/file?format=${format}`, { raw: true });
  const url = URL.createObjectURL(await res.blob());
  el('a', { href: url, download: `PLUS日报-${issue.issue_date}.${format}` }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

async function moveTo(material, category) {
  const updated = await api(`/api/materials/${material.id}`, { method: 'PATCH', json: { category } });
  Object.assign(material, updated);
  render();
}

// ---------- 选择 ----------

function togglePick(id) {
  if (state.picked.has(id)) state.picked.delete(id);
  else state.picked.add(id);
  savePicked();
  render();
}

function autoPick() {
  state.picked.clear();
  for (const s of SECTIONS) {
    sectionItems(s.id).slice(0, s.quota).forEach((m) => state.picked.add(m.id));
  }
  savePicked();
  render();
}

function savePicked() {
  store(pickKey(), JSON.stringify([...state.picked]));
}
function restorePicked() {
  try {
    state.picked = new Set(JSON.parse(load(pickKey()) || '[]'));
  } catch {
    state.picked = new Set();
  }
}

function pickedBySection() {
  return SECTIONS.map((s) => ({
    ...s,
    items: sectionItems(s.id).filter((m) => state.picked.has(m.id)),
  })).filter((s) => s.items.length);
}

// ---------- 编辑（不覆盖采集原文，存为编辑版本） ----------

function originalParagraphs(m) {
  return (m.interpretation?.modules ?? []).flatMap((mod) => mod.paragraphs).filter(Boolean);
}
function originalImages(m) {
  const ids = [...m.images, ...(m.interpretation?.modules ?? []).flatMap((mod) => mod.images)].map((i) => i.asset_id).filter(Boolean);
  return [...new Set(ids)];
}
const eff = {
  title: (m) => m.edits?.title || m.title,
  news: (m) => m.edits?.news ?? m.content,
  paragraphs: (m) => m.edits?.interpretation ?? originalParagraphs(m),
  images: (m) => m.edits?.images ?? originalImages(m),
};

async function saveEdits(m, changes) {
  const updated = await api(`/api/materials/${m.id}/edits`, { method: 'PUT', json: { ...(m.edits ?? {}), ...changes } });
  Object.assign(m, updated);
  render();
}

async function clearEdits(m) {
  if (!confirm('恢复采集原文？当前的文字与配图修改会被清除。')) return;
  Object.assign(m, await api(`/api/materials/${m.id}/edits`, { method: 'DELETE' }));
  render();
}

/** 上传图片：ID = sha256(来源地址)[:32]，本地上传用 upload://随机ID 作为来源 */
async function uploadImage(file) {
  if (!file.type.startsWith('image/')) throw new Error('请选择图片文件');
  const source = `upload://${crypto.randomUUID()}/${file.name}`;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source));
  const id = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
  const res = await fetch(`/api/assets/${id}`, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${state.token}`, 'Content-Type': file.type, 'X-Source-Url': encodeURIComponent(source) },
    body: file,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `上传失败（HTTP ${res.status}）`);
  return id;
}

function pickImageFile() {
  return new Promise((resolve) => {
    const input = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', onchange: () => resolve(input.files[0] ?? null) });
    input.click();
  });
}

/** 替换第 index 张图（index = 图片数量时为新增） */
async function replaceImage(m, index) {
  const file = await pickImageFile();
  if (!file) return;
  const id = await uploadImage(file);
  const images = [...eff.images(m)];
  images.splice(index, 1, id);
  await saveEdits(m, { images });
}

async function removeImage(m, index) {
  const images = eff.images(m).filter((_, i) => i !== index);
  await saveEdits(m, { images });
}

const thumbCache = new Map();
function thumb(assetId) {
  const img = el('img', { alt: '配图' });
  if (!thumbCache.has(assetId)) {
    thumbCache.set(assetId, api(`/api/assets/${assetId}`, { raw: true }).then((r) => r.blob()).then((b) => URL.createObjectURL(b)));
  }
  thumbCache.get(assetId).then((url) => { img.src = url; }).catch(() => { img.alt = '图片加载失败'; });
  return img;
}

function renderImages(m) {
  const ids = eff.images(m);
  return el('div', { class: 'images' },
    ids.map((id, i) => el('figure', {},
      thumb(id),
      el('figcaption', {},
        el('button', { class: 'link', onclick: () => replaceImage(m, i).catch(alert) }, '替换'),
        el('button', { class: 'link danger', onclick: () => removeImage(m, i).catch(alert) }, '移除')))),
    el('button', { class: 'add-image', onclick: () => replaceImage(m, ids.length).catch(alert) }, '+ 上传图片'));
}

function renderEditor(m) {
  const title = el('input', { value: eff.title(m) });
  const news = el('textarea', { rows: 5 }, eff.news(m));
  const interp = el('textarea', { rows: 6, placeholder: '每段之间空一行' }, eff.paragraphs(m).join('\n\n'));
  const save = async () => {
    await saveEdits(m, {
      title: title.value.trim() || m.title,
      news: news.value.trim(),
      interpretation: interp.value.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean),
    });
    state.editing = null;
    render();
  };
  return el('div', { class: 'editor' },
    el('label', {}, '标题', title),
    el('label', {}, '【新闻】', news),
    el('label', {}, '【解读】（每段之间空一行）', interp),
    el('div', { class: 'editor-actions' },
      el('button', { class: 'primary', onclick: () => save().catch(alert) }, '保存'),
      el('button', { onclick: () => { state.editing = null; render(); } }, '取消')));
}

/** 标题原地编辑：回车 / 失焦保存，Esc 取消 */
const canEdit = () => state.me?.user.role !== 'viewer';

function renderTitleEditor(m) {
  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    state.editingTitle = null;
    const value = input.value.trim();
    if (commit && value && value !== eff.title(m)) await saveEdits(m, { title: value === m.title ? null : value });
    else render();
  };
  const input = el('input', {
    class: 'title-input',
    value: eff.title(m),
    'aria-label': '标题',
    onkeydown: (e) => {
      if (e.key === 'Enter') finish(true).catch(alert);
      else if (e.key === 'Escape') finish(false).catch(alert);
    },
    onblur: () => finish(true).catch(alert),
  });
  setTimeout(() => { input.focus(); input.select(); });
  return input;
}

function renderDetail(m) {
  if (state.editing === m.id) return el('div', { class: 'item-detail' }, renderEditor(m), renderImages(m));
  const paragraphs = eff.paragraphs(m);
  return el('div', { class: 'item-detail' },
    el('p', {}, el('strong', {}, '【新闻】'), eff.news(m)),
    paragraphs.map((p, i) => el('p', {}, i === 0 ? el('strong', {}, '【解读】') : null, p)),
    renderImages(m),
    el('div', { class: 'editor-actions' },
      el('button', { onclick: () => { state.editing = m.id; render(); } }, '编辑文字'),
      m.edits ? el('button', { class: 'link', onclick: () => clearEdits(m).catch(alert) }, '恢复原文') : null),
    m.ai_reason ? el('p', { class: 'reason' }, m.ai_reason) : null);
}

// ---------- 渲染 ----------

function renderItem(m) {
  const picked = state.picked.has(m.id);
  const expanded = state.expanded.has(m.id);
  const images = eff.images(m).length;

  const select = el('select', { title: '调整栏目（编辑指定后 AI 不再覆盖）', onchange: (e) => moveTo(m, e.target.value).catch(alert) },
    SECTIONS.map((s) => el('option', { value: s.id, selected: s.id === m.category }, s.label)));
  if (!SECTIONS.some((s) => s.id === m.category)) select.prepend(el('option', { value: '', selected: true, disabled: true }, '选择栏目'));

  const li = el('li', {
    class: `item${picked ? ' picked' : ''}`,
    // 编辑文字时不可拖动，避免选中文字时误触发拖拽
    draggable: state.editing === m.id || state.editingTitle === m.id ? 'false' : 'true',
    title: '拖动可调整顺序，或拖到其他栏目',
    ondragstart: (e) => {
      state.dragId = m.id;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', m.id);
      li.classList.add('dragging');
      document.body.classList.add('is-dragging');
    },
    ondragend: () => {
      state.dragId = null;
      li.classList.remove('dragging');
      document.body.classList.remove('is-dragging');
      clearDropMarks();
    },
    ondragover: (e) => {
      if (!state.dragId || state.dragId === m.id) return;
      e.preventDefault();
      e.stopPropagation();
      clearDropMarks();
      const r = li.getBoundingClientRect();
      li.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
      li.closest('section')?.classList.add('drop-target');
    },
    ondrop: (e) => {
      e.preventDefault();
      e.stopPropagation();
      const after = li.classList.contains('drop-after');
      const id = state.dragId;
      clearDropMarks();
      if (!id || id === m.id) return;
      const section = sectionOf(m);
      const ids = sectionItems(section).map((x) => x.id).filter((x) => x !== id);
      dropMaterial(id, section, ids.indexOf(m.id) + (after ? 1 : 0)).catch(alert);
    },
  },
    el('input', { type: 'checkbox', checked: picked, onchange: () => togglePick(m.id), 'aria-label': '选入日报' }),
    state.editingTitle === m.id
      ? renderTitleEditor(m)
      : el('div', { class: 'item-title' },
          el('span', { onclick: () => { expanded ? state.expanded.delete(m.id) : state.expanded.add(m.id); render(); } }, eff.title(m)),
          canEdit() ? el('button', { class: 'title-edit', title: '修改标题', 'aria-label': '修改标题', onclick: () => { state.editingTitle = m.id; render(); } }, '✎') : null),
    el('div', { class: 'item-meta' },
      m.level ? el('span', { class: `level ${m.level}` }, m.level) : null,
      m.ai_status === 'done' ? el('span', {}, `${m.final_score} 分`) : el('span', { class: 'chip warn' }, { pending: 'AI 分析中', analyzing: 'AI 分析中', failed: 'AI 分析失败' }[m.ai_status] ?? m.ai_status),
      el('span', {}, fmtTime(m.publish_time ?? m.collect_time)),
      m.interpretation_status === 'fetched' ? el('span', { class: 'chip plus' }, 'PLUS 解读') : null,
      images ? el('span', { class: 'chip' }, `配图 ${images}`) : null,
      m.edits ? el('span', { class: 'chip edited' }, '已编辑') : null,
      m.collected_by.length > 1 ? el('span', { class: 'chip' }, `${m.collected_by.length} 人收藏`) : null,
      m.ai_confidence !== null && m.ai_confidence < 0.5 && !m.category_locked ? el('span', { class: 'chip warn' }, '栏目待确认') : null,
      select),
    expanded ? renderDetail(m) : null,
  );
  return li;
}

function render() {
  const groups = [...SECTIONS, UNSORTED].map((s, idx) => {
    const items = sectionItems(s.id);
    if (s === UNSORTED && !items.length) return null;
    const count = items.filter((m) => state.picked.has(m.id)).length;
    return el('section', {
      class: 'card',
      // 拖到栏目空白处：追加到该栏目末尾
      ondragover: (e) => {
        if (!state.dragId || s === UNSORTED) return;
        e.preventDefault();
        clearDropMarks();
        e.currentTarget.classList.add('drop-target');
      },
      ondragleave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) e.currentTarget.classList.remove('drop-target'); },
      ondrop: (e) => {
        e.preventDefault();
        const id = state.dragId;
        clearDropMarks();
        if (id && s !== UNSORTED) dropMaterial(id, s.id, Infinity).catch(alert);
      },
    },
      el('div', { class: 'section-head' },
        el('h2', {}, s === UNSORTED ? null : el('span', { class: 'no' }, String(idx + 1).padStart(2, '0')), s.label),
        s.quota ? el('span', { class: `quota${count >= s.quota ? ' full' : ''}` }, `已选 ${count} / 建议 ${s.quota}`) : null),
      items.length ? el('ul', { class: 'items' }, items.map(renderItem)) : el('p', { class: 'empty' }, '暂无素材'));
  });
  $('#sections').replaceChildren(...groups.filter(Boolean));

  const picked = pickedBySection();
  const total = picked.reduce((n, s) => n + s.items.length, 0);
  $('#pick-summary').textContent = total ? `共 ${total} 条；目录固定 6 个栏目，未选素材的栏目生成空白页供补充` : '尚未选择素材';
  $('#pick-list').replaceChildren(...picked.flatMap((s) => [el('li', { class: 'group' }, s.label), ...s.items.map((m) => el('li', {}, eff.title(m)))]));
  $('#generate').disabled = total === 0;
  $('#preview').disabled = total === 0;
}

// ---------- 生成 ----------

function issueRequest() {
  return {
    issue_date: $('#day').value,
    sections: pickedBySection().map((s) => ({ id: s.id, material_ids: s.items.map((m) => m.id) })),
  };
}

// ---------- 预览（与生成同一流程：服务端生成 PDF 后逐页转图） ----------

async function openPreview() {
  const view = $('#preview-view');
  view.hidden = false;
  $('#preview-info').textContent = '正在渲染…';
  $('#preview-pages').replaceChildren();
  try {
    const res = await api('/api/issues/preview', { method: 'POST', json: issueRequest() });
    $('#preview-info').textContent = `共 ${res.page_count} 页${res.warnings.length ? ' · ' + res.warnings.join('；') : ''}　所见即所得：点「生成 PDF」得到的文件与此完全一致`;
    $('#preview-pages').replaceChildren(...res.pages.map((src, i) => el('img', { src, alt: `第 ${i + 1} 页` })));
  } catch (err) {
    $('#preview-info').textContent = `预览失败：${err.message}`;
  }
}

// ---------- 垃圾桶 / 恢复 AI 排版 ----------

async function deleteMaterial(id) {
  const m = state.materials.find((x) => x.id === id);
  if (!m || !confirm(`删除「${eff.title(m)}」？删除后团队成员都看不到这条素材。`)) return;
  await api(`/api/materials/${id}`, { method: 'DELETE' });
  state.materials = state.materials.filter((x) => x.id !== id);
  state.picked.delete(id);
  for (const k of Object.keys(state.order)) state.order[k] = state.order[k].filter((x) => x !== id);
  savePicked();
  saveOrder();
  render();
}

async function clearPool() {
  const ids = state.materials.map((m) => m.id);
  if (!ids.length) return alert('当前没有素材');
  const range = $('#days').value === '1' ? $('#day').value : `${$('#day').value} 起近 ${$('#days').value} 天`;
  if (!confirm(`删除当前显示的全部 ${ids.length} 条素材（${range}）？
团队成员都将看不到这些素材；重新采集同一条快讯可恢复。`)) return;
  await api('/api/materials/bulk-delete', { method: 'POST', json: { ids } });
  state.materials = [];
  state.picked.clear();
  state.order = {};
  savePicked();
  saveOrder();
  render();
}

async function resetAiLayout() {
  if (!confirm('恢复 AI 排版？手动调整的排序与栏目会被撤销（文字与配图的编辑保留）。')) return;
  state.order = {};
  saveOrder();
  state.materials = await api(`/api/pool/reset-ai?day=${$('#day').value}&days=${$('#days').value}`, { method: 'POST' });
  render();
}

async function generate() {
  const { sections } = issueRequest();
  const status = $('#gen-status');
  const button = $('#generate');
  button.disabled = true;
  status.hidden = false;
  status.replaceChildren('正在生成 PPT 并转换 PDF，通常需要十几秒…');
  try {
    const issue = await api('/api/issues', { method: 'POST', json: { issue_date: $('#day').value, sections } });
    status.replaceChildren(
      el('strong', {}, `已生成 ${issue.page_count} 页`),
      ...issue.warnings.map((w) => el('div', { class: 'muted' }, `· ${w}`)),
      el('div', { class: 'downloads' },
        issue.has_pdf ? el('button', { class: 'primary', onclick: () => download(issue, 'pdf') }, '下载 PDF') : null,
        el('button', { onclick: () => download(issue, 'pptx') }, '下载 PPT')));
    if (issue.has_pdf) await download(issue, 'pdf');
    await loadHistory(0);
  } catch (err) {
    status.replaceChildren(el('span', { class: 'error' }, `生成失败：${err.message}`));
  } finally {
    button.disabled = false;
  }
}

// ---------- 启动 ----------

async function enterApp() {
  state.me = await api('/api/auth/me');
  $('#whoami').textContent = `${state.me.team.name} · ${state.me.user.name}`;
  $('#login-view').hidden = true;
  $('#app-view').hidden = false;
  const editable = canEdit();
  $('#generate').title = editable ? '' : '只读账号不能生成日报';
  restorePicked();
  restoreOrder();
  await Promise.all([refresh(), loadHistory()]);
  if (!editable) { $('#generate').disabled = true; $('#clear-pool').disabled = true; }
}

function wire() {
  $('#day').value = beijingToday();
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const error = $('#login-error');
    error.hidden = true;
    try {
      await login(form.get('email'), form.get('password'));
      await enterApp();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
  $('#logout').addEventListener('click', logout);
  $('#refresh').addEventListener('click', () => refresh().catch(alert));
  $('#day').addEventListener('change', () => { restorePicked(); restoreOrder(); refresh().catch(alert); });
  $('#days').addEventListener('change', () => refresh().catch(alert));
  $('#auto-pick').addEventListener('click', autoPick);
  $('#clear-pick').addEventListener('click', () => { state.picked.clear(); savePicked(); render(); });
  $('#generate').addEventListener('click', generate);
  $('#preview').addEventListener('click', openPreview);
  $('#preview-refresh').addEventListener('click', openPreview);
  $('#preview-close').addEventListener('click', () => { $('#preview-view').hidden = true; });
  $('#preview-generate').addEventListener('click', () => { $('#preview-view').hidden = true; generate(); });
  $('#reset-ai').addEventListener('click', () => resetAiLayout().catch(alert));
  $('#clear-pool').addEventListener('click', () => clearPool().catch(alert));
  const trash = $('#trash');
  trash.addEventListener('dragover', (e) => { if (!state.dragId) return; e.preventDefault(); clearDropMarks(); trash.classList.add('drop-target'); });
  trash.addEventListener('dragleave', () => trash.classList.remove('drop-target'));
  trash.addEventListener('drop', (e) => {
    e.preventDefault();
    trash.classList.remove('drop-target');
    const id = state.dragId;
    if (id) deleteMaterial(id).catch(alert);
  });
  // 定时刷新，看到同事新采集的素材与 AI 分析结果
  setInterval(() => { if (state.token && !document.hidden && !state.dragId && !state.editing && !state.editingTitle) refresh().catch(() => {}); }, REFRESH_MS);
}

wire();
state.token = load(TOKEN_KEY);
if (state.token) enterApp().catch(logout);
else logout();
