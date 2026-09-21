export type ErrorCode =
  | 'UNKNOWN'
  | 'NO_RECEIVER'
  | 'NOT_JIN10'
  | 'NOT_FOUND'
  | 'PARSE_FAILED'
  | 'STORAGE'
  | 'NETWORK'
  | 'UNAUTHORIZED';

export interface AppError {
  code: ErrorCode;
  message: string;
}

export class AppException extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = 'AppException';
    this.code = code;
  }
}

const NO_RECEIVER_PATTERN = /Receiving end does not exist|Could not establish connection/i;

export function toAppError(err: unknown): AppError {
  if (err instanceof AppException) return { code: err.code, message: err.message };
  const message = err instanceof Error ? err.message : String(err);
  if (NO_RECEIVER_PATTERN.test(message)) {
    return { code: 'NO_RECEIVER', message: '插件尚未注入该页面，请刷新金十页面后重试' };
  }
  return { code: 'UNKNOWN', message };
}
