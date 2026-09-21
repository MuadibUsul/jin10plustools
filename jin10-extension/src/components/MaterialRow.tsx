import type { InterpretationStatus, NewsMaterial } from '@/types/material';
import { getSection } from '@/types/section';

const INTERPRETATION_CHIP: Record<InterpretationStatus, { label: string; cls: string } | null> = {
  none: null,
  pending: { label: '待读解读', cls: 'bg-slate-100 text-slate-500' },
  fetched: { label: 'PLUS 解读', cls: 'bg-plus-100 text-plus-600' },
  manual: { label: '手写解读', cls: 'bg-plus-100 text-plus-600' },
  no_permission: { label: '解读无权限', cls: 'bg-amber-50 text-amber-600' },
  failed: { label: '解读失败', cls: 'bg-rose-50 text-rose-600' },
};

const AI_LABEL: Record<NewsMaterial['ai_status'], string> = {
  pending: '待分析',
  analyzing: '分析中',
  done: '已分析',
  failed: '分析失败',
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' });
}

export function MaterialRow({ material }: { material: NewsMaterial }) {
  const interp = INTERPRETATION_CHIP[material.interpretation_status];
  const imageCount =
    material.images.length + (material.interpretation?.modules.reduce((n, m) => n + m.images.length, 0) ?? 0);
  return (
    <li className="rounded-lg border border-slate-100 bg-white p-3 shadow-sm">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 text-xs tabular-nums text-slate-400">
          {formatTime(material.publish_time ?? material.collect_time)}
        </span>
        <p className="line-clamp-2 text-sm font-medium text-slate-800">{material.title}</p>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
        {interp && <span className={`rounded px-1.5 py-0.5 ${interp.cls}`}>{interp.label}</span>}
        {imageCount > 0 && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">配图 {imageCount}</span>
        )}
        {material.collected_by.length > 1 && (
          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-600">{material.collected_by.length} 人收藏</span>
        )}
        {material.sync_status !== 'synced' && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-400">待同步</span>
        )}
        {material.locked && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">VIP 锁定</span>}
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
          {material.category ? getSection(material.category).label : '未分类'} · {AI_LABEL[material.ai_status]}
        </span>
      </div>
    </li>
  );
}
