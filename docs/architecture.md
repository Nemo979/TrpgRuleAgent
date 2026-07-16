# Architecture

## 设计目标

第一版只支持 Pathfinder 1E，但核心模块不能依赖某一本规则书。增加规则集时，应以 Rule Pack、资料索引和领域工具为主，而不是复制 Agent。

## 在线问答链路

```text
User
  -> CLI/Web Adapter
  -> Pi Agent Runtime
  -> search_rules
  -> Retrieval API
  -> vector + BM25 child candidate search
  -> weighted reciprocal-rank fusion
  -> read_rules
  -> parent document store
  -> Citation Registry
  -> grounded final answer
```

`search_rules` 只返回候选摘要，降低上下文占用。`read_rules` 才返回完整父文档，并在应用侧注册 `[S1]`、`[S2]` 等稳定引用。最终来源展示来自 Citation Registry，不从模型输出中反向解析。

## 离线索引链路

```text
PF source files
  -> source adapter
  -> structural parser
  -> normalized parent documents
  -> child chunker
  -> BGE embeddings
  -> Chroma child index + parent document store
```

父文档保持完整章节结构，供 `read_rules` 返回；约 500 字的重叠子块只参与召回。当前默认使用 `BAAI/bge-small-zh-v1.5`，由 FastEmbed/ONNX 在 CPU 上生成归一化向量，Chroma 使用 cosine 距离。在线检索同时取得向量候选与 BM25 候选，通过加权 Reciprocal Rank Fusion 合并父文档排名，再应用轻量规则来源优先级。BM25 使用中文单字/双字词元和英文单词，并缓存语料 IDF；嵌入引擎与检索器通过接口隔离，不需要修改 Agent 工具协议。

索引构建会记录源文档 SHA-256、模型、嵌入引擎和切块参数。中断后按已有 Chroma 条目续建；配置不一致时拒绝继续，防止混入语义空间不同的向量。

## 检索质量闭环

`rulepacks/pathfinder-1e/evals/retrieval.jsonl` 保存问题与相关父文档 ID。评测命令直接调用真实检索器，报告 Hit@K 和 MRR，并保留逐题排名与路径。扩充规则或更换索引策略时，应先运行同一评测集再比较结果。

## 安全边界

- Agent 不能直接读取文件系统或数据库。
- 所有规则访问都必须经过有类型的工具。
- 每轮限制工具调用、搜索次数和完整文档数量。
- 规则集 ID 由运行时注入，模型不能跨库切换。
- 演示资料带有 `metadata.demo=true`，Prompt 强制披露其非正式性质。

## 后续领域工作流

规则问答稳定后，再增加结构化角色状态和确定性工具：

```text
get_character_options
apply_character_choice
calculate_character
validate_character
export_character
```

LLM 负责理解意图和解释选择；数值计算、前置条件和合法性判断由确定性代码负责。
