# Web/Python 重构目标

本目录记录从 `release` 并行建立的新纵向链路。旧实现会保持可运行，直到新链路完成自动验收和实际试用。

## 目标边界

- React Web 是唯一正式用户入口，适配桌面和手机浏览器。
- 单一 Python 后端负责登录、聊天、模型调用、检索和来源预览。
- 一个对话固定绑定一个“游戏系统 + 版本”规则库。
- 模型由服务端 YAML 配置，密钥只来自环境变量。
- 对话仅保存在浏览器；服务端不持久化原始问题和回答。
- 规则库由管理员命令分阶段构建和原子发布。
- 保留父子切块、BM25、向量召回、RRF 与现有 PF1E 检索评测。

## 当前新链路

```text
apps/web-next
  -> /api/auth/login
  -> /api/bootstrap
  -> /api/chat (SSE)
  -> services/app-python
  -> trpg_retrieval
  -> data/libraries/<library>/current
```

`current` 是指向 `builds/<revision>` 的符号链接。构建失败或未经发布的版本不会被 Web 进程加载。

## 本地验证

```bash
npm install --ignore-scripts
npm run build --workspace @trpg-rule-agent/web-next

PYTHONPATH=services/app-python/src:services/retrieval-python/src \
  python -m unittest discover -s services/app-python/tests -v
```

配置参考 `config/app.example.yaml`。运行时必须提供共享密码、会话签名密钥以及每个模型对应的 API Key 环境变量。

## 管理员规则库流程

文本型 PDF：

```bash
trpg-library extract-pdf \
  --id coc7 \
  --edition 7E \
  --source-title "守秘人规则书" \
  --pdf /rules/coc7.pdf \
  --output /tmp/coc7-import
```

CHM：

```bash
trpg-library extract-chm \
  --id pf1e \
  --edition 1E \
  --chm /rules/pathfinder.chm \
  --output /tmp/pf1e-import
```

导入器会保留原文件，并生成 `documents.jsonl` 与 `import-report.json`。PDF 报告包含表格数量、不规则表格、空白页和图片主导页面。扫描 PDF 与图示理解不在当前范围内。

构建与发布分离：

```bash
trpg-library --root data/libraries build-jsonl \
  --id coc7 \
  --name "Call of Cthulhu 7E" \
  --system "Call of Cthulhu" \
  --edition 7E \
  --documents /tmp/coc7-import/documents.jsonl

trpg-library --root data/libraries publish \
  --id coc7 \
  --revision 20260729T120000Z
```

`build-jsonl` 会拒绝空库、规则库 ID 不一致和完全重复章节。只有显式执行 `publish` 才会原子替换 `current`。

## PF1E 迁移验收

2026-07-29 已将旧工作目录中的完整 PF1E CHM 切分结果迁移到新规则库：

- 原始文档 2140 条；按内容哈希保留源文件中首次出现的条目，移除 6 条完全重复文档。
- 发布文档 2134 条，SHA-256 为
  `b631bb5995645d6ae6a8c55e547f25de9eec5e4372e0e2e81ca7c6c62ff17b49`。
- 从旧索引的 29629 个子块中移除重复父文档对应的 8 个子块，发布 29621 个子块。
- 发布版本为 `20260729T145539Z`，规则库名称为“Pathfinder 1E 中文规则库”。
- 85 题混合检索回归结果为 Hit@5 96.5%、MRR 0.8210，达到迁移前基线。
- 新服务验证了共享密码鉴权、规则库发现、真实规则检索、引用原文读取和 Web 静态入口。

完整规则数据位于被 Git 忽略的 `data/libraries/pathfinder-1e`，不会进入源码提交或远程仓库。

## V1 稳定性收口验收

2026-07-30 完成当前 Web/Python 链路的 V1 稳定性收口：

- 修复旧测试对组合 AbortSignal 对象身份的错误断言，改为验证真实取消传播和 499 错误语义。
- 模型配置增加 `request_timeout_seconds` 与 `max_retries`；重试上限限制为 0–3。
- 模型错误按超时、限流、连接、鉴权、请求拒绝、证据预算和内部错误分类。
- 每次聊天返回 `X-Request-ID`，SSE 错误包含同一请求 ID；日志不记录问题正文、回答或密钥。
- 新增 `/health` 与 `/ready`，Docker 镜像增加健康检查。
- 启动脚本改为校验完整 YAML 和全部模型环境变量，不再只检查单个 `LLM_API_KEY`。
- Agnes 使用官方 OpenAI 兼容参数
  `chat_template_kwargs.enable_thinking=false`，不再依赖缓冲后过滤。
- 模型必须读取规则证据后才能回答；若已读取证据但正文漏写脚注，自动补充实际来源标签。
- SenseNova、MiMo、Agnes 均完成真实 PF1E RAG 回归，无错误事件和 thinking 标签。
- 390×844 手机视口完成对话切换、模型选择、Markdown、输入和来源抽屉验收。

自动验证结果：

- TypeScript/Vitest：26 个测试文件、257 项测试通过。
- Python 应用服务：23 项测试通过。
- Python 检索服务：14 项测试通过。
- 全仓 TypeScript 类型检查通过。
- React Web 生产构建通过。
