export type Role = 'admin' | 'editor' | 'viewer';

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  team_id: string;
}

export interface SessionTeam {
  id: string;
  name: string;
}

/** 登录会话（只保存服务端签发的令牌，不保存密码） */
export interface AuthSession {
  api_base_url: string;
  access_token: string;
  expires_at: string;
  user: SessionUser;
  team: SessionTeam;
}

export interface SyncState {
  /** 增量拉取游标 */
  cursor: string | null;
  /** 游标所属团队；切换团队时重置 */
  team_id: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  syncing: boolean;
}

export interface SyncReport {
  skipped: 'not_logged_in' | null;
  pushed: number;
  pulled: number;
  assets_uploaded: number;
}
