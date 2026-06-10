# 模块 6：CLI 人机交互与模块编排

## 1. 模块目标

本模块提供统一 CLI，用于让人以交互式或脚本化方式调用前 5 个模块，完成从数据准备、抽取、优化到发布审计的完整流程。

CLI 的目标不是替代各模块内部逻辑，而是作为人机协作入口：

- 引导用户选择知识库、PDF、Wiki、代码仓库、目标领域和输出目录。
- 调用模块 1 到模块 5，并传递标准配置和产物路径。
- 展示运行进度、诊断、成本、失败原因和下一步建议。
- 对高风险动作做人类确认，例如联网调用模型、写工作区、运行智能体、发布 Skill。
- 支持自动化脚本调用，便于 CI 或定时任务接入。

## 2. 输入要求

### 2.1 必填输入

| 输入 | 类型 | 要求 |
|---|---|---|
| `workspace` | path | 本次运行的工作目录 |
| `project_config` | YAML/JSON | 项目级配置，包含数据源、模块参数、模型路由和输出目录 |
| `command` | CLI command | 用户要执行的命令，例如 `init`、`run`、`status` |
| `mode` | enum | `interactive` 或 `batch` |

### 2.2 可选输入

| 输入 | 用途 |
|---|---|
| `--domain` | 指定领域，例如 payment、monitoring |
| `--repo` | 指定代码仓库路径 |
| `--docs` | 指定文档路径或目录 |
| `--config` | 指定配置文件 |
| `--from-step` | 从指定模块恢复运行 |
| `--to-step` | 运行到指定模块停止 |
| `--approve` | 非交互模式下允许的审批项 |
| `--dry-run` | 只校验配置和计划，不执行。支持 `--dry-run-level` 指定深度 |
| `--json` | 输出机器可读 JSON |
| `--verbose` | 输出详细日志 |

### 2.3 输入约束

- CLI 不直接绕过模块接口读写内部产物，必须通过各模块定义的输入/输出契约调用。
- 批处理模式下不得默认执行高风险动作；必须通过显式参数或配置授权。
- 涉及模型调用、联网、Agent 写工作区、执行 shell 命令时必须展示或记录审批信息。
- CLI 必须支持断点恢复，不应因为单个模块失败而破坏已完成产物。

### 2.4 `config.yaml` schema

权威模板：仓库根目录 [`config.template.yaml`](../../config.template.yaml)。顶层两段：

| 段 | 主要内容 |
|---|---|
| `project` | `name`、`benchmark`、`initial_skill`、`sources.repos/docs`、`code_graph.custom_patterns`、`graph_role_hints` |
| `settings` | `code_graph`、`document_normalizer`、`atom_extractor`、`skillopt`、`self_evolution`、`pipeline`、`model_provider`、`output`、`approvals` |

最小示例：

```yaml
project:
  name: my-project
  benchmark: benchmarks/my-project
  initial_skill: skills/my-project/SKILL.md
  sources:
    repos:
      - id: main
        path: /path/to/repo
        include: ["src/**"]

settings:
  skillopt:
    use_llm_rollout: true
    num_epochs: 5
  self_evolution:
    enabled: false
  pipeline:
    validate_context_refs: true
    run_atoms_when_benchmark_present: false
  model_provider:
    routes:
      target: { primary: deepseek }
      optimizer: { primary: deepseek }
      judge: { primary: deepseek }
  output:
    root: runs/
```

运行 `skill-lab config validate` 可打印生效配置与未接线项。

## 3. 输出与存储内容

推荐目录：

```text
runs/<run_id>/
├── run_config.resolved.yaml
├── run_manifest.json
├── run_state.json
├── events.jsonl
├── approvals.jsonl
├── module_outputs.json
├── sources/
│   ├── code/
│   └── docs/
├── atoms/
├── benchmarks/
├── optimization/
├── model_interactions/
├── reports/
│   ├── summary.md
│   ├── diagnostics.md
│   └── publish_checklist.md
└── logs/
    ├── cli.log
    └── module_errors.log
```

### 3.1 `run_manifest.json`（✅ Phase 4）

