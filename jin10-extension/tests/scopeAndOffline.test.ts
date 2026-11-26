import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NewsMaterial } from '@/types/material';
import type { AuthSession } from '@/types/settings';
import type * as ServerApi from '@/services/api/serverApi';
import { STORAGE_KEYS } from '@/shared/constants';
import { LocalMaterialRepository } from '@/services/repository/LocalMaterialRepository';
import { dedupeKeys } from '@/services/materialService';
import { getSyncState, readTeamQueue, updateSyncState } from '@/services/settingsStore';
import { activateScope, createSyncService, deactivateScope } from '@/services/syncService';
import { isActive, lifecycleSignal, onLifecycleAbort, takeOver } from '@/content/lifecycle';
import { isPopupVisible } from '@/content/plus/plusFetcher';

const serverMocks = vi.hoisted(() => ({
  pushMaterials: vi.fn(),
  fetchChanges: vi.fn(),
  missingAssets: vi.fn(async () => [] as string[]),
  uploadAsset: vi.fn(),
}));

vi.mock('@/services/api/serverApi', async (orig) => ({
  ...(await orig<typeof ServerApi>()),
  pushMaterials: (...args: unknown[]) => serverMocks.pushMaterials(...args),
  fetchChanges: (...args: unknown[]) => serverMocks.fetchChanges(...args),
  missingAssets: () => serverMocks.missingAssets(),
  uploadAsset: () => serverMocks.uploadAsset(),
}));

function makeMaterial(id: string, patch: Partial<NewsMaterial> = {}): NewsMaterial {
  return {
    id,
    source: 'jin10',
    source_item_id: `flash-${id}`,
    item_kind: 'flash',
    locked: false,
    tags: [],
    detail_url: null,
    title: `标题-${id}`,
    flash_title: `标题-${id}`,
    content: `内容-${id}`,
    content_raw: `内容-${id}`,
    plus_title: null,
    has_plus_link: false,
    images: [],
    publish_time: null,
    url: 'https://www.jin10.com/',
    raw_html: '<div></div>',
    collect_time: new Date().toISOString(),
    collect_method: 'context_menu',
    fingerprint: id.padEnd(64, '0').slice(0, 64),
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
    updated_at: new Date().toISOString(),
    deleted_at: null,
    schema_version: 1,
    ...patch,
  };
}

function makeSession(teamId: string, apiBase = 'https://plus.tlines.tech'): AuthSession {
  return {
    api_base_url: apiBase,
    access_token: `tok-${teamId}`,
    expires_at: '2099-01-01T00:00:00Z',
    user: { id: `u-${teamId}`, email: `${teamId}@test.com`, name: teamId, role: 'editor', team_id: teamId },
    team: { id: teamId, name: `团队-${teamId}` },
  };
}

