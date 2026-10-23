import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const root = resolve('../jin10-server/app/web');
const script = readFileSync(join(root, 'skills.js'), 'utf8').replace(/^import[^\n]*\n/gm, '');
const listHtml = readFileSync(join(root, 'skills.html'), 'utf8');
const detailHtml = readFileSync(join(root, 'skill-detail.html'), 'utf8');
const $ = (selector: string) => document.querySelector(selector)!;

const skills = [
  { category: 'volume', label: '成交额', group: 'A股温度计', skill_id: 'news.fixed-volume',
    version: '0.1.0', enabled: true, customized: false, instructions: '# 内置', bundled_instructions: '# 内置',
    description: '成交额文段', digest: 'abc', bundled_digest: 'abc', samples: 20, revision: 0,
    updated_at: null, updated_by: null, updated_by_name: null, history: [] },
  { category: 'turnover', label: '换手率', group: 'A股温度计', skill_id: 'news.fixed-turnover',
    version: '0.1.0', enabled: true, customized: false, instructions: '# 内置', bundled_instructions: '# 内置',
    description: '', digest: 'abc', bundled_digest: 'abc', samples: 20, revision: 0,
    updated_at: null, updated_by: null, updated_by_name: null, history: [] },
  { category: 'liquidity', label: '流动性', group: '流动性', skill_id: 'news.fixed-liquidity',
    version: '0.1.0', enabled: true, customized: false, instructions: '# 内置', bundled_instructions: '# 内置',
    description: '', digest: 'abc', bundled_digest: 'abc', samples: 20, revision: 0,
    updated_at: null, updated_by: null, updated_by_name: null, history: [] },
  { category: 'news.event-extraction', label: '事件提取', group: '研究型 Skill',
    skill_id: 'news.event-extraction', version: '0.1.0', enabled: true, editable: false, kind: 'runtime',
    customized: false, instructions: '', bundled_instructions: '', description: '提取事件与断言。',
    digest: 'abc', bundled_digest: 'abc', samples: 0, revision: 0, updated_at: null,
    updated_by: null, updated_by_name: null, history: [], model_profile: 'deterministic-fixture',
    allowed_tools: ['local_fixture'], permissions: { network: false }, quality_gates: [], dependencies: [] },
];
let api: ReturnType<typeof vi.fn>;

async function page(html: string, pathname: string) {
  document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]!;
  return runInNewContext(`(async () => { ${script}\n return true; })()`, {
    $, document, window: { confirm: () => true }, location: { pathname },
    node: (tag: string, text?: string, className?: string) => {
      const value = document.createElement(tag);
      if (text !== undefined) value.textContent = text;
      if (className) value.className = className;
      return value;
    },
    api, login: async () => ({ user: { role: 'admin' } }),
    displayTime: (value: string) => value, toast: vi.fn(),
  });
}

beforeEach(() => {
  api = vi.fn(async (path: string, options?: { method?: string; json?: unknown }) => {
    if (path === '/api/settings/skills') return { skills: structuredClone(skills) };
    if (path.startsWith('/api/settings/skills/')) {
      const category = path.split('/').pop();
      const item = structuredClone(skills.find((entry) => entry.category === category)!);
      if (options?.method === 'PUT') {
        const body = options.json as { revision: number; enabled: boolean; instructions: string };
        item.revision = body.revision + 1; item.enabled = body.enabled;
        item.instructions = body.instructions || item.instructions;
      }
      return { skill: item };
    }
    throw new Error(`unexpected ${path}`);
  });
});

describe('standalone Skill library', () => {
  it('renders grouped small cards that link to detail pages', async () => {
    await page(listHtml, '/skills/');
    await vi.waitFor(() => expect(document.querySelectorAll('.skill-card-link').length).toBe(4));
    expect(document.querySelectorAll('.skill-group').length).toBe(3);
    expect($('.skill-card-link').getAttribute('href')).toBe('/skills/volume/');
    expect($('#skills-status').textContent).toContain('4 项');
  });

  it('opens a runtime research Skill read-only', async () => {
    await page(detailHtml, '/skills/news.event-extraction/');
    await vi.waitFor(() => expect($('#skill-title').textContent).toBe('事件提取'));
    expect(document.querySelector('#skill-detail textarea')).toBeNull();
    expect($('#skill-detail').textContent).toContain('只读');
  });

  it('opens a Skill detail page and saves a team override', async () => {
    await page(detailHtml, '/skills/volume/');
    await vi.waitFor(() => expect($('#skill-title').textContent).toBe('成交额'));
    const area = $('#skill-detail textarea') as HTMLTextAreaElement;
    area.value = '# 团队规范';
    ($('.skill-detail-card .primary') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('/api/settings/skills/volume',
      expect.objectContaining({ method: 'PUT' })));
    expect(JSON.stringify(api.mock.calls.find(([path, options]) => path === '/api/settings/skills/volume'
      && options?.method === 'PUT')![1].json)).toContain('# 团队规范');
  });
});
