import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const root = resolve('../jin10-server/app/web');
const shell = readFileSync(join(root, 'retro-computer.js'), 'utf8')
  .replace(/^import[^\n]*\n/gm, '').replace(/^export /gm, '');
const script = shell + '\n' + readFileSync(join(root, 'briefs.js'), 'utf8').replace(/^import[^\n]*\n/gm, '');
const html = readFileSync(join(root, 'briefs.html'), 'utf8');
const $ = (selector: string) => document.querySelector(selector)!;
type Job = { id: string; status: string; created_at: string; progress?: { step: number; events: { at: string; message: string }[] }; error?: string };
const issue = (number = 1) => ({
  id: 'turnover', label: '换手率', category: 'turnover', issue_date: '2026-10-15',
  version: number, confirmed_version: null, jobs: [] as Job[],
  versions: [{ number, title: `版本${number}`, dynamic_body: '正文', fixed_notes: '',
    body: '正文', notes_placement: 'body', created_at: '2026-10-15T01:00:00Z',
    data_date: '2026-10-14', deepseek: { status: 'not_configured' }, warnings: [],
    facts: {}, valid: true, images: [{ name: 'chart.png', url: `/files/${number}` }] }],
});
type Page = { open: (value: ReturnType<typeof issue>, number?: number) => Promise<void>; refresh: () => Promise<void>; pollJobs: () => Promise<void>; runJob: (category: string, action: string) => Promise<void> };
let api: ReturnType<typeof vi.fn>, decode: ReturnType<typeof vi.fn>;
let createUrl: ReturnType<typeof vi.fn>, revokeUrl: ReturnType<typeof vi.fn>;
let rows = [issue()];

async function page(): Promise<Page> {
  return runInNewContext(`(async () => { ${script}\n return { open, refresh, pollJobs, runJob }; })()`, {
    $, document, window, Intl, Date, confirm: () => true,
    URL: { createObjectURL: createUrl, revokeObjectURL: revokeUrl },
    node: (tag: string, text?: string, className?: string) => {
      const value = document.createElement(tag);
      if (text !== undefined) value.textContent = text;
      if (className) value.className = className;
      return value;
    },
    copyDate: (value: string) => value, displayTime: (value: string) => value, api, login: async () => ({ user: { role: 'editor' } }),
    download: vi.fn(), copy: vi.fn(), toast: vi.fn(), setInterval: vi.fn(),
  }) as Promise<Page>;
}

beforeEach(() => {
  document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]!;
  rows = [issue()];
  api = vi.fn(async (path: string) => {
    if (path === '/api/briefs/categories') return { categories: [{ id: 'turnover', label: '换手率' }] };
    if (path.startsWith('/api/briefs/issues?')) return { items: structuredClone(rows) };
    return { blob: async () => new Blob(['image']) };
  });
  decode = vi.fn(async () => undefined);
  Object.defineProperty(HTMLImageElement.prototype, 'decode', { configurable: true, value: decode });
  let serial = 0;
  createUrl = vi.fn(() => `blob:chart-${++serial}`);
  revokeUrl = vi.fn();
});
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

