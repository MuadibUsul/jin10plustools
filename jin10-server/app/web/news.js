import { $, node, api, login, displayTime, toast } from '/workspace.js';

const target = location.pathname.startsWith('/annotations/') ? 'annotation' : 'fresh';
const targetLabel = target === 'annotation' ? 'Plus注解辅助' : 'Plus新鲜事';
let me, canEdit = false, events = [], drafts = [];
const draftPageSize = 12;
let draftPage = 0, draftHasMore = false, draftRequest = 0, draftLoading = false;
const dialog = $('#news-dialog');
const draftStates = { draft: '草稿', pending_review: '待审核', approved: '已通过', rejected: '已驳回', archived: '已归档' };
let dialogBusy = false;
let draftEditChanged = null;

function message(text, error = false) { $('#news-status').textContent = text; $('#news-status').className = error ? 'error' : 'muted'; }
async function safe(action) { try { await action(); } catch (e) { message(e.message, true); toast(e.message, 'error'); } }
function openDialog(title, status = '') {
  draftEditChanged = null;
  $('#news-dialog-title').textContent = title;
  $('#news-dialog-status').textContent = status;
  $('#news-dialog-action-status').textContent = '';
  $('#news-dialog-content').replaceChildren();
  if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); }
}
function closeDialog() {
  if (!dialog?.open || dialogBusy) return;
  if (draftEditChanged?.() && !window.confirm('放弃未保存的修改？')) return;
  if (dialog.close) dialog.close(); else dialog.removeAttribute('open');
}
async function dialogAction(progress, success, action, onSaved) {
  if (dialogBusy) return;
  dialogBusy = true;
  const controls = [...dialog.querySelectorAll('button:not(:disabled)')];
  const status = $('#news-dialog-action-status');
  for (const control of controls) control.disabled = true;
  status.className = 'muted'; status.textContent = progress;
  let result;
  try {
    result = await action();
  } catch (error) {
    status.className = 'error'; status.textContent = error.message;
    return;
  } finally {
    dialogBusy = false;
    for (const control of controls) control.disabled = false;
  }
  if (onSaved) onSaved(result);
  else closeDialog();
  toast(success);
  await safe(load);
}
function empty(text) { return node('p', text, 'news-empty'); }
function stamp(value) { return value ? displayTime(value) : ''; }
function textSection(host, title, values) {
  const list = (values || []).filter(Boolean);
  if (!list.length) return;
  host.append(node('h3', title));
  for (const value of list) host.append(node('p', String(value), 'muted'));
}

async function load(firstPage = false) {
  await Promise.all([loadEvents(), loadDrafts(firstPage ? 0 : draftPage)]);
}

async function loadEvents() {
  const eventData = await api('/api/news-skill-os/events');
  events = eventData.items || [];
  renderEvents();
}

function updateDraftPager() {
  $('#draft-pager').hidden = draftPage === 0 && !draftHasMore;
  $('#draft-prev').disabled = draftLoading || draftPage === 0;
  $('#draft-next').disabled = draftLoading || !draftHasMore;
  $('#draft-page').textContent = draftLoading ? '正在加载…' : `第 ${draftPage + 1} 页`;
}

async function loadDrafts(page) {
  const request = ++draftRequest;
  draftLoading = true;
  updateDraftPager();
  try {
    let data;
    do {
      data = await api(`/api/news-skill-os/drafts?target=${target}&limit=${draftPageSize}&offset=${page * draftPageSize}`);
      if (request !== draftRequest) return;
      if (data.items.length || page === 0) break;
      page--;
    } while (true);
    draftPage = page;
    draftHasMore = data.has_more;
    drafts = data.items;
    renderDrafts();
  } finally {
    if (request === draftRequest) {
      draftLoading = false;
      updateDraftPager();
    }
  }
}

