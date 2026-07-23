# TRPG 规则 Agent · Web 调试台

基于 `@trpg-rule-agent/gateway-client` 的浏览器调试 UI（Vite 静态产物，零运行时依赖）。

## 启动

```bash
# 在仓库根目录安装 workspace 依赖（含 vite 8.1.5）
npm install

# 开发服务器（默认 http://localhost:5173）
npm run dev --workspace @trpg-rule-agent/web
# 或
cd apps/web && npm run dev

# 生产构建（输出 apps/web/dist 静态文件，base 为相对路径）
npm run build --workspace @trpg-rule-agent/web

# 预览构建产物
npm run preview --workspace @trpg-rule-agent/web
```

## 演示模式（开发模式 + ?mock=1）

默认访问走**真实** `BrowserTransport`（同源相对路径，需后端 Gateway）。
仅当【开发模式（`import.meta.env.DEV`，即 `npm run web:dev`）**且** URL 带 `?mock=1`
查询参数】两个条件同时满足时，才动态加载内置 `MockTransport` 脚本化返回一段 SSE，
用于无后端时的端到端演示：

```
http://localhost:5173/?mock=1
```

> mock 分支以 `import.meta.env.DEV` 静态门控：生产构建（`npm run web:build`）时该条件被替换为
> `false`，整个分支（含动态 `import("./mock-transport.ts")`）被 tree-shake 移除。因此**生产构建 /
> `npm run web:preview` 中 `?mock=1` 无效**，永远走真实路径，且产物中不含任何 mock 代码 / fixture。

## 凭据说明

- API Key 仅保存在浏览器内存 JS 变量中，**刷新页面即丢失**；
- 创建会话成功后输入框会清空，但 key 仍留内存变量供后续每轮 `runTurn` 使用（用完即弃）；
- 绝不写入 `localStorage` / `sessionStorage` / `cookie` / `dataset` / URL。

## 测试

```bash
npm run test --workspace @trpg-rule-agent/web
# 或（仓库根目录）
npx vitest run apps/web
```

- `state.test.ts`：纯函数 reducer 测试（流式累加、固化、工具时间线、来源、错误、重置）。
- `controller.test.ts`：注入 fake client，验证事件派发顺序、`abort` / `createSession` 透传与错误路径。均不访问真实网络。
