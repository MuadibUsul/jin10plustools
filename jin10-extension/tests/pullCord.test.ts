import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { afterEach, expect, it } from 'vitest';

const root = resolve('../jin10-server/app/web');
const shell = readFileSync(resolve(root, 'retro-computer.js'), 'utf8')
  .replace(/^import[^\n]*\n/gm, '').replace(/^export /gm, '');
const source = readFileSync(resolve(root, 'pull-cord.js'), 'utf8')
  .replace(/^import[^\n]*\n/gm, '').replace(/^export /gm, '');

function boot(pathname = '/briefs/') {
  document.head.innerHTML = '';
  document.body.innerHTML = '<header><nav>PLUS</nav></header><main>content</main>';
  runInNewContext(`${shell}\n${source}\ninitPullCord();`, {
    document, window, location: { pathname },
    node: (tag: string, text?: string, className?: string) => {
      const value = document.createElement(tag);
      if (text !== undefined) value.textContent = text;
      if (className) value.className = className;
      return value;
    },
  });
  return document.querySelector('#pull-cord') as HTMLElement;
}

const click = (element: Element) => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

afterEach(() => { document.head.replaceChildren(); document.body.replaceChildren(); });

it('drops the retro computer when the cord is pulled and retracts on outside clicks', () => {
  const dock = boot();
  const rope = dock.querySelector('.pull-cord-rope') as HTMLElement;
  const computer = dock.querySelector('.retro-computer') as Element;
  expect(dock.dataset.open).toBe('false');
  expect(document.querySelector('link[data-pull-cord]')).toBeTruthy();
  expect(document.querySelector('link[data-retro-terminal]')).toBeTruthy();
  expect(computer).toBeTruthy();
  rope.click();
  expect(dock.dataset.open).toBe('true');
  expect(rope.getAttribute('aria-expanded')).toBe('true');
  click(computer);
  expect(dock.dataset.open).toBe('true');
  click(document.querySelector('main')!);
  expect(dock.dataset.open).toBe('false');
});

it('keeps the computer open when a live card re-renders itself during the click', () => {
  const dock = boot();
  (dock.querySelector('.pull-cord-rope') as HTMLElement).click();
  const card = document.createElement('button');
  dock.querySelector('.terminal-screen')!.append(card);
  card.addEventListener('click', () => card.remove());
  click(card);
  expect(dock.dataset.open).toBe('true');
  click(document.querySelector('main')!);
  expect(dock.dataset.open).toBe('false');
});

it('labels the idle screen with the current page and hides the live hotspot', () => {
  const dock = boot('/daily/');
  expect(dock.querySelector('.terminal-head')!.textContent).toContain('日报选稿');
  expect((dock.querySelector('.computer-mouse') as HTMLElement).closest('[data-idle]')).toBeTruthy();
});
