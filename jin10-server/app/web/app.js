// PLUS 日报选稿页：待选素材按栏目分组、按综合分排序；勾选后生成 PDF。
// 同源调用 /api，登录令牌只保存在本浏览器。
import { account, copyDate, displayTime } from '/workspace.js';
import '/pull-cord.js';

const SECTIONS = [
  { id: 'macro', label: '宏观消息', quota: 3 },
  { id: 'cn_equity', label: '国内股市动向', quota: 2 },
  { id: 'global_equity', label: '海外股市动向', quota: 2 },
  { id: 'ai', label: 'AI热点', quota: 2 },
  { id: 'research', label: '精选观点', quota: 1 },
  { id: 'charts', label: '精选图表', quota: 1 },
];
const UNSORTED = { id: 'unsorted', label: '待分类', quota: 0 };
const TOKEN_KEY = 'plus.token';
const REFRESH_MS = 30_000;
const BOARD_REFRESH_MS = 4_000;

const $ = (sel) => document.querySelector(sel);
const state = { token: null, me: null, materials: [], picked: new Set(), coverIds: null, coverSaving: false, expanded: new Set(), order: {}, dragId: null, editing: null, editingTitle: null, bulk: null, presence: new Map() };
const COVER_LIMIT = 12;

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

async function api(path, { method = 'GET', json, raw = false, headers: extraHeaders = {} } = {}) {
  const headers = { ...extraHeaders, Authorization: `Bearer ${state.token}` };
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
  return displayTime(utc);
}
/** 同事正在编辑这条时先提醒，避免后保存的覆盖先保存的 */
function confirmEditing(m) {
  const who = state.presence.get(m.id);
  return !who || confirm(`${who} 正在编辑这条素材，同时编辑时后保存的会覆盖先保存的。仍要编辑？`);
}

// ---------- 协作：正在编辑心跳 ----------

async function heartbeat() {
  const mine = state.editing || state.editingTitle || null;
  const others = await api('/api/presence', { method: 'POST', json: { material_id: mine } });
  const next = new Map(others.map((p) => [p.material_id, p.user_name]));
  const changed = JSON.stringify([...next]) !== JSON.stringify([...state.presence]);
  state.presence = next;
  // 自己在编辑时不重绘（会打断输入），标记在编辑结束后显示
  if (changed && !state.editing && !state.editingTitle && !state.dragId) render();
}

// ---------- 解析异常提醒（金十可能改版） ----------

const PARSE_ALERT_MIN = 3;
const KIND_TEXT = { parse_failed: '解析失败', unrecognized_title: '标题未识别', image_failed: '配图下载失败', plus_failed: 'PLUS 解读读取失败' };

async function loadParseAlert() {
  const s = await api('/api/parse-reports/summary');
  const btn = $('#parse-alert');
  btn.hidden = s.total < PARSE_ALERT_MIN;
  btn.textContent = `⚠ 采集异常 ${s.total} 次`;
  btn.onclick = () => alert(
    `最近 24 小时插件上报的采集异常：${Object.entries(s.by_kind).map(([k, n]) => `${KIND_TEXT[k] ?? k} ${n} 次`).join('，')}\n` +
    '集中出现时通常是金十页面改版，请把这个提示转给维护人员。\n\n最近记录：\n' +
    s.latest.slice(0, 8).map((r) => `${fmtTime(r.created_at)} ${KIND_TEXT[r.kind] ?? r.kind} · ${r.user_name ?? ''} · v${r.extension_version} · ${r.detail}`).join('\n'),
  );
}

// ---------- 回收站 ----------

