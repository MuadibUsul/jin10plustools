import { useMemo, useState } from 'react';
import { DraftCard } from '@/components/DraftCard';
import { MaterialRow } from '@/components/MaterialRow';
import { StatusDot } from '@/components/StatusDot';
import { sendToBackground, sendToTab } from '@/shared/messaging';
import { beijingDayKey } from '@/shared/time';
import { useTeamSession } from '@/components/useTeamSession';
import type { ScanResponse } from '@/types/messages';
import { useConnection } from './hooks/useConnection';
import { useMaterials } from './hooks/useMaterials';

type ScanState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'done'; result: ScanResponse }
  | { status: 'error'; message: string };

const RECENT_LIMIT = 8;


export function App() {
  const conn = useConnection();
  const { materials, loading } = useMaterials();
  const [scan, setScan] = useState<ScanState>({ status: 'idle' });
  const { session, sync } = useTeamSession();

  const { today, mineToday, pending } = useMemo(() => {
    const key = beijingDayKey();
    const todayList = materials.filter((m) => beijingDayKey(new Date(m.collect_time)) === key);
    const isMine = (m: (typeof materials)[number]) =>
      m.sync_status !== 'synced' || (session !== null && m.collected_by.includes(session.user.id));
    return {
      today: todayList,
      mineToday: todayList.filter(isMine).length,
      pending: materials.filter((m) => m.sync_status !== 'synced').length,
    };
  }, [materials, session]);

  const runScan = async () => {
    if (conn.jin10TabId === null) return;
    setScan({ status: 'loading' });
    const res = await sendToTab(conn.jin10TabId, 'SCAN_PAGE', undefined);
    setScan(res.ok ? { status: 'done', result: res.data } : { status: 'error', message: res.error.message });
  };

  return (
    <div className="flex w-[380px] flex-col gap-4 bg-plus-50 p-4">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-plus-500 px-2.5 py-0.5 text-xs font-semibold text-white">PLUS</span>
          <h1 className="text-base font-bold text-plus-900">
            PLUS Research
            <span className="ml-1.5 text-xs font-normal text-slate-400">v{chrome.runtime.getManifest().version}</span>
          </h1>
        </div>
        <button
          type="button"
          onClick={() =>
            session
              ? void chrome.tabs.create({ url: `${session.api_base_url}/` })
              : void chrome.runtime.openOptionsPage()
          }
          className="text-xs text-plus-600 hover:underline"
        >
          选稿网页 →
        </button>
      </header>

      <section className="flex items-end justify-between rounded-lg bg-white p-4">
        <div>
          <p className="text-xs text-slate-500">{session ? '团队今日素材' : '今日收藏'}</p>
          <p className="text-3xl font-bold tabular-nums text-plus-900">
            {loading ? '—' : today.length}
            <span className="ml-1 text-sm font-normal text-slate-500">条</span>
          </p>
          {session && <p className="text-xs text-slate-500">我采集 {mineToday} 条</p>}
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusDot state={conn.background} label="后台服务" />
          <StatusDot state={conn.content} label="金十页面" detail={conn.contentDetail} />
        </div>
      </section>

      <section className="flex items-center justify-between rounded-lg bg-white px-3 py-2 text-xs">
        {session ? (
          <>
            <span className="truncate text-slate-600">
              {session.team.name} · {session.user.name}
              <span className="ml-2 text-slate-400">
                {sync?.syncing
                  ? '同步中…'
                  : sync?.last_error
                    ? '同步失败'
                    : pending > 0
                      ? `待同步 ${pending} 条`
                      : '已同步'}
              </span>
            </span>
            <button
              type="button"
              disabled={sync?.syncing}
              onClick={() => void sendToBackground('SYNC_NOW', undefined)}
              className="shrink-0 text-plus-600 hover:underline disabled:text-slate-300"
            >
              立即同步
            </button>
          </>
        ) : (
          <>
            <span className="text-slate-500">未登录 · 素材仅保存在本机</span>
            <button type="button" onClick={() => void chrome.runtime.openOptionsPage()} className="text-plus-600 hover:underline">
              登录团队
            </button>
          </>
        )}
      </section>

      {conn.content === 'ok' && (
        <p className="text-xs text-slate-500">
          在快讯上右键 →「PLUS Research › 加入日报素材库」，
          或按住 <kbd className="rounded border border-slate-300 bg-white px-1 font-mono">Ctrl</kbd> 点击快讯（加 Shift 同时 AI 分析）
        </p>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-xs font-semibold text-slate-600">最近采集</h2>
        {!loading && materials.length === 0 && (
          <p className="rounded-lg bg-white p-4 text-center text-xs text-slate-400">还没有素材</p>
        )}
        <ul className="flex max-h-[300px] flex-col gap-2 overflow-y-auto">
          {materials.slice(0, RECENT_LIMIT).map((m) => (
            <MaterialRow key={m.id} material={m} />
          ))}
        </ul>
      </section>

      <details className="rounded-lg bg-white p-3 text-xs">
        <summary className="cursor-pointer text-slate-600">页面扫描（预览）</summary>
        <div className="mt-2 flex flex-col gap-2">
          <button
            type="button"
            disabled={conn.jin10TabId === null || scan.status === 'loading'}
            onClick={runScan}
            className="rounded-md bg-plus-500 py-1.5 font-medium text-white hover:bg-plus-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {scan.status === 'loading' ? '扫描中…' : '扫描当前页面'}
          </button>
          {scan.status === 'error' && <p className="text-rose-600">{scan.message}</p>}
          {scan.status === 'done' && (
            <>
              <p className="text-slate-500">
                识别 {scan.result.total} 条，解析成功 {scan.result.drafts.length} 条
              </p>
              <ul className="flex max-h-[240px] flex-col gap-2 overflow-y-auto">
                {scan.result.drafts.map((d, i) => (
                  <DraftCard key={d.source_item_id ?? i} draft={d} />
                ))}
              </ul>
            </>
          )}
        </div>
      </details>
    </div>
  );
}
