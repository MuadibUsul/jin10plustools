import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { NewsMaterial } from '@/types/material';
import type { AuthSession, SyncState } from '@/types/settings';
import type * as ServerApi from '@/services/api/serverApi';
import type { ServerMaterial, SyncResult } from '@/services/api/serverApi';
import { acknowledgedMaterial } from '@/services/repository/syncAcknowledgement';

// ---- 模拟服务端接口与本地设置 ----
const api = {
  pushMaterials: vi.fn<(m: NewsMaterial[]) => Promise<SyncResult[]>>(),
  fetchChanges: vi.fn(),
  missingAssets: vi.fn(async () => [] as string[]),
  uploadAsset: vi.fn(),
};
vi.mock('@/services/api/serverApi', async (orig) => ({
  ...(await orig<typeof ServerApi>()),
  pushMaterials: (m: NewsMaterial[]) => api.pushMaterials(m),
  fetchChanges: (c: string | null) => api.fetchChanges(c),
  missingAssets: () => api.missingAssets(),
  uploadAsset: () => api.uploadAsset(),
}));

let session: AuthSession | null;
let syncState: SyncState;
const teamQueues = new Map<string,NewsMaterial[]>();
vi.mock('@/services/settingsStore', () => ({
  getSession: async () => session,
  getSyncState: async () => syncState,
  updateSyncState: async (p: Partial<SyncState>) => (syncState = { ...syncState, ...p }),
  saveTeamQueue: async (scope:string,items:NewsMaterial[])=>{teamQueues.set(scope,items);},
  readTeamQueue: async (scope:string)=>teamQueues.get(scope) || [],
}));
vi.mock('@/services/assetStore', () => ({ getImage: async () => null }));

const { createSyncService } = await import('@/services/syncService');
const { fromServer } = await import('@/services/api/serverApi');

class Repo {
  items = new Map<string, NewsMaterial>();
  async listAll() {
    return [...this.items.values()];
  }
  async list() {
    return (await this.listAll()).filter((m) => !m.deleted_at);
  }
  async get(id: string) {
    return this.items.get(id) ?? null;
  }
  async findByDedupeKeys() {
    return null;
  }
  async save(m: NewsMaterial) {
    this.items.set(m.id, m);
  }
  async applyRemote(m: NewsMaterial) {
    if (this.items.get(m.id)?.sync_status==='local') return false;
    this.items.set(m.id,m);return true;
  }
  async replace(oldId: string, m: NewsMaterial) {
    this.items.delete(oldId);
    this.items.set(m.id, m);
  }
  async acknowledge(sent: NewsMaterial, received: NewsMaterial | null) {
    const next = acknowledgedMaterial(sent, await this.get(sent.id), received);
    this.items.delete(sent.id);
    if (next) this.items.set(next.id, next);
  }
  async remove(id: string) {
    this.items.delete(id);
  }
  async softDelete() {
    return true;
  }
}

function local(id: string, patch: Partial<NewsMaterial> = {}): NewsMaterial {
  return {
    id,
    source: 'jin10',
    source_item_id: `flash-${id}`,
    item_kind: 'flash',
    locked: false,
    tags: [],
    detail_url: null,
    title: id,
    flash_title: id,
    content: '正文',
    content_raw: '正文',
    plus_title: null,
    has_plus_link: false,
    images: [],
    publish_time: null,
    url: 'https://www.jin10.com/',
    raw_html: '<div></div>',
    collect_time: '2026-09-21T09:00:00Z',
    collect_method: 'context_menu',
    fingerprint: 'f'.repeat(64),
    interpretation_status: 'none',
    interpretation: null,
    analyze_requested: false,
    category: null,
    importance_score: 0,
    final_score: 0,
    level: null,
    ai_status: 'pending',
    ai_reason: null,
    ai_error: null,
    collector_id: null,
    collected_by: [],
    team_id: null,
    sync_status: 'local',
    updated_at: '2026-09-21T09:00:00Z',
    deleted_at: null,
    schema_version: 1,
    ...patch,
  };
}

function server(id: string, patch: Partial<ServerMaterial> = {}): ServerMaterial {
  const rest: Partial<NewsMaterial> = { ...local(id) };
  delete rest.collector_id;
  delete rest.sync_status;
  return { ...(rest as ServerMaterial), created_by: 'u2', collected_by: ['u1', 'u2'], team_id: 't1', ...patch };
}

let repo: Repo;
beforeEach(() => {
  teamQueues.clear();
  repo = new Repo();
  session = {
    api_base_url: 'https://plus.test',
    access_token: 'x',
    expires_at: '2099-01-01T00:00:00Z',
    user: { id: 'u1', email: 'a@b', name: '甲', role: 'editor', team_id: 't1' },
    team: { id: 't1', name: '研究组' },
  };
  syncState = { cursor: null, team_id: 't1', last_sync_at: null, last_error: null, syncing: false };
  api.pushMaterials.mockReset();
  api.fetchChanges.mockReset().mockResolvedValue({ items: [], next_cursor: 'c1', has_more: false });
});

