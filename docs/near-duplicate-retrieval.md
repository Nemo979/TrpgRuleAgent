# 近重复资料处理机制：保覆盖 · 去冗余 · 防冲突（修订版 v2）

> 设计文档，本回合不改动代码。
> 本版（v2）根据评审意见修订：真实可读上限为 `max_answer_documents=8`；相似度改用 trigram shingle；
> 新增 `semantic_key` 分桶 + 代表中心聚类（禁并查集链式合并）；冲突检测前移；
> 不声称"一致"；权威判定仅依赖显式 `supersedes`；provenance 不冒充已读。
> 后续又吸收 V2.2 推延评审的 7 处修订（见 §0 与正文标注）。
>
> ⚠️ **本机制不作为 PF1e V2.1 发布阻塞项。** 它属于 V2.1 稳定性收口之后的 **V2.2** 设计范畴；
> 第一项实际工作是近重复**只读审计**（Phase0，不改排序），而非直接修改检索排序。详见 §0。

---

## 0. 项目定位与前置条件

**定位**：本机制解决"近重复挤占 Top-8 可读候选"的检索侧问题，是 V2.2 的候选优化，
**不是 V2.1 的发布阻塞项**。在 V2.1 收口与 Phase0 审计完成前，不应改动线上排序。

**Phase1 前置条件（须满足后方可进入 V2.2 Phase1 开发）**：

1. **V2.1 稳定性收口完成**：85/30 题 `relevantIds` ID 重映射完毕；Vitest 恢复可跑；
   MiMo 四轮验收通过；检索与回答评测回归达标。
2. **建立真正可用的规则库构建回滚点**：按 revision 保存**完整、不可变**的规则库构建目录，至少包含
   `documents.jsonl`、向量索引、`manifest`/`build-state`、当前 revision 指向、对应代码提交。
   *仅打 Git tag 不够——`.gitignore` 已忽略 `build/ data/ vector_store/`，tag 只保代码、不保构建产物，
   而 PF1e 旧构建已无可用副本，必须连同构建产物一起留存才能回滚。*
3. **完成近重复只读审计（Phase0）**：对候选池同时执行「语义键桶内审计」和「Top-50 跨桶探索审计」，
   使用 `derivedSemanticKey` + shingle Jaccard 量化**跨父级 / 跨键**近重复占比、潜在冲突信号数量，
   作为阈值标定、语义键修正与收益评估的基线（只产出报告，不改排序）。
4. **阈值标定起点（非写死默认值）**：Phase0 从 **0.90** 起实验，据误合并 / 漏合并结果再校准；
   **校准前不把 0.82 等数值写成默认值**（见 §4.B）。
5. **灰度与回退就绪**：`enable_diversity` 开关默认 off，具备一键回退路径，可秒级关闭。

**Phase2 前置条件（进入元数据驱动来源关系前必须满足）**：

- **来源元数据补全（§7）**：至少落地 `sourceKind` / `publicationDate` / `supersedes`，
  使同规则来源能够建立可追溯关系；其中只有显式 `supersedes` 才能触发自动替代，
  `errataFor` / `clarifies` 只用于关联与提示核对，不直接决定哪一方更权威。
  *注意：元数据是 **Phase2** 的前置，而非 Phase1——Phase1 高置信去重不依赖来源元数据。*

---

## 1. 问题界定（修正：真实约束是 Top-8）

检索侧（`services/retrieval-python`）：
```
coordinator.search(query, documents, limit)            (coordinator.py:67)
  → expand_query → base.search(..., max(limit, candidate_limit=50))  # 混合 RRF 融合
  → 结构感知重排 → return reranked[:limit]
```
Agent 侧（`services/app-python/chat.py`）：
- `EvidenceBudget.max_documents = 24`（文档预算，`chat.py:235`）——**不是读取上限**。
- `ToolLoopController.max_answer_documents = 8`（**真正可读章节上限**，`chat.py:280`）。
- 搜索工具 `limit` 参数上限 20（`chat.py:77`），模型通常请求更少；最终被 `read_rules` 实际读取的 ≤ 8。

**真实问题**：近重复占据**可被读取的前 8 个候选**，把真正不同的规则点挤出 Top-8，而非"占满 24 个文档预算"。机制仍有价值，但**评测指标必须以 Top-8 为重点**（见 §6）。

