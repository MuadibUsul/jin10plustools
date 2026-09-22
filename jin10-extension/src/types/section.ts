/**
 * PLUS 日报固定栏目（与模板 PPT 目录一一对应，顺序即日报中的顺序）。
 * id 为跨端稳定标识，服务端枚举必须与此保持一致。
 */
export const SECTIONS = [
  { id: 'macro', label: '宏观消息', kind: 'news' },
  { id: 'cn_equity', label: '国内股市动向', kind: 'news' },
  { id: 'global_equity', label: '海外股市动向', kind: 'news' },
  { id: 'ai', label: 'AI热点', kind: 'news' },
  { id: 'research', label: '精选观点', kind: 'feature' },
  { id: 'charts', label: '精选图表', kind: 'feature' },
] as const;

export type SectionId = (typeof SECTIONS)[number]['id'];

/** news：标题 +【新闻】+【解读】；feature：标题 + 段落 + 配图 */
export type SectionKind = (typeof SECTIONS)[number]['kind'];

export function getSection(id: SectionId) {
  const section = SECTIONS.find((s) => s.id === id);
  if (!section) throw new Error(`Unknown section: ${id}`);
  return section;
}
