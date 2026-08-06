# Architecture

> 本文档反映**当前正式架构**：以 `apps/web-next` + `services/app-python` + `services/retrieval-python` 组成的 Web/Python 单体链路为主。
> 旧的 Node/BYOK 实现（`packages/agent`、`apps/gateway`、`apps/web`、`apps/miniprogram` 等）已被列为**遗留、待删除**，仅保留在文末「遗留实现」一节供追溯，不再代表正式入口。详见 [项目现状](project-status.md)。

## 1. 设计目标

第一版只支持 Pathfinder 1E，但核心模块不能依赖某一本规则书。增加规则集时，应以 Rule Pack、资料索引和领域工具为主，而不是复制 Agent。

当前 V1 面向少量可信用户，采用单体 Web/Python 架构：模型 ID、Base URL 和 API Key 均由管理员在服务端配置，浏览器不能提交或读取模型密钥。服务端统一负责共享密码登录、签名会话、规则检索、证据引用与来源预览；每个对话固定绑定一个规则库，跨系统严格隔离。

## 2. 当前正式架构（Web / Python 单体）

```text
React Web (apps/web-next)
  -> 共享密码 + HTTP-only 签名会话 (services/app-python/auth)
  -> SSE 规则 Agent 编排 (services/app-python/chat + conversation_state)
  -> 服务端配置的多个 OpenAI-compatible 模型 (YAML + 环境变量，Key 不进浏览器)
  -> search_rules / read_rules
  -> Python 检索服务 (services/retrieval-python)
  -> BM25 + BGE/Chroma + RRF 混合召回
  -> 独立的「游戏系统 + 版本」规则库（按 libraryId 隔离）
```

模型配置、API Key 与规则正文都只在服务端；对话内容默认只保存在当前浏览器 `localStorage`，服务端不持久化原始问题与回答。

### 2.1 apps/web-next（React Web 入口）

- 支持桌面与手机浏览器，新建对话必须明确选择规则库，不再默认使用第一个。
- 仅持有浏览器本地的会话与对话状态；**绝不持有或提交模型 API Key**。
- 通过 SSE 接收流式回答、工具时间线与来源列表；来源由服务端引用注册表生成，不从模型输出反向解析。
- 对话固定绑定系统与版本，并记录创建时的规则库修订；规则库更新后界面会提示旧回答的来源可能失效。

### 2.2 services/app-python（应用服务与 Agent 编排）

`trpg_app` 包负责鉴权、SSE 聊天、服务端工具编排、会话状态、库管理与管理员命令：

- **鉴权**：共享密码登录 + HTTP-only 签名会话；`/health` 检查进程存活，`/ready` 检查已发布规则库与模型配置是否可用。
- **模型配置**：模型由 `config/app.yaml` + 环境变量配置，API Key 只从环境变量读取；已接入 SenseNova `deepseek-v4-flash`、Xiaomi `mimo-v2.5` 与 `agnes-2.5-flash`，每个模型可独立配置上下文窗口、最大输出、请求超时与重试次数（Agnes 经 `chat_template_kwargs.enable_thinking=false` 原生关闭 thinking）。
- **工具编排与收敛**：服务端统一串行执行 `search_rules` / `read_rules`；对重复查询、重复结果、重复读取、连续无效调用、只搜索不读取和总决策上限进行收敛控制；达到总上限时使用已读来源作答或安全返回证据不足，不再抛出工具循环错误。模型首次跳过工具时，服务端用最后一条用户问题执行检索；模型提交不存在的文档 ID 时，服务端改读最近一次搜索的真实候选。
- **取证与最终回答分离**：取证（工具轨迹）与最终回答使用独立消息，最终阶段只接收用户问题与已读证据，不会把模型中间计划显示成答案；最终最多携带 8 个章节。
- **结构化会话状态**：`conversation_state` 维护当前任务范围，多轮追问继承用户明确建立的任务，避免只保留实体词而丢失范围。
- **引用与安全日志**：工具步骤记录请求、模型、规则库、预算、停止原因与查询摘要哈希，不记录用户问题正文；错误响应提供稳定错误码、是否可重试与请求 ID；安全日志不记录问题正文、回答或密钥。
- **库管理与发布**：`libraries` / `library_boundary` / `admin` + `importers`（CHM / PDF）提供规则库导入、构建与原子发布；`answer_evaluation` 提供答案级评测支撑。

### 2.3 services/retrieval-python（检索、索引与评测）

`trpg_retrieval` 包负责规则导入、结构解析、索引、混合召回、重排与评测：

- **导入与结构解析**：CHM / PDF 结构感知解析（`importers`、`chm_structured`、`chm_structure_audit`），把正确内容切进可检索父文档；PF1e 当前发布 2,498 个结构化父文档、33,585 个检索子块。
- **切块与索引**：父文档保留完整章节结构供 `read_rules` 返回；约 500 字的重叠子块只参与召回。向量由 FastEmbed/ONNX 在 CPU 上生成归一化 BGE 向量，Chroma 使用 cosine 距离；索引构建记录源文档 SHA-256、模型、嵌入引擎与切块参数。
- **混合召回**：在线检索同时取得向量候选与 BM25 候选（中文单字/双字词元 + 英文单词，缓存 IDF），经加权 Reciprocal Rank Fusion 合并父文档排名，再应用轻量来源优先级。
- **重排与协调**：`coordinator` + `retriever` / `hybrid_retriever` / `vector_retriever` 负责结构感知召回、候选重排与跨块协调（V2 RAG 重构重点）。
- **评测**：`evaluation` 直接调用真实检索器，报告 Hit@K 与 MRR，并保留逐题排名与路径，用于回归比较。

