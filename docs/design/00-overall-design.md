# 从知识库和代码提取并优化 Agent Skill 的总体设计文档

> 本文是项目级总体设计文档，定义从知识库、PDF/Wiki、代码仓库与历史执行轨迹中提取、生成、评测并持续优化 Agent Skill 的整体架构。分模块输入、输出、存储内容与执行细节见本目录下的模块设计文档。

## 1. 背景与目标

Agent Skill 不是知识库摘要，也不是一次性提示词。它是面向特定仓库、业务域或工作流的可复用能力包，用来告诉 Agent 在什么条件下执行什么流程、调用什么工具、遵守什么约束，以及如何验证结果。

本系统的目标是建立一条可审计、可评测、可持续优化的 Skill 生产流水线：

1. 从代码仓库生成代码图谱、模块树与叶子上下文，提取代码中的真实行为约束。
2. 从知识库、PDF、Wiki 等材料生成规范化文档块，保留来源锚点、版本与结构信息。
3. 将代码侧和文档侧证据统一抽取为结构化 `SkillAtom`。
4. 基于 `SkillAtom` 生成候选 Skill，并通过 SkillOpt 式循环进行离线优化。
5. 通过可插拔模型/智能体交互层统一管理 LLM、工具型 Agent 与外部评测 Agent。
6. 通过 CLI 提供人机交互、审批、运行编排、状态查看和发布入口。

最终产物是一组可部署、可回滚、可复现的 Agent Skill 包：

```text
skills/<skill_name>/
├── SKILL.md
├── references/
│   ├── domain-map.md
│   ├── api-contracts.md
│   └── failure-modes.md
└── scripts/
    └── deterministic_checker.py
```

其中 `SKILL.md` 只保留 Agent 高频使用的核心流程、工具策略和判断准则；长事实、详细 API、表格和历史案例进入 `references/`；稳定且可确定执行的检查、格式转换、验证逻辑进入 `scripts/`。

## 2. 设计范围

### 2.1 范围内

| 范围 | 说明 |
|---|---|
| 代码结构理解 | 从仓库快照中抽取文件、符号、调用关系、依赖关系、入口点和模块树 |
| 文档规范化 | 将 Markdown、PDF、Wiki 导出内容转为统一 `DocumentChunk` 与索引 |
| SkillAtom 抽取 | 从代码图谱和规范化文档中抽取可执行、可验证、可追踪的技能原子 |
| Skill 生成与优化 | 生成候选 `SKILL.md`、引用资料和脚本，并用 SkillOpt 循环改进 |
| 模型交互管理 | 统一裸模型、结构化模型调用、其他智能体调用和观测追踪 |
| CLI 编排 | 以命令行方式执行初始化、抽取、优化、审批、评测、发布和恢复 |

### 2.2 范围外

| 非目标 | 说明 |
|---|---|
| 在线 RAG 问答系统 | 本系统生产 Skill，不替代运行时检索问答 |
| 通用代码文档站 | 代码图谱和模块树服务于 Skill 抽取，不以生成完整代码百科为首要目标 |
| 自动无审核发布 | 涉及权限、事实冲突、低置信度规则和发布动作时需要显式审批 |
| 只靠模型自由改写 | Skill 修改必须保留来源、评分、评测结果和拒绝记录 |

## 3. 总体架构

```mermaid
flowchart LR
  C[代码仓库] --> M1[模块 1<br/>代码图谱与模块树]
  D[知识库 / PDF / Wiki] --> M2[模块 2<br/>文档规范化]

  M1 --> M3[模块 3<br/>SkillAtom 抽取]
  M2 --> M3
  T[历史任务轨迹 / CI / Review] --> M3

  M3 --> M4[模块 4<br/>SkillOpt 优化循环]
  M5[模块 5<br/>模型与智能体交互管理] --> M1
  M5 --> M2
  M5 --> M3
  M5 --> M4

  M4 --> S[SkillBundle]
  S --> P[发布 / 回滚 / 在线反馈]
  P --> T

  CLI[模块 6<br/>CLI 人机交互与编排] --> M1
  CLI --> M2
  CLI --> M3
  CLI --> M4
  CLI --> M5
  CLI --> P
```

