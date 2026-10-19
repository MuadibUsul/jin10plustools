import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const source = readFileSync(resolve('../jin10-server/app/web/generation-process.js'),'utf8').replace(/^import[^\n]*\n/,'').replaceAll('export ','');
let show: (options: {title: string; load: () => Promise<unknown>}) => Promise<void>;
const response = (title='本期标题') => ({job:{status:'succeeded',issue_date:'2026-10-16',progress:{events:[{at:'2026-10-16T01:15:00Z',message:'已读取原始来源'}]}},details:{action:'生成稿件',available:true,reused:false,sources:[{label:'官方数据',url:'https://example.org/data'}],inputs:[{label:'当期值',value:'60',unit:'%'}],calculations:['60 ÷ 100 × 100 = 60%'],assembly:['读取同一批已校验数据。'],evidence:[],facts:{value:'60'},result:{title,body:'计算后的正文',images:['图.png'],ai_status:'ok',ai_model:'model',warnings:[]}}});
beforeEach(()=>{
  document.body.replaceChildren();document.head.replaceChildren();
  Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.open=true;}});
  Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.open=false;this.dispatchEvent(new Event('close'));}});
  const node=(tag:string,text?:string,className?:string)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
  show=runInNewContext(`${source}\nshowGenerationProcess`,{document,node,copyDate:(s:string)=>String(s||'').replace('2026-10-16','2026年10月16日'),displayTime:()=> '2026年10月16日 9:15'});
});
afterEach(()=>{vi.restoreAllMocks();});

it('opens an independent inspector and fetches only on open or manual refresh',async()=>{
 const load=vi.fn(async()=>response());await show({title:'换手率',load});
 const modal=document.querySelector('dialog')!;
 expect(modal.open).toBe(true);expect(load).toHaveBeenCalledTimes(1);
 expect(modal.textContent).toContain('数据从哪里来');expect(modal.textContent).toContain('60 ÷ 100');
 expect(modal.textContent).toContain('2026年10月16日 9:15');expect(modal.textContent).toContain('本期标题');
 (modal.querySelector('.generation-toolbar button') as HTMLButtonElement).click();
 for(let i=0;i<5;i++)await Promise.resolve();
 expect(load).toHaveBeenCalledTimes(2);
 (modal.querySelector('.generation-head button') as HTMLButtonElement).click();expect(modal.open).toBe(false);
});
it('ignores a previous task response after switching tasks',async()=>{
 let release:(value:unknown)=>void=()=>{};
 const earlier=show({title:'前一个',load:()=>new Promise(resolve=>{release=resolve;})});
 await show({title:'后一个',load:async()=>response('新的结果')});
 release(response('过期结果'));await earlier;
 expect(document.querySelector('dialog')!.textContent).toContain('新的结果');
 expect(document.querySelector('dialog')!.textContent).not.toContain('过期结果');
});
it('renders untrusted copy as text and explains reuse without changing the computer',async()=>{
 const computer=document.createElement('aside');computer.id='job-process';computer.textContent='原有进程动态';document.body.append(computer);
 const data=response('<img src=x onerror=alert(1)>');data.details.reused=true;
 await show({title:'检查',load:async()=>data});
 expect(document.querySelector('dialog img')).toBeNull();
 expect(document.querySelector('dialog')!.textContent).toContain('复用已有同日期快照');
 expect(computer.textContent).toBe('原有进程动态');
});
it('keeps failures visible and does not claim an unfinished result is complete',async()=>{
 await show({title:'检查',load:async()=>{throw new Error('网络暂不可用');}});
 expect(document.querySelector('[role="status"]')!.textContent).toContain('网络暂不可用');
 const data=response();data.details.available=false;data.job.status='running';
 await show({title:'检查',load:async()=>data});
 expect(document.querySelector('dialog')!.textContent).toContain('本次尚未保存完整结果');
 expect(document.querySelector('dialog')!.textContent).not.toContain('规则标题：本期标题');
});