由 `PipelineRunRecorder` 写入，记录 M1–M4 各阶段 `status` / `skip_reason` / `duration_sec` / `artifacts` / `metrics`。M4 失败时仍写入 `status: failed`。`inspect run` 直接消费。

```json
{
  "schema_version": "1.0",
  "run_id": "fineract-finance-20260603-001",
  "domain": "fintech",
  "created_at": "2026-06-03T00:00:00Z",
  "status": "completed",
  "duration_sec": 842.5,
  "effective_settings": {},
  "phases": [
    {"phase": "m1_code_graph", "status": "skipped", "skip_reason": "..."},
    {"phase": "m4_skillopt", "status": "completed", "metrics": {"best_score": 0.72}}
  ],
  "summary": {"best_score": 0.72, "train_items": 7},
  "flags": {"command": "run all"},
  "effective_settings": {}
}
```

### 3.2 `run_state.json`

用于断点恢复。

```json
{
  "schema_version": "1.0",
  "run_id": "fineract-finance-20260603-001",
  "status": "running",
  "current_module": "skillatom_extraction",
  "completed_modules": [
    "code_graph_module_tree",
    "document_normalization"
  ],
  "failed_modules": [],
  "artifacts": {
    "code_graph": "runs/fineract-finance-20260603-001/sources/code/fineract/develop/graph.json",
    "doc_chunks": "runs/fineract-finance-20260603-001/sources/docs/fineract-user-manual/v1.10.0/chunks.jsonl"
  }
}
```

### 3.3 `events.jsonl`

记录面向用户和自动化系统的事件流。

```json
{
  "schema_version": "1.0",
  "ts": "2026-06-03T00:00:00Z",
  "level": "info",
  "module": "document_normalization",
  "event": "chunks_written",
  "message": "Wrote 128 document chunks.",
  "artifact": "runs/fineract-finance-20260603-001/sources/docs/fineract-user-manual/v1.10.0/chunks.jsonl"
}
```

### 3.4 `approvals.jsonl`

记录用户确认。

```json
{
  "schema_version": "1.0",
  "approval_id": "appr-001",
  "requested_action": "invoke_agent_cli_with_workspace_write",
  "module": "skillopt_loop",
  "decision": "approved",
  "scope": "run_id=fineract-finance-20260603-001",
  "ts": "2026-06-03T00:00:00Z"
}
```

## 4. CLI 命令设计

### 4.1 总览

```text
skill-lab init
skill-lab config validate
skill-lab run <pipeline|module>
skill-lab status [run_id]
skill-lab inspect <artifact>
skill-lab approve <approval_id>
skill-lab eval <skill>
skill-lab publish <run_id>
skill-lab resume <run_id>
```

### 4.2 `init`

初始化项目目录和配置模板。

```bash
skill-lab init --workspace ./agent-skill-lab --domain fintech
```

生成：

- `project.yaml`
- `runs/`
- `skills/`
- `configs/`
- `fixtures/`

### 4.3 `config validate`

校验配置，不执行模块。

检查内容（L1 `config-only`）：

- 数据源路径存在。
- 模型路由与 **生效配置表**（`build_effective_settings_report`：M1/M2/M3/M4 已接线项）。
- 必需密钥通过环境变量存在，但不打印明文。

L2 `static-analysis`：在 L1 基础上对每个 repo 做文件扫描 + 符号解析（无 LLM 聚类），对 `local_file` 文档做格式解析（无 OCR）。实现：`cli/static_analysis.py`。

L3 `full-simulate`：L2 + M1–M4 全流程，全部 LLM 走 `MockReplayBackend` + 内置 fixture（`cli/full_simulate.py`）。

**`--dry-run` 三级模式**：

`--dry-run` 不是简单的布尔开关，而是支持三级深度控制：

```bash
skill-lab run all --dry-run                # 默认 Level 1
skill-lab run all --dry-run-level config-only    # Level 1
skill-lab run all --dry-run-level static-analysis # Level 2
skill-lab run all --dry-run-level full-simulate   # Level 3
```