describe('fixed brief image refresh', () => {
  it('shows the current processing stage and keeps the spinner node stable during polling', async () => {
    rows[0]!.jobs = [{ id: 'job-1', status: 'running', created_at: '2026-10-15T01:00:00Z', progress: { step: 2, events: [{ at: '2026-10-15T01:00:05Z', message: '正在读取PE原始来源' }] } }];
    const ui = await page(); await ui.open(issue());
    const spinner = $('.job-indicator.spinning'), image = $('#images img');
    expect($('#terminal-feed').textContent).toContain('正在取数');
    expect($('#terminal-state').textContent).toBe('RUNNING 01');
    const firstLine = $('#terminal-feed').firstElementChild;
    api.mockImplementationOnce(async () => structuredClone(rows[0]!.jobs[0]));
    await ui.pollJobs();
    expect($('.job-indicator.spinning')).toBe(spinner);
    expect($('#images img')).toBe(image);
    expect($('#terminal-feed').firstElementChild).toBe(firstLine);
    expect($('#terminal-feed').children).toHaveLength(1);
    rows[0]!.jobs[0]!.status = 'failed'; rows[0]!.jobs[0]!.error = '来源暂未更新';
    api.mockImplementationOnce(async () => rows[0]!.jobs[0]);
    await ui.pollJobs();
    expect(document.querySelector('.job-indicator.spinning')).toBeNull();
    expect($('.brief-card-body').textContent).toContain('来源暂未更新');
    expect($('#terminal-feed').textContent).toContain('生成未完成');
    expect($('#terminal-state').textContent).toBe('TASK NEEDS REVIEW');
  });

  it('disables duplicate submissions immediately while the request is pending', async () => {
    const ui = await page();
    let release!: (value: unknown) => void;
    api.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const submit = ui.runJob('turnover', 'produce');
    expect($('.job-indicator.spinning')).toBeTruthy();
    expect(($('.brief-card-body .toolbar button:last-child') as HTMLButtonElement).disabled).toBe(true);
    const count = api.mock.calls.length;
    await ui.runJob('turnover', 'produce');
    expect(api.mock.calls.length).toBe(count);
    release({ status: 'queued' }); await submit;
  });

  it('streams human-readable stages without exposing internal logs or a category switcher', async () => {
    rows[0]!.jobs = [{ id: 'job-1', status: 'running', created_at: '2026-10-15T01:00:00Z', progress: { step: 3, events: Array.from({ length: 9 }, (_, i) => ({ at: '2026-10-15T01:00:00Z', message: `阶段动态${i}` })) } }];
    await page();
    expect(document.querySelector('#process-category')).toBeNull();
    expect(document.querySelector('#job-process select')).toBeNull();
    expect($('#job-process').textContent).not.toContain('阶段动态');
    expect($('#terminal-feed').textContent).toContain('正在制图');
    expect(document.querySelector('.computer-frame')).not.toBeNull();
    expect($('#job-process').parentElement!.id).toBe('brief-workspace');
    expect($('#cards').contains($('#job-process'))).toBe(false);
    expect($('#job-process').classList.contains('brief-card')).toBe(false);
    expect(document.querySelector('.retro-heading')).toBeNull();
    expect(document.querySelector('.retro-caption')).toBeNull();
  });

  it('tilts the standalone computer on mouse movement and respects reduced motion', async () => {
    await page();
    const panel = $('#job-process') as HTMLElement, computer = $('.retro-computer') as HTMLElement;
    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 300, height: 300 } as DOMRect);
    const media = vi.fn(() => ({ matches: false }));
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: media });
    panel.onpointermove!.call(panel, { pointerType: 'mouse', clientX: 300, clientY: 0 } as PointerEvent);
    expect(computer.style.getPropertyValue('--tilt-y')).toBe('4.00deg');
    expect(computer.style.getPropertyValue('--tilt-x')).toBe('3.00deg');
    panel.onpointerleave!.call(panel, {} as PointerEvent);
    expect(computer.style.getPropertyValue('--tilt-y')).toBe('0deg');
    media.mockReturnValue({ matches: true });
    panel.onpointermove!.call(panel, { pointerType: 'mouse', clientX: 300, clientY: 0 } as PointerEvent);
    expect(computer.style.getPropertyValue('--tilt-y')).toBe('0deg');
  });

  it('shows multiple categories in the same terminal and appends changed stages', async () => {
    const valuation = { ...issue(), id: 'valuation', category: 'valuation' };
    rows = [issue(), valuation];
    rows[0]!.jobs = [{ id: 'turnover-job', status: 'running', created_at: '2026-10-15T01:00:00Z', progress: { step: 2, events: [] } }];
    valuation.jobs = [{ id: 'valuation-job', status: 'running', created_at: '2026-10-15T01:00:01Z', progress: { step: 3, events: [] } }];
    api.mockImplementationOnce(async () => ({ categories: [{ id: 'turnover', label: '换手率' }, { id: 'valuation', label: '市场估值指标' }] }));
    const ui = await page();
    expect($('#terminal-feed').textContent).toContain('[换手率]');
    expect($('#terminal-feed').textContent).toContain('[估值]');
    expect($('#terminal-state').textContent).toBe('RUNNING 02');
    rows[0]!.jobs[0]!.progress!.step = 4;
    await ui.refresh();
    expect($('#terminal-feed').children).toHaveLength(3);
    expect($('#terminal-feed').lastElementChild!.textContent).toContain('整理文案');
    await ui.refresh();
    expect($('#terminal-feed').children).toHaveLength(3);
    ($('#day') as HTMLInputElement).value = '2026-10-14'; rows = [];
    await ui.refresh();
    expect($('#terminal-feed').children).toHaveLength(0);
    expect($('#terminal-state').textContent).toBe('STANDBY');
  });

  it('bounds the rolling feed while preserving successful task status', async () => {
    const ui = await page();
    for (let i = 0; i < 55; i++) {
      rows[0]!.jobs = [{ id: `job-${i}`, status: 'succeeded', created_at: '2026-10-15T01:00:00Z' }];
      await ui.refresh();
    }
    expect($('#terminal-feed').children).toHaveLength(50);
    expect($('#terminal-feed').lastElementChild!.textContent).toContain('稿件已保存');
    expect($('#terminal-state').textContent).toBe('SAVED 01 / IDLE');
  });

  it('does not pull readers away from older terminal entries', async () => {
    rows[0]!.jobs = [{ id: 'job-1', status: 'running', created_at: '2026-10-15T01:00:00Z', progress: { step: 2, events: [] } }];
    const ui = await page(), feed = $('#terminal-feed');
    Object.defineProperties(feed, { scrollHeight: { configurable: true, value: 300 }, clientHeight: { configurable: true, value: 80 } });
    feed.scrollTop = 20;
    rows[0]!.jobs[0]!.progress!.step = 3; await ui.refresh();
    expect(feed.scrollTop).toBe(20);
    feed.scrollTop = 220;
    rows[0]!.jobs[0]!.progress!.step = 4; await ui.refresh();
    expect(feed.scrollTop).toBe(300);
  });
  it('keeps the same image node and makes no image requests on unchanged polls', async () => {
    const ui = await page(); await ui.open(issue());
    const image = $('#images img');
    api.mockClear();
    for (let i = 0; i < 4; i++) await ui.refresh();
    expect($('#images img')).toBe(image);
    expect(api.mock.calls.every(([path]) => String(path).startsWith('/api/briefs/issues?'))).toBe(true);
    expect(revokeUrl).not.toHaveBeenCalled();
  });

  it('retains the old preview until the replacement finishes decoding', async () => {
    const ui = await page(); await ui.open(issue());
    const old = $('#images img');
    let ready!: () => void;
    decode.mockImplementationOnce(() => new Promise<void>((resolve) => { ready = resolve; }));
    const changing = ui.open(issue(2));
    await vi.waitFor(() => expect(decode).toHaveBeenCalledTimes(2));
    expect($('#images img')).toBe(old);
    expect(revokeUrl).not.toHaveBeenCalled();
    ready(); await changing;
    expect($('#images img')).not.toBe(old);
    expect(revokeUrl).toHaveBeenCalledWith('blob:chart-1');
  });

  it('preserves the old image if the next image fails to load', async () => {
    const ui = await page(); await ui.open(issue());
    const old = $('#images img');
    decode.mockRejectedValueOnce(new Error('decode failed'));
    await ui.open(issue(2));
    expect($('#images img')).toBe(old);
    expect($('#message').textContent).toContain('原预览保留');
    expect(revokeUrl).toHaveBeenCalledWith('blob:chart-2');
    expect(revokeUrl).not.toHaveBeenCalledWith('blob:chart-1');
  });

  it('discards a slow response after a newer version has been selected', async () => {
    const ui = await page(); await ui.open(issue());
    let release!: (value: unknown) => void;
    api.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const slow = ui.open(issue(2));
    await ui.open(issue(3));
    const latest = $('#images img');
    release({ blob: async () => new Blob(['old']) }); await slow;
    expect($('#images img')).toBe(latest);
    expect((latest as HTMLImageElement).src).toBe('blob:chart-2');
    expect(createUrl).toHaveBeenCalledTimes(2);
  });

  it('cancels a pending replacement when returning to the displayed version', async () => {
    const ui = await page(); await ui.open(issue());
    const old = $('#images img');
    let release!: (value: unknown) => void;
    api.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const slow = ui.open(issue(2));
    await ui.open(issue());
    release({ blob: async () => new Blob(['old']) }); await slow;
    expect($('#images img')).toBe(old);
    expect(createUrl).toHaveBeenCalledTimes(1);
  });

  it('releases displayed and pending blobs when closing the detail', async () => {
    const ui = await page(); await ui.open(issue());
    let ready!: () => void;
    decode.mockImplementationOnce(() => new Promise<void>((resolve) => { ready = resolve; }));
    const changing = ui.open(issue(2));
    await vi.waitFor(() => expect(decode).toHaveBeenCalledTimes(2));
    ($('#close-detail') as HTMLButtonElement).click();
    ready(); await changing;
    expect($('#images').children).toHaveLength(0);
    expect(revokeUrl.mock.calls.map(([url]) => url).sort()).toEqual(['blob:chart-1', 'blob:chart-2']);
  });
});
