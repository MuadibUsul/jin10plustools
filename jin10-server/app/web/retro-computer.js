// 复古电脑终端外壳：/briefs/ 的实时终端与拉绳里的空闲电脑共用同一套 DOM。
import { node } from '/workspace.js';

export function computerShell({ live = false, pageName = '' } = {}) {
  const panel = node('aside', undefined, 'retro-panel');
  if (live) {
    panel.id = 'job-process'; panel.tabIndex = -1;
    panel.setAttribute('aria-label', '进程动态：所有栏目的实时处理过程');
  } else {
    panel.dataset.idle = 'true';
  }
  const floating = node('div', undefined, 'computer-float');
  const computer = node('div', undefined, 'retro-computer'), screen = node('div', undefined, 'terminal-screen');
  const rain = node('div', undefined, 'matrix-rain'); rain.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 7; i++) {
    const column = node('span', i % 2 ? '01ｱ7ｳﾈ1ﾄ0ｶ9ﾐｦ10ｷ4ﾘ1' : '10ﾘ3ｴ0ﾑ1ﾂ8ﾃ01ｦ5ﾖﾆ0');
    column.style.setProperty('--column', i); rain.append(column);
  }
  const screenHead = node('div', undefined, 'terminal-head');
  screenHead.append(node('span', 'PLUS://TASKS'), node('span', live ? '● ONLINE' : `● ${pageName}`));
  const feed = node('div', undefined, 'terminal-feed');
  feed.tabIndex = 0; feed.setAttribute('role', 'log');
  feed.setAttribute('aria-label', '所有栏目处理动态');
  feed.setAttribute('aria-live', 'polite'); feed.setAttribute('aria-relevant', 'additions');
  if (live) feed.id = 'terminal-feed';
  const empty = node('p', '系统就绪，等待任务…', 'terminal-empty');
  if (live) empty.id = 'terminal-empty';
  const status = node('div', undefined, 'terminal-prompt'), state = node('span', 'STANDBY');
  if (live) { state.id = 'terminal-state'; state.setAttribute('role', 'status'); }
  status.append(node('span', '›'), state, node('span', '▌', 'terminal-cursor'));
  screen.append(rain, screenHead, empty, feed, status);
  const frame = node('img', undefined, 'computer-frame');
  frame.src = '/assets/retro-computer-frame.png'; frame.alt = '';
  frame.setAttribute('aria-hidden', 'true'); frame.width = frame.height = 1254;
  const light = node('span', '', 'computer-power-light'); light.setAttribute('aria-hidden', 'true');
  const mouse = node('button', '', 'computer-mouse'); mouse.type = 'button';
  mouse.setAttribute('aria-label', live ? '直播' : '小电脑');
  computer.append(screen, frame, light, mouse); floating.append(computer); panel.append(floating);
  return { panel, floating, computer, screen, frame, light, mouse, feed, empty, state };
}