---

## 2. 设计目标

| 目标 | 含义 |
| --- | --- |
| 保覆盖 | Top-8 内每个**不同规则点**至少 1 个代表，不被近重复淹没 |
| 去冗余 | 近似资料聚为一簇，每簇默认只进 1 个代表 |
| 防冲突 | 簇内表述潜在冲突时不强行合并，保留来源与"潜在冲突"标记 |
| 准确覆盖 | 关键信息（含少数但重要的异见/勘误）不丢，且带来源可追溯 |
| 安全边界 | 只有经 `read_rules` 真正读取的文档才能成为 [S1] 引用/进入来源抽屉 |

原则：**不删除语料，只调整当前候选中的重复结果优先级**——近重复一律「聚簇、代表优先、成员可追溯」，
而非从语料中删除。Phase1 的簇成员关系只用于内部排序与诊断，不改变 Agent 协议；
`relatedSourceIds` 等来源关系到 Phase3 才对答案阶段显式透传。

---

## 3. 机制总览

> 下图是**最终目标架构**（含 Phase2/3 的完整冲突检测与 `possibleDisagreement` 暴露能力），用于说明机制全貌。
> **Phase1 实际只落地其中去重部分**（见下「Phase1 实际范围」）；冲突 / `possibleDisagreement` 留到 Phase2/3。

**最终目标架构（全貌）**：
```
候选(≤50) ──► [A] 计算 semantic_key（硬分桶）
           ──► [B] 桶内代表中心聚类（禁并查集）
           ──► [C] 每簇先做潜在冲突检测（前移！）
           ──► [D] 未发现冲突信号的高相似簇选 1 代表 / 冲突簇标记 possibleDisagreement
           ──► [E] 多样性排序取 Top-limit（按请求 limit，不写死 8）
           ──► [F] 仅透传 relatedSourceIds + possibleDisagreement（不声称"一致"）
```

**Phase1 实际范围（仅高置信去重，不做冲突判定，返回按 `limit`）**：
```
候选(≤50) ──► [A] 按 ruleLibraryId 隔离，再计算 derivedSemanticKey（Phase1 只用自动键）
           ──► [B] 桶内代表中心聚类 + dedupSafetyVeto（禁并查集）
           ──► [E1] 两遍多样性排序取 Top-limit（先簇代表，仍不足再补簇内次级候选）
           # Phase1 不输出 [C] 冲突结论、不强制保留双方、不声明一致
           # 但必须执行安全否决：关键字段不一致时禁止自动合并
           # 8 条可读上限由 Agent 侧截断，不在此硬编码
```

---

## 4. 详细设计

### A. 两级语义键（替代 `_has_entity_category_pair`）

`_has_entity_category_pair`（`coordinator.py:205`）用于判断**查询词**是否同时命中实体+规则类别，**不适用于判断两个候选是否同一规则点**。改为两级语义键：

**`derivedSemanticKey`（自动提取，仅用于高置信去重，Phase1 使用）**
- 由条目名称 / 法术名 / 专长名等**结构化字段**自动生成，例如 `entryType + entryNameZh/entryNameEn`、`规范化 headingPath`、`title + fullPath` 退化键。
- **同源形态内**的高置信近重复（如同一核心书的不同版次段落、同一 FAQ 内的重复表述）可靠聚簇。
- 解析置信度不足时 → **该候选独立成簇、始终保留，不参与自动去重**（避免误并）。

**`canonicalRuleKey`（显式提供，用于跨正文/FAQ/勘误关联，Phase2 使用）**
- 正文、FAQ、勘误的 `headingPath` / `fullPath` 通常**完全不同**，仅靠标题与路径归一化**无法**自动认定它们讲同一条规则。
- 因此跨来源关联**不能靠自动派生**，必须由**导入器、规则映射表或管理员显式提供** `canonicalRuleKey`
  （如 `pf1e:spell:grease`），把同一规则点在不同来源下的文档显式绑定。
- 在 `canonicalRuleKey` 缺失前，正文 / FAQ / 勘误**不被自动认定为同一簇**。

