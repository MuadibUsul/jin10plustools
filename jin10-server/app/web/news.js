import { $, node, api, login, displayTime, toast } from '/workspace.js';

const target = location.pathname.startsWith('/annotations/') ? 'annotation' : 'fresh';
const targetLabel = target === 'annotation' ? 'Plus注解辅助' : 'Plus新鲜事';
let me, canEdit = false, events = [], drafts = [];
const dialog = $('#news-dialog');

function message(text, error = false) { $('#news-status').textContent = text; $('#news-status').className = error ? 'error' : 'muted'; }
async function safe(action) { try { await action(); } catch (e) { message(e.message, true); toast(e.message, 'error'); } }
function openDialog(title, status = '') {
  $('#news-dialog-title').textContent = title;
  $('#news-dialog-status').textContent = status;
  $('#news-dialog-content').replaceChildren();
  if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); }
}
function closeDialog() {
  if (!dialog?.open) return;
  if (dialog.close) dialog.close(); else dialog.removeAttribute('open');
}
function empty(text) { return node('p', text, 'news-empty'); }
function stamp(value) { return value ? displayTime(value) : ''; }
function textSection(host, title, values) {
  const list = (values || []).filter(Boolean);
  if (!list.length) return;
  host.append(node('h3', title));
  for (const value of list) host.append(node('p', String(value), 'muted'));
}

async function load() {
  const [eventData, draftData] = await Promise.all([
    api('/api/news-skill-os/events'),
    api('/api/news-skill-os/drafts'),
  ]);
  events = eventData.items || [];
  drafts = (draftData.items || []).filter((item) => item.target === target);
  renderEvents();
  renderDrafts();
  message(`事件 ${events.length} 个 · ${targetLabel}草稿 ${drafts.length} 个`);
}

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
  if (!drafts.length) { host.append(empty(`暂无${targetLabel}草稿。链接分析确认后可生成。`)); return; }
  for (const item of drafts) {
    const card = node('article', undefined, 'news-card');
    const head = node('div', undefined, 'toolbar');
    head.append(node('h3', item.title), node('span', item.status, 'news-badge'));
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
    toast('Plus新鲜事草稿已生成，等待人工审核。');
    message(`已生成待审草稿：${draft.title}`);
    await load();
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

function showAnnotationResult(result) {
  openDialog(result.title, '三段注解 · 先人工核对再保存送审');
  const host = $('#news-dialog-content');
  host.append(node('div', result.body, 'news-draft-body'));
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
    const save = node('button', '保存为待审草稿', 'primary');
    save.onclick = () => safe(async () => {
      save.disabled = true;
      try {
        await api('/api/news-skill-os/drafts', { method: 'POST', json: {
          title: result.title, body: result.body, target: 'annotation',
          payload: { flash: $('#annotation-text').value.trim(),
                     source_url: $('#annotation-source').value.trim() || null,
                     sources: result.sources || [], warnings: result.warnings || [] },
        }});
        toast('注解草稿已保存，等待人工审核。');
        closeDialog(); await load();
      } finally { save.disabled = false; }
    });
    actions.append(save); host.append(actions);
  }
}

async function showDraft(item) {
  const detail = await api(`/api/news-skill-os/drafts/${item.id}`);
  openDialog(detail.draft.title, `${detail.draft.status} · 修订 ${detail.draft.current_revision}`);
  const host = $('#news-dialog-content');
  host.append(node('div', detail.draft.body, 'news-draft-body'));
  const details = node('details'); details.append(node('summary', `版本记录（${detail.revisions.length}）`));
  for (const version of [...detail.revisions].reverse()) {
    details.append(node('p', `第 ${version.number} 次 · ${stamp(version.created_at)} · ${version.review_status}${version.review_note ? ` · ${version.review_note}` : ''}`, 'muted'));
  }
  host.append(details);
  if (!canEdit) return;
  const actions = node('div', undefined, 'news-dialog-actions');
  const submit = node('button', '送审'); submit.onclick = () => safe(async () => {
    await api(`/api/news-skill-os/drafts/${item.id}/submit`, { method: 'POST' });
    await showDraft(item); await load();
  });
  const approve = node('button', '通过', 'primary'); approve.onclick = () => safe(async () => {
    await api(`/api/news-skill-os/drafts/${item.id}/review`, { method: 'POST', json: { decision: 'approve', note: '' } });
    toast('草稿已通过人工审核。'); await showDraft(item); await load();
  });
  const reject = node('button', '驳回'); reject.onclick = () => safe(async () => {
    const note = window.prompt('驳回原因（必填）');
    if (!note) return;
    await api(`/api/news-skill-os/drafts/${item.id}/review`, { method: 'POST', json: { decision: 'reject', note } });
    toast('草稿已驳回。'); await showDraft(item); await load();
  });
  actions.append(submit, approve, reject); host.append(actions);
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
  const button = $('#annotation-submit'); button.disabled = true;
  safe(async () => {
    const result = await api('/api/news-skill-os/annotations/analyze', {
      method: 'POST', json: {
        text: $('#annotation-text').value.trim(),
        source_url: $('#annotation-source').value.trim() || null,
      },
    });
    toast('三段注解已生成，请核对后保存。');
    showAnnotationResult(result);
  }).finally(() => { button.disabled = false; });
};
$('#news-dialog-close').onclick = closeDialog;
$('#news-dialog').addEventListener('close', () => $('#news-dialog-content').replaceChildren());

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
