import { useEffect, useState, type FormEvent } from 'react';
import { useTeamSession } from '@/components/useTeamSession';
import { sendToBackground } from '@/shared/messaging';
import { getLastApiBaseUrl, normalizeApiBaseUrl } from '@/services/settingsStore';

/** 团队服务端默认地址 */
const DEFAULT_API_BASE_URL = 'https://plus.tlines.tech';

const ROLE_LABEL = { admin: '管理员', editor: '编辑', viewer: '只读' } as const;

function isInsecureRemote(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' && !['localhost', '127.0.0.1'].includes(u.hostname);
  } catch {
    return false;
  }
}

export function OptionsApp() {
  const { session, sync } = useTeamSession();
  const [baseUrl, setBaseUrl] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getLastApiBaseUrl().then((url) => setBaseUrl((prev) => prev || url || DEFAULT_API_BASE_URL));
  }, []);

  const onLogin = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    let origin: string;
    try {
      origin = normalizeApiBaseUrl(baseUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : '服务端地址无效');
      return;
    }
    setBusy(true);
    try {
      // 默认服务端已在安装时授权；其他地址才需要单独申请（浏览器会弹出确认框）
      const origins = [`${origin}/*`];
      if (!(await chrome.permissions.contains({ origins }))) {
        const granted = await chrome.permissions.request({ origins });
        if (!granted) throw new Error('需要允许访问团队服务端地址才能同步');
      }
      const timeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('连接服务端超时，请检查网络或服务端地址')), 35_000),
      );
      const res = await Promise.race([sendToBackground('LOGIN', { api_base_url: origin, email, password }), timeout]);
      if (!res.ok) throw new Error(res.error.message);
      setPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onLogout = () => void sendToBackground('LOGOUT', undefined);
  const onSync = () => void sendToBackground('SYNC_NOW', undefined);

  return (
    <div className="min-h-screen bg-plus-50 py-12">
      <main className="mx-auto flex max-w-md flex-col gap-6 rounded-2xl bg-white p-8 shadow-sm">
        <header>
          <h1 className="text-xl font-bold text-plus-900">PLUS Research 设置</h1>
          <p className="mt-1 text-sm text-slate-500">登录团队账号后，采集的素材会自动同步到团队素材库。</p>
        </header>

        {session ? (
          <section className="flex flex-col gap-3 text-sm">
            <div className="rounded-lg bg-plus-50 p-4">
              <p className="font-medium text-plus-900">
                {session.team.name} · {session.user.name}（{ROLE_LABEL[session.user.role]}）
              </p>
              <p className="mt-1 text-xs text-slate-500">{session.user.email}</p>
              <p className="mt-1 text-xs text-slate-500">服务端：{session.api_base_url}</p>
            </div>
            <p className="text-xs text-slate-500">
              {sync?.syncing
                ? '同步中…'
                : sync?.last_sync_at
                  ? `上次同步：${new Date(sync.last_sync_at).toLocaleString('zh-CN')}`
                  : '尚未同步'}
            </p>
            {sync?.last_error && <p className="text-xs text-rose-600">同步失败：{sync.last_error}</p>}
            <div className="flex gap-2">
              <button type="button" onClick={onSync} className="flex-1 rounded-lg bg-plus-500 py-2 font-medium text-white hover:bg-plus-600">
                立即同步
              </button>
              <button type="button" onClick={onLogout} className="rounded-lg border border-slate-200 px-4 py-2 text-slate-600 hover:bg-slate-50">
                退出登录
              </button>
            </div>
          </section>
        ) : (
          <form onSubmit={onLogin} className="flex flex-col gap-3 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-slate-600">团队服务端地址</span>
              <input
                required
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://plus.example.com"
                className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-plus-500"
              />
              {isInsecureRemote(baseUrl) && (
                <span className="text-xs text-amber-600">非 HTTPS 地址仅建议在内网使用</span>
              )}
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-slate-600">邮箱</span>
              <input
                required
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-plus-500"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-slate-600">密码</span>
              <input
                required
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-plus-500"
              />
            </label>
            {error && <p className="text-xs text-rose-600">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-plus-500 py-2 font-medium text-white hover:bg-plus-600 disabled:bg-slate-300"
            >
              {busy ? '登录中…' : '登录'}
            </button>
            <p className="text-xs text-slate-400">账号由团队管理员在服务端创建。未登录时素材只保存在本机。</p>
          </form>
        )}
      </main>
    </div>
  );
}
