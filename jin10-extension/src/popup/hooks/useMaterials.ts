import { useEffect, useState } from 'react';
import type { NewsMaterial } from '@/types/material';
import { STORAGE_KEYS, STORAGE_PREFIX } from '@/shared/constants';
import { LocalMaterialRepository } from '@/services/repository/LocalMaterialRepository';

const repository = new LocalMaterialRepository();

/** 只读订阅本地素材库；写操作一律通过 background 消息 */
export function useMaterials(): { materials: NewsMaterial[]; loading: boolean } {
  const [materials, setMaterials] = useState<NewsMaterial[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      void repository.list().then((list) => {
        if (cancelled) return;
        setMaterials([...list].sort((a, b) => Date.parse(b.collect_time) - Date.parse(a.collect_time)));
        setLoading(false);
      });

    const onChanged = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area !== 'local') return;
      const keys = Object.keys(changes);
      if (keys.some((k) => k === STORAGE_KEYS.materialIndex || k.startsWith(STORAGE_PREFIX.material))) load();
    };

    load();
    chrome.storage.onChanged.addListener(onChanged);
    return () => {
      cancelled = true;
      chrome.storage.onChanged.removeListener(onChanged);
    };
  }, []);

  return { materials, loading };
}