async function openRecycle() {
  const dlg = $('#recycle');
  const list = $('#recycle-list');
  list.replaceChildren(el('li', { class: 'muted' }, '加载中…'));
  dlg.showModal();
  const rows = await api('/api/materials/deleted');
  const chosen = new Set();
  const sync = () => { $('#recycle-restore').textContent = `恢复所选（${chosen.size}）`; $('#recycle-restore').disabled = !chosen.size; };
  list.replaceChildren(...(rows.length ? rows.map((m) => el('li', {},
    el('label', {},
      el('input', { type: 'checkbox', onchange: (e) => { e.target.checked ? chosen.add(m.id) : chosen.delete(m.id); sync(); } }),
      el('span', {}, eff.title(m)),
      el('span', { class: 'muted' }, ` · 删除于 ${fmtTime(m.deleted_at)}`)))) : [el('li', { class: 'muted' }, '回收站是空的')]));
  $('#recycle-all').onclick = () => { list.querySelectorAll('input').forEach((i) => { i.checked = true; }); rows.forEach((m) => chosen.add(m.id)); sync(); };
  $('#recycle-restore').onclick = async () => {
    await api('/api/materials/restore', { method: 'POST', json: { ids: [...chosen] } });
    dlg.close();
    await refresh();
  };
  sync();
}

function collectorNames(ids) {
  const names = new Map((state.me?.members ?? []).map((u) => [u.id, u.name]));
  return ids.map((id) => names.get(id) ?? '已停用账号').join('、');
}
function sectionOf(m) {
  return SECTIONS.some((s) => s.id === m.category) ? m.category : UNSORTED.id;
}

/** 栏目内的素材：拖拽排过序的在前（按拖拽顺序），其余按综合分 */
function sectionItems(sectionId) {
  const items = state.materials.filter((m) => sectionOf(m) === sectionId);
  const order = state.order[sectionId] ?? [];
  const rank = (m) => { const i = order.indexOf(m.id); return i === -1 ? Infinity : i; };
  return items.map((m, i) => ({ m, i })).sort((a, b) => rank(a.m) - rank(b.m) || a.i - b.i).map((x) => x.m);
}
// ---------- 团队共享选稿板（已选 + 手动排序，多账号同步） ----------

let boardVersion = -1;
let boardQueue = Promise.resolve();
let boardPending = 0;

function adoptBoard(board) {
  boardVersion = board.version;
  state.picked = new Set(board.picked);
  state.order = board.order;
  state.coverIds = board.cover_ids ?? null;
}

/** 拉取选稿板；有本地操作未提交完时跳过，避免把刚做的改动闪回去 */
async function loadBoard() {
  const day = $('#day').value;
  const board = await api(`/api/board?day=${day}`);
  if (boardPending || day !== $('#day').value) return false;
  const changed = board.version !== boardVersion;
  adoptBoard(board);
  return changed;
}

/** 旧版把已选 / 排序存在本机：服务端当天还是空板时上传一次，之后以服务端为准 */
function migrateLocalBoard() {
  const day = $('#day').value;
  const keys = [`plus.picked.${day}`, `plus.order.${day}`];
  let picked = [];
  let order = {};
  try {
    picked = JSON.parse(load(keys[0]) || '[]');
    order = JSON.parse(load(keys[1]) || '{}');
  } catch { /* 损坏的旧数据直接丢弃 */ }
  keys.forEach((k) => store(k, null));
  if (boardVersion !== 0 || !canEdit() || (!picked.length && !Object.keys(order).length)) return;
  state.picked = new Set(picked);
  state.order = order;
  boardOps({ pick_set: picked, order });
  render();
}

/** 本地已先行更新（界面即时响应），再把增量操作按顺序提交给服务端合并 */
function boardOps(ops) {
  const day = $('#day').value;
  boardPending++;
  boardQueue = boardQueue
    .then(() => api(`/api/board/ops?day=${day}`, { method: 'POST', json: ops }))
    .then((board) => { if (boardPending === 1 && day === $('#day').value) { adoptBoard(board); render(); } })
    .catch(async (err) => {
      alert(`选稿同步失败：${err.message ?? err}`);
      if (ops.cover_add || ops.cover_remove || ops.cover_reset) {
        const board = await api(`/api/board?day=${day}`).catch(() => null);
        if (board && boardPending === 1 && day === $('#day').value) adoptBoard(board);
      }
    })
    .finally(() => { boardPending--; });
  return boardQueue;
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
    boardOps({ order: { [targetSection]: ids, [from]: state.order[from] } });
    await moveTo(m, targetSection);
  } else {
    boardOps({ order: { [targetSection]: ids } });
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
  $('#account').replaceChildren();
}

