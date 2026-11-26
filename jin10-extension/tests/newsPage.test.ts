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
    document, location: { pathname: '/fresh/', origin: 'https://plus.tlines.tech' }, $: (selector: string) => document.querySelector(selector), node, URL,
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

it.each(['analysis', 'annotation'])('blocks javascript: and data: URIs in %s source links', (kind) => {
  const malicious = ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'https://safe.example/news'];
  if (kind === 'analysis') {
    view.showAnalysis({
      title: '核查',
      body: '正文',
      sources: malicious,
      external_research: {
        status: 'completed',
        sources: [{ id: 's1', title: '恶意来源', url: 'javascript:evil()' }],
        checks: [{ claim_id: 'c1', claim: '断言', status: 'external_match' }],
        matches: [{ claim_id: 'c1', source_id: 's1', stance: 'supports', quote: '引文' }],
      },
    }, 'event');
  } else {
    view.showAnnotationResult({ title: '核查', body: '正文', sources: malicious }, { text: '原文', source_url: null });
  }
  const anchors = [...document.querySelectorAll<HTMLAnchorElement>('#news-dialog-content a')];
  expect(anchors).toHaveLength(1);
  expect(anchors[0]!.href).toBe('https://safe.example/news');
  expect(document.querySelector('#news-dialog-content')!.textContent).toContain('javascript:alert(1)');
});
