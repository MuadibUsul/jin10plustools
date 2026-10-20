import { $, node, api, login, displayTime, toast } from '/workspace.js';

let me, canAdmin = false;
function field(label, input) { const row = node('label', label, 'field'); row.append(input); return row; }
function msg(text, error = false) {
  const target = $('#skills-status') || $('#skill-status');
  target.textContent = text; target.className = error ? 'error' : 'muted';
}
async function safe(action) { try { await action(); } catch (e) { msg(e.message, true); toast(e.message, 'error'); } }
function badge(item) {
  if (item.editable === false) return node('span', '只读', 'badge');
  return node('span', item.enabled ? '已启用' : '已停用', `badge ${item.enabled ? 'ready' : 'warning'}`);
}
function meta(item) {
  if (item.editable === false) return `${item.skill_id}@${item.version} · 运行时 Skill · 只读`;
  return `${item.skill_id}@${item.version} · ${item.customized ? '团队规范' : '内置规范'} · ${item.samples} 条历史样本`;
}

async function renderList() {
  const data = await api('/api/settings/skills');
  const groups = new Map();
  for (const item of data.skills) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  const host = $('#skills-groups'); host.replaceChildren();
  for (const [group, items] of groups) {
    const section = node('section', undefined, 'skill-group');
    section.append(node('h2', group));
    const grid = node('div', undefined, 'skill-grid');
    for (const item of items) {
      const card = node('a', undefined, 'skill-card-link');
      card.href = `/skills/${item.category}/`;
      const head = node('div', undefined, 'skill-card-link-head');
      head.append(node('h3', item.label), badge(item));
      card.append(head, node('p', meta(item), 'skill-id'));
      if (item.description) card.append(node('p', item.description, 'skill-description'));
      card.append(node('p', item.updated_at ? `最后修改 ${displayTime(item.updated_at)}${item.updated_by_name ? ` · ${item.updated_by_name}` : ''}` : '尚未创建团队版本，使用内置规范', 'skill-meta'));
      grid.append(card);
    }
    section.append(grid);
    host.append(section);
  }
  msg(`共 ${data.skills.length} 项 Skill。点击卡片进入设置。`);
}

async function saveSkill(item, area, enabled, button, instructions = area.value) {
  button.disabled = true;
  try {
    await api(`/api/settings/skills/${encodeURIComponent(item.category)}`, {
      method: 'PUT', json: { revision: item.revision, enabled: enabled.checked, instructions },
    });
    toast('Skill 已保存。');
    await renderDetail(item.category);
  } finally { button.disabled = false; }
}

async function renderDetail(category) {
  const data = await api(`/api/settings/skills/${encodeURIComponent(category)}`);
  const item = data.skill;
  document.title = `${item.label} · Skill 库`;
  $('#skill-title').textContent = item.label;
  $('#skill-subtitle').textContent = `${item.skill_id}@${item.version} · ${item.customized ? '团队规范' : '内置规范'}`;
  const host = $('#skill-detail'); host.replaceChildren();
  const card = node('article', undefined, 'skill-detail-card');
  const head = node('div', undefined, 'skill-card-head');
  head.append(node('h2', 'Skill 规范'), badge(item));
  card.append(head, node('p', meta(item), 'skill-id'));
  if (item.description) card.append(node('p', item.description, 'muted'));
  if (item.editable === false) {
    const info = node('div', undefined, 'skill-runtime-info');
    info.append(node('p', `模型配置：${item.model_profile || '—'}`, 'muted'));
    info.append(node('p', `允许工具：${(item.allowed_tools || []).join('、') || '无'}`, 'muted'));
    const permissions = Object.entries(item.permissions || {}).map(([key, enabled]) => `${key}=${enabled ? '允许' : '拒绝'}`).join('，') || '无';
    info.append(node('p', `权限：${permissions}`, 'muted'));
    info.append(node('p', `质量门槛：${(item.quality_gates || []).join('、') || '无'}`, 'muted'));
    info.append(node('p', `依赖：${(item.dependencies || []).join('、') || '无'}`, 'muted'));
    card.append(info, node('p', '研究型 Skill 是服务端运行时定义，团队库当前只读；通用 News Skill OS 页面尚未部署。', 'skill-readonly-note'));
    host.append(card);
    msg('研究型 Skill 为只读。');
    return;
  }
  const area = node('textarea', undefined, 'skill-instructions');
  area.value = item.instructions; area.rows = 16; area.maxLength = 8000;
  area.readOnly = !canAdmin;
  area.setAttribute('aria-label', `${item.label} Skill 规范`);
  const counter = node('p', `${item.instructions.length} / 8000`, 'muted');
  area.addEventListener('input', () => { counter.textContent = `${area.value.length} / 8000`; });
  card.append(field('编辑规范', area), counter);
  const controls = node('div', undefined, 'toolbar');
  const enabled = node('input'); enabled.type = 'checkbox'; enabled.checked = item.enabled;
  enabled.disabled = !canAdmin;
  const enabledLabel = node('label', ' 启用此 Skill', 'check-row'); enabledLabel.prepend(enabled);
  controls.append(enabledLabel);
  const save = node('button', '保存 Skill', 'primary'); save.disabled = !canAdmin;
  save.onclick = () => safe(() => saveSkill(item, area, enabled, save));
  controls.append(save);
  const restore = node('button', '恢复内置规范'); restore.hidden = !canAdmin || !item.customized;
  restore.onclick = () => safe(async () => {
    if (!window.confirm('恢复内置规范会覆盖当前团队版本，继续？')) return;
    await saveSkill(item, area, enabled, restore, '');
  });
  controls.append(restore);
  card.append(controls);
  if (item.updated_at) card.append(node('p', `最后修改：${displayTime(item.updated_at)}${item.updated_by_name ? ` · ${item.updated_by_name}` : ''}`, 'muted'));
  if (item.history?.length) {
    const details = node('details');
    details.append(node('summary', `修改记录（${item.history.length}）`));
    for (const entry of [...item.history].reverse()) {
      details.append(node('p', `第 ${entry.revision} 次 · ${displayTime(entry.at)}${entry.by_name ? ` · ${entry.by_name}` : ''} · ${entry.enabled ? '启用' : '停用'} · ${entry.customized ? '团队规范' : '内置规范'}`, 'muted'));
    }
    card.append(details);
  }
  host.append(card);
  msg(canAdmin ? '修改会影响后续固定快讯任务的 Skill 文案产出。' : '只读账号可查看 Skill 规范，修改需管理员。');
}

const path = location.pathname.replace(/\/+$/, '');
const detail = path.match(/^\/skills\/([a-z0-9._-]+)$/);
try {
  me = await login();
  canAdmin = me.user.role === 'admin';
  if (detail) await renderDetail(detail[1]);
  else await renderList();
} catch (error) { msg(error.message, true); }
