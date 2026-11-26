import { $, login, api, beijingToday } from '/workspace.js';
import '/pull-cord.js';
await login();
try { const today = beijingToday(); const result = await api(`/api/briefs/issues?date=${today}`); $('#portal-status').textContent = `今日固定快讯：${result.items.filter((x) => x.confirmed_version).length} 项已确认，${result.items.filter((x) => x.versions.length && !x.confirmed_version).length} 项待核对。`; } catch (e) { $('#portal-status').textContent = e.message; }