describe('extension scope, offline mode, sync recovery & pruning', () => {
  let store: Record<string, unknown>;
  let badgeText: string;

  beforeEach(() => {
    store = {};
    badgeText = '';
    serverMocks.pushMaterials.mockReset().mockResolvedValue([]);
    serverMocks.fetchChanges.mockReset().mockResolvedValue({ items: [], next_cursor: 'cur-1', has_more: false });
    vi.stubGlobal('chrome', {
      runtime: { id: 'ext-test-id', getManifest: () => ({ version: '0.3.7' }) },
      action: {
        setBadgeBackgroundColor: vi.fn(async () => undefined),
        setBadgeText: vi.fn(async ({ text }: { text: string }) => { badgeText = text; }),
      },
      storage: {
        local: {
          get: vi.fn(async (keys?: string | string[] | null) => {
            if (keys === null || keys === undefined) return { ...store };
            const list = Array.isArray(keys) ? keys : [keys];
            return Object.fromEntries(list.map((k) => [k, store[k]]));
          }),
          set: vi.fn(async (patch: Record<string, unknown>) => {
            Object.assign(store, patch);
          }),
          remove: vi.fn(async (keys: string | string[]) => {
            for (const k of Array.isArray(keys) ? keys : [keys]) delete store[k];
          }),
        },
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lists offline captures when logged out, hides expired team items, and migrates offline items on login', async () => {
    const repo = new LocalMaterialRepository();
    const offline = makeMaterial('offline-1');
    await repo.save(offline, dedupeKeys(offline.source_item_id, offline.fingerprint));

    // Logged out with no prior team: offline capture is visible!
    expect((await repo.list()).map((m) => m.id)).toEqual(['offline-1']);

    // Simulate expired session while syncState still points to team-a: never leak team items!
    await updateSyncState({ team_id: 'team-a', server_url: 'https://plus.tlines.tech' });
    expect(await repo.list()).toEqual([]);

    // Reset back to clean logged-out state and log into team-b: offline item migrates to team-b!
    await updateSyncState({ team_id: null, server_url: undefined });
    const sessionB = makeSession('team-b');
    store[STORAGE_KEYS.authSession] = sessionB;
    await activateScope(repo, sessionB);

    const listed = await repo.list();
    expect(listed).toHaveLength(1);
    expect(listed[0]?.id).toBe('offline-1');
    expect(listed[0]?.team_id).toBe('team-b');
  });

  it('archives unsynced team items, clears repo, and resets badge on deactivateScope (LOGOUT)', async () => {
    const repo = new LocalMaterialRepository();
    const sessionA = makeSession('team-a');
    store[STORAGE_KEYS.authSession] = sessionA;
    await activateScope(repo, sessionA);

    const unsynced = makeMaterial('draft-a', { team_id: 'team-a', sync_status: 'local' });
    const synced = makeMaterial('synced-a', { team_id: 'team-a', sync_status: 'synced' });
    await repo.save(unsynced, dedupeKeys(unsynced.source_item_id, unsynced.fingerprint));
    await repo.save(synced, dedupeKeys(synced.source_item_id, synced.fingerprint));

    const { refreshBadge } = await import('@/background/badge');
    await refreshBadge(await repo.list());
    expect(badgeText).toBe('2');

    // Perform LOGOUT flow
    delete store[STORAGE_KEYS.authSession];
    await deactivateScope(repo, sessionA.api_base_url);
    await refreshBadge(await repo.list());

    expect(badgeText).toBe('');
    expect(await repo.listAll()).toEqual([]);
    expect((await getSyncState()).team_id).toBeNull();
    const queued = await readTeamQueue('https://plus.tlines.tech:team-a');
    expect(queued.map((m) => m.id)).toEqual(['draft-a']);
  });

  it('recovers from stale syncing: true when sync_started_at is missing or older than 90s', async () => {
    store[STORAGE_KEYS.syncState] = { cursor: null, team_id: 't1', last_sync_at: null, last_error: null, syncing: true };
    expect((await getSyncState()).syncing).toBe(false);

    const twoMinutesAgo = new Date(Date.now() - 120_000).toISOString();
    store[STORAGE_KEYS.syncState] = {
      cursor: null, team_id: 't1', last_sync_at: null, last_error: null, syncing: true, sync_started_at: twoMinutesAgo,
    };
    expect((await getSyncState()).syncing).toBe(false);

    const recent = new Date(Date.now() - 10_000).toISOString();
    store[STORAGE_KEYS.syncState] = {
      cursor: null, team_id: 't1', last_sync_at: null, last_error: null, syncing: true, sync_started_at: recent,
    };
    expect((await getSyncState()).syncing).toBe(true);
  });

  it('dedupe-checks applyRemote so local captures are not duplicated and synced old IDs are replaced', async () => {
    const repo = new LocalMaterialRepository();
    const localCapture = makeMaterial('local-id', { source_item_id: 'flash-shared', sync_status: 'local' });
    const keys = dedupeKeys(localCapture.source_item_id, localCapture.fingerprint);
    await repo.save(localCapture, keys);

    // Server sends same flash under different team UUID while local capture is still unsynced
    const remoteVersion = makeMaterial('server-id', {
      source_item_id: 'flash-shared',
      fingerprint: localCapture.fingerprint,
      sync_status: 'synced',
      team_id: 'team-a',
    });
    expect(await repo.applyRemote(remoteVersion, keys)).toBe(false);
    expect((await repo.listAll()).map((m) => m.id)).toEqual(['local-id']);

    // Once local capture is already synced under old ID, applyRemote replaces old ID cleanly
    await repo.save({ ...localCapture, sync_status: 'synced' }, keys);
    expect(await repo.applyRemote(remoteVersion, keys)).toBe(true);
    const all = await repo.listAll();
    expect(all.map((m) => m.id)).toEqual(['server-id']);
    expect(await repo.get('local-id')).toBeNull();
  });

  it('prunes >14d synced records and orphan assets while keeping unsynced & queued assets', async () => {
    const repo = new LocalMaterialRepository();
    const sessionA = makeSession('team-a');
    store[STORAGE_KEYS.authSession] = sessionA;
    store[STORAGE_KEYS.syncState] = {
      cursor: 'c0', team_id: 'team-a', server_url: sessionA.api_base_url, last_sync_at: null, last_error: null, syncing: false,
    };

    const twentyDaysAgo = new Date(Date.now() - 20 * 86400_000).toISOString();
    const oldSynced = makeMaterial('old-synced', {
      team_id: 'team-a', sync_status: 'synced', collect_time: twentyDaysAgo, updated_at: twentyDaysAgo,
    });
    const freshSynced = makeMaterial('fresh-synced', {
      team_id: 'team-a', sync_status: 'synced', images: [{ url: 'https://img/synced.jpg', source: 'flash', asset_id: 'orphan-synced-asset' }],
    });
    const localWithAsset = makeMaterial('local-pending', {
      team_id: 'team-a', sync_status: 'local', images: [{ url: 'https://img/local.jpg', source: 'flash', asset_id: 'keep-local-asset' }],
    });
    await repo.resetAll([oldSynced, freshSynced, localWithAsset]);

    store['asset:orphan-synced-asset'] = { id: 'orphan-synced-asset' };
    store['asset:keep-local-asset'] = { id: 'keep-local-asset' };
    store['asset:keep-queued-asset'] = { id: 'keep-queued-asset' };
    store['plus.teamQueue.https://plus.tlines.tech:team-other'] = [
      makeMaterial('queued-other', { images: [{ url: 'https://img/q.jpg', source: 'flash', asset_id: 'keep-queued-asset' }] }),
    ];

    // Push fails to ack localWithAsset? Let's make viewer role so localWithAsset stays 'local' during sync
    store[STORAGE_KEYS.authSession] = { ...sessionA, user: { ...sessionA.user, role: 'viewer' } };
    await createSyncService(repo).sync();

    const remainingIds = (await repo.listAll()).map((m) => m.id);
    expect(remainingIds).not.toContain('old-synced');
    expect(remainingIds).toContain('fresh-synced');
    expect(remainingIds).toContain('local-pending');
    expect(store['asset:orphan-synced-asset']).toBeUndefined();
    expect(store['asset:keep-local-asset']).toBeDefined();
    expect(store['asset:keep-queued-asset']).toBeDefined();
  });

  it('aborts prior content script lifecycle signal on takeover and ignores hidden .iframe-popup', () => {
    takeOver();
    const firstSignal = lifecycleSignal();
    let cleanedUp = false;
    onLifecycleAbort(() => { cleanedUp = true; });
    expect(firstSignal.aborted).toBe(false);
    expect(isActive()).toBe(true);

    // Simulate re-injection takeover
    takeOver();
    expect(firstSignal.aborted).toBe(true);
    expect(cleanedUp).toBe(true);
    expect(lifecycleSignal().aborted).toBe(false);
    expect(isActive()).toBe(true);

    const popup = document.createElement('div');
    popup.className = 'iframe-popup';
    document.body.append(popup);
    // Zero client rects in jsdom -> treated as not visible
    expect(isPopupVisible(popup)).toBe(false);
    popup.getClientRects = () => ([{ width: 400, height: 300 }] as unknown as DOMRectList);
    expect(isPopupVisible(popup)).toBe(true);
    popup.style.display = 'none';
    expect(isPopupVisible(popup)).toBe(false);
    popup.remove();
  });
});
