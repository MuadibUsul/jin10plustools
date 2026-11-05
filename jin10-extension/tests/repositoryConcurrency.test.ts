import {beforeEach,expect,it,vi} from 'vitest';
import {LocalMaterialRepository} from '@/services/repository/LocalMaterialRepository';
import type {NewsMaterial} from '@/types/material';

beforeEach(()=>{
  const values:Record<string,unknown>={};
  vi.stubGlobal('chrome',{storage:{local:{
    get:async(keys:string|string[])=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,values[k]])),
    set:async(p:Record<string,unknown>)=>{Object.assign(values,p);},
    remove:async(key:string)=>{delete values[key];},
  }}});
});

it('checks pending deletion inside the same queue that applies a remote record',async()=>{
  const repo=new LocalMaterialRepository();
  const old={id:'a',team_id:'t1',sync_status:'synced',deleted_at:null} as NewsMaterial;
  await repo.save(old,[]);
  const deleted=repo.softDelete('a','2026-11-05T01:00:00Z');
  const remote=repo.applyRemote(old,[]);
  expect(await deleted).toBe(true);
  expect(await remote).toBe(false);
  expect((await repo.get('a'))?.deleted_at).toBe('2026-11-05T01:00:00Z');
});