// ---------- 数据 ----------

async function refresh() {
  const day = $('#day').value;
  const days = $('#days').value;
  const [materials] = await Promise.all([api(`/api/pool?day=${day}&days=${days}`), loadBoard()]);
  state.materials = materials;
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
            el('span', {}, `${copyDate(i.issue_date)} · ${i.page_count} 页`, el('br'), el('span', { class: 'muted' }, fmtTime(i.created_at))),
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
  if (state.picked.has(id)) {
    state.picked.delete(id);
    boardOps({ pick_remove: [id] });
  } else {
    state.picked.add(id);
    boardOps({ pick_add: [id] });
  }
  render();
}

function autoPick() {
  state.picked.clear();
  for (const s of SECTIONS) {
    sectionItems(s.id).slice(0, s.quota).forEach((m) => state.picked.add(m.id));
  }
  boardOps({ pick_set: [...state.picked] });
  render();
}


function pickedBySection() {
  return SECTIONS.map((s) => ({
    ...s,
    items: sectionItems(s.id).filter((m) => state.picked.has(m.id)),
  })).filter((s) => s.items.length);
}

function coverSelection() {
  const items = pickedBySection().flatMap((s) => s.items);
  const chosen = new Set(state.coverIds ?? [...items].sort((a, b) =>
    b.importance_score - a.importance_score || b.final_score - a.final_score || a.id.localeCompare(b.id)
  ).slice(0, COVER_LIMIT).map((m) => m.id));
  return items.filter((m) => chosen.has(m.id)).map((m) => m.id);
}

function toggleCover(id) {
  if (!canEdit() || state.coverSaving) return;
  const current = coverSelection();
  const included = current.includes(id);
  if (!included && current.length >= COVER_LIMIT) {
    alert('首页已选满 12 条，请先取消一条再换入');
    render();
    return;
  }
  state.coverIds = included ? current.filter((mid) => mid !== id) : [...current, id];
  state.coverSaving = true;
  boardOps({ cover_base_ids: current, [included ? 'cover_remove' : 'cover_add']: [id] })
    .finally(() => { state.coverSaving = false; render(); });
  render();
}

function resetCover() {
  if (!canEdit() || state.coverSaving) return;
  state.coverIds = null;
  state.coverSaving = true;
  boardOps({ cover_reset: true }).finally(() => { state.coverSaving = false; render(); });
  render();
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
  const updated = await api(`/api/materials/${m.id}/edits`, { method: 'PUT', headers: {'If-Match':m.updated_at}, json: { ...(m.edits ?? {}), ...changes } });
  Object.assign(m, updated);
  render();
}

async function clearEdits(m) {
  if (!confirm('恢复采集原文？当前的文字与配图修改会被清除。')) return;
  Object.assign(m, await api(`/api/materials/${m.id}/edits`, { method: 'DELETE', headers: {'If-Match':m.updated_at} }));
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
      el('button', { onclick: () => { if (confirmEditing(m)) { state.editing = m.id; render(); } } }, '编辑文字'),
      m.edits ? el('button', { class: 'link', onclick: () => clearEdits(m).catch(alert) }, '恢复原文') : null),
    m.ai_reason ? el('p', { class: 'reason' }, m.ai_reason) : null);
}

// ---------- 渲染 ----------