| Level | 名称 | 执行内容 | 不执行的内容 |
|---|---|---|---|
| L1 `config-only` | 配置校验 | 校验 YAML schema、路径存在性、环境变量、模块间输入/输出路径一致性、审批策略完整性 | 任何模块代码、任何 LLM 调用 |
| L2 `static-analysis` | 静态分析 | L1 + 运行模块 1 的文件清单和符号抽取（不调用 LLM 聚类）、模块 2 的格式解析（不调用 OCR） | LLM 聚类、OCR、模块 3-4 全部内容 |
| L3 `full-simulate` | 完整模拟 | L2 + 按正常流程走完所有模块，但所有模型调用使用 `MockReplayBackend`（返回固定 mock 响应） | 真实 LLM/Agent 调用 |

默认 dry-run 为 L1。L3 使用内置 fixture：`cli/fixtures/full_simulate/mock-backend/responses.json`（✅ 已实现 `cli/full_simulate.py`）。

```bash
skill-lab config --dry-run-level full-simulate
skill-lab run all --dry-run --dry-run-level full-simulate
```

### 4.4 `run`

运行单个模块或全流程。

```bash
skill-lab run all --config-path config.yaml
skill-lab run all --dry-run --dry-run-level full-simulate
skill-lab run code-graph --repo fineract
skill-lab run normalize-docs --docs ./kb/fineract/user-manual.md
skill-lab run extract-atoms --from runs/<run_id>
skill-lab run bootstrap-benchmark --from-run runs/<run_id> [--merge]
skill-lab run optimize-skill --benchmark benchmarks/fineract -o runs/<id>/optimization
skill-lab run training-curve plot <run_id>
```

支持范围：

| 名称 | 调用模块 | 说明 |
|---|---|---|
| `code-graph` | M1 | 构建 `graph.db` |
| `normalize-docs` | M2 | 文档规范化 |
| `extract-atoms` | M3 | 必须 `--from <run_dir>`（含 M1/M2 产物） |
| `bootstrap-benchmark` | M3→benchmark | 高置信种子写入 `train/items.json` |
| `optimize-skill` | M4 | SkillOpt；`--resume` 续训 |
| `training-curve` | M4 可观测 | 子命令 `plot` / `backfill` |
| `all` | M1→M4 | 见下方编排 flag |
| `model-check` | M5 | 模型连通性（预留） |

**`run all` 编排 flag**（`settings.pipeline` 可设默认值）：

| Flag | 作用 |
|------|------|
| `--with-atoms` | 有 benchmark 时仍跑 M3 |
| `--with-docs` | 跳过 M3 时仍跑 M2 |
| `--bootstrap-benchmark` | M3 高置信种子填充/合并 train |
| `--merge-benchmark` | 与 bootstrap 同用：追加而非覆盖 train |
| `--suggest-skill-rules` | 高置信 atom 追加 `### Auto-suggested rules` |
| `--resume-run-id` | 复用 run 目录，跳过 M1–M3（有 graph.db） |
| `--dry-run` + `--dry-run-level` | L1 配置 / L2 静态分析 / L3 mock 全流程 |

### 4.5 `status`

查看运行状态。

```bash
skill-lab status fineract-finance-20260603-001
```

输出：

- 当前模块。
- 已完成模块。
- 失败模块。
- 最近事件。
- 等待审批项。
- 关键产物路径。

### 4.6 `inspect`

查看产物摘要。

```bash
# Run 级汇总（推荐）
skill-lab inspect run <run_id>

# 单文件
skill-lab inspect file runs/<run_id>/optimization/best_skill.md
skill-lab inspect file runs/<run_id>/atoms/merged_atoms.jsonl
```

**`inspect run`** 输出：`run_manifest.json` 各阶段 skip/耗时、`history.json` gate 历史（近 5 步）、`test_report`、`context_ref_report` 解析率、`artifact_contract` sidecar、`training_curve` 路径、最近 step `metrics.json`（证据命中 / custom reflect / scenario_rules）。

### 4.7 `approve`

审批等待中的高风险动作。

```bash
skill-lab approve appr-001
skill-lab approve appr-001 --deny
```

常见审批项：

