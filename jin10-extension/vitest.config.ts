import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

// 测试不加载 crx 插件，只保留路径别名
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
  },
});
