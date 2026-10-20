import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const root = resolve('../jin10-server/app/web');
const script = readFileSync(join(root, 'settings.js'), 'utf8').replace(/^import[^\n]*\n/gm, '');
const html = readFileSync(join(root, 'settings.html'), 'utf8');
const $ = (selector: string) => document.querySelector(selector)!;

const settings = {
  revision: 0, can_edit: true, can_admin: true, schedule_enabled: false,
  categories: [{ id: 'volume', label: '成交额', notes: '' }], note_revisions: [],
  ai_provider: 'deepseek',
  ai: {
    jev: { configured: false, enabled: true, model: 'jev-latest' },
    deepseek: { configured: false, enabled: true, model: 'deepseek-chat' },
    chat2api: { configured: false, enabled: true, model: 'gpt-4o-mini', model_supported: true },
    openai: { configured: false, enabled: true, model: 'gpt-4o-mini' },
  },
  live_url: '', live_urls: {}, live_catalog: { categories: [], channels: [] },
};
const skill = {
  category: 'volume', label: '成交额', skill_id: 'news.fixed-volume', version: '0.1.0',
  enabled: true, customized: false, instructions: '# 内置规范', bundled_instructions: '# 内置规范',
  description: '成交额文段', digest: 'abc', bundled_digest: 'abc', samples: 20,
  revision: 0, updated_at: null, updated_by: null, updated_by_name: null, history: [],
};
let api: ReturnType<typeof vi.fn>;

async function page() {
  return runInNewContext(`(async () => { ${script}\n return true; })()`, {
    $, document, window: { addEventListener: vi.fn() }, location: { hash: '#skills' },
    history: { replaceState: vi.fn() }, Intl, Date, setInterval: vi.fn(), clearInterval: vi.fn(),
    node: (tag: string, text?: string, className?: string) => {
      const value = document.createElement(tag);
      if (text !== undefined) value.textContent = text;
      if (className) value.className = className;
      return value;
    },
    api, login: async () => ({ user: { role: 'admin' } }),
    displayTime: (value: string) => value, copyDate: (value: string) => value,
    download: vi.fn(), toast: vi.fn(),
  });
}

beforeEach(() => {
  document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]!;
  api = vi.fn(async (path: string, options?: { method?: string; json?: unknown }) => {
    if (path === '/api/settings') return structuredClone(settings);
    if (path === '/api/settings/skills') return { skills: [structuredClone(skill)] };
    if (path === '/api/settings/skills/volume') {
      const body = typeof options!.json === 'string' ? JSON.parse(options!.json) : options!.json as any;
      return { skill: { ...structuredClone(skill), revision: body.revision + 1,
        enabled: body.enabled, instructions: body.instructions || skill.instructions,
        customized: Boolean(body.instructions) } };
    }
    throw new Error(`unexpected ${path}`);
  });
});

describe('Skill library settings panel', () => {
  it('lists the five-skill entry and saves a team override', async () => {
    await page();
    await vi.waitFor(() => expect($('#skill-library').children.length).toBe(1));
    expect($('#skill-library').textContent).toContain('news.fixed-volume');
    const area = $('#skill-library textarea') as HTMLTextAreaElement;
    area.value = '# 团队规范';
    ($('#skill-library .primary') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('/api/settings/skills/volume', expect.anything()));
    expect(JSON.stringify(api.mock.calls.find(([path]) => path === '/api/settings/skills/volume')![1].json))
      .toContain('# 团队规范');
    await vi.waitFor(() => expect($('#settings-message').textContent).toContain('已保存'));
  });
});