## 3. 在线问答链路

```text
User
  -> React Web (apps/web-next)
  -> 应用服务 SSE 聊天与工具编排 (services/app-python/chat)
  -> OpenAI-compatible Model (服务端配置)
  -> search_rules
  -> 检索服务 (services/retrieval-python)
  -> vector + BM25 child candidate search
  -> weighted reciprocal-rank fusion
  -> read_rules
  -> parent document store
  -> Citation Registry
  -> grounded final answer
```

`search_rules` 只返回候选摘要，降低上下文占用；`read_rules` 才返回完整父文档，并在应用侧注册 `[S1]`、`[S2]` 等稳定引用。最终来源展示来自引用注册表，不从模型输出中反向解析。

## 4. 离线索引链路

```text
PF / GSS source files
  -> source adapter
  -> structural parser (CHM / PDF 结构感知)
  -> normalized parent documents
  -> child chunker
  -> BGE embeddings (FastEmbed / ONNX, CPU)
  -> Chroma child index + parent document store
```

父文档保持完整章节结构，供 `read_rules` 返回；约 500 字的重叠子块只参与召回。嵌入引擎与检索器通过接口隔离，更换嵌入模型不需要修改工具协议。

## 5. 检索质量闭环

`rulepacks/pathfinder-1e/evals/retrieval.jsonl` 与 `rulepacks/golden-sky-stories-zh-1-2/evals/retrieval.jsonl` 保存问题与相关父文档 ID。评测命令直接调用真实检索器，报告 Hit@K 和 MRR，并保留逐题排名与路径。扩充规则或更换索引策略时，应先运行同一评测集再比较结果。PF1e 长期集 85/85 Hit@5 100% / MRR 0.8676，结构化集 30/30 MRR 0.9361；《夕妖晚谣》22/22 MRR 0.9773。检索评测不等同于最终答案准确率，答案级验收见 [项目现状](project-status.md)。

## 6. 安全边界

- **密钥不进浏览器**：模型 API Key 只从服务端环境变量读取；浏览器既不能提交也不能查看模型密钥。
- **错误脱敏**：对外错误链统一把 API Key 替换为 `[REDACTED]`（纵深防御，不单点信任 Provider）；涉及外部请求时使用 `fetch(redirect:"error")` 禁止自动跟随 30x，避免重定向到内网绕过白名单（SSRF）。
- **规则访问受控**：Agent 只能通过有类型的工具访问规则；每轮限制工具调用、搜索次数与完整文档数量；规则集 ID 由运行时注入，模型不能跨库切换。
- **引用可信**：最终来源由程序持有的引用注册表生成，而不是依赖模型编造路径。
- **演示数据披露**：演示资料带 `metadata.demo=true`，Prompt 强制披露其非正式性质。

## 7. 遗留 Node / BYOK 实现（待删除）

> 以下为旧实现，**不再代表当前正式产品入口**，计划于 V1 正式链路完成实际试用验收后一次性删除。保留说明仅供追溯；其详细设计文档（`gateway-api.md`、`web-client.md`）将随删除一并归档。

- **自有 Agent Runtime**（`packages/agent`）：零运行时第三方依赖的 Agent 内核（core / providers / rule-agent）、OpenAI-compatible 流式 Provider 与工具循环。
- **BYOK Gateway**（`apps/gateway`）：BYOK HTTP/SSE 网关，模型 Key 仅经 `X-Model-Api-Key` 头、服务端零持久化；含会话白名单、来源限流、安全响应头等加固（原「阶段六」）。
- **共享 SDK 与 Web 调试 UI**（`packages/gateway-client`、`apps/web`）：浏览器/小程序可复用 SDK 与无框架调试台，把「API Key 永不离开宿主内存」延伸到前端。
- **微信小程序宿主骨架**（`apps/miniprogram`）：含 `createWeChatAdapters` 与页面级 `MiniProgramSession` 门面，非完整可发布工程。
- **检索客户端**（`packages/rules-client`、`packages/rules-types`）：检索协议类型与零依赖 HTTP 客户端（当前正式链路由 `services/retrieval-python` 直接提供检索，不再经由该客户端）。
- **CLI**（`apps/cli`）：流式命令行入口。

这些遗留实现的测试当前仍全绿（TypeScript/Vitest 261 项中部分来自此处），但删除后整体测试面会显著缩小，符合既定清理计划。

## 8. 后续领域工作流

规则问答稳定后，再增加结构化角色状态和确定性工具：

```text
get_character_options
apply_character_choice
calculate_character
validate_character
export_character
```

LLM 负责理解意图和解释选择；数值计算、前置条件和合法性判断由确定性代码负责。