整体架构按“来源接入 -> 证据规范化 -> 技能原子抽取 -> Skill 优化 -> 发布回流”组织。模块 1 和模块 2 是证据生产层；模块 3 是证据到 Skill 语义的转换层；模块 4 是优化与评测层；模块 5 是模型能力适配层；模块 6 是用户入口和编排层。

**分层依赖规则**：模块 5 是基础设施层，处于最底层。模块 1-4 只允许依赖模块 5 的接口定义（`InteractionBackend` 抽象类、`InteractionRequest`/`InteractionResponse` 等标准类型），不得直接依赖任何具体 backend 实现。模块 4 调用 target Agent 或 optimizer 模型时，通过依赖注入接收已配置好的 backend 实例，避免在模块 4 内部 import 模块 5 的具体类。CLI（模块 6）负责在启动时将配置好的 backend 实例注入各模块。

## 4. 模块划分

| 模块 | 设计文档 | 核心职责 | 主要产物 |
|---|---|---|---|
| 1. 代码仓库到代码图谱与模块树 | [01-code-repo-to-code-graph-module-tree.md](01-code-repo-to-code-graph-module-tree.md) | 解析仓库快照，抽取符号、依赖、调用链、入口点和模块层级 | `graph.json`、`module_tree.json`、`leaf_contexts/` |
| 2. 知识库/PDF/Wiki 到文档规范化 | [02-knowledge-pdf-wiki-normalization.md](02-knowledge-pdf-wiki-normalization.md) | 统一解析多格式文档，恢复结构、切分 chunk、保留来源锚点 | `document_index.json`、`chunks.jsonl`、`tables.jsonl` |
| 3. SkillAtom 抽取 | [03-skillatom-extraction.md](03-skillatom-extraction.md) | 将代码证据和文档证据转成可执行、可验证的技能原子 | `skill_atoms.jsonl`、`conflicts.jsonl`、`bench_seed.jsonl` |
| 4. SkillOpt 优化循环 | [04-skillopt-loop.md](04-skillopt-loop.md) | 基于 rollout、反思、聚合、选择、更新、评测循环优化 Skill | `candidate_skill/`、`history.json`、`eval_report.json` |
| 5. 模型与智能体交互管理 | [05-model-agent-interaction-manager.md](05-model-agent-interaction-manager.md) | 提供可插拔模型、外部 Agent、路由、预算、追踪和结构化输出能力 | `model_trace.jsonl`、`ModelResponse`、`AgentResponse` |
| 6. CLI 人机交互与模块编排 | [06-cli-human-interaction-orchestrator.md](06-cli-human-interaction-orchestrator.md) | 提供命令行入口、运行计划、审批、状态、恢复、发布和报告 | `run_manifest.json`、`run_state.json`、`events.jsonl` |

模块之间通过文件化中间产物和稳定 schema 解耦。除 CLI 外，其它模块应优先设计为可被库调用，也可被单独命令执行。

## 5. 端到端数据流

### 5.1 输入层

系统接收四类输入：

| 输入 | 来源示例 | 使用方式 |
|---|---|---|
| 代码仓库 | Git 仓库、本地目录、指定 commit | 构建代码图谱、模块树、入口点、调用链、测试与配置约定 |
| 知识文档 | Markdown、PDF、Wiki 导出、内部 SOP | 规范化为带来源锚点的 `DocumentChunk` |
| 历史轨迹 | Agent 任务、CI 日志、人工 review、工单 | 抽取失败模式、验证要求和高价值改进点 |
| 运行配置 | 项目配置、模型配置、预算、审批策略 | 控制抽取范围、模型路由、评测策略和发布门禁 |

### 5.2 中间层

