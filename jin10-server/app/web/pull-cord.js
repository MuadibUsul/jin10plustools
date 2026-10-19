// 全局拉绳：四个工作台页面共用。点击拉绳，页头下沿掉出复古电脑（/briefs/ 的实时终端会挂进这里），
// 点击电脑以外的任何地方缩回。拉绳与电脑外壳都是矢量/现有组件。
import { computerShell } from '/retro-computer.js';

const PAGE_NAMES = {
  '/daily/': '日报选稿', '/briefs/': '固定快讯',
  '/fresh/': 'Plus新鲜事', '/annotations/': 'Plus注解辅助',
};

function pageName() {
  return PAGE_NAMES[location.pathname.replace(/\/?$/, '/')] || 'PLUS 工作台';
}

const ROPE = `
<svg class="pull-cord-svg" viewBox="0 0 64 84" aria-hidden="true">
  <defs>
    <linearGradient id="pull-cord-wood" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#F0E6CF"/><stop offset=".55" stop-color="#E4D8BE"/><stop offset="1" stop-color="#CDBE9E"/>
    </linearGradient>
    <linearGradient id="pull-cord-plate" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#F4EEE1"/><stop offset="1" stop-color="#D9D0BC"/>
    </linearGradient>
  </defs>
  <g class="pull-cord-swing">
    <rect x="17" y="0" width="30" height="11" rx="4.5" fill="url(#pull-cord-plate)" stroke="#A99C82" stroke-width="1.4"/>
    <circle cx="23" cy="5.5" r="1.5" fill="#8F856F"/><circle cx="41" cy="5.5" r="1.5" fill="#8F856F"/>
    <path d="M30 11 C27.4 26 34.6 38 30 62" fill="none" stroke="#B3A183" stroke-width="3.1" stroke-linecap="round"/>
    <path d="M34 11 C36.6 26 29.4 38 34 62" fill="none" stroke="#D9CDB4" stroke-width="2.1" stroke-linecap="round"/>
    <path d="M30 11 C27.4 26 34.6 38 30 62" fill="none" stroke="#8E7F63" stroke-width=".8" stroke-dasharray="2.4 3.1" stroke-linecap="round" opacity=".75"/>
    <circle cx="32" cy="61" r="4.6" fill="none" stroke="#8E8674" stroke-width="2"/>
    <rect x="20" y="62" width="24" height="17" rx="6" fill="url(#pull-cord-wood)" stroke="#A99C82" stroke-width="1.5"/>
    <path d="M24.5 67.5h15" stroke="#BFAF90" stroke-width="1.3" stroke-linecap="round"/>
    <circle cx="32" cy="73.5" r="1.7" fill="#9C8F75"/>
  </g>
</svg>`;

function style(href, key) {
  // Pages declare these stylesheets in <head>; the fallback injection keeps other hosts working.
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet'; link.href = href;
  if (key === 'pull') link.dataset.pullCord = ''; else link.dataset.retroTerminal = '';
  document.head.append(link);
}

function tilt(panel, computer) {
  panel.onpointermove = (event) => {
    if (event.pointerType !== 'mouse' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = panel.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1));
    const y = Math.max(-1, Math.min(1, (event.clientY - rect.top) / rect.height * 2 - 1));
    computer.style.setProperty('--tilt-x', `${(-y * 3).toFixed(2)}deg`);
    computer.style.setProperty('--tilt-y', `${(x * 4).toFixed(2)}deg`);
  };
  panel.onpointerleave = () => {
    computer.style.setProperty('--tilt-x', '0deg');
    computer.style.setProperty('--tilt-y', '0deg');
  };
}

export function initPullCord() {
  if (document.querySelector('#pull-cord')) return;
  style('/retro-terminal.css', 'retro');
  style('/pull-cord.css', 'pull');
  const dock = document.createElement('div');
  dock.id = 'pull-cord';
  dock.className = 'pull-cord';
  dock.dataset.open = 'false';
  dock.innerHTML = `<button class="pull-cord-rope" type="button" aria-label="拉绳：掉出电脑" aria-expanded="false">${ROPE}</button>
    <div class="pull-cord-slot" id="pull-cord-slot"></div>`;
  document.body.append(dock);
  const slot = dock.querySelector('#pull-cord-slot');
  const idle = computerShell({ pageName: pageName() });
  slot.append(idle.panel);
  tilt(idle.panel, idle.computer);
  const rope = dock.querySelector('.pull-cord-rope');
  const setOpen = (open) => {
    dock.dataset.open = String(open);
    rope.setAttribute('aria-expanded', String(open));
  };
  const insideComputer = (event) => {
    const panel = slot.querySelector('.retro-panel');
    if (!panel) return false;
    // 直播卡片在点击处理里会重绘并移除原节点；用派发时固定的 composedPath 判断，
    // 否则会被误判为“点了电脑外面”而收起。
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
    return path.includes(panel) || panel.contains(event.target);
  };
  document.addEventListener('click', (event) => {
    if (rope.contains(event.target)) { setOpen(dock.dataset.open !== 'true'); return; }
    if (dock.dataset.open === 'true' && !insideComputer(event)) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && dock.dataset.open === 'true') setOpen(false);
  });
  window.__plusPullCord = {
    mount(panel) { slot.replaceChildren(panel); return true; },
    open() { setOpen(true); },
  };
  const header = document.querySelector('body > header');
  const place = () => {
    const top = header ? header.getBoundingClientRect().bottom + window.scrollY : 76;
    dock.style.setProperty('--pull-cord-top', `${Math.round(top)}px`);
  };
  place();
  window.addEventListener('resize', place);
}

initPullCord();