**分层边界（修订 1/2）**：运行时去重必须先以 `ruleLibraryId` 作为硬隔离边界，再在库内使用
`derivedSemanticKey` / `canonicalRuleKey` 分桶。不同游戏系统、不同规则版本、不同规则库**绝不进入同一去重簇**；
同一规则库的重新构建 revision 仍属于同一去重范围，不因构建时间变化而分裂。
`canonicalRuleKey` 可以表达跨版本稳定的规则身份（如 `pf:spell:grease`），但它不能绕过 `ruleLibraryId` 的运行时隔离。
**Phase1 只使用可信的 `derivedSemanticKey`；跨正文/FAQ/勘误的 `canonicalRuleKey` 关联放到 Phase2**，
待来源元数据（§7）与映射表就绪后再启用。

### B. 相似度：trigram shingle Jaccard（非字符集合）

字符集合 Jaccard 丢失顺序/重复/数值位置/否定-条件关系，不同规则因常用字多也会高分。改为：

```
NFKC → 小写 → 统一空白与标点
→ 中文：字符三元组；英文：词组三元组（按空格切词）
→ shingle Jaccard = |A∩B| / |A∪B|
```

- 阈值 `dup_threshold` **不预设最终值**，作为**软**相似度用于桶内成员判定；Phase0 从 **0.90** 起实验，据误合并 / 漏合并结果再校准，**校准前不写死默认值**（见 §9）。
- **Phase1 去重安全否决 `dedupSafetyVeto`**（即使整体高度相似也必须额外检查，避免"不能/可以"被吞掉）：
  `可以/不能`、`是/否`、`必须/可以`、数字、骰子表达式（`d20`/`2d6`）、距离、动作类型、持续时间。
  任一关键字段相反或无法安全对齐 → **Phase1 禁止自动合并、分别成簇保留**，但不向 Agent 输出冲突结论。
  Phase2/3 可在此安全信号基础上进一步判断并透传 `possibleDisagreement`（见 D/E）。

### C. 聚类：semantic_key 分桶 + 代表中心（禁并查集）

链式误合并风险（A~B 0.85、B~C 0.84、A~C 0.60 → 被并查集合并）在规则资料里很危险（基础规则/FAQ/例外形成链式相似）。改用：

1. **先按 `ruleLibraryId` 隔离，再按 `semantic_key` 硬分桶**——跨库、跨版本、跨桶绝不自动合并。
2. **显式簇代表选择规则（修订 3/5）**：每桶的**簇代表**按下述确定性优先级选出，避免"首个候选即代表"带来的不稳定：
   - (a) 重排分数 `score` 最高者优先（与最终可读性一致）；
   - (b) Phase1 分数相同时，取 `sourceId` 字典序最小者（完全确定性，保证可复现）；
   - (c) Phase2 元数据就绪后，如果存在**显式 `supersedes` 关系**，才允许在同分候选间优先
     「声明 supersedes 旧来源的新来源」；被替代的旧来源不得反向获得优先级。
   *Phase1 **不把来源类型（`sourceKind`）本身作为权威顺序**——`core > errata > faq` 之类默认顺序可能让旧正文压过更新后的勘误，
   与"无明确 `supersedes` 不判权威"原则冲突；来源类型只作可读信息透传，不作代表选择依据。*
   后续候选仅当与**簇代表** shingle Jaccard ≥ `dup_threshold` 才入该簇；否则开新簇。
3. **不使用纯传递式并查集**——新候选只对比代表，不对比簇内任意成员，杜绝链式传播。

### D. 潜在冲突检测：检索层只发信号，不在检索侧"保留双方"（边界修订 4）

> 范围说明：冲突 / `possibleDisagreement` 的**完整处理**属于 **Phase2/3**（见 §8）。
> Phase1 不输出冲突判定，但必须执行 §4.B 的 `dedupSafetyVeto`；安全否决只决定「不能自动合并」，不声明两份规则一定冲突。
> 下文描述的是最终目标架构中检索层应承担的**最小职责**。

原顺序"聚类→选代表→冲突标注"会在选代表时丢掉冲突一方。修正为：检索层在聚类后**检测并标记**簇内 `possibleDisagreement` 信号，但**不替用户决定保留哪些来源、也不依赖是否已被读取或 Top-8 上限**：

