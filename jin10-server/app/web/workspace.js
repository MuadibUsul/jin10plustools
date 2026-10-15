export const $ = (selector) => document.querySelector(selector);
export function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
export async function api(path, { method = 'GET', json, raw = false } = {}) {
  const token = localStorage.getItem('plus.token');
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
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
function account(me) { const target = $('#account'); if (!target) return; const out = node('button', '退出'); out.onclick = () => { localStorage.removeItem('plus.token'); location.reload(); }; target.replaceChildren(node('span', me.user.name), out); }
export async function download(path, filename) { const res = await api(path, { raw: true }); const url = URL.createObjectURL(await res.blob()); const a = node('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
export async function copy(text) { try { await navigator.clipboard.writeText(text); } catch { throw new Error('浏览器未允许复制，请选中文字手动复制'); } }