- 调用联网模型。
- 调用 Agent CLI 并允许写工作区。
- 执行测试命令。
- 覆盖已有输出目录。
- 发布 Skill。

### 4.8 `eval`

对指定 Skill 运行评测。

```bash
skill-lab eval runs/fineract-finance-20260603-001/optimization/best_skill.md --split test
```

调用模块 4 的 eval-only 能力，并通过模块 5 调用 target/judge。

### 4.9 `publish`

发布通过门禁的 Skill。

```bash
skill-lab publish fineract-finance-20260603-001 --target ~/.codex/skills/fineract-agent
```

发布前必须检查：

- test 分数已生成。
- best_skill 有来源和版本。
- 敏感信息扫描通过。
- 高风险规则人工 review 通过。
- 发布目标目录不会覆盖未备份内容。

### 4.10 `resume`

从 `run_state.json` 恢复。

```bash
skill-lab resume fineract-finance-20260603-001
```

恢复策略：

- 已完成模块默认跳过。
- 失败模块可重试。
- 允许 `--from-step` 强制从指定模块重跑。
- 重跑时不删除旧产物，写入新 run 或新版本目录。

## 5. 执行过程

### 5.1 流程图

```mermaid
flowchart TD
  A[用户输入命令] --> B[加载配置]
  B --> C[校验与解析执行计划]
  C --> D{需要审批?}
  D -->|是| E[交互确认或读取预授权]
  D -->|否| F[调用模块]
  E --> F
  F --> G[写事件与状态]
  G --> H{模块成功?}
  H -->|否| I[保存错误并给出恢复建议]
  H -->|是| J{还有模块?}
  J -->|是| F
  J -->|否| K[生成 summary/report]
```

### 5.2 步骤 1：加载配置

CLI 合并配置优先级：

1. 命令行参数。
2. `project.yaml`。
3. 环境变量。
4. 默认配置。

合并后写入 `run_config.resolved.yaml`。

### 5.3 步骤 2：构建执行计划

执行计划包含：

- 模块顺序。
- 输入产物路径。
- 输出产物路径。
- 需要的模型/Agent route。
- 需要审批的动作。
- 可恢复检查点。

示例：

```json
{
  "steps": [
    {"module": "code_graph_module_tree", "action": "run"},
    {"module": "document_normalization", "action": "run"},
    {"module": "skillatom_extraction", "action": "run"},
    {"module": "skillopt_loop", "action": "run"}
  ]
}
```

### 5.4 步骤 3：审批处理

交互模式：

- CLI 展示动作、影响范围、权限和成本估算。
- 用户输入确认或拒绝。
- 决策写入 `approvals.jsonl`。

批处理模式：

- 只允许执行 `project.yaml` 或命令行中预授权的动作。
- 未授权动作返回 `approval_required`，不中断已完成产物。

### 5.5 步骤 4：模块调用

CLI 调用模块时传递标准上下文：

```json
{
  "schema_version": "1.0",
  "run_id": "fineract-finance-20260603-001",
  "workspace": "/abs/path/agent-skill-lab",
  "input_paths": {},
  "output_root": "runs/fineract-finance-20260603-001",
  "model_provider": "config.yaml#settings.model_provider",
  "mode": "interactive"
}
```

模块返回：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "artifacts": {},
  "metrics": {},
  "warnings": [],
  "next_actions": []
}
```

### 5.6 步骤 5：事件与状态更新

每个模块开始、结束、警告、失败、审批、产物写入都记录事件。

CLI 必须保证：

- 事件追加写入，不覆盖。
- 状态文件原子写入。
- 崩溃后可以从最后一个完成模块恢复。

### 5.7 步骤 6：报告生成

全流程完成后生成：

- `reports/summary.md`：人读摘要。
- `reports/diagnostics.md`：警告、失败、低置信产物。
- `reports/publish_checklist.md`：发布前检查项。

## 6. 人机交互设计

### 6.1 交互原则

- 默认展示用户需要决策的信息，不刷屏展示内部日志。
- 高风险动作必须清楚说明会读什么、写什么、调用什么外部服务。
- 每个失败都给出“重试、跳过、查看日志、修改配置”的下一步。
- 所有交互都可被 `--json` 或 batch 模式替代，避免阻塞自动化。

### 6.2 典型交互

```text
? Select pipeline:
  > Full: code graph -> docs -> atoms -> optimize
    Only normalize documents
    Only optimize existing skill

