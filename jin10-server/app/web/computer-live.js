import { api, node } from '/workspace.js';

let session = null;
export function embedUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.origin === window.location.origin) throw new Error('请使用外部 HTTPS 播放器链接。');
  const host = url.hostname.replace(/^www\./, '');
  if (['youtube.com', 'm.youtube.com', 'youtu.be', 'youtube-nocookie.com'].includes(host)) {
    const id = host === 'youtu.be' ? url.pathname.split('/')[1] : url.searchParams.get('v') || url.pathname.match(/^\/(?:live|embed|shorts)\/([\w-]{11})\/?$/)?.[1];
    if (!id || !/^[\w-]{11}$/.test(id)) throw new Error('请使用具体的 YouTube 直播分享链接或播放器链接。');
    return `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&playsinline=1&enablejsapi=1&origin=${encodeURIComponent(window.location.origin)}`;
  }
  return url.href;
}
function stopPlayer(s) {
  s.sequence++;
  if (document.fullscreenElement === s.body) document.exitFullscreen?.().catch(() => {});
  if (s.fullscreen) s.fullscreen.hidden = true;
  s.abort?.abort(); s.abort = null;
  s.hls?.destroy(); s.hls = null;
  const video = s.live.querySelector('video');
  if (video) { video.pause(); video.removeAttribute('src'); video.load(); video.remove(); }
  const frame = s.live.querySelector('iframe');
  if (frame) { frame.src = 'about:blank'; frame.remove(); }
  s.release?.(); s.release = null; s.claim = null;
}
function close(s) {
  if (s.closed) return;
  s.closed = true; stopPlayer(s);
  window.clearInterval(s.timer); window.clearInterval(s.policyTimer);
  s.wheelTarget.removeEventListener('wheel', s.wheel);
  s.live.remove(); s.screen.classList.remove('show-live');
  if (session === s) session = null;
}
function policy(s, data) {
  s.expires = performance.now() + Math.max(0, Date.parse(data.closes_at) - Date.parse(data.server_time));
  return data.allowed;
}
function notice(s, text) { s.body.replaceChildren(node('p', text, 'live-notice')); }
function button(text, label, action) { const b = node('button', text); b.type = 'button'; b.setAttribute('aria-label', label); b.onclick = action; return b; }
function menu(s, category = null, index = 0) {
  stopPlayer(s); s.category = category; s.index = index; s.mode = 'menu';
  const rank = item => !item.available ? 3 : item.access === 'direct' ? 0 : item.access === 'embedded' ? 1 : 2;
  const entries = category ? s.data.channels.filter(c => c.category === category.id).sort((a,b) => rank(a)-rank(b)) : s.data.categories;
  s.entries = entries;
  s.title.textContent = category?.label || '直播频道';
  s.body.replaceChildren();
  const deck = node('div', undefined, 'live-deck'); deck.tabIndex = 0; deck.setAttribute('aria-label', category ? '选择直播' : '选择类别');
  const card = node('button', undefined, 'live-card'); card.type = 'button';
  const item = entries[index], art = category || item;
  const image = node('img'); image.src = art.image; image.alt = ''; image.draggable = false;
  const caption = node('span', undefined, 'live-card-caption');
  caption.append(node('strong', item.name || item.label), node('small', category ? '点击收看' : item.hint));
  card.append(image, caption); card.onclick = () => category ? start(s, item) : menu(s, item);
  const navigation = node('div', undefined, 'live-navigation');
  const move = delta => menu(s, category, (s.index + delta + entries.length) % entries.length);
  navigation.append(button('‹', '上一张', () => move(-1)), node('span', `${index + 1} / ${entries.length} · 滚轮选择`), button('›', '下一张', () => move(1)));
  deck.append(card); s.body.append(deck, navigation);
  deck.onkeydown = e => { if (['ArrowDown','ArrowRight','ArrowUp','ArrowLeft'].includes(e.key)) { e.preventDefault(); move(['ArrowDown','ArrowRight'].includes(e.key) ? 1 : -1); } };
}
let hlsLoading;
function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  if (!hlsLoading) hlsLoading = new Promise((resolve, reject) => {
    const script = node('script'); script.src = '/assets/live/hls.light.min.js';
    script.onload = () => resolve(window.Hls);
    script.onerror = () => { script.remove(); hlsLoading = null; reject(new Error('播放器加载失败，请重试。')); };
    document.head.append(script);
  });
  return hlsLoading;
}
async function mount(s, data, sequence) {
  const src = embedUrl(data.url);
  if (/\.m3u8(?:[?#]|$)/i.test(src)) {
    const video = node('video'); video.controls = true; video.autoplay = true; video.playsInline = true;
    // Muted autoplay avoids a lost user activation after the server policy check. Users can unmute in controls.
    video.muted = true; video.setAttribute('aria-label', data.name);
    if (video.canPlayType('application/vnd.apple.mpegurl')) { s.body.replaceChildren(video); video.src = src; }
    else {
      const Hls = await loadHls();
      if (s.closed || sequence !== s.sequence) return;
      if (!Hls.isSupported()) throw new Error('当前浏览器不支持此直播格式。');
      s.body.replaceChildren(video);
      const hls = new Hls({ enableWorker: true, maxBufferLength: 10, maxMaxBufferLength: 20 }); s.hls = hls;
      hls.on(Hls.Events.ERROR, (_, error) => { if (error.fatal && !s.closed && sequence === s.sequence) { stopPlayer(s); notice(s, '直播暂不可用，请返回选择其他频道。'); } });
      hls.loadSource(src); hls.attachMedia(video);
    }
    video.play().catch(() => { /* Native controls allow explicit play. */ });
  } else {
    const frame = node('iframe'); frame.title = data.name; frame.allow = 'autoplay; encrypted-media; fullscreen'; frame.allowFullscreen = true;
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation');
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    frame.src = src; s.body.replaceChildren(frame);
  }
  s.fullscreen.hidden = false;
}
async function start(s, item) {
  stopPlayer(s); s.mode = 'player'; s.title.textContent = item.name;
  if (!item.available) { notice(s, item.note || '暂无获准嵌入的播放器地址。'); return; }
  notice(s, '正在接入…'); const sequence = s.sequence;
  try {
    const data = await api(`/api/live/play/${encodeURIComponent(item.id)}`);
    if (s.closed || sequence !== s.sequence) return;
    if (!policy(s, data)) { close(s); return; }
    if (!window.navigator.locks) throw new Error('请使用支持单路播放保护的新版浏览器。');
    s.claim = window.crypto.randomUUID(); window.localStorage.setItem('plus.live.claim', s.claim);
    s.abort = new AbortController();
    // The exclusive lock stays held until iframe removal / HLS destruction has completed.
    window.navigator.locks.request('plus-live-player', { signal: s.abort.signal }, async () => {
      if (s.closed || sequence !== s.sequence || window.localStorage.getItem('plus.live.claim') !== s.claim) return;
      const held = new Promise(resolve => { s.release = resolve; });
      try { await mount(s, data, sequence); await held; }
      catch (error) { if (!s.closed && sequence === s.sequence) { stopPlayer(s); notice(s, error.message); } }
    }).catch(error => { if (error.name !== 'AbortError' && !s.closed && sequence === s.sequence) { stopPlayer(s); notice(s, error.message); } });
  } catch (error) { if (!s.closed && sequence === s.sequence) { stopPlayer(s); notice(s, error.message); } }
}
export async function toggleLive(screen) {
  if (session?.screen === screen) { close(session); return; }
  if (session) close(session);
  if (screen.closest('.retro-panel')?.dataset.active === 'true') return;
  const live = node('div', undefined, 'computer-live'), bar = node('div', undefined, 'live-bar');
  const title = node('span', '直播频道'), body = node('div', undefined, 'live-body');
  const s = { screen, live, body, title, sequence: 0, closed: false, mode: 'loading', expires: Infinity };
  s.fullscreen = button('⛶', '全屏直播', () => { body.requestFullscreen?.().catch(() => {}); }); s.fullscreen.hidden = true;
  session = s;
  bar.append(button('‹', '返回', () => { if (s.mode === 'player') menu(s, s.category, s.index); else if (s.category) menu(s); else close(s); }), title, s.fullscreen, button('×', '返回处理动态', () => close(s)));
  live.append(bar, body); screen.append(live); screen.classList.add('show-live'); notice(s, '正在载入频道…');
  s.wheelTarget = screen.closest('.retro-computer') || screen;
  let wheelTotal = 0, lastWheel = -Infinity;
  s.wheel = e => {
    if (s.mode !== 'menu' || !e.deltaY || e.ctrlKey) return;
    e.preventDefault(); if (performance.now() - lastWheel < 220) return;
    wheelTotal += e.deltaY * (e.deltaMode === 1 ? 16 : 1);
    if (Math.abs(wheelTotal) < 30) return;
    const delta = Math.sign(wheelTotal); wheelTotal = 0; lastWheel = performance.now();
    menu(s, s.category, (s.index + delta + s.entries.length) % s.entries.length);
  };
  s.wheelTarget.addEventListener('wheel', s.wheel, { passive: false });
  try {
    s.data = await api('/api/live/catalog');
    if (s.closed) return;
    if (!policy(s, s.data)) { notice(s, s.data.reason); return; }
    menu(s);
    s.timer = window.setInterval(() => { if (performance.now() >= s.expires) close(s); }, 500);
    let checking = false;
    s.policyTimer = window.setInterval(async () => {
      if (checking || s.closed) return; checking = true;
      try { const data = await api('/api/live/state'); if (!s.closed && !policy(s, data)) close(s); }
      catch { close(s); } finally { checking = false; }
    }, 8000);
  } catch (error) { if (!s.closed) notice(s, error.message); }
}
window.addEventListener('plus-brief-generation', () => { if (session) close(session); });
window.addEventListener('storage', e => {
  if (session && (e.key === 'plus.live.stop' || (e.key === 'plus.live.claim' && session.claim && window.localStorage.getItem('plus.live.claim') !== session.claim))) close(session);
});
window.addEventListener('pagehide', () => { if (session) close(session); });
document.addEventListener('visibilitychange', () => { if (document.hidden && session) close(session); });