核心中间对象包括：

| 对象 | 生产模块 | 消费模块 | 用途 |
|---|---|---|---|
| `SourceManifest` | 模块 1、2、6 | 全部模块 | 记录输入快照、版本、hash、解析器版本 |
| `CodeGraph` | 模块 1 | 模块 3、4 | 表达符号、调用、依赖、入口点和影响范围 |
| `ModuleTree` | 模块 1 | 模块 3、CLI | 支持大仓库分治、模块级抽取和审查 |
| `DocumentChunk` | 模块 2 | 模块 3 | 表达可引用、可追踪的规范化文档片段 |
| `SkillAtom` | 模块 3 | 模块 4 | 表达最小可复用技能规则、约束和验证断言 |
| `BenchmarkItem` | 模块 3、4 | 模块 4 | 评测 Skill 是否提高目标 Agent 表现 |
| `SkillBundle` | 模块 4 | CLI、发布流程 | 可部署 Skill 包及其评测、版本、来源记录 |
| `RunState` | 模块 6 | CLI、恢复流程 | 记录当前运行阶段、失败点、审批状态和产物路径 |

### 5.3 输出层

系统输出三类产物：

1. **发布产物**：`skills/<skill_name>/SKILL.md`、`references/`、`scripts/`、版本元数据。
2. **审计产物**：来源清单、Atom 来源链、优化历史、评测结果、被拒绝修改记录。
3. **运行产物**：CLI 事件、模型调用 trace、审批记录、失败恢复状态。

## 6. 核心数据对象

### 6.1 `SourceManifest`

记录一次运行的输入快照和解析环境，确保结果可复现。

```json
{
  "schema_version": "1.0",
  "manifest_id": "skill-src-2026-06-03",
  "repos": [{"path": "repo/payment", "commit": "abc123"}],
  "documents": [{"path": "docs/paybook.md", "sha256": "..."}],
  "pdfs": [{"path": "docs/CodeWiki_paper.pdf", "sha256": "..."}],
  "extractor_versions": {"codegraph": "local", "document_normalizer": "0.1.0"},
  "created_at": "2026-06-03T00:00:00Z"
}
```

### 6.2 `SkillAtom`

`SkillAtom` 是 Skill 的最小候选单位，必须同时包含规则、适用条件、来源和验证方式。

```json
{
  "schema_version": "1.0",
  "atom_id": "api-error-handling.retry-timeout",
  "kind": "procedure",
  "claim": "调用支付 API 超时时先查询幂等键状态，再按指数退避重试，最多 3 次。",
  "applicability": "支付链路外部 API 调用超时",
  "source_refs": ["doc://paybook.md#timeout", "code://payment/client.py::retry"],
  "checks": ["回答中必须提到幂等键", "不得建议直接重复扣款"],
  "confidence": 0.86
}
```

### 6.3 `SkillBundle`

`SkillBundle` 是候选或已发布 Skill 的完整描述。

```json
{
  "schema_version": "1.0",
  "skill_id": "payment-agent-skill",
  "version": "0.3.0",
  "entry_file": "SKILL.md",
  "token_budget": 1800,
  "included_atoms": ["api-error-handling.retry-timeout"],
  "references": ["references/api-contracts.md"],
  "scripts": ["scripts/check_payment_patch.py"],
  "eval_report": "evals/payment-agent-skill/v0.3.0.json"
}
```

### 6.4 `ModelRequest` 与 `AgentRequest`

模型交互统一通过模块 5 的请求对象进入，避免各模块直接绑定某个供应商、模型名称或外部智能体协议。

```json
{
  "role": "atom_extractor",
  "task": "extract_skill_atoms",
  "input_refs": ["runs/2026-06-03/chunks.jsonl"],
  "output_schema": "SkillAtom[]",
  "budget": {"max_tokens": 4000, "max_cost_usd": 1.5}
}
```

## 7. 运行生命周期