function renderItem(m, cover) {
  const picked = state.picked.has(m.id);
  const expanded = state.expanded.has(m.id);
  const images = eff.images(m).length;

  const select = el('select', { title: '调整栏目（编辑指定后 AI 不再覆盖）', onchange: (e) => moveTo(m, e.target.value).catch(alert) },
    SECTIONS.map((s) => el('option', { value: s.id, selected: s.id === m.category }, s.label)));
  if (!SECTIONS.some((s) => s.id === m.category)) select.prepend(el('option', { value: '', selected: true, disabled: true }, '选择栏目'));

  const marked = state.bulk?.has(m.id) ?? false;
  // 多人采集的同一条快讯：团队共识度高，醒目标出
  const collectors = m.collected_by ?? [];
  const multi = collectors.length >= 2;
  const li = el('li', {
    class: `item${picked ? ' picked' : ''}${state.bulk ? ' bulk' : ''}${marked ? ' marked' : ''}${multi ? ' multi' : ''}`,
    // 批量删除模式：点卡片任意位置勾选 / 取消
    onclick: (e) => {
      if (!state.bulk || e.target.closest('input, select, button, a')) return;
      toggleBulk(m.id);
    },
    // 编辑文字时不可拖动，避免选中文字时误触发拖拽
    draggable: state.bulk || state.editing === m.id || state.editingTitle === m.id ? 'false' : 'true',
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
    state.bulk
      ? el('input', { type: 'checkbox', class: 'bulk-check', checked: marked, onchange: () => toggleBulk(m.id), 'aria-label': '勾选删除' })
      : el('input', { type: 'checkbox', checked: picked, onchange: () => togglePick(m.id), 'aria-label': '选入日报' }),
    state.editingTitle === m.id
      ? renderTitleEditor(m)
      : el('div', { class: 'item-title' },
          el('span', { onclick: () => { if (state.bulk) return; expanded ? state.expanded.delete(m.id) : state.expanded.add(m.id); render(); } }, eff.title(m)),
          canEdit() && !state.bulk ? el('button', { class: 'title-edit', title: '修改标题', 'aria-label': '修改标题', onclick: () => { if (confirmEditing(m)) { state.editingTitle = m.id; render(); } } }, '✎') : null),
    el('div', { class: 'item-meta' },
      m.level ? el('span', { class: `level ${m.level}` }, m.level) : null,
      m.ai_status === 'done' ? el('span', {}, `${m.final_score} 分`) : el('span', { class: 'chip warn' }, { pending: 'AI 分析中', analyzing: 'AI 分析中', failed: 'AI 分析失败' }[m.ai_status] ?? m.ai_status),
      el('span', {}, fmtTime(m.publish_time ?? m.collect_time)),
      state.presence.has(m.id) ? el('span', { class: 'chip editing' }, `${state.presence.get(m.id)} 正在编辑`) : null,
      multi ? el('span', { class: 'chip multi', title: `采集人：${collectorNames(collectors)}` }, `${collectors.length} 人采集`) : null,
      m.interpretation_status === 'fetched' ? el('span', { class: 'chip plus' }, 'PLUS 解读') : null,
      images ? el('span', { class: 'chip' }, `配图 ${images}`) : null,
      m.edits ? el('span', { class: 'chip edited' }, '已编辑') : null,
      m.collected_by.length > 1 ? el('span', { class: 'chip' }, `${m.collected_by.length} 人收藏`) : null,
      m.ai_confidence !== null && m.ai_confidence < 0.5 && !m.category_locked ? el('span', { class: 'chip warn' }, '栏目待确认') : null,
      select,
      picked && sectionOf(m) !== UNSORTED.id && !state.bulk
        ? el('button', { class: `cover-toggle${cover.has(m.id) ? ' selected' : ''}`,
            disabled: !canEdit() || state.coverSaving, 'aria-pressed': String(cover.has(m.id)),
            title: '仅决定是否出现在首页目录，正文内容仍保留', onclick: () => toggleCover(m.id) },
          cover.has(m.id) ? '★ 首页精选' : '☆ 设为首页') : null),
    expanded ? renderDetail(m) : null,
  );
  return li;
}

function render() {
  const coverIds = coverSelection();
  const cover = new Set(coverIds);
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
        state.bulk && items.length
          ? el('button', { class: 'link', onclick: () => markAll(items.map((x) => x.id)) }, items.every((x) => state.bulk.has(x.id)) ? '取消本栏' : '全选本栏')
          : s.quota ? el('span', { class: `quota${count >= s.quota ? ' full' : ''}` }, `已选 ${count} / 建议 ${s.quota}`) : null),
      items.length ? el('ul', { class: 'items' }, items.map((m) => renderItem(m, cover))) : el('p', { class: 'empty' }, '暂无素材'));
  });
  $('#sections').replaceChildren(...groups.filter(Boolean));
  renderBulkBar();

  const picked = pickedBySection();
  const total = picked.reduce((n, s) => n + s.items.length, 0);
  $('#pick-summary').textContent = total ? `正文共 ${total} 条，未选素材的栏目仍保留空白页` : '尚未选择素材';
  $('#cover-summary').textContent = `首页精选 ${coverIds.length}/${COVER_LIMIT}`;
  $('#cover-mode').textContent = state.coverIds === null ? '系统推荐。勾选下方标题可改为人工精选。' : '人工精选已保存。未勾选的稿件仍进入正文。';
  $('#cover-reset').disabled = !canEdit() || state.coverSaving || state.coverIds === null;
  $('#pick-list').replaceChildren(...picked.flatMap((s) => [el('li', { class: 'group' }, s.label), ...s.items.map((m) =>
    el('li', {}, el('label', { class: 'cover-choice' },
      el('input', { type: 'checkbox', checked: cover.has(m.id), disabled: !canEdit() || state.coverSaving,
        'aria-label': `首页精选：${eff.title(m)}`, onchange: () => toggleCover(m.id) }), eff.title(m))))]));
  $('#generate').disabled = total === 0;
  $('#preview').disabled = total === 0;
}

