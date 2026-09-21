import { beforeEach, describe, expect, it } from 'vitest';
import { createMaterialService, dedupeKeys } from '@/services/materialService';
import type { MaterialRepository } from '@/services/repository/MaterialRepository';
import type { NewsMaterial, NewsMaterialDraft, PlusInterpretation } from '@/types/material';
import type { SaveMaterialRequest } from '@/types/messages';

class MemoryRepository implements MaterialRepository {
  items = new Map<string, NewsMaterial>();
  index = new Map<string, string>();

  async list() {
    return [...this.items.values()].filter((m) => !m.deleted_at);
  }
  async get(id: string) {
    return this.items.get(id) ?? null;
  }
  async findByDedupeKeys(keys: string[]) {
    for (const k of keys) {
      const id = this.index.get(k);
      if (id) return this.items.get(id) ?? null;
    }
    return null;
  }
  async save(m: NewsMaterial, keys: string[]) {
    this.items.set(m.id, m);
    keys.forEach((k) => this.index.set(k, m.id));
  }
  async listAll() {
    return [...this.items.values()];
  }
  async replace(oldId: string, m: NewsMaterial, keys: string[]) {
    this.items.delete(oldId);
    await this.save(m, keys);
  }
  async remove(id: string) {
    this.items.delete(id);
  }
  async softDelete(id: string, at: string) {
    const m = this.items.get(id);
    if (!m) return false;
    this.items.set(id, { ...m, deleted_at: at });
    return true;
  }
}

const draft: NewsMaterialDraft = {
  source: 'jin10',
  source_item_id: 'flash20260921164125290800',
  item_kind: 'flash',
  locked: false,
  tags: [],
  detail_url: null,
  title: '英债供需结构变了',
  flash_title: '晨星：英国国债收益率高企或加大公共财政压力',
  content: '晨星分析师指出……',
  content_raw: '金十数据9月21日讯，晨星分析师指出……',
  plus_title: '英债供需结构变了',
  has_plus_link: true,
  images: [],
  publish_time: '2026-09-21T16:41:25+08:00',
  url: 'https://www.jin10.com/',
  raw_html: '<div></div>',
};

const IMG = 'https://img.jin10.com/misc/26/09/Qth2R_ghf4ISbSp7WS-ja.jpg';

const interpretation: PlusInterpretation = {
  plus_id: '6620',
  title: '英债供需结构变了',
  modules: [
    { kind: 'observation', heading: '重点观察', paragraphs: ['要点一'], images: [{ url: IMG, source: 'plus', asset_id: null }] },
  ],
  fetched_at: '2026-09-21T09:00:00Z',
};

function request(patch: Partial<SaveMaterialRequest> = {}): SaveMaterialRequest {
  return {
    draft,
    interpretation: null,
    interpretation_status: 'failed',
    interpretation_error: '超时',
    images: [],
    method: 'context_menu',
    analyze: false,
    ...patch,
  };
}

let repo: MemoryRepository;
let service: ReturnType<typeof createMaterialService>;

beforeEach(() => {
  repo = new MemoryRepository();
  service = createMaterialService({
    repository: repo,
    putImage: async (u) => `asset-${u.url.length}`,
    now: () => '2026-09-21T09:00:00.000Z',
  });
});

describe('materialService.save', () => {
  it('新素材：生成 ID、指纹、默认 AI 状态', async () => {
    const res = await service.save(request());
    expect(res.outcome).toBe('created');
    expect(res.material.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(res.material.fingerprint).toMatch(/^[0-9a-f]{64}$/);
    expect(res.material.ai_status).toBe('pending');
    expect(res.material.sync_status).toBe('local');
  });

  it('重复采集且无新内容：返回 duplicate，不写库', async () => {
    const first = await service.save(request());
    const second = await service.save(request());
    expect(second.outcome).toBe('duplicate');
    expect(second.material.id).toBe(first.material.id);
    expect(repo.items.size).toBe(1);
  });

  it('上次解读失败、本次成功：补充解读并关联已下载图片', async () => {
    await service.save(request());
    const res = await service.save(
      request({
        interpretation,
        interpretation_status: 'fetched',
        interpretation_error: null,
        images: [{ url: IMG, source: 'plus', mime: 'image/jpeg', size: 10, data_url: 'data:image/jpeg;base64,AA==' }],
      }),
    );
    expect(res.outcome).toBe('updated');
    expect(res.material.interpretation_status).toBe('fetched');
    expect(res.material.interpretation?.modules[0]?.images[0]?.asset_id).toBe(`asset-${IMG.length}`);
  });

  it('已有解读时不被失败结果覆盖', async () => {
    await service.save(request({ interpretation, interpretation_status: 'fetched', interpretation_error: null }));
    const res = await service.save(request());
    expect(res.outcome).toBe('duplicate');
    expect(res.material.interpretation_status).toBe('fetched');
  });

  it('「加入并 AI 分析」会标记已有素材', async () => {
    await service.save(request());
    const res = await service.save(request({ analyze: true }));
    expect(res.outcome).toBe('updated');
    expect(res.material.analyze_requested).toBe(true);
  });

  it('删除后再次采集：沿用原 ID 恢复', async () => {
    const first = await service.save(request());
    await service.delete(first.material.id);
    const again = await service.save(request());
    expect(again.outcome).toBe('created');
    expect(again.material.id).toBe(first.material.id);
    expect(again.material.deleted_at).toBeNull();
  });

  it('无条目 ID 时按内容指纹去重', () => {
    expect(dedupeKeys(null, 'abc')).toEqual(['fp:abc']);
    expect(dedupeKeys('flash1', 'abc')).toEqual(['item:flash1', 'fp:abc']);
  });
});