? This run will call external model route `optimizer.deepseek`.
  Estimated max cost: $3.20.
  Approve? [y/N]

? SkillOpt target is `codex-cli-target` with workspace write enabled.
  Workspace: /abs/path/fineract
  Approve? [y/N]
```

## 7. 与其它模块的接口

| CLI 命令 | 调用模块 | 输入 | 输出 |
|---|---|---|---|
| `run code-graph` | 模块 1 | repo config | graph/module tree |
| `run normalize-docs` | 模块 2 | docs config | chunks/tables/assets |
| `run extract-atoms` | 模块 3 | graph + docs | SkillAtom |
| `run optimize-skill` | 模块 4 | initial skill + benchmark | best_skill |
| `run model-check` | 模块 5 | interaction config | backend health report |
| `eval` | 模块 4 + 5 | skill + benchmark | eval report |
| `publish` | 发布模块/文件系统 | best_skill + checklist | deployed Skill |

## 8. 安全与权限

| 风险 | 控制 |
|---|---|
| 覆盖用户文件 | 发布和重跑前检查目标路径，默认备份 |
| 执行危险命令 | CLI 不直接执行任意命令，只请求模块 5 的受控 Agent 后端 |
| 泄漏密钥 | 配置中只引用 env var；日志和事件脱敏 |
| 批处理误执行高风险动作 | batch 模式必须显式 `--approve` |
| 长任务中断 | run_state 支持恢复 |
| 错误产物被发布 | publish 前强制读取门禁报告 |

## 9. 质量校验

| 校验项 | 通过标准 |
|---|---|
| 命令可发现 | `--help` 覆盖所有命令和关键参数 |
| 配置可验证 | `config validate` 不执行副作用 |
| 状态可恢复 | 中断后 `resume` 能继续或明确失败原因 |
| 事件完整 | 每个模块 start/end/fail 均有事件 |
| 审批可审计 | 高风险动作都有 approval 记录 |
| JSON 输出稳定 | `--json` 输出 schema 稳定 |
| 跨平台 | 路径处理支持 macOS/Linux，避免 shell 特有假设 |

## 10. 失败处理

| 失败 | 处理 |
|---|---|
| 配置缺失 | 提示缺失字段和示例 |
| 数据源不存在 | 中止当前模块，保留 run_state |
| 模块返回失败 | 写入 module_errors.log，展示恢复命令 |
| 审批被拒 | 标记 skipped_by_user，不执行后续依赖模块 |
| 运行中断 | 下次 `resume` 从最后完成模块继续 |
| 报告生成失败 | 保留模块产物，允许单独重跑 report |

## 11. MVP 范围

MVP 必须实现：

- `init`
- `config validate`
- `run all`
- `run <single-module>`
- `status`
- `resume`
- `eval`
- `--json`
- approvals 记录
- run_state 和 events

MVP 可以暂缓：

- TUI 仪表盘。
- Web UI。
- 多用户权限系统。
- 远程任务队列。
- 复杂插件市场。

## 12. 流水线整合（已实施）

> 状态: Phase -1 ~ P5（2026-06）；仍待 P1-3 role 元数据入 `graph.db`  
> 总览: [00-overall-design.md](00-overall-design.md) §16

### 12.1 问题与目标态

主路径 `skill-lab run all` 曾将数据流压缩为「benchmark + initial_skill + graph.db → M4」，M2/M3 产物多写不读。整合后要求：**每个 run 产物有明确消费者**；M4 启动前产出 `artifact_contract.json` / `context_ref_report.json`；M1 sidecar 与 M3 `evidence_index` 进入 reflect/rollout。

### 12.2 优化原则

1. **单源真相**：任务与验证 → benchmark；领域规则 → initial_skill；代码定位 → graph.db + sidecar；架构词汇 → `project.code_graph.custom_patterns`（仅 M1 标注，M4 按 role 消费）。
2. **写前读**：无消费者的产物标记 deprecated 或按需生成。
3. **配置即契约**：`config.yaml` 键须传入模块或 template 标明预留。
4. **框架无领域硬编码**：项目知识只在 config / benchmark / initial_skill。
5. **先解析再优化**：M4 训练前解析 `context_refs`，写 `context_ref_report.json`。
6. **精确证据优先**：ref 精确命中 → role/entrypoint → evidence_index → `get_code_context` fallback（须计数）。

### 12.3 分阶段实施摘要

| Phase | 目标 | 要点 |
|---|---|---|
| -1 契约 | 可解析性 | `pipeline_config.py`、`artifact_contract.json`、`context_ref_report.json`、step `metrics.json` |
| 0 编排 | 减无效计算 | 有 benchmark 默认跳过 M2/M3；meta_skill 与 slow_update 解耦；`publish_target`；`extract-atoms --from` |
| 1 M1→M4 | 结构化证据 | entrypoints、role_index、evidence_index、context_mode 三模式 |
| 2 M3↔benchmark | 种子协同 | `bootstrap-benchmark`、种子 schema、`atom_extractor` 配置贯通 |
| 3 配置贯通 | 诚实 YAML | `ModuleRunSettings`、`reflect_prompts`、`routes.judge` |
| 4 可观测 | inspect / curve | `run_manifest.json`、`training_curve.svg`、`inspect run` |
| 5 工具统一 | MCP + Handler | `build_code_tools_handler()` |

### 12.4 `settings.pipeline` 与契约产物

见本文 §2 配置示例 `pipeline:` 段。关键产物：

| 产物 | 写入时机 | 消费者 |
|---|---|---|
| `optimization/artifact_contract.json` | M4 启动前 | M4、`inspect run` |
| `optimization/context_ref_report.json` | split 加载后 | M4、`inspect run` |
| `steps/step_*/metrics.json` | 每 reflect/gate step | `training_curve`、`inspect run` |

### 12.5 实现索引

| 区域 | 路径 |
|---|---|
| 契约 / 编排 | `cli/pipeline_config.py`、`cli/run_manifest.py`、`cli/benchmark_bootstrap.py` |
| M1 sidecar | `code_graph/role_index.py` |
| M4 证据 | `skillopt_loop/code_evidence.py`、`graph_sidecars.py` |
| 可观测 | `cli/inspect_run.py` |
| 测试 | `tests/test_pipeline_config.py`、`test_context_mode.py` |

## 13. Skill 自进化 CLI

> 算法与产物: [04-skillopt-loop.md](04-skillopt-loop.md) §13  
> 配置: `settings.self_evolution`（默认 `enabled: false`）

### 13.1 命令

```bash
# 完整自进化：trace pool + proposals + 严格 gate + 归因
skill-lab run optimize-skill --self-evolve

