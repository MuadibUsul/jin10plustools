import { useEffect, useState } from 'react';
import type { ConnectionState } from '@/components/StatusDot';
import { isJin10Url } from '@/shared/constants';
import { sendToBackground, sendToTab } from '@/shared/messaging';

export interface ConnectionInfo {
  background: ConnectionState;
  content: ConnectionState;
  contentDetail: string | undefined;
  jin10TabId: number | null;
}

const INITIAL: ConnectionInfo = {
  background: 'checking',
  content: 'checking',
  contentDetail: undefined,
  jin10TabId: null,
};

/**
 * 检查 popup ↔ background ↔ content 三方通信。
 * 未申请 tabs 权限：只有 jin10 标签页（已在 host_permissions 中）才能读到 url。
 */
export function useConnection(): ConnectionInfo {
  const [info, setInfo] = useState<ConnectionInfo>(INITIAL);

  useEffect(() => {
    let cancelled = false;
    const update = (patch: Partial<ConnectionInfo>) => {
      if (!cancelled) setInfo((prev) => ({ ...prev, ...patch }));
    };

    void sendToBackground('PING', undefined).then((res) =>
      update({ background: res.ok ? 'ok' : 'error' }),
    );

    void chrome.tabs.query({ active: true, currentWindow: true }).then(async ([tab]) => {
      if (!tab?.id || !isJin10Url(tab.url)) {
        update({ content: 'inactive', contentDetail: '当前不是金十页面' });
        return;
      }
      const res = await sendToTab(tab.id, 'PING', undefined);
      update(
        res.ok
          ? { content: 'ok', contentDetail: undefined, jin10TabId: tab.id }
          : { content: 'error', contentDetail: res.error.message },
      );
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return info;
}
