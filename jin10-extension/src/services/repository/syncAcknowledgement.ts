import type { NewsMaterial } from '@/types/material';

/** A response acknowledges only the snapshot sent, never a later local edit. */
export function acknowledgedMaterial(sent: NewsMaterial, current: NewsMaterial | null, received: NewsMaterial | null): NewsMaterial | null {
  if (!current) return null;
  if (JSON.stringify(current) === JSON.stringify(sent)) return received;
  if (!received) return current;
  return { ...current, id: received.id, team_id: received.team_id,
    collected_by: [...new Set([...received.collected_by, ...current.collected_by])], sync_status: 'local' };
}