function changeDraftPage(page) {
  if (draftLoading) return;
  $('#draft-page').focus({ preventScroll: true });
  return safe(() => loadDrafts(page));
}
$('#draft-prev').onclick = () => changeDraftPage(draftPage - 1);
$('#draft-next').onclick = () => changeDraftPage(draftPage + 1);

function renderEvents() {
  const host = $('#news-events'); host.replaceChildren();
  if (!events.length) { host.append(empty('暂无事件。粘贴一个新闻链接开始分析。')); return; }
  for (const item of events) {
    const card = node('article', undefined, 'news-card');
    card.append(node('h3', item.title));
    card.append(node('p', `${stamp(item.event_time)} · 修订 ${item.current_revision}`, 'muted'));
    const controls = node('div', undefined, 'toolbar');
    const view = node('button', '查看事实链'); view.onclick = () => safe(() => showEvent(item)); controls.append(view);
    if (item.payload?.body && item.payload?.title) {
      const analysis = node('button', '查看分析'); analysis.onclick = () => showAnalysis(item.payload, item.id); controls.append(analysis);
      if (canEdit && target === 'fresh') {
        const draft = node('button', '生成快讯', 'primary'); draft.onclick = () => safe(() => createFreshDraft(item.id, draft)); controls.append(draft);
      }
    }
    card.append(controls);
    host.append(card);
  }
}

function renderDrafts() {
  const host = $('#news-drafts'); host.replaceChildren();
  if (!drafts.length) {
    host.append(empty(canEdit
      ? target === 'annotation' ? '暂无注解草稿。分析一条快讯，核对后保存草稿。' : '暂无新鲜事草稿。分析新闻链接，核对后生成草稿。'
      : '本团队暂无可查看的草稿。'));
    return;
  }
  for (const item of drafts) {
    const card = node('article', undefined, 'news-card');
    const head = node('div', undefined, 'toolbar');
    head.append(node('h3', item.title), node('span', draftStates[item.status] || item.status, 'news-badge'));
    card.append(head, node('p', item.body.slice(0, 160), 'muted'));
    const view = node('button', '查看与审核'); view.onclick = () => safe(() => showDraft(item)); card.append(view);
    host.append(card);
  }
}

function showAnalysis(analysis, eventId) {
  openDialog(analysis.title, '分析报告 · 确认后再生成 400 字快讯');
  const host = $('#news-dialog-content');
  textSection(host, '时间线', analysis.timeline);
  textSection(host, '关键数字', analysis.key_numbers);
  textSection(host, '来源原话', analysis.source_quotes);
  textSection(host, '编辑判断（与信源原话分开）', analysis.judgment);
  textSection(host, '未核实 / 口径提醒', analysis.unverified);
  textSection(host, '其他提醒', analysis.warnings);
  const research = analysis.external_research;
  if (research) {
    host.append(node('h3', '外部来源核查'));
    host.append(node('p', research.status === 'completed'
      ? '已实时检索并读取外部网页；以下是可回看的原文线索，断言仍需人工确认。'
      : '本次未完成外部来源核查；请依据原始链接人工核对。', 'muted'));
    const sources = new Map((research.sources || []).map((item) => [item.id, item]));
    for (const check of research.checks || []) {
      const label = check.status === 'possible_conflict' ? '可能冲突'
        : check.status === 'external_match' ? '发现相符原文' : '尚无外部证据';
      const block = node('div', undefined, 'news-claim');
      block.append(node('strong', `${label} · ${check.claim}`));
      for (const match of (research.matches || []).filter((item) => item.claim_id === check.claim_id)) {
        block.append(node('p', `${match.stance === 'contradicts' ? '相反原文' : '相符原文'}：${match.quote}`, 'news-evidence'));
        const source = sources.get(match.source_id);
        if (source) {
          const link = node('a', source.title || source.url);
          link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          const paragraph = node('p'); paragraph.append(link); block.append(paragraph);
        }
      }
      host.append(block);
    }
  }
  host.append(node('h3', '拟稿预览'));
  host.append(node('div', analysis.body, 'news-draft-body'));
  host.append(node('p', analysis.notes, 'muted'));
  if (analysis.sources?.length) {
    host.append(node('h3', '来源链接'));
    for (const url of analysis.sources) {
      const link = node('a', url); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      const paragraph = node('p'); paragraph.append(link); host.append(paragraph);
    }
  }
  if (canEdit && target === 'fresh') {
    const actions = node('div', undefined, 'news-dialog-actions');
    const create = node('button', '生成 400 字快讯', 'primary');
    create.onclick = () => safe(async () => { await createFreshDraft(eventId, create); closeDialog(); });
    actions.append(create); host.append(actions);
  }
}

