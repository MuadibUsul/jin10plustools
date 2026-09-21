export type ConnectionState = 'checking' | 'ok' | 'error' | 'inactive';

const COLORS: Record<ConnectionState, string> = {
  checking: 'bg-amber-400 animate-pulse',
  ok: 'bg-emerald-500',
  error: 'bg-rose-500',
  inactive: 'bg-slate-300',
};

interface Props {
  state: ConnectionState;
  label: string;
  detail?: string | undefined;
}

export function StatusDot({ state, label, detail }: Props) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${COLORS[state]}`} />
      <span className="text-slate-700">{label}</span>
      {detail && <span className="truncate text-slate-400">{detail}</span>}
    </div>
  );
}
