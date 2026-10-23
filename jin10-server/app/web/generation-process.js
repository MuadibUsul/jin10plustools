import { node, copyDate, displayTime } from '/workspace.js';

// Shared, on-demand inspector. A product supplies a loader; no polling or page layout dependency.
let dialog, content, status, refresh, heading, loader, request = 0, opener;

function section(title) {
  const box = node('section', undefined, 'generation-section');
  box.append(node('h3', title)); content.append(box); return box;
}
function paragraph(box, text, className) { box.append(node('p', copyDate(text || ''), className)); }
function render(data) {
  const { job, details: info } = data;
  content.replaceChildren();
  const states = {queued:'等待执行',running:'正在执行',waiting:'等待来源更新',succeeded:'生成完成',failed:'生成失败',skipped:'已停止'};
  status.textContent = `${states[job.status] || job.status} · ${info.action} · 生产日 ${copyDate(job.issue_date)}`;
  const path = section('本次执行方式');
  paragraph(path, info.reused ? '复用已有同日期快照，本次未重新取数。以下指标与原始证据来自被复用的结果。' : job.action === 'render' ? '从已保存的原始快照重新制图，不请求外部数据。' : job.action === 'ai' ? '复用已校验数据与图片，仅补跑AI文案。' : '以实际执行记录和已保存快照为准；未完成的步骤不计为完成。');
  const history = section('执行记录');
  const events = node('ol', undefined, 'generation-events');
  for (const event of job.progress?.events || []) {
    const row = node('li'), time = node('time', displayTime(event.at)); time.dateTime = event.at;
    row.append(time, node('span', copyDate(event.message))); events.append(row);
  }
  history.append(events);
  if (job.error) paragraph(history, job.error, 'error');
  const sources = section('数据从哪里来');
  for (const source of info.sources || []) {
    const row = node('p'), link = node('a', source.label); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    row.append(link, node('small', source.url)); sources.append(row);
  }
  if (!info.sources?.length) paragraph(sources, '本次尚未保存可展示的来源记录。', 'muted');
  if (info.fetched_at) paragraph(sources, `本程序抓取时间：${displayTime(info.fetched_at)}`);
  if (info.inputs?.length) {
    const input = section('本期输入与计算结果'), list = node('dl', undefined, 'generation-values');
    for (const item of info.inputs) list.append(node('dt', item.label), node('dd', `${copyDate(item.value)}${item.unit ? ` ${item.unit}` : ''}`));
    input.append(list);
  }
  const calculation = section('如何计算');
  const formulas = node('ol'); for (const formula of info.calculations || []) formulas.append(node('li', copyDate(formula))); calculation.append(formulas);
  if (!info.calculations?.length) paragraph(calculation, '暂无专属计算说明，可核对下方保存的原始指标。', 'muted');
  const assembly = section('如何组成最终结果');
  const steps = node('ol'); for (const text of info.assembly || []) steps.append(node('li', text)); assembly.append(steps);
  if (!info.available) paragraph(assembly, '本次尚未保存完整结果，以上为处理规则；请按执行记录确认当前进行到哪一步。', 'muted');
  else {
    if (info.rule_copy?.body) { paragraph(assembly, `规则标题：${info.rule_copy.title}`); paragraph(assembly, info.rule_copy.body); }
    paragraph(assembly, `本期标题：${info.result.title}`);
    paragraph(assembly, info.result.body);
    paragraph(assembly, `配图：${info.result.images?.length ? info.result.images.join('、') : '本栏目无配图'}`);
    const ai = {ok:'候选文案已通过校验',not_configured:'未配置，使用规则稿',disabled:'已关闭',pending:'处理中',failed:'调用失败，保留规则稿',timeout:'请求超时，保留规则稿',invalid_response:'未通过校验，保留规则稿'};
    paragraph(assembly, `AI（${{chat2api:'chat2api',openai:'OpenAI',deepseek:'DeepSeek'}[info.result.ai_provider] || '未配置'}）：${ai[info.result.ai_status] || info.result.ai_status}${info.result.ai_model ? `（${info.result.ai_model}）` : ''}`);
    for (const warning of info.result.warnings || []) paragraph(assembly, warning, 'warning');
  }
  const evidence = section('原始证据');
  if (!info.evidence?.length) paragraph(evidence, '暂未保存原始快照。', 'muted');
  for (const file of info.evidence || []) {
    const row = node('details'), summary = node('summary', `${file.name} · ${file.bytes} 字节${file.rows === null ? '' : ` · ${file.rows} 条记录`}`);
    row.append(summary, node('p', `SHA-256：${file.sha256}`, 'generation-digest'));
    const sample = info.samples?.find(s => s.name === file.name);
    if (sample) { paragraph(row, '最后两条保存的记录'); row.append(node('pre', JSON.stringify(sample.rows, null, 2))); }
    evidence.append(row);
  }
  if (Object.keys(info.facts || {}).length) {
    const raw = node('details'); raw.append(node('summary', '查看保存的原始指标字段'), node('pre', JSON.stringify(info.facts, null, 2))); evidence.append(raw);
  }
}
async function load() {
  const id = ++request;
  refresh.disabled = true; status.textContent = '正在读取本次生成记录…';
  try { const data = await loader(); if (id === request && dialog.open) render(data); }
  catch (error) { if (id === request && dialog.open) status.textContent = `读取失败：${error.message}`; }
  finally { if (id === request) refresh.disabled = false; }
}
export async function showGenerationProcess({ title, load: fetchDetails }) {
  if (!dialog) {
    const style = node('link'); style.rel = 'stylesheet'; style.href = '/generation-process.css'; document.head.append(style);
    dialog = node('dialog', undefined, 'generation-dialog'); dialog.setAttribute('aria-labelledby', 'generation-title');
    const head = node('div', undefined, 'generation-head'); heading = node('h2'); heading.id = 'generation-title';
    const close = node('button', '关闭'); close.type = 'button'; close.onclick = () => dialog.close(); head.append(heading, close);
    status = node('p', '', 'muted'); status.setAttribute('role','status');
    refresh = node('button', '刷新记录'); refresh.type = 'button'; refresh.onclick = load;
    const toolbar = node('div', undefined, 'generation-toolbar'); toolbar.append(status, refresh);
    content = node('div', undefined, 'generation-content'); dialog.append(head, toolbar, content); document.body.append(dialog);
    dialog.addEventListener('close', () => { request++; loader = null; opener?.focus(); });
    dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
  }
  opener = document.activeElement; loader = fetchDetails; heading.textContent = `${title} · 生成过程`;
  content.replaceChildren(); if (!dialog.open) dialog.showModal(); await load();
}