describe('syncService', () => {
  it('discards an old-team response and cursor after an account change', async()=>{
    api.fetchChanges.mockImplementation(async()=>{
      session={...session!,user:{...session!.user,id:'u9',team_id:'t2'},team:{id:'t2',name:'other-team'}};
      return {items:[server('private-team-one')],next_cursor:'private-cursor',has_more:false};
    });
    const report=await createSyncService(repo).sync();
    expect(report.skipped).toBe('session_changed');
    expect(repo.items.has('private-team-one')).toBe(false);
    expect(syncState.cursor).toBeNull();
  });
  it('stops subsequent push batches and pull when login changes during push',async()=>{
    for(let i=0;i<51;i++)await repo.save(local(String(i)));
    api.pushMaterials.mockImplementation(async(batch)=>{
      session=null;return batch.map(m=>({client_id:m.id,outcome:'created' as const,material:server(m.id)}));
    });
    const report=await createSyncService(repo).sync();
    expect(report.skipped).toBe('session_changed');
    expect(api.pushMaterials).toHaveBeenCalledTimes(1);
    expect(api.fetchChanges).not.toHaveBeenCalled();
    expect(repo.items.get('0')?.sync_status).toBe('local');
  });
  it('retains an edit queued before a pulled response is applied',async()=>{
    await repo.save(local('race',{sync_status:'synced',team_id:'t1'}));
    const apply=repo.applyRemote.bind(repo);
    repo.applyRemote=async(m)=>{
      await repo.save(local(m.id,{team_id:'t1',title:'NEW EDIT'}));
      return apply(m);
    };
    api.fetchChanges.mockResolvedValue({items:[server('race',{title:'OLD'})],next_cursor:'c2',has_more:false});
    await createSyncService(repo).sync();
    expect(repo.items.get('race')?.title).toBe('NEW EDIT');
    expect(repo.items.get('race')?.sync_status).toBe('local');
  });
  it('preserves a local deletion made while push is in flight', async () => {
    await repo.save(local('a'));
    api.pushMaterials.mockImplementation(async () => {
      await repo.save(local('a', { deleted_at: '2026-11-03T01:00:00Z' }));
      return [{client_id:'a',outcome:'created',material:server('a')}];
    });
    await createSyncService(repo).sync();
    expect(repo.items.get('a')?.deleted_at).toBe('2026-11-03T01:00:00Z');
    expect(repo.items.get('a')?.sync_status).toBe('local');
  });
  it('未登录时跳过', async () => {
    session = null;
    const report = await createSyncService(repo).sync();
    expect(report.skipped).toBe('not_logged_in');
    expect(api.pushMaterials).not.toHaveBeenCalled();
  });

  it('推送后用服务端版本替换本地记录（同事已收藏时采用团队 ID）', async () => {
    await repo.save(local('mine'));
    api.pushMaterials.mockResolvedValue([{ client_id: 'mine', outcome: 'merged', material: server('team-id') }]);
    const report = await createSyncService(repo).sync();
    expect(report.pushed).toBe(1);
    expect(repo.items.has('mine')).toBe(false);
    expect(repo.items.get('team-id')?.sync_status).toBe('synced');
    expect(repo.items.get('team-id')?.collected_by).toEqual(['u1', 'u2']);
    expect(syncState.cursor).toBe('c1');
  });

  it('拉取不覆盖本地尚未推送的修改', async () => {
    await repo.save(local('a', { sync_status: 'synced', title: '旧' }));
    await repo.save(local('b', { title: '本地改过' }));
    api.pushMaterials.mockResolvedValue([]); // 推送失败前的场景：不返回结果
    api.fetchChanges.mockResolvedValue({
      items: [server('a', { title: '新' }), server('b', { title: '服务端' }), server('c')],
      next_cursor: 'c2',
      has_more: false,
    });
    const report = await createSyncService(repo).sync();
    expect(report.pulled).toBe(2);
    expect(repo.items.get('a')?.title).toBe('新');
    expect(repo.items.get('b')?.title).toBe('本地改过');
    expect(repo.items.get('c')).toEqual(fromServer(server('c')));
  });

  it('切换团队隔离离线队列，切回原团队才恢复上传', async () => {
    syncState = { ...syncState, team_id: 'old-team', cursor: 'old' };
    await repo.save(local('old', { sync_status: 'synced' }));
    await repo.save(local('unsent'));
    api.pushMaterials.mockResolvedValue([{ client_id: 'unsent', outcome: 'created', material: server('unsent') }]);
    await createSyncService(repo).sync();
    expect(repo.items.has('old')).toBe(false);
    expect(repo.items.has('unsent')).toBe(false);
    expect(api.pushMaterials).not.toHaveBeenCalled();
    expect(teamQueues.get('https://plus.test:old-team')?.map(m=>m.id)).toEqual(['unsent']);
    expect(api.fetchChanges).toHaveBeenCalledWith(null);
    session={...session!,team:{id:'old-team',name:'旧团队'}};
    await createSyncService(repo).sync();
    expect(api.pushMaterials).toHaveBeenCalledTimes(1);
    expect(api.pushMaterials.mock.calls[0]![0].map(m=>m.id)).toEqual(['unsent']);
  });

  it('只读账号不推送，只拉取', async () => {
    session = { ...session!, user: { ...session!.user, role: 'viewer' } };
    await repo.save(local('x'));
    await createSyncService(repo).sync();
    expect(api.pushMaterials).not.toHaveBeenCalled();
    expect(api.fetchChanges).toHaveBeenCalled();
  });

  it('失败时记录错误', async () => {
    api.fetchChanges.mockRejectedValue(new Error('网络断开'));
    await expect(createSyncService(repo).sync()).rejects.toThrow('网络断开');
    expect(syncState.last_error).toBe('网络断开');
    expect(syncState.syncing).toBe(false);
  });
});