```
聚类 → 检测簇内 possibleDisagreement 信号 → 随候选一并透传信号
      （检索层只负责"可能存在冲突"的判定与标注，不强制保留双方、不占用读取位）
→ 多样性排序（仅去重，不做冲突裁决）
→ Agent 读取阶段据证据预算与 possibleDisagreement 自行决定是否多读一方
```

**检索层冲突信号验证条件（仅用于"是否标记 possibleDisagreement"，不用于"强制保留双方"）**：
1. 命中的 `possibleDisagreement` 信号来自**同一规则方面**（如同一字段的数值/可否/动作），而非无关段落的偶然词面相反；
2. 冲突信号不是由摘录截断/排版噪声造成（必要时比对更大上下文窗口）。

满足以上即标记 `possibleDisagreement=true` 并随候选返回；**不再要求**"双方必须可被读取"或"保留后不超可读上限"——那是读取阶段的事。

**保留 / 核对由 Agent 决定（边界修订 4）**：是否给两个来源各留读取位，是 Agent 的证据预算决策，不是检索服务的职责。检索服务在任何情况下都**不**以"某来源已/未被读取"或"Top-8 是否放得下"作为冲突判定或去留依据。Agent 在读取阶段若判断应核对冲突双方：
- 预算充足：读取双方，并把 `possibleDisagreement` 信号如实带入答案；
- 预算不足：**宁可声明"尚未完整核对相关来源"**，也不要替用户二选一并下结论。

### E. possibleDisagreement（仅"潜在冲突"，非确定性）

文本不一致 ≠ 规则冲突。第一版只检测**潜在冲突信号**：
- 数值不同 / 骰子表达式不同；
- 肯定 vs 否定（`可以`/`不能`、`是`/`否`）；
- 动作类型不同 / 条件词不同；
- FAQ/勘误标志词：`改为`、`现在`、`应为`、`不再`。

输出键名 **`possibleDisagreement`**（布尔 + 命中的信号列表），**不是**确定性的 `disagreement`。最终是否冲突由**完整证据读取 + 答案阶段**判定。

### F. 不声称"来源一致" + provenance 不冒充已读（安全边界）

- 两份摘录 Jaccard 高，**不能**向模型声明"N 个来源表述一致"——只比较了命中摘录，未进入摘录的部分可能有例外。
- Phase3 最多透传：
  ```json
  { "relatedSourceIds": ["..."], "similarExcerptCount": 3 }
  ```
  不得升级为事实性"一致来源"。
- **provenance 不冒充真实读取**：仅在搜索结果中提示"还有 N 个相似来源"；只有经 `read_rules` 真正读取的文档才能：注册为 `[S1]`、出现在来源抽屉、支撑最终结论。否则破坏"只有已读证据才能引用"的安全边界。

### G. 输出口径：按请求 `limit` 返回多样化候选，不写死 8（边界修订 3）

**边界修订 3**：检索服务**不把 Agent 的 8 条证据预算硬编码为自身的返回上限**。"最多读 8 章"是 Agent 侧的 `max_answer_documents` 约束（见 §1），不是检索的职责。检索侧与搜索 `limit` 一致返回多样化候选，由 Agent 再自行截断到 8；评测单独计算 Top-8 多样性：

- `diversify()` 的取数上限 = 请求传入的 `limit`（与搜索 `limit` 一致，可到 20），**不写死为 8**；
- 先从**不同语义簇**各取 1 个代表（按 C 的代表选择规则）；
- **第一遍**按代表分数从不同语义簇各取 1 个，直到达到 `limit` 或所有簇都已有代表；
- **第二遍**若仍不足 `limit`，再按原始重排分数补入各簇的次级候选；次级候选只能在所有独立簇代表之后出现，
  因而不会挤掉任何已经召回的独立规则点；
- 普通查询只有在独立簇代表不足 `limit` 时，次级来源才会在第二遍低优先级补位；
- 当用户**显式**询问「FAQ 怎么说」「是否有勘误」「不同来源有什么差异」（`is_variant_query` 命中）时，
  被追问的目标来源必须提升到第一遍候选并保证不被去重压制；必要时同簇第二来源也在 `limit` 内返回（见 §6）；
