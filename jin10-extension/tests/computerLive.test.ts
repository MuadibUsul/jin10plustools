import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const source = readFileSync(resolve('../jin10-server/app/web/computer-live.js'), 'utf8').replace(/^import[^\n]*\n/, '').replaceAll('export ', '');
let api: ReturnType<typeof vi.fn>;
let toggleLive: (screen: HTMLElement) => Promise<void>;
let maxPlayers: number, held: number;
const state = () => ({ allowed: true, server_time: new Date().toISOString(), closes_at: '2026-10-16T15:00:00Z' });
const categories = ['news','markets','cities','nature'].map(id => ({ id, label: id, image: `/assets/live/${id}.webp`, hint:'请选择' }));
const channels = categories.flatMap(c => [1,2].map(i => ({ id:`${c.id}-${i}`,category:c.id,name:`频道${i}`,available:true })));
const flush = async () => { for (let i=0;i<20;i++) await Promise.resolve(); };
const screen = () => { document.body.innerHTML='<aside class="retro-panel" data-active="false"><div class="retro-computer"><div class="terminal-screen"><p class="terminal-line">处理中</p></div></div></aside>'; return document.querySelector('.terminal-screen') as HTMLElement; };
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-16T01:00:00Z')); maxPlayers=held=0;
  window.localStorage.clear();
  Object.defineProperty(window.navigator,'locks',{configurable:true,value:{request:vi.fn(async (_name, options, callback) => {
    if (options.signal.aborted) return;
    held++;maxPlayers=Math.max(maxPlayers,held);
    try { await callback({}); } finally { held--; }
  })}});
  api=vi.fn(async (path:string) => path==='/api/live/catalog' ? { ...state(),categories,channels } : path==='/api/live/state' ? state() : { ...state(),name:'频道',url:'https://youtu.be/abcdefghijk' });
  const node=(tag:string,text?:string,className?:string)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(className)el.className=className;return el;};
  const result=runInNewContext(`${source}\n({toggleLive,embedUrl})`,{URL,window,document,api,node,AbortController,performance:{now:()=>Date.now()}});
  toggleLive=result.toggleLive;
});
afterEach(async()=>{window.dispatchEvent(new Event('plus-brief-generation'));await flush();document.body.replaceChildren();vi.useRealTimers();vi.restoreAllMocks();});
it('opens categories without any player, wheel-selects a category, then plays only the selected channel',async()=>{
  const el=screen(),row=el.firstChild;await toggleLive(el);
  expect(el.querySelector('iframe,video')).toBeNull();expect(el.textContent).toContain('news');
  el.parentElement!.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true}));
  expect(el.textContent).toContain('markets');
  (el.querySelector('.live-card') as HTMLButtonElement).click();
  expect(el.textContent).toContain('频道1');expect(el.querySelector('iframe')).toBeNull();
  (el.querySelector('.live-card') as HTMLButtonElement).click();await flush();
  expect(el.querySelectorAll('iframe')).toHaveLength(1);
  expect(el.querySelector('iframe')?.src).toContain('youtube-nocookie.com/embed/abcdefghijk');
  await toggleLive(el);await flush();expect(el.querySelector('iframe')).toBeNull();expect(el.firstChild).toBe(row);expect(held).toBe(0);
});
it('destroys playback before returning to cards and selecting another channel',async()=>{
 const el=screen();await toggleLive(el);(el.querySelector('.live-card') as HTMLButtonElement).click();(el.querySelector('.live-card') as HTMLButtonElement).click();await flush();
 (el.querySelector('[aria-label="返回"]') as HTMLButtonElement).click();await flush();expect(el.querySelector('iframe')).toBeNull();
 el.parentElement!.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true}));(el.querySelector('.live-card') as HTMLButtonElement).click();await flush();expect(el.querySelectorAll('iframe')).toHaveLength(1);expect(maxPlayers).toBe(1);
});
it('cancels a delayed play response when generation starts',async()=>{
 const el=screen();await toggleLive(el);(el.querySelector('.live-card') as HTMLButtonElement).click();let release:(v:unknown)=>void=()=>{};
 api.mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));(el.querySelector('.live-card') as HTMLButtonElement).click();
 window.dispatchEvent(new Event('plus-brief-generation'));release({...state(),url:'https://youtu.be/abcdefghijk'});await flush();expect(el.querySelector('.computer-live')).toBeNull();expect(held).toBe(0);
});
it('closes at Beijing 23:00 and blocks opening outside the allowed period',async()=>{
 const el=screen();api.mockResolvedValueOnce({...state(),closes_at:new Date(Date.now()+1000).toISOString(),categories,channels});await toggleLive(el);
 await vi.advanceTimersByTimeAsync(1000);expect(el.querySelector('.computer-live')).toBeNull();
 api.mockResolvedValueOnce({...state(),allowed:false,reason:'北京时间 8:00—23:00',categories,channels});await toggleLive(el);expect(el.textContent).toContain('8:00—23:00');expect(el.querySelector('iframe')).toBeNull();
});
it('stops on another tab taking ownership and on another tab starting generation',async()=>{
 const el=screen();await toggleLive(el);(el.querySelector('.live-card') as HTMLButtonElement).click();(el.querySelector('.live-card') as HTMLButtonElement).click();await flush();
 window.localStorage.setItem('plus.live.claim','other');window.dispatchEvent(new StorageEvent('storage',{key:'plus.live.claim',newValue:'other'}));await flush();expect(el.querySelector('iframe')).toBeNull();expect(held).toBe(0);
 await toggleLive(el);window.dispatchEvent(new StorageEvent('storage',{key:'plus.live.stop',newValue:'generation'}));expect(el.querySelector('.computer-live')).toBeNull();
});
it('formats visible dates in Chinese with an unpadded hour',()=>{
 const script=readFileSync(resolve('../jin10-server/app/web/workspace.js'),'utf8').replaceAll('export ','');
 const {copyDate,displayTime}=runInNewContext(`${script}\n({copyDate,displayTime})`,{Intl,Date});
 expect(copyDate('2026-10-16 09:15')).toBe('2026年10月16日 9:15');expect(displayTime('2026-10-16T01:15:00Z')).toBe('2026年10月16日 9:15');
});
it('destroys the HLS engine and detaches video when generation starts',async()=>{
 const destroy=vi.fn();
 class HlsMock { static Events={ERROR:'error'};static isSupported(){return true;}on(){}loadSource(){}attachMedia(){}destroy(){destroy();} }
 Object.defineProperty(window,'Hls',{configurable:true,value:HlsMock});
 vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{});
 const el=screen();await toggleLive(el);(el.querySelector('.live-card') as HTMLButtonElement).click();
 api.mockResolvedValueOnce({...state(),name:'HLS',url:'https://example.com/live.m3u8'});(el.querySelector('.live-card') as HTMLButtonElement).click();await flush();expect(el.querySelectorAll('video')).toHaveLength(1);
 window.dispatchEvent(new Event('plus-brief-generation'));await flush();expect(destroy).toHaveBeenCalledOnce();expect(el.querySelector('video')).toBeNull();expect(held).toBe(0);
});
it('keeps channel cards free of source and login annotations',async()=>{
 const el=screen();api.mockResolvedValueOnce({...state(),categories,channels:channels.map(c=>({...c,access:'youtube'}))});await toggleLive(el);(el.querySelector('.live-card') as HTMLButtonElement).click();
 expect(el.querySelector('.live-card')?.textContent).toContain('点击收看');expect(el.querySelector('.live-card')?.textContent).not.toMatch(/免登录|YouTube|验证|待配置/);
});
