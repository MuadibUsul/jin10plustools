import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { beforeEach, expect, it } from 'vitest';

const source = readFileSync(resolve('../jin10-server/app/web/news.js'), 'utf8')
  .replace(/^import[^\n]*\n/, '').split("$('#news-form').onsubmit")[0];
const page = readFileSync(resolve('../jin10-server/app/web/news.html'), 'utf8');
let view: {
  showAnalysis: (value: object, id: string) => void;
  showAnnotationResult: (value: object, input: { text: string; source_url: string | null }) => void;
};

beforeEach(() => {
  document.body.innerHTML = new DOMParser().parseFromString(page, 'text/html').body.innerHTML;
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
  else view.showAnnotationResult(data, { text: '原文', source_url: null });
  const link = document.querySelector<HTMLAnchorElement>('#news-dialog-content p a')!;
  expect(link.href).toBe(data.sources[0]);
  expect(link.rel).toBe('noopener noreferrer');
  expect(document.querySelector('#news-dialog-content')!.textContent).not.toContain('undefined');
});