async function createFreshDraft(eventId, button) {
  button.disabled = true;
  try {
    const draft = await api(`/api/news-skill-os/events/${eventId}/fresh-draft`, { method: 'POST' });
    toast('草稿已生成，核对后可送审。');
    message(`已生成草稿：${draft.title}`);
    await load(true);
  } finally { button.disabled = false; }
}

async function showEvent(item) {
  const detail = await api(`/api/news-skill-os/events/${item.id}`);
  const current = detail.event?.current_revision || detail.revisions?.at(-1)?.number;
  openDialog(item.title, `${stamp(item.event_time)} · 修订 ${item.current_revision}`);
  const host = $('#news-dialog-content');
  if (!current) { host.append(empty('事件尚无事实修订。')); return; }
  const revision = await api(`/api/news-skill-os/events/${item.id}/revisions/${current}`);
  const snapshot = revision.snapshot || {};
  const sources = new Map((snapshot.sources || []).map((source) => [source.id, source]));
  const evidence = new Map((snapshot.evidence || []).map((row) => [row.id, row]));
  const links = snapshot.links || [];
  for (const claim of snapshot.claims || []) {
    const block = node('div', undefined, 'news-claim');
    block.append(node('strong', `${claim.status} · ${claim.text}`));
    const refs = links.filter((link) => link.claim_id === claim.id).map((link) => evidence.get(link.evidence_id)).filter(Boolean);
    for (const ref of refs) {
      block.append(node('p', `证据：${ref.excerpt}`, 'news-evidence'));
      const source = sources.get(ref.source_id);
      if (source) block.append(node('p', `来源：${source.name}（${source.kind}）${source.url ? ` · ${source.url}` : ''}`, 'news-evidence'));
    }
    host.append(block);
  }
  if (!(snapshot.claims || []).length) host.append(empty('该修订没有 Claim。'));
}

function showAnnotationResult(result, input) {
  openDialog(result.title, '三段注解 · 先人工核对再保存送审');
  const host = $('#news-dialog-content');
  host.append(node('div', result.body, 'news-draft-body'));
  const original = node('details');
  original.append(node('summary', '核对本次快讯原文'), node('p', input.text, 'news-draft-body'));
  if (input.source_url) original.append(node('p', input.source_url, 'news-draft-body'));
  host.append(original);
  if (result.warnings?.length) {
    host.append(node('h3', '核对提醒'));
    for (const warning of result.warnings) host.append(node('p', warning, 'warning'));
  }
  if (result.sources?.length) {
    host.append(node('h3', '检索参考'));
    for (const url of result.sources) {
      const link = node('a', url); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      const paragraph = node('p'); paragraph.append(link); host.append(paragraph);
    }
  }
  if (canEdit && target === 'annotation') {
    const actions = node('div', undefined, 'news-dialog-actions');
    const save = node('button', '保存草稿', 'primary');
    save.onclick = () => dialogAction('正在保存注解草稿…', '注解草稿已保存，核对后可送审。', () =>
      api('/api/news-skill-os/drafts', { method: 'POST', json: {
        title: result.title, body: result.body, target: 'annotation',
        payload: { flash: input.text, source_url: input.source_url,
                   sources: result.sources || [], warnings: result.warnings || [],
                   quality_review: result.quality_review || null },
      }}), () => { draftPage = 0; closeDialog(); });
    actions.append(save); host.append(actions);
  }
}