一次完整运行由 CLI 发起，并由 `RunState` 驱动恢复和审计。

```mermaid
sequenceDiagram
  participant User as 用户
  participant CLI as 模块 6 CLI
  participant Code as 模块 1 代码图谱
  participant Doc as 模块 2 文档规范化
  participant Atom as 模块 3 Atom 抽取
  participant Model as 模块 5 模型/Agent 管理
  participant Opt as 模块 4 SkillOpt

  User->>CLI: run config.yaml
  CLI->>Code: build graph/module tree
  CLI->>Doc: normalize docs
  Code-->>CLI: CodeGraph + ModuleTree
  Doc-->>CLI: DocumentChunk index
  CLI->>Atom: extract atoms
  Atom->>Model: structured extraction calls
  Model-->>Atom: SkillAtom candidates
  Atom-->>CLI: atoms + conflicts + bench seeds
  CLI->>User: approve high-risk atoms/conflicts
  User-->>CLI: approval decision
  CLI->>Opt: optimize SkillBundle
  Opt->>Model: rollout/reflect/update/evaluate
  Model-->>Opt: model or agent results
  Opt-->>CLI: best SkillBundle + eval report
  CLI->>User: publish or inspect
```

生命周期分为 7 个阶段：

1. **初始化**：读取配置，创建运行目录，生成 `run_manifest.json`。
2. **证据构建**：并行执行代码图谱构建和文档规范化。
3. **Atom 抽取**：融合代码、文档和历史轨迹，生成 `SkillAtom`、冲突记录和评测种子。
4. **人工审批**：对高风险、低置信度、冲突或权限敏感内容进行确认。
5. **Skill 优化**：执行 SkillOpt 循环，生成候选 Skill 并记录每轮改动。
6. **质量门禁**：在 selection/held-out benchmark 上验证收益和退化风险。
7. **发布回流**：发布通过门禁的版本，并将在线反馈写回轨迹池。

## 8. SkillOpt 优化策略

优化循环采用离线可审计策略，而不是让模型直接覆盖 Skill 文档。核心阶段为：

| 阶段 | 目标 | 主要记录 |
|---|---|---|
| Rollout | 使用当前 Skill 在 benchmark 上执行任务 | 输入、输出、工具调用、得分、失败原因 |
| Reflect | 对失败和低分样本生成改进建议 | 问题归因、候选编辑、关联 Atom |
| Aggregate | 合并相似建议，去重并排序 | 聚类结果、支持证据、风险标签 |
| Select | 选择进入候选更新的编辑 | 被采纳和被拒绝的理由 |
| Update | 生成候选 `SKILL.md`、`references/`、`scripts/` | diff、版本号、来源链 |
| Evaluate / Gate | 在验证集和保留集上评估候选版本 | 分数、退化项、发布结论 |

每次更新必须满足：

1. 能追溯到一个或多个 `SkillAtom`、rollout 失败样本或人工审批记录。
2. 有明确适用条件，不能将局部经验泛化为全局规则。
3. 不超过配置的 `SKILL.md` token 预算。
4. 在关键 benchmark 上不产生不可接受退化。
5. 被拒绝的修改进入 rejected-edit buffer，防止后续循环反复提出同类无效编辑。

## 9. 质量门禁

### 9.1 内容质量

| 门禁 | 要求 |
|---|---|
| 来源完整性 | 核心规则必须有文档、代码或轨迹来源；无来源内容不得进入发布版 |
| 可执行性 | `SKILL.md` 中的规则应表达为条件、动作或验证要求 |
| 去重与压缩 | 重复、单例事实、训练样本痕迹应被移除或下沉到引用材料 |
| 冲突处理 | 未解决冲突不得写入核心 Skill，只能进入冲突引用或审批队列 |
| 上下文预算 | `SKILL.md` 保持轻量；长资料进入 `references/` |

### 9.2 评测质量

