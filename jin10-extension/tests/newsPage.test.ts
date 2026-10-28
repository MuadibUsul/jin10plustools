import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { beforeEach, expect, it } from 'vitest';

const source = readFileSync(resolve('../jin10-server/app/web/news.js'), 'utf8')
  .replace(/^import[^\n]*\n/, '').split("$('#news-form').onsubmit")[0];
let view: { showAnalysis: (value: object, id: string) => void; showAnnotationResult: (value: object) => void };

beforeEach(() => {
  document.body.innerHTML = '<dialog id="news-dialog"><h2 id="news-dialog-title"></h2><p id="news-dialog-status"></p><div id="news-dialog-content"></div></dialog>';
  const node = (tag: string, text?: string) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  view = runInNewContext(`${source}\n({showAnalysis, showAnnotationResult})`, {
    document, location: { pathname: '/fresh/' }, $: (selector: string) => document.querySelector(selector), node,
  });
});

it.each(['analysis', 'annotation'])('mounts usable source links in the %s inspector', (kind) => {
  const data = { title: '核查', body: '正文', sources: ['https://www.example.com/source'] };
  if (kind === 'analysis') view.showAnalysis(data, 'event');
  else view.showAnnotationResult(data);
  const link = document.querySelector<HTMLAnchorElement>('#news-dialog-content p a')!;
  expect(link.href).toBe(data.sources[0]);
  expect(link.rel).toBe('noopener noreferrer');
  expect(document.querySelector('#news-dialog-content')!.textContent).not.toContain('undefined');
});

it.each(['analysis', 'annotation'])('shows advisory quality status in the %s inspector', (kind) => {
  const data = { title: '核查', body: '正文', quality_review: {
    status: 'review', issues: ['文字质量提示：请确认空泛表述。'],
  }, warnings: ['文字质量提示：请确认空泛表述。'] };
  if (kind === 'analysis') view.showAnalysis(data, 'event');
  else view.showAnnotationResult(data);
  const text = document.querySelector('#news-dialog-content')!.textContent!;
  expect(text).toContain('文字质量检查：发现需编辑复核的表述');
  expect(text).toContain('不代表事实已核实');
});