// ---------- 生成 ----------

function issueRequest() {
  return {
    issue_date: $('#day').value,
    sections: pickedBySection().map((s) => ({ id: s.id, material_ids: s.items.map((m) => m.id) })),
    cover_material_ids: coverSelection(),
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
  removeLocal([id]);
}

/** 从页面状态中移除已删除的素材 */
function removeLocal(ids) {
  const gone = new Set(ids);
  state.materials = state.materials.filter((x) => !gone.has(x.id));
  const wasPicked = ids.filter((id) => state.picked.has(id));
  for (const id of gone) { state.picked.delete(id); state.bulk?.delete(id); }
  for (const k of Object.keys(state.order)) state.order[k] = state.order[k].filter((x) => !gone.has(x));
  if (wasPicked.length) boardOps({ pick_remove: wasPicked });
  render();
}

// ---------- 批量删除 ----------

function setBulk(on) {
  state.bulk = on ? new Set() : null;
  document.body.classList.toggle('bulk-mode', on);
  $('#bulk-delete').textContent = on ? '退出批量删除' : '批量删除';
  render();
}

function toggleBulk(id) {
  state.bulk.has(id) ? state.bulk.delete(id) : state.bulk.add(id);
  render();
}

/** 全部已勾选则全部取消，否则全部勾选 */
function markAll(ids) {
  const all = ids.every((id) => state.bulk.has(id));
  for (const id of ids) all ? state.bulk.delete(id) : state.bulk.add(id);
  render();
}

function renderBulkBar() {
  const bar = $('#bulk-bar');
  if (!state.bulk) return bar.replaceChildren();
  const n = state.bulk.size;
  bar.replaceChildren(
    el('span', {}, '批量删除：已选 ', el('b', {}, String(n)), ' 条'),
    el('button', { onclick: () => markAll(state.materials.map((m) => m.id)) }, '全选'),
    el('button', { class: 'danger-solid', disabled: n === 0, onclick: () => deleteMarked().catch(alert) }, `删除所选（${n}）`),
    el('button', { onclick: () => setBulk(false) }, '取消'),
  );
}

async function deleteMarked() {
  const ids = [...state.bulk];
  if (!ids.length || !confirm(`删除所选的 ${ids.length} 条素材？删除后团队成员都看不到；重新采集同一条快讯可恢复。`)) return;
  await api('/api/materials/bulk-delete', { method: 'POST', json: { ids } });
  removeLocal(ids);
  setBulk(false);
}

const CLEAR_PHRASE = '我要清除所有素材';

async function clearPool() {
  const ids = state.materials.map((m) => m.id);
  if (!ids.length) return alert('当前没有素材');
  const range = $('#days').value === '1' ? $('#day').value : `${$('#day').value} 起近 ${$('#days').value} 天`;
  // 三道确认：确认范围 → 手动输入口令 → 最终确认
  if (!confirm(`【第 1 步，共 3 步】即将清空当前显示的全部 ${ids.length} 条素材（${range}）。
团队成员都将看不到这些素材。继续？`)) return;
  const typed = prompt(`【第 2 步，共 3 步】请输入「${CLEAR_PHRASE}」以继续：`);
  if (typed === null) return;
  if (typed.trim() !== CLEAR_PHRASE) return alert('输入不一致，已取消清空。');
  if (!confirm(`【第 3 步，共 3 步】最后确认：立即删除这 ${ids.length} 条素材？`)) return;
  await api('/api/materials/bulk-delete', { method: 'POST', json: { ids } });
  state.order = {};
  state.picked.clear();
  boardOps({ pick_set: [], order_reset: true });
  removeLocal(ids);
}

async function resetAiLayout() {
  if (!confirm('恢复 AI 排版？手动调整的排序与栏目会被撤销（文字与配图的编辑保留）。')) return;
  state.order = {};
  boardOps({ order_reset: true });
  state.materials = await api(`/api/pool/reset-ai?day=${$('#day').value}&days=${$('#days').value}`, { method: 'POST' });
  render();
}

async function generate() {
  const request = issueRequest();
  const status = $('#gen-status');
  const button = $('#generate');
  button.disabled = true;
  status.hidden = false;
  status.replaceChildren('正在生成 PPT 并转换 PDF，通常需要十几秒…');
  try {
    const issue = await api('/api/issues', { method: 'POST', json: request });
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
  account(state.me);
  $('#login-view').hidden = true;
  $('#app-view').hidden = false;
  const editable = canEdit();
  $('#generate').title = editable ? '' : '只读账号不能生成日报';
  await Promise.all([refresh(), loadHistory()]);
  loadParseAlert().catch(() => {});
  migrateLocalBoard();
  if (!editable) { $('#generate').disabled = true; $('#clear-pool').disabled = true; $('#bulk-delete').disabled = true; $('#recycle-restore').hidden = true; }
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
  $('#refresh').addEventListener('click', () => refresh().catch(alert));
  $('#day').addEventListener('change', () => { boardVersion = -1; refresh().catch(alert); });
  $('#days').addEventListener('change', () => refresh().catch(alert));
  $('#auto-pick').addEventListener('click', autoPick);
  $('#clear-pick').addEventListener('click', () => { state.picked.clear(); boardOps({ pick_set: [] }); render(); });
  $('#cover-reset').addEventListener('click', resetCover);
  $('#generate').addEventListener('click', generate);
  $('#preview').addEventListener('click', openPreview);
  $('#preview-refresh').addEventListener('click', openPreview);
  $('#preview-close').addEventListener('click', () => { $('#preview-view').hidden = true; });
  $('#preview-generate').addEventListener('click', () => { $('#preview-view').hidden = true; generate(); });
  $('#reset-ai').addEventListener('click', () => resetAiLayout().catch(alert));
  $('#clear-pool').addEventListener('click', () => clearPool().catch(alert));
  $('#bulk-delete').addEventListener('click', () => setBulk(!state.bulk));
  $('#recycle-open').addEventListener('click', () => openRecycle().catch(alert));
  $('#recycle-close').addEventListener('click', () => $('#recycle').close());
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
  const idle = () => state.token && !document.hidden && !state.dragId && !state.editing && !state.editingTitle;
  setInterval(() => { if (idle() && !state.bulk) refresh().catch(() => {}); }, REFRESH_MS);
  setInterval(() => { if (state.token && !document.hidden) heartbeat().catch(() => {}); }, BOARD_REFRESH_MS);
  setInterval(() => { if (state.token && !document.hidden) loadParseAlert().catch(() => {}); }, 5 * 60_000);
  // 选稿板轮询更频繁：同事勾选 / 排序几秒内可见
  setInterval(() => { if (idle()) loadBoard().then((changed) => changed && render()).catch(() => {}); }, BOARD_REFRESH_MS);
}

wire();
state.token = load(TOKEN_KEY);
if (state.token) enterApp().catch(logout);
else logout();