- Agent 在拿到多样化候选后，**自行取前 `max_answer_documents=8`** 作为真正可读章节——这一步属于 Agent，不属检索。

---

## 5. 集成点（最小改动）

在 `coordinator.search` 的 `reranked` 生成之后、`return reranked[:limit]` 之前插入。**边界修订 3**：检索侧按请求 `limit` 多样化，**不写死 8**——8 由 Agent 侧截断。
```python
from .diversity import diversify
if enable_diversity:
    # 按请求 limit 多样化（与搜索 limit 一致），不把 Agent 的 8 条证据预算藏进检索服务
    reranked = diversify(reranked, cap=limit,
                         require_explicit_variants=is_variant_query(query))
return reranked[:limit]  # 搜索 limit 不变；Agent 侧再自行取前 8 作为可读章节
```
- `candidate_limit=50` 已保证有足够候选供多样性挑选，**无需改基检索**。
- `enable_diversity` 开关 + `dup_threshold` 配置化，可灰度、可回退（前置条件 5）。
- `is_variant_query` 识别"FAQ/勘误/差异"类提问，决定是否在同簇内补充第二来源（不压制目标来源）。
- **Top-8 多样性由评测单独计算**：评测对 `diversify` 输出取前 8 计算不同簇覆盖率，与检索返回的 `limit` 解耦。

---

## 6. 验证（重点 Top-8）

- 复用 `evaluation.py` 的 Hit@5 / MRR 做混合检索回归，须不退化。
- **新增近重复覆盖指标（Top-8）**：
  - 同规则多来源构造查询，断言 Top-8 内**不同 semantic_key 簇数 ≥ 阈值**；
  - 当候选池中存在至少 8 个独立簇时，同一 semantic_key 簇在 Top-8 内**近重复 ≤ 1 份**；
    独立簇不足 8 个时允许第二遍补入次级候选，但不得改变第一遍的代表顺序；
  - 断言真正不同的规则点未被挤出前 8。
- **possibleDisagreement 分层探针**：
  - 普通查询：代表候选含 `possibleDisagreement=true`，且 `relatedSourceIds` 能定位待核对的双方；不要求双方都成为顶层候选；
  - 显式差异 / FAQ / 勘误查询：双方都作为顶层候选返回；
  - Agent 核对流程：预算充足时可据相关 ID 读取双方；预算不足时不得输出确定冲突结论，并提示尚未完整核对。
- **显式 FAQ/勘误查询保真（边界修订）**：当用户显式追问某规则的 FAQ 或勘误（如「这条的 FAQ 怎么说」「有没有勘误」「不同来源什么差异」，`is_variant_query` 命中）时，去重**不得压制被追问的目标来源本身**——只要该 FAQ/勘误文档本身落在搜索 `limit` 内，就必须进入候选；重复抑制只作用于"与已选代表同簇的非目标副本"，不得把被追问的目标来源当作冗余丢弃。断言：显式追问查询下，目标 FAQ/勘误文档出现在候选且未被 dedup 过滤。
- 人工抽检：PF1E 多源规则点（如「借机攻击」「法术抗力」）多源表述是否正确聚类且覆盖。

---

## 7. 来源元数据缺口与权威判定（待补）

当前 `RuleDocument` 元数据仅有 `sourceTitle/title/fullPath/version/priority/metadata`（`domain.py`），**不足以**可靠实现"勘误/最新版 > 核心书 > 旧版/FAQ"：
- 缺稳定字段：`sourceKind`、`publicationDate`、`printing`、`supersedes`、`errataFor`、`officialStatus`、`effectiveVersion`。
- 且 PF1e 官方 FAQ 常用于澄清/更新核心规则，**不应固定排在核心书之后**。

**第一版权威策略**：
- 自动优先**仅当存在显式 `supersedes` 关系**时才采用声明替代旧来源的新来源；
- `errataFor` / `clarifies` 只负责把相关来源关联起来并触发核对，不单独产生覆盖关系；
- 否则**不做自动权威排序**，冲突簇内双方并列保留，交由 `possibleDisagreement` 标记 + 答案阶段判断。
- 建议补充来源关系元数据（落地后再启用自动优先）：
  ```json
  {
    "sourceKind": "core | supplement | faq | errata | compilation | guide",
    "officialStatus": "official | translation | community",
    "publicationDate": "...",
    "printing": "...",
    "supersedes": ["source-id"],
    "errataFor": ["source-id"],
    "clarifies": ["source-id"]
  }
  ```