async function showDraft(item) {
  renderDraftDetail(await api(`/api/news-skill-os/drafts/${item.id}`));
}

function renderDraftDetail(detail) {
  const draft = detail.draft;
  const current = detail.revisions.find((version) => version.number === draft.current_revision);
  openDialog(draft.title, `${draftStates[draft.status] || draft.status} · 修订 ${draft.current_revision}`);
  const host = $('#news-dialog-content');
  if (draft.status === 'rejected' && current?.review_note) {
    host.append(node('p', `驳回原因：${current.review_note}`, 'warning'));
  }
  host.append(node('div', detail.draft.body, 'news-draft-body'));
  const details = node('details'); details.append(node('summary', `版本记录（${detail.revisions.length}）`));
  for (const version of [...detail.revisions].reverse()) {
    details.append(node('p', `第 ${version.number} 次 · ${stamp(version.created_at)} · ${version.review_status}${version.review_note ? ` · ${version.review_note}` : ''}`, 'muted'));
  }
  host.append(details);
  if (!canEdit || ['approved', 'archived'].includes(draft.status)) return;
  const actions = node('div', undefined, 'news-dialog-actions');
  const path = `/api/news-skill-os/drafts/${draft.id}`;
  if (draft.status !== 'pending_review') {
    const edit = node('button', '编辑新修订', draft.status === 'rejected' ? 'primary' : undefined);
    edit.onclick = () => editDraft(detail, current);
    actions.append(edit);
  }
  if (draft.status === 'draft') {
    const submit = node('button', '送审');
    submit.onclick = () => dialogAction('正在送审…', '草稿已送审。', () => api(`${path}/submit`, { method: 'POST' }),
      (saved) => renderDraftDetail({ ...detail, draft: saved }));
    actions.append(submit);
  }
  if (draft.status !== 'pending_review') { host.append(actions); return; }
  const review = (decision, note, success) => dialogAction('正在保存审核结果…', success, () =>
    api(`${path}/review`, { method: 'POST', json: { decision, note, expected_revision: draft.current_revision } }));
  const approve = node('button', '通过', 'primary');
  approve.onclick = () => review('approve', '', '草稿已通过人工审核。');
  const reject = node('button', '驳回');
  reject.onclick = () => {
    const note = window.prompt('驳回原因（必填）')?.trim();
    if (note) review('reject', note, '草稿已驳回。');
  };
  actions.append(approve, reject); host.append(actions);
}

