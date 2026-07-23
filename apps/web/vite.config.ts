import { defineConfig } from "vite";

// 注意：本文件位于 apps/web，Vite 默认以“配置所在目录”为 root，
// 因此 index.html / src 都相对 apps/web 解析。
// base 用相对路径 "./"，便于同源反代 / 任意静态目录托管。
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
  },
  server: {
    port: 5173,
  },
});