# 仅轨迹聚类与 proposal 归纳（不改严格 gate）
skill-lab run optimize-skill --trace-merge

# 离线 hygiene（合并/删除冗余规则，经 selection gate）
skill-lab run skill-hygiene <run_id>

# 可观测
skill-lab inspect run <run_id> --trace-pool
skill-lab inspect run <run_id> --rule-attribution
skill-lab inspect run <run_id> --validate-self-evolution

# 发布时移除 rule_id 注释
skill-lab publish ... --strip-rule-ids
```

### 13.2 与 `run all` 的关系

- 默认有 `initial_skill` + benchmark 时跳过 M2/M3，仍完整跑 M4。
- 需要 `artifact_quality.json` 或 M3 种子路径时加 `--with-atoms`。
- `--self-evolve` / `--trace-merge` 在 `run all` 与 `optimize-skill` 上均可使用（见 `config.template.yaml`）。

### 13.3 成功指标（运维参考）

| 指标 | 说明 |
|---|---|
| gate 可信度 | accepted 候选在 test 上不下降 |
| Skill 膨胀 | `best_skill.md` token 稳定在 `max_skill_tokens` 内 |
| proposal 质量 | accepted proposal 平均 `support_count >= 2` |
| 证据利用 | accepted edits 含 `evidence_refs` 比例上升 |