---

## 8. 风险与渐进式上线

| 风险 | 缓解 |
| --- | --- |
| 链式误合并 | semantic_key 硬分桶 + 代表中心聚类，禁并查集 |
| 误并不同规则/版本 | `ruleLibraryId` 硬隔离；跨桶绝不合并；semantic_key 不可靠时不自动去重 |
| 假"一致"声明 | 只透传 relatedSourceIds + similarExcerptCount，不声明事实一致 |
| 差异内容被提前丢弃 | Phase1 合并前执行 `dedupSafetyVeto`；Phase3 在代表抑制前生成冲突信号 |
| provenance 冒充已读 | 仅提示相似来源；引用/抽屉只认 read_rules 文档 |
| 阈值敏感 | `dup_threshold` 配置化 + 回归集标定 |

**推荐上线顺序（修订 6/7：先测量再开发，分 Phase）**：

- **Phase0 — 近重复只读审计（第一项实际工作，不改排序）**：对每次查询的候选池执行两类扫描：
  1. **桶内审计**：按 `ruleLibraryId + derivedSemanticKey` 计算 shingle Jaccard，验证可安全自动去重的候选；
  2. **跨桶探索审计**：在同一 `ruleLibraryId` 的 Top-50 内扫描高相似但 semantic key 不同的候选，
     发现语义键漏检、跨父级重复与潜在误分桶。跨桶结果只用于报告和改进语义键，**绝不直接触发线上去重**。
  Phase0 仅产出**统计报告**（桶内/跨桶近重复簇、潜在冲突信号数、Top-8 挤占比例），**不修改任何排序或返回**。
  这是进入 Phase1 的硬性前置（前置条件 3）。
- **Phase1 — 高置信冗余抑制（检索侧、不改 Agent 协议）**：`enable_diversity` 默认 off，灰度开启；
  阈值从 **0.90 起**，只压明显近重复，并强制执行 `dedupSafetyVeto`；本阶段不输出冲突判定，
  采用「簇代表优先、次级候选补齐」的两遍排序，观测 Top-8 覆盖与回归；
  *注：Phase1 改变的是检索侧排序，并非"协议零变更"——但仅限 retrieval 内部，Agent 协议与 `read_rules` 接口不变。*
- **Phase2 — 元数据驱动来源关系**：待 §7 来源元数据补全（前置条件 4）后，使用
  `supersedes` / `errataFor` / `clarifies` 建立来源关系；只有显式 `supersedes` 能触发自动替代，
  `errataFor` / `clarifies` 只触发关联与核对。`sourceKind` 仅用于分类、过滤与展示，不单独构成权威 tiebreak。
- **Phase3 — possibleDisagreement 显性暴露**：在 Phase1/2 稳定后，将 `possibleDisagreement` 作为显式信号透传给答案阶段，
  并在显式追问时补充同簇第二来源。

> 说明："协议零变更"严格说不成立——Phase1 本就改变检索排序；准确表述是**仅检索侧变更、Agent 协议与已读安全边界不变**。

---

## 9. 待确认

1. `ruleLibraryId + semantic_key` 的分层是否覆盖当前所有文档形态？跨桶审计发现的漏检形态需要补哪些规则？
2. `dup_threshold` **不预设默认值**；Phase0 从 **0.90** 起标定（见 §4.B / 前置条件 4），是否需据误合并 / 漏合并下修？待 Phase0 审计报告确定。
3. 强制信号校验的词典（可以/不能、骰子表达式等）是否够？是否需要补充 PF1e 特有术语？
4. Phase1 `dedupSafetyVeto` 与 Phase3 `possibleDisagreement` 的词典是否需要分别维护？
5. 显式追问识别（`is_variant_query`）用关键词白名单是否足够，还是需更稳的意图判定？
6. §7 来源元数据补充的字段集合是否采纳？是否本阶段就要落地还是仅留接口？
