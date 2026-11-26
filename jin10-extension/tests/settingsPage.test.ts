import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const root = resolve('../jin10-server/app/web');
const html = readFileSync(join(root, 'settings.html'), 'utf8');
const script = readFileSync(join(root, 'settings.js'), 'utf8').replace(/^import[^\n]*\n/gm, '');

const baseSettings = () => ({
  revision: 1,
  can_edit: true,
  can_admin: true,
  live_url: '',
  live_urls: {},
  live_catalog: { categories: [], channels: [] },
  categories: [{ id: 'volume', label: '沪深两市成交额', notes: '默认注释' }],
  note_revisions: [],
  brief_templates: {},
  schedule_enabled: true,
  data_collection_enabled: true,
  ai_provider: 'deepseek',
  editorial: { mode: 'sample', packs: [] },
  ai: {
    jev: { configured: true, source: 'server', enabled: true, model: 'jev-latest' },
    deepseek: { configured: true, source: 'server', enabled: true, model: 'deepseek-chat' },
    chat2api: { configured: true, source: 'server', enabled: true, model: 'gpt-4o-mini' },
    openai: { configured: false, source: 'none', enabled: false, model: 'gpt-5.6-sol' },
  },
});

describe('settings page regressions', () => {
  let api: ReturnType<typeof vi.fn>;
  let currentHash = '#feedback';

  beforeEach(() => {
    document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]!;
    currentHash = '#feedback';
    api = vi.fn(async (path: string, opts?: { method?: string }) => {
      if (path === '/api/settings') return baseSettings();
      if (path === '/api/feedback') return { items: [] };
      if (path === '/api/feedback/agent-token' && (!opts?.method || opts.method === 'GET')) return { enabled: false };
      if (path === '/api/feedback/agent-token' && opts?.method === 'POST') return { token: 'mcp-secret-token-123' };
      if (path === '/api/chat2api') return { configured: true, online: true, expiry_available: true, earliest_expiry: null, expired_tokens: 0, tokens_count: 2 };
      throw new Error(`unexpected ${path}`);
    });
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  async function boot() {
    const loc = { origin: 'https://plus.tlines.tech', pathname: '/settings/', hash: currentHash };
    const win = { addEventListener: vi.fn() };
    await runInNewContext(`(async () => { ${script}\n })()`, {
      $: (sel: string) => document.querySelector(sel),
      node: (tag: string, text?: string, className?: string) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        if (className) el.className = className;
        return el;
      },
      api,
      login: async () => ({ user: { name: '管理员', role: 'admin' } }),
      displayTime: (v: string) => v,
      copyDate: (v: string) => v,
      download: vi.fn(),
      toast: vi.fn(),
      document,
      window: win,
      location: loc,
      history: { replaceState: (_s: unknown, _t: string, url: string) => { loc.hash = url; } },
      URL,
      URLSearchParams,
      FormData,
      Date,
      localStorage: window.localStorage,
      fetch: vi.fn(),
      confirm: () => true,
      setInterval: vi.fn(() => 1),
      clearInterval: vi.fn(),
    });
  }

  it('retains newly issued Feedback MCP token while on #feedback and clears it when leaving', async () => {
    await boot();
    const issueBtn = document.querySelector<HTMLButtonElement>('#feedback-token-issue')!;
    const tokenField = document.querySelector<HTMLElement>('#feedback-token-field')!;
    const tokenInput = document.querySelector<HTMLInputElement>('#feedback-token')!;
    await vi.waitFor(() => expect(issueBtn.disabled).toBe(false));

    issueBtn.click();
    await vi.waitFor(() => expect(tokenField.hidden).toBe(false));
    expect(tokenInput.value).toBe('mcp-secret-token-123');

    // Leaving #feedback panel clears the displayed secret token
    document.querySelector<HTMLAnchorElement>('[data-panel="team"]')!.click();
    expect(tokenInput.value).toBe('');
    expect(tokenField.hidden).toBe(true);
  });

  it('rejects blank custom AI model names during form validation', async () => {
    currentHash = '#ai';
    await boot();
    const select = document.querySelector<HTMLSelectElement>('#model-deepseek')!;
    const custom = document.querySelector<HTMLInputElement>('#model-custom-deepseek')!;
    select.value = '__custom__';
    select.dispatchEvent(new Event('change'));
    custom.value = '   ';

    document.querySelector<HTMLFormElement>('#settings-form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(document.querySelector('#settings-message')!.textContent).toContain('自定义模型名称');
    expect(api).not.toHaveBeenCalledWith('/api/settings', expect.objectContaining({ method: 'PUT' }));
  });
});
