import { putImage } from '@/services/assetStore';
import { createMaterialService } from '@/services/materialService';
import { LocalMaterialRepository } from '@/services/repository/LocalMaterialRepository';
import { refreshBadge } from './badge';

/** background 内的服务单例（service worker 重启后重新创建，状态全部在 storage 中） */
export const materialRepository = new LocalMaterialRepository();

export const materialService = createMaterialService({
  repository: materialRepository,
  putImage,
});

export async function restoreBadge(): Promise<void> {
  await refreshBadge(await materialService.list());
}
