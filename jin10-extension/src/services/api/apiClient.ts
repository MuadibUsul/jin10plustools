import type { AuthSession } from '@/types/settings';
import { AppException } from '@/shared/errors';
import { clearSession, getSession } from '../settingsStore';

const TIMEOUT_MS = 30_000;

interface RequestOptions {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  /** 不带登录令牌（仅登录接口） */
  baseUrl?: string;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch {
    // 非 JSON 响应
  }
  return `服务端错误（HTTP ${res.status}）`;
}

/**
 * 调用团队服务端。自动附带登录令牌；401 时清除会话，调用方提示重新登录。
 * 只在扩展页面 / background 中使用（已获得服务端地址的可选主机权限，不受 CORS 限制）。
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let session: AuthSession | null = null;
  let baseUrl = options.baseUrl;
  if (!baseUrl) {
    session = await getSession();
    if (!session) throw new AppException('UNAUTHORIZED', '未登录团队账号');
    baseUrl = session.api_base_url;
  }

  const headers: Record<string, string> = { ...options.headers };
  if (session) headers.Authorization = `Bearer ${session.access_token}`;
  let body = options.body;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.json);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, { method: options.method ?? 'GET', headers, body, signal: controller.signal });
  } catch (err) {
    throw new AppException('NETWORK', `无法连接服务端：${err instanceof Error ? err.message : String(err)}`);
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401 && session) {
    await clearSession();
    throw new AppException('UNAUTHORIZED', await errorMessage(res));
  }
  if (!res.ok) throw new AppException(res.status === 401 ? 'UNAUTHORIZED' : 'NETWORK', await errorMessage(res));
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T;
  const type = res.headers.get('content-type') ?? '';
  return (type.includes('application/json') ? await res.json() : undefined) as T;
}
