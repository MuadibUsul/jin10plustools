import type { NewsMaterialDraft } from '@/types/material';

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return iso.slice(11, 16);
}

export function DraftCard({ draft }: { draft: NewsMaterialDraft }) {
  return (
    <li className="rounded-lg border border-slate-100 bg-white p-3 shadow-sm">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 text-xs tabular-nums text-slate-400">
          {formatTime(draft.publish_time)}
        </span>
        <p className="line-clamp-2 text-sm font-medium text-slate-800">{draft.title}</p>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
        {draft.has_plus_link && (
          <span className="rounded bg-plus-100 px-1.5 py-0.5 text-plus-600">PLUS 注解</span>
        )}
        {draft.images.length > 0 && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
            配图 {draft.images.length}
          </span>
        )}
        {!draft.flash_title && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">无标题快讯</span>
        )}
      </div>
    </li>
  );
}
