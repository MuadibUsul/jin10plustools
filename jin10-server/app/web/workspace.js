export const $ = (selector) => document.querySelector(selector);
export function copyDate(value) { return String(value || '').replace(/(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?/g, (_, y, m, d, h, min) => `${y}年${Number(m)}月${Number(d)}日${h === undefined ? '' : ` ${Number(h)}:${min}`}`); }
export function displayTime(value) {
  const parts = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`));
  const p = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${p.year}年${p.month}月${p.day}日 ${Number(p.hour)}:${p.minute}`;
}
export function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
export function toast(text, type = 'success') {
  if (!text) return;
  const target = [...document.querySelectorAll('dialog[open]')].pop() || document.body;
  let host = target.querySelector('.plus-toasts');
  if (!host) {
    host = node('div', undefined, 'plus-toasts'); host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite');
    target.append(host);
  }
  const item = node('div', text, `plus-toast ${type}`); host.append(item);
  requestAnimationFrame(() => item.classList.add('show'));
  setTimeout(() => { item.classList.remove('show'); setTimeout(() => item.remove(), 220); }, 2800);
}
export async function api(path, { method = 'GET', json, raw = false, headers: extraHeaders = {} } = {}) {
  if (method === 'POST' && path === '/api/briefs/jobs') {
    // Notify every local tab before generation is submitted, including a tab still loading a player.
    localStorage.setItem('plus.live.stop', crypto.randomUUID());
    window.dispatchEvent(new Event('plus-brief-generation'));
  }
  const token = localStorage.getItem('plus.token');
  const headers = { ...extraHeaders, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  if (json !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: json === undefined ? undefined : JSON.stringify(json) });
  if (!res.ok) { let detail = `请求失败 (${res.status})`; try { const data = await res.json(); if (typeof data.detail === 'string') detail = data.detail; } catch { /* not json */ } if (res.status === 401) localStorage.removeItem('plus.token'); throw new Error(detail); }
  return raw ? res : res.json();
}
export async function login() {
  try { const me = await api('/api/auth/me'); account(me); return me; } catch { /* show shared login */ }
  return new Promise((resolve) => {
    const overlay = node('div', undefined, 'login-overlay'); const form = node('form', undefined, 'login-box');
    form.append(node('h2', '登录 PLUS 工作台'), node('p', '使用现有团队账号，同日报选稿。', 'muted'));
    const email = node('input'); email.type = 'email'; email.placeholder = '邮箱'; email.autocomplete = 'username'; email.required = true;
    const password = node('input'); password.type = 'password'; password.placeholder = '密码'; password.autocomplete = 'current-password'; password.required = true;
    const error = node('p', '', 'error'); const submit = node('button', '登录', 'primary'); submit.type = 'submit';
    form.append(email, password, error, submit); overlay.append(form); document.body.append(overlay);
    form.addEventListener('submit', async (event) => { event.preventDefault(); submit.disabled = true; error.textContent = ''; try { const data = await api('/api/auth/login', { method: 'POST', json: { email: email.value, password: password.value } }); localStorage.setItem('plus.token', data.access_token); const me = await api('/api/auth/me'); overlay.remove(); account(me); resolve(me); } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; } });
  });
}
export function account(me) {
  const target = $('#account'); if (!target) return;
  const settings = node('a', '⚙ 设置', 'settings-link'); settings.href = '/settings/';
  if (location.pathname === '/settings/') settings.setAttribute('aria-current', 'page');
  const skills = node('a', '◇ Skill 库', 'settings-link'); skills.href = '/skills/';
  if (location.pathname === '/skills/' || location.pathname.startsWith('/skills/')) skills.setAttribute('aria-current', 'page');
  const out = node('button', '退出'); out.onclick = () => { localStorage.removeItem('plus.token'); location.reload(); };
  const notices = node('button', '反馈通知');
  notices.type = 'button';
  target.replaceChildren(settings, skills, notices, node('span', me.user.name), out);
  mountFeedbackNotifications(notices);
}

let unmountFeedbackNotifications = () => {};
function mountFeedbackNotifications(button) {
  unmountFeedbackNotifications();
  const dialog = node('dialog', undefined, 'feedback-notifications');
  const items = node('div');
  items.setAttribute('aria-live', 'polite');
  const close = node('button', '关闭');
  close.type = 'button';
  close.onclick = () => dialog.close();
  dialog.append(node('h2', '反馈完成通知'), items, close);
  document.body.append(dialog);
  let loading = false;

  async function refresh() {
    if (loading || !localStorage.getItem('plus.token')) return;
    loading = true;
    try {
      const data = await api('/api/feedback/notifications');
      const count = data.items.length;
      button.textContent = count ? `反馈通知（${count}${data.has_more ? '+' : ''}）` : '反馈通知';
      if (!dialog.open) return;
      if (!count) { items.replaceChildren(node('p', '没有未读通知。')); return; }
      items.replaceChildren(...data.items.map((item) => {
        const row = node('article', undefined, 'feedback-notice');
        const read = node('button', '标为已读');
        read.type = 'button';
        read.onclick = async () => {
          read.disabled = true;
          try {
            await api(`/api/feedback/notifications/${item.id}/read`, { method: 'POST', raw: true });
            await refresh();
          } catch (error) { read.disabled = false; toast(error.message, 'error'); }
        };
        row.append(node('strong', item.title), node('p', item.resolution),
          node('small', displayTime(item.completed_at)), read);
        return row;
      }));
    } catch (error) {
      if (dialog.open) items.replaceChildren(node('p', error.message, 'error'));
    } finally { loading = false; }
  }
  button.onclick = () => {
    dialog.showModal();
    items.replaceChildren(node('p', '正在读取通知…'));
    refresh();
  };
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  const onVisible = () => { if (!document.hidden) refresh(); };
  document.addEventListener('visibilitychange', onVisible);
  unmountFeedbackNotifications = () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisible);
    dialog.remove();
  };
  refresh();
}
export async function download(path, filename) { const res = await api(path, { raw: true }); const disposition = res.headers.get('Content-Disposition') || ''; const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i); const plain = disposition.match(/filename=["]([^"]+)["]/i); const url = URL.createObjectURL(await res.blob()); const a = node('a'); a.href = url; a.download = encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || filename || 'image'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
export async function copy(text) { try { await navigator.clipboard.writeText(text); } catch { throw new Error('浏览器未允许复制，请选中文字手动复制'); } }