| 门禁 | 要求 |
|---|---|
| 基线对比 | 候选 Skill 必须与无 Skill 或旧版 Skill 对比 |
| 保留集 | 发布前必须通过未参与优化的 held-out 任务 |
| 退化检查 | 关键任务和安全约束不能明显退化 |
| 可复现 | 评测配置、输入、模型路由、随机种子和产物路径必须记录 |
| 人工抽检 | 高风险领域需要人工抽检样本和发布确认 |

## 10. 工程目录建议

```text
code-to-skill/
├── docs/
│   ├── requirements.md
│   └── design/
│       ├── 00-overall-design.md
│       ├── 01-code-repo-to-code-graph-module-tree.md
│       ├── 02-knowledge-pdf-wiki-normalization.md
│       ├── 03-skillatom-extraction.md
│       ├── 04-skillopt-loop.md
│       ├── 05-model-agent-interaction-manager.md
│       └── 06-cli-human-interaction-orchestrator.md
├── external/
│   ├── CodeWiki/
│   ├── codegraph/
│   └── SkillOpt/
├── runs/
│   └── <run_id>/
│       ├── run_manifest.json
│       ├── run_state.json
│       ├── code/
│       ├── docs/
│       ├── atoms/
│       ├── optimization/
│       └── reports/
├── skills/
│   └── <skill_name>/
├── configs/
│   └── <project>.yaml
└── src/
    ├── code_graph/
    ├── document_normalizer/
    ├── atom_extractor/
    ├── skillopt_loop/
    ├── model_gateway/
    └── cli/
```

`external/` 只承载参考实现和论文相关代码，不作为生产运行时的直接依赖边界。生产实现应在 `src/` 中封装，按接口吸收参考实现中的算法和设计。

## 11. 参考实现关系

| 参考来源 | 可借鉴内容 | 本系统中的位置 |
|---|---|---|
| `external/CodeWiki` | 代码依赖图、模块聚类、面向模块的文档生成流程 | 模块 1 的模块树构建与上下文分包 |
| `external/codegraph` | 符号搜索、调用关系、影响分析、上下文检索接口 | 模块 1 的图谱查询与模块 3 的代码证据对齐 |
| `external/SkillOpt` | rollout、reflect、aggregate、select、update、evaluate 循环 | 模块 4 的优化主循环和发布门禁 |
| `docs/skillopt_2605.23904.pdf` | 将 Skill 视为可优化外部状态的训练思路 | 模块 4 的整体算法设计 |
| `docs/CodeWiki_paper.pdf` | 大仓库分层理解、上下文控制和模块级文档生成 | 模块 1 与模块 3 的大仓库分治策略 |

参考实现只提供结构与算法启发。实际落地时应通过本系统 schema、CLI、模型交互层和质量门禁重新封装，避免把研究代码中的路径假设、模型调用方式或临时评测逻辑直接暴露给生产流程。

## 12. MVP 路线

### Phase 0：资料准备

- 固定一个目标仓库、一个知识库目录和一组 PDF/Wiki 导出材料。
- 建立 `config.yaml`、运行目录规范和模型路由配置。
- 准备最小 benchmark：10 到 20 个真实任务或历史失败样本。

### Phase 1：离线证据构建

- 实现模块 1 的仓库快照、符号抽取、依赖图和模块树。
- 实现模块 2 的 Markdown/PDF 规范化和 chunk 索引。
- 输出可人工审查的 `CodeGraph`、`ModuleTree` 和 `DocumentChunk`。

### Phase 2：SkillAtom 与初版 Skill

- 实现模块 3 的候选规则抽取、证据对齐、冲突检测和置信度评分。
- 生成初版 `SkillBundle`，先人工审查再进入优化。

### Phase 3：优化与门禁

- 接入模块 5 的模型/Agent 路由。
- 实现模块 4 的单轮 SkillOpt 循环和 held-out gate。
- 通过 CLI 执行 `run`、`inspect`、`approve`、`eval`、`publish`。

### Phase 4：回流与规模化

