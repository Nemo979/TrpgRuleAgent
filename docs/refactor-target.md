# Web/Python 重构目标

> **历史定位**：本文记录 V1 Web/Python 迁移目标、验收结果和管理员导入/发布命令，
> 不再定义 V2.2 或后续阶段的实施顺序。当前总体路线以
> [V2.2 可行性与执行方案](v2.2-feasibility-plan.md) 为准。

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

导入器会保留原文件，并生成 `documents.jsonl` 与 `import-report.json`。PDF 报告包含
表格数量、空白页和启发式警告：`image_dominant_page` 仅表示页面含图片对象且提取文本
少于 80 个字符，`unreliable_table` 仅表示表格为单列或各行列数不一致。这些警告不判断
图片面积、表格语义或抽取内容是否正确；扫描 PDF、图片、图示和复杂表格理解不在当前范围内。

构建与发布分离：

```bash
trpg-library --root data/libraries build-jsonl \
  --id coc7 \
  --name "Call of Cthulhu 7E" \
  --system "Call of Cthulhu" \
  --alias CoC7 \
  --alias 克苏鲁7版 \
  --edition 7E \
  --documents /tmp/coc7-import/documents.jsonl \
  --import-report /tmp/coc7-import/import-report.json

trpg-library --root data/libraries publish \
  --id coc7 \
  --revision 20260729T120000Z
```

`build-jsonl` 可重复使用 `--alias` 写入常用简称；会拒绝空库、规则库 ID
不一致和完全重复章节。传入提取阶段生成的
`--import-report` 后，构建报告会保留源文件、页数、表格数量和警告等提取信息。
只有显式执行 `publish` 才会原子替换 `current`。

## PF1E 迁移验收

2026-07-29 已将旧工作目录中的完整 PF1E CHM 切分结果迁移到新规则库：

- 原始文档 2140 条；按内容哈希保留源文件中首次出现的条目，移除 6 条完全重复文档。
- 发布文档 2134 条，SHA-256 为
  `b631bb5995645d6ae6a8c55e547f25de9eec5e4372e0e2e81ca7c6c62ff17b49`。
- 从旧索引的 29629 个子块中移除重复父文档对应的 8 个子块，发布 29621 个子块。
- 当前发布版本为 `20260801T074810Z`，规则库名称为“Pathfinder 1E 中文规则库”，
  并包含 `PF1E` 与 `Pathfinder 1E` 显式别名。
- 85 题混合检索回归结果为 Hit@5 96.5%、MRR 0.8210，达到迁移前基线。
- 新服务验证了共享密码鉴权、规则库发现、真实规则检索、引用原文读取和 Web 静态入口。

完整规则数据位于被 Git 忽略的 `data/libraries/pathfinder-1e`，不会进入源码提交或远程仓库。

## 第二套规则库与跨系统隔离验收

2026-07-30 导入并发布《夕妖晚谣》1.2 中文合订翻译版：

- 原始 PDF 159 页，带可用文本层；发布 154 个父文档、1271 个向量子块。
- 规范化源文件名为 `夕妖晚谣-1.2.pdf`，SHA-256 为
  `fceb27ce9ffdb111cfa44922de6066a95b36bdea29d06ec28feef9e75b04083e`。
- 规则库 ID 为 `golden-sky-stories-zh-1-2`，当前发布修订为 `20260801T074827Z`，
  并包含 `GSS` 与 `Golden Sky Stories` 显式别名。
- 导入报告记录 59 个表格和 18 条启发式警告；5 个无可用文本或表格内容的页面没有进入文本库，
  第 125–127 页的 3 个单列表格因形状检查而标记为不可靠。
- 14 题混合检索为 Hit@5 100%、MRR 0.9107；纯向量为
  Hit@5 92.9%、MRR 0.7405。
- SenseNova、MiMo、Agnes 均完成真实规则检索、回答、脚注与来源验收。
- 新建对话必须明确选择规则库；对话固定绑定系统与版本并记录创建时修订，规则库发布更新后界面会警告旧回答的来源可能失效。
- Bootstrap、聊天工具和来源接口均完成 PF1E 与《夕妖晚谣》的双向隔离测试。
- 390×844 手机视口验证规则库选择底部面板无横向溢出。
- 当前全量回归为 TypeScript/Vitest 27 个测试文件、261 项测试，
  Python 应用 49 项测试、Python 检索 14 项测试，类型检查和 Web 生产构建全部通过。
- 当前 Python Agent 由服务端强制串行执行工具，并对重复检索、重复读取、无效工具调用、只搜索不读取和总决策上限统一收敛；模型在没有已注册来源时不能自由生成最终答案，达到总上限时也不再抛出工具循环错误。
- Agnes、MiMo、DeepSeek 已用同一无明确归属问题完成真实回归，模型行为虽不同，但均不会再以工具上限错误结束或在无来源时生成伪脚注。
- 最终回答阶段已与工具规划历史隔离，只输入最后问题和最多 8 个已读章节；连续搜索不读取、重复结果或已有证据后的第三轮搜索由服务端补读候选并直接收尾。
- Provider 首次跳过工具或提交不存在的文档 ID 时，服务端分别按最后一条用户问题检索、按最近搜索结果读取，保证追问仍重新取得本轮可引用证据。
- Provider 重复读取已注册章节时，服务端补读该次搜索尚未使用的候选，避免仅凭部分章节提前回答。

PDF、抽取正文、向量索引和报告均位于被 Git 忽略的 `data/`，不会进入源码提交。
图片、扫描页和复杂图表理解仍不在当前文本型 V1 范围内。

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
