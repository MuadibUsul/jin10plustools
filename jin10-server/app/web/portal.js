import { $, login, api } from '/workspace.js';
import '/pull-cord.js';
const path = location.pathname;
if (path.startsWith('/fresh/') || path.startsWith('/annotations/')) {
  $('#page-title').textContent = path.startsWith('/fresh/') ? 'Plus新鲜事' : 'Plus注解辅助';
  $('#page-description').textContent = '功能正在规划，当前已预留独立入口。';
  $('#home-cards').hidden = true;
}
await login();
try { const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date()); const result = await api(`/api/briefs/issues?date=${today}`); $('#portal-status').textContent = `今日固定快讯：${result.items.filter((x) => x.confirmed_version).length} 项已确认，${result.items.filter((x) => x.versions.length && !x.confirmed_version).length} 项待核对。`; } catch (e) { $('#portal-status').textContent = e.message; }