- 接入真实 Agent 任务轨迹、CI 日志和人工 review。
- 支持多仓库、多 Skill、多模型后端和增量更新。
- 建立 rejected-edit buffer 与发布回滚策略。

## 13. 产物版本兼容性策略

系统各模块产出的中间文件（`graph.json`、`chunks.jsonl`、`skill_atoms.jsonl` 等）在迭代中 schema 会演进。为避免模块间隐性不兼容，所有中间产物必须遵守以下规则：

### 13.1 强制 `schema_version` 字段

每个模块产出的 JSON/JSONL 文件必须包含顶层 `schema_version` 字段（语义化版本 `MAJOR.MINOR`）。消费模块在读取时必须校验：

- **MAJOR 不变**：向后兼容，可正常读取。
- **MAJOR 升级**：消费模块必须拒绝读取，报告不兼容错误，并建议重新运行生产模块。

示例：

```json
{
  "schema_version": "1.0",
  "nodes": [...],
  "edges": [...]
}
```

### 13.2 各模块产物当前目标版本

| 产物 | 生产模块 | `schema_version` | 说明 |
|---|---|---|---|
| `graph.json` | 模块 1 | `1.0` | 代码图谱节点与边 |
| `module_tree.json` | 模块 1 | `1.0` | 模块分层结构 |
| `leaf_contexts/*.json` | 模块 1 | `1.0` | 叶子模块上下文包 |
| `chunks.jsonl` | 模块 2 | `1.0` | 规范化文档块 |
| `tables.jsonl` | 模块 2 | `1.0` | 结构化表格 |
| `document_index.json` | 模块 2 | `1.0` | 文档结构索引 |
| `merged_atoms.jsonl` | 模块 3 | `1.0` | 已合并 SkillAtom |
| `benchmark_seeds.jsonl` | 模块 3 | `1.0` | 评测种子 |
| `best_skill.md` | 模块 4 | `1.0` | 最终 Skill 产物 |
| `history.json` | 模块 4 | `1.0` | 训练历史 |
| `run_state.json` | 模块 6 | `1.0` | 运行状态 |

### 13.3 版本升级流程

1. 生产模块升级 schema 时，递增 `MAJOR` 或 `MINOR`。
2. 在模块设计文档中记录 changelog。
3. 消费模块同步更新读取逻辑以支持新版本。
4. 旧版本产物不会被自动迁移——需通过 CLI 的 `--from-step` 重跑生产模块。

## 14. 主要风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| Skill 变成文档堆积 | `SKILL.md` 过长、事实太多、流程不清 | 用 token 预算、Atom 类型和可执行性门禁控制 |
| 模型幻觉进入 Skill | 无来源规则被写入发布版 | 强制 source refs、置信度评分、人工审批和 held-out 评测 |
| 代码与文档冲突 | 文档 SOP 和实际代码行为不一致 | 冲突进入 `conflicts.jsonl`，不得自动写入核心 Skill |
| 优化过拟合 | benchmark 分数提升但真实任务退化 | 使用 selection split、held-out set、退化检查和拒绝记录 |
| 模型供应商绑定 | 各模块直接调用固定模型 | 统一走模块 5 的 provider/router/capability 抽象 |
| 运行不可恢复 | 长任务失败后无法定位阶段 | CLI 维护 `run_state.json`、事件日志和幂等输出目录 |

## 15. 成功标准

系统达到可用状态时，应满足以下标准：

1. 给定一个仓库和一组文档，能够生成可审查的代码图谱、文档索引和 `SkillAtom`。
2. 每条进入核心 Skill 的规则都有来源、适用条件和验证方式。
3. 候选 Skill 能在 benchmark 上相对基线产生可解释提升。
4. 发布过程能记录版本、diff、评测报告和回滚路径。
5. 替换模型或改为调用外部智能体时，不需要修改模块 1 到模块 4 的业务逻辑。
6. CLI 能支持从初始化到发布的完整人机协作流程。