function editDraft(detail, current) {
  const { draft } = detail;
  openDialog('编辑草稿修订', `基于第 ${draft.current_revision} 版。保存后可送审。`);
  const host = $('#news-dialog-content');
  if (draft.status === 'rejected' && current?.review_note) {
    host.append(node('p', `驳回原因：${current.review_note}`, 'warning'));
  }
  const form = node('form');
  const title = node('input');
  title.value = draft.title; title.maxLength = 300; title.required = true;
  const body = node('textarea');
  body.value = draft.body; body.rows = 10; body.maxLength = 100000; body.required = true;
  for (const [label, control] of [['标题', title], ['正文', body]]) {
    const field = node('label', label, 'field');
    field.append(control); form.append(field);
  }
  const save = node('button', '保存新修订', 'primary');
  save.type = 'submit';
  const cancel = node('button', '取消'); cancel.type = 'button';
  draftEditChanged = () => title.value !== draft.title || body.value !== draft.body;
  cancel.onclick = () => {
    if (draftEditChanged() && !window.confirm('放弃未保存的修改？')) return;
    renderDraftDetail(detail);
  };
  const actions = node('div', undefined, 'news-dialog-actions');
  actions.append(save, cancel); form.append(actions); host.append(form);
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (dialogBusy) return;
    const request = {
      expected_revision: draft.current_revision, title: title.value.trim(),
      body: body.value, payload: current?.payload ?? draft.payload ?? {},
    };
    if (!request.title || !request.body.trim()) {
      const status = $('#news-dialog-action-status');
      status.className = 'error'; status.textContent = '请填写标题和正文。';
      return;
    }
    title.readOnly = body.readOnly = true;
    try {
      await dialogAction('正在保存新修订…', '新修订已保存，可以送审。', async () => {
        if (draft.event_id && current?.event_revision_id) {
          const event = await api(`/api/news-skill-os/events/${draft.event_id}`);
          const source = event.revisions.find((version) => version.id === current.event_revision_id);
          if (!source) throw new Error('无法核对原事实修订，请刷新后重试。');
          request.event_revision = source.number;
        }
        return api(`/api/news-skill-os/drafts/${draft.id}/revisions`, { method: 'POST', json: request });
      }, (revision) => {
        renderDraftDetail({
          draft: { ...draft, status: 'draft', current_revision: revision.number, title: revision.title, body: revision.body },
          revisions: [...detail.revisions, revision],
        });
        host.querySelector('.news-dialog-actions button:last-child')?.focus();
      });
    } finally { title.readOnly = body.readOnly = false; }
  };
  title.focus();
}

$('#news-form').onsubmit = (event) => {
  event.preventDefault();
  const button = $('#news-submit'); button.disabled = true;
  message('正在抓取原文、联网检索并核对证据…');
  safe(async () => {
    const result = await api('/api/news-skill-os/from-link/analyze', {
      method: 'POST', json: { url: $('#news-link').value.trim(), focus: $('#news-focus').value.trim() },
    });
    toast(`已提取 ${result.claim_count} 条待核查断言。`);
    message('分析完成。核对时间线、数字和来源后，再生成 400 字快讯。');
    showAnalysis(result.analysis, result.event.id);
    await load();
  }).finally(() => { button.disabled = false; });
};
$('#annotation-form').onsubmit = (event) => {
  event.preventDefault();
  const button = $('#annotation-submit');
  if (button.disabled) return;
  const input = {
    text: $('#annotation-text').value.trim(),
    source_url: $('#annotation-source').value.trim() || null,
  };
  button.disabled = true;
  message('正在分析快讯并检索参考来源…');
  safe(async () => {
    const result = await api('/api/news-skill-os/annotations/analyze', {
      method: 'POST', json: input,
    });
    toast('三段注解已生成，请核对后保存。');
    message('分析已完成。核对本次原文、注解和参考来源后保存。');
    showAnnotationResult(result, input);
  }).finally(() => { button.disabled = false; });
};
$('#news-dialog-close').onclick = closeDialog;
dialog.addEventListener('cancel', (event) => {
  if (dialogBusy || (draftEditChanged?.() && !window.confirm('放弃未保存的修改？'))) event.preventDefault();
});
dialog.addEventListener('close', () => {
  draftEditChanged = null;
  $('#news-dialog-content').replaceChildren();
});

try {
  me = await login();
  canEdit = me.user.role !== 'viewer';
  $('#news-title').textContent = targetLabel;
  $('#news-subtitle').textContent = target === 'annotation'
    ? '粘贴一条财经快讯，生成前因、后果和补充说明，核对来源后保存送审。'
    : '粘贴任意公开新闻链接，先得到时间线、关键数字、来源原话和编辑判断，确认后再生成不超过400字的快讯。';
  $('#news-target-label').textContent = targetLabel;
  for (const link of document.querySelectorAll('header nav a')) {
    if (link.getAttribute('href') === location.pathname) link.classList.add('active');
  }
  $('#news-create').hidden = !canEdit || target !== 'fresh';
  $('#annotation-create').hidden = !canEdit || target !== 'annotation';
  await load();
} catch (error) { message(error.message, true); }
