export async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** 去重指纹：忽略空白与标点差异，按快讯标题 + 正文计算 */
export function materialFingerprint(flashTitle: string | null, content: string): Promise<string> {
  const norm = (s: string) => s.replace(/[\s\p{P}]/gu, '');
  return sha256Hex(`${norm(flashTitle ?? '')}|${norm(content)}`);
}
