# code-to-skill

从知识库和代码仓库中提取、生成并持续优化 **Agent Skill** 的离线流水线。

> Agent Skill 不是知识库摘要，而是告诉 Agent 在什么条件下执行什么流程、调用什么工具、遵守什么约束的可复用能力包。

设计文档见 [docs/design/](docs/design/)（`00` 总体设计；流水线整合见 [06-cli-human-interaction-orchestrator.md](docs/design/06-cli-human-interaction-orchestrator.md) §12；Skill 自进化见 [04-skillopt-loop.md](docs/design/04-skillopt-loop.md) §13 / [06](docs/design/06-cli-human-interaction-orchestrator.md) §13）。

## 快速开始

```bash
# 安装（建议在仓库根目录）
pip install -e .
# 可选: OCR
pip install -e ".[ocr]"

# API Key：在项目根目录放置 .env（启动时自动加载）
#   DEEPSEEK_API_KEY=...
#   DEEPSEEK_BASE_URL=https://api.deepseek.com

# 复制并编辑配置（完整注释模板见 config.template.yaml）
cp config.template.yaml config.yaml

# 拉取示例源码（Fineract clone，不纳入 git）
./demo-project/fetch-sources.sh

# 环境诊断 + 配置校验
skill-lab doctor --config-path config.yaml
skill-lab config --config-path config.yaml

# 完整流水线 M1→M4（fineract-fast 示例见 config.yaml）
skill-lab run all --config-path config.yaml --with-atoms
```

若已配置 `initial_skill` + benchmark，默认会**跳过 M2/M3**；要跑齐四段模块请加 **--with-atoms**。开发中若 CLI 未重装，可在命令前加 `PYTHONPATH=src`。

## 流水线

```
代码仓库 ─→ M1 代码图谱 ─→ M3 SkillAtom ─→ M4 SkillOpt ─→ SKILL.md
知识文档 ─→ M2 文档规范化 ─┘         │              (best_skill.md)
  ↑                                  │
M5 模型/Agent 交互层 (基础设施)       └─ M4 自进化 self_evolution（可选）
M6 CLI 编排层 (贯穿)
```


| 模块  | 职责            | 输入                        | 输出                                                                   |
| --- | ------------- | ------------------------- | -------------------------------------------------------------------- |
| M1  | 代码图谱与模块树      | Git 仓库/本地目录               | `graph.db`、`graph.json`、`leaf_contexts/`                             |
| M2  | 知识库文档规范化      | Markdown/PDF/HTML/DOCX    | `chunks.jsonl`、`tables.jsonl`                                        |
| M3  | SkillAtom 抽取  | M1 + M2 产物                | `merged_atoms.jsonl`、`artifact_quality.json`、`benchmark_seeds.jsonl` |
| M4  | SkillOpt 优化循环 | initial_skill + benchmark | `best_skill.md`、`history.json`、`trace_pool/`（可选）                     |
| M5  | 模型与 Agent 交互  | InteractionRequest        | `traces/`、ModelResponse                                              |
| M6  | CLI 人机交互      | 命令行 / config.yaml         | `run_manifest.json`、`logs/run.log`                                   |


---

## CLI 命令参考

入口为 **skill-lab**。全局选项：多数命令支持 `--config-path config.yaml`（默认 `config.yaml`）。

### 命令总览


| 命令                            | 用途                              | 常用选项                                           |
| ----------------------------- | ------------------------------- | ---------------------------------------------- |
| `init`                        | 初始化项目骨架与 config 模板              | `--workspace`, `--name`, `--domain`            |
| `doctor`                      | 环境诊断（tree-sitter、配置、数据源路径）      | `--config-path`                                |
| `config`                      | 校验配置并打印生效 wired 设置              | `--dry-run-level` L1/L2/L3                     |
| `run all`                     | 完整流水线 M1→M4                     | `--with-atoms`, `--dry-run`, `--resume-run-id` |
| `run code-graph`              | 仅 M1                            | `--repo`                                       |
| `run code-graph-daemon`       | CodeGraph MCP daemon（Cursor 接入） | `--output`, `--no-watch`                       |
| `run code-graph-watch`        | 监听仓库增量更新 graph.db               | `--debounce`                                   |
| `run normalize-docs`          | 仅 M2                            | `--docs`                                       |
| `run extract-atoms`           | 仅 M3                            | `--from <run_dir>`                             |
| `run bootstrap-benchmark`     | M3 种子 → benchmark train         | `--from-run`, `--merge`, `--benchmark`         |
| `run optimize-skill`          | 仅 M4 SkillOpt                   | `-o`, `--epochs`, `--resume`, `--self-evolve`  |
| `run skill-hygiene`           | 离线 hygiene + gate               | `<run_id>`, `--force`                          |
| `run training-curve plot`     | 绘制训练曲线 SVG                      | `<run_id>`, `-o`                               |
| `run training-curve backfill` | 从历史回填 training_curve.json       | `<run_id>`                                     |
| `status`                      | 查看 run 状态                       | `[run_id]`（无参数列最近 5 次）                         |
| `inspect run`                 | run 目录摘要                        | `--trace-pool`, `--validate-self-evolution`    |
| `inspect file`                | 单文件产物预览                         | `<path>`                                       |
| `eval`                        | 独立评测 best_skill                 | `--split test`, `--benchmark`                  |
| `publish`                     | 发布 SKILL.md                     | `--target`, `--strip-rule-ids`, `--force`      |
| `resume`                      | M4 断点续训                         | `<run_id>`                                     |
| `approve`                     | 审批高风险动作                         | `<approval_id>`, `--deny`                      |
| `codegraph`                   | 图谱查询 CLI                        | 见 `skill-lab codegraph -h`                     |


### 初始化与诊断

**skill-lab init** — 创建 `sources/`、`runs/`、`benchmarks/` 等目录并写入 config 模板；不写入 API Key。

```bash
skill-lab init --workspace . --name my-skill --domain fintech
```

**skill-lab doctor** — 检查 tree-sitter 语法、config 加载、repos/docs 路径可达性。建议首次 `run all` 前执行。

**skill-lab config** — 校验 YAML 并输出 **Effective settings (wired to CLI/modules)** 段（实际接线项，非注释占位）。


| `--dry-run-level`     | 别名  | 执行内容                                   |
| --------------------- | --- | -------------------------------------- |
| `config-only`（L1，默认）  | —   | schema、路径、benchmark 目录、生效配置表           |
| `static-analysis`（L2） | —   | L1 + M1 文件扫描/符号解析、M2 格式解析（无 LLM/OCR）   |
| `full-simulate`（L3）   | —   | L2 + M1–M4 全流程，LLM 走 MockReplayBackend |


```bash
skill-lab config --config-path config.yaml
skill-lab config --dry-run-level static-analysis
skill-lab run all --dry-run --dry-run-level full-simulate   # 等价于 L3 校验，不跑真实 LLM
```

### 流水线 `run`

#### `run all` — 完整流水线


| 选项                              | 说明                                                  |
| ------------------------------- | --------------------------------------------------- |
| `--from-step` / `--to-step`     | 起止模块（`code-graph`/`m1` … `optimize-skill`/`m4`）     |
| `--resume-run-id`               | 复用已有 `<output.root>/<run_id>`；有 graph.db 时跳过 M1–M3  |
| `--dry-run` + `--dry-run-level` | 仅校验，不执行流水线                                          |
| `--with-atoms`                  | 有 benchmark 时仍运行 M3（默认跳过）                           |
| `--with-docs`                   | 跳过 M3 时仍运行 M2                                       |
| `--bootstrap-benchmark`         | 用 M3 高置信种子填充/扩充 benchmark train                     |
| `--merge-benchmark`             | 与 bootstrap 同用：追加而非覆盖 train                         |
| `--suggest-skill-rules`         | 高置信 atom 追加到 initial_skill 的 Auto-suggested rules 节 |


#### 单模块命令

```bash
# M1：构建 graph.db（默认全部 repos）
skill-lab run code-graph --config-path config.yaml [--repo fineract]

# M1 常驻：MCP daemon / 文件监听增量更新
skill-lab run code-graph-daemon --config-path config.yaml
skill-lab run code-graph-watch --config-path config.yaml

# M2：文档规范化
skill-lab run normalize-docs --config-path config.yaml [--docs path/to/doc.md]

# M3：需已有 M1/M2 产物的 run 目录
skill-lab run extract-atoms --from demo-project/runs/<run_id> --config-path config.yaml

# M3 种子 → benchmark train
skill-lab run bootstrap-benchmark --from-run demo-project/runs/<run_id> \
  --benchmark demo-project/benchmarks/fineract-fast [--merge] [--dry-run]
```

#### `run optimize-skill` — M4 SkillOpt


| 选项                               | 说明                                               |
| -------------------------------- | ------------------------------------------------ |
| `-o` / `--output`                | optimization 输出目录（默认 `runs/latest/optimization`） |
| `--benchmark`                    | 覆盖 `project.benchmark`                           |
| `--epochs`                       | 训练 epoch 数（CLI 默认 3，覆盖 config）                   |
| `--batch-size`                   | 每 epoch train batch 条数（CLI 默认 20）                |
| `--accumulation`                 | 梯度累积步数                                           |
| `--slow-update` / `--meta-skill` | 强制启用 epoch 级 slow update / meta skill 重写         |
| `--resume`                       | 从 `--output` 目录 `runtime_state.json` 断点续训        |
| `--self-evolve`                  | 完整自进化：trace pool + proposals + 严格 gate + 归因      |
| `--trace-merge`                  | 仅 trace 聚类归纳（不启用严格 gate / 归因）                    |


```bash
skill-lab run optimize-skill \
  --config-path config.yaml \
  --benchmark demo-project/benchmarks/fineract-fast \
  -o demo-project/runs/<run_id>/optimization \
  --epochs 3 --batch-size 5 \
  --self-evolve
```

**run skill-hygiene <run_id>** — 对已有 `best_skill.md` 做离线规则合并/裁剪，并经 selection gate 验证；`--force` 忽略 token/规则阈值。

**run training-curve plot|backfill <run_id>** — 从 `optimization/training_curve.json` 生成 SVG，或从历史 step 日志回填。

### 运行后命令

```bash
# 状态（无 run_id 列最近 5 次）
skill-lab status [<run_id>]

# run 摘要：manifest、gate 历史、test_report、context_ref 解析率、training_curve
skill-lab inspect run <run_id> [--trace-pool] [--rule-attribution] [--frontier] \
  [--validate-self-evolution]

# 单文件预览
skill-lab inspect file demo-project/runs/<run_id>/optimization/best_skill.md

# 独立评测（不训练）；默认 test split
skill-lab eval <run_id> --split test [--benchmark path]

# 发布 best_skill.md → SKILL.md
skill-lab publish <run_id> [--target dir] [--strip-rule-ids] [--force]

# M4 断点续训（等价于 optimize-skill --resume -o .../optimization）
skill-lab resume <run_id> --config-path config.yaml

# 审批高风险动作（runs/approvals.jsonl）
skill-lab approve <approval_id> [--deny]
```

### `codegraph` 子命令

图谱查询 CLI，与 M1 产出的 `graph.db` 配合使用。子命令包括 `status`、`search`、`context`、`explore`、`source`、`files`、`callers`、`callees`、`node`、`trace`、`impact` 等。完整参数见：

```bash
skill-lab codegraph -h
skill-lab codegraph search -h
```

### 典型工作流

**首次全流程（含 M3）**

```bash
skill-lab doctor --config-path config.yaml
skill-lab run all --config-path config.yaml --with-atoms
skill-lab inspect run <run_id>
skill-lab eval <run_id> --split test
```

**已有 benchmark，只跑 M4**

```bash
# 需 run 目录内已有 graph.db（或单独跑过 M1）
skill-lab run optimize-skill \
  --config-path config.yaml \
  -o demo-project/runs/<run_id>/optimization \
  --benchmark demo-project/benchmarks/fineract-fast
```

**断点续训**

```bash
skill-lab status <run_id>
skill-lab resume <run_id> --config-path config.yaml
# 或
skill-lab run optimize-skill --resume -o demo-project/runs/<run_id>/optimization --config-path config.yaml
```

**独立 test 评测**

```bash
skill-lab eval <run_id> --split test --config-path config.yaml
skill-lab run training-curve plot <run_id>
```

**发布 skill**

```bash
skill-lab publish <run_id> --config-path config.yaml --strip-rule-ids
# 目标目录：--target 或 settings.output.publish_target（默认 skills/agent）
```

---

## 配置参考

主配置文件为 **config.yaml**。**完整注释模板与全部键的权威来源**为 [config.template.yaml](config.template.yaml) — 复制后按需填写；README 以下为摘要。

查看实际生效的已接线项：

```bash
skill-lab config --config-path config.yaml
# 输出 Effective settings (wired to CLI/modules) 段
```

配置文件分两段：**settings**（框架如何运行）与 **project**（处理哪个项目）。

### `settings.code_graph`（M1）


| 键                          | 默认值           | 说明                                                |
| -------------------------- | ------------- | ------------------------------------------------- |
| `max_leaf_tokens`          | `8000`        | 叶子上下文 token 上限                                    |
| `max_module_depth`         | `3`           | 模块树最大深度                                           |
| `tokenizer`                | `cl100k_base` | 分词器                                               |
| `max_components_per_group` | `200`         | 每组最大组件数                                           |
| `split_strategy`           | `top_dir`     | `top_dir` | `package_path` | `file_then_function` |
| `llm_clustering_enabled`   | `false`       | LLM 辅助模块聚类（走 `routes.clusterer`）                  |
| `use_cache`                | `true`        | 增量解析 graph.db，二次运行跳过未变更文件                         |


### `settings.document_normalizer`（M2）


| 键                          | 默认值           | 说明           |
| -------------------------- | ------------- | ------------ |
| `max_chunk_tokens`         | `2000`        | 文档块 token 上限 |
| `ocr_engine`               | `tesseract`   | OCR 引擎       |
| `ocr_languages`            | `chi_sim+eng` | OCR 语言       |
| `ocr_confidence_threshold` | `0.6`         | OCR 置信度阈值    |


### `settings.atom_extractor`（M3）


| 键                          | 默认值    | 说明                 |
| -------------------------- | ------ | ------------------ |
| `confidence_tier_1_max`    | `0.95` | 最高置信 tier 上限       |
| `llm_adjustment`           | `0.05` | LLM 置信度微调幅度        |
| `max_source_refs_per_atom` | `24`   | 每个 atom 最多保留的来源引用数 |


### `settings.skillopt`（M4）


| 键                                  | 默认值                                  | 说明                                    |
| ---------------------------------- | ------------------------------------ | ------------------------------------- |
| `use_llm_rollout`                  | `true`（模板）；代码未设置时 runtime 默认 `false` | **必须为 true** 才有可信 rollout/reflect 信号  |
| `rollout_backend`                  | `null`                               | 覆盖 `routes.target`（例：`qwen-local`）    |
| `optimizer_backend`                | `null`                               | 覆盖 `routes.optimizer`                 |
| `judge_backend`                    | `null`                               | 覆盖 `routes.judge`（LLM Judge scorer 时） |
| `num_epochs`                       | `3`                                  | 训练 epoch 数                            |
| `batch_size`                       | `20`                                 | 每 epoch train batch 条数                |
| `edit_budget`                      | `3`                                  | 每步最多编辑条数 L                            |
| `budget_strategy`                  | `cosine`                             | `constant` | `cosine` | `linear`      |
| `gate_metric`                      | `soft`                               | selection gate 指标（见下表）                |
| `patience`                         | `10`                                 | 连续 reject 早停步数                        |
| `accumulation`                     | `1`                                  | 梯度累积                                  |
| `enable_slow_update`               | `false`                              | epoch 级 slow update                   |
| `enable_meta_skill`                | `false`                              | epoch 级 meta skill 重写                 |
| `slow_update_gate`                 | `true`                               | slow update 是否经 selection gate        |
| `enable_code_tools`                | `true`                               | Reflect/Rollout 可调 CodeGraph 工具       |
| `max_tool_rounds`                  | `5`                                  | reflect 工具轮次上限                        |
| `rollout_max_tool_rounds`          | `2`                                  | rollout 工具轮次上限                        |
| `rollout_workers`                  | `4`                                  | batch 内并行 rollout 数（1=串行）             |
| `expose_expected_checks_to_target` | `false`                              | 是否向 target 暴露 expected_checks         |
| `check_aliases`                    | `{}`                                 | 全局 keyword scorer 别名映射                |


**gate_metric 语义**（与 selection 规模联动，详见 [04-skillopt-loop.md](docs/design/04-skillopt-loop.md) §12.4）：


| 配置值     | 适用 selection 规模 | 行为                        |
| ------- | --------------- | ------------------------- |
| `soft`  | < 5 条（小集自动降级）   | 软通过率，允许 train 信号辅助 accept |
| `mixed` | 5–19 条          | hard + soft 混合            |
| `hard`  | ≥ 20 条          | 严格硬通过率（论文默认）              |


本仓库在 selection 持平时还可因 **train_improved**（train rollout 提升 ≥ 0.03）而 accept，以缓解小 validation set 噪声。

**token_budgets**（各阶段 LLM 输出 token 上限，须 ≤ backend `max_output_tokens`）：


| 键                 | 默认               |
| ----------------- | ---------------- |
| `rollout`         | `8192`           |
| `reflect_failure` | `16384`          |
| `reflect_success` | `4096`           |
| `reflect_retry`   | `[32768, 65536]` |
| `select_edits`    | `4096`           |
| `judge`           | `4096`           |
| `aggregate`       | `4096`           |
| `slow_update`     | `4096`           |
| `meta_skill`      | `2048`           |
| `atom_extract`    | `8192`           |


### `settings.self_evolution`（M4 自进化，默认关闭）


| 键         | 默认值     | 说明                             |
| --------- | ------- | ------------------------------ |
| `enabled` | `false` | 配置级启用（也可用 CLI `--self-evolve`） |


**trace_pool**


| 键                   | 默认                                             | 说明      |
| ------------------- | ---------------------------------------------- | ------- |
| `enabled`           | `true`                                         | 轨迹池     |
| `min_support_count` | `2`                                            | 聚类最小支持数 |
| `cluster_by`        | `["task_type","missed_checks","context_refs"]` | 聚类维度    |


**proposals**


| 键                             | 默认                                      | 说明              |
| ----------------------------- | --------------------------------------- | --------------- |
| `include_success`             | `true`                                  | 成功轨迹也生成提案       |
| `include_failure`             | `true`                                  | 失败轨迹生成提案        |
| `hierarchical_merge`          | `true`                                  | 分层合并提案          |
| `max_merge_fan_in`            | `8`                                     | 合并扇入上限          |
| `success_ignore_checks`       | `[]`                                    | 成功归纳时忽略的 check  |
| `success_default_checks_text` | `"verified task-specific requirements"` | 成功规则默认 check 文案 |
| `success_rule_tail`           | （见模板）                                   | 成功规则尾部约束        |


**gate**（自进化严格 gate，区别于 skillopt.gate_metric）


| 键                     | 默认      | 说明              |
| --------------------- | ------- | --------------- |
| `strict_improvement`  | `true`  | 须严格提升           |
| `reject_ties`         | `true`  | 持平即拒绝           |
| `allowed_regressions` | `0`     | 允许 regression 数 |
| `frontier_enabled`    | `false` | frontier pool   |
| `frontier_size`       | `3`     | frontier 大小     |


**edits**


| 键                        | 默认     | 说明                              |
| ------------------------ | ------ | ------------------------------- |
| `max_edits_per_step`     | `null` | null 时使用 `skillopt.edit_budget` |
| `max_new_rules_per_step` | `2`    | 每步新增规则上限                        |
| `max_skill_tokens`       | `2000` | Skill token 上限                  |


**hygiene**


| 键                    | 默认     | 说明         |
| -------------------- | ------ | ---------- |
| `enabled`            | `true` | 启用 hygiene |
| `run_each_epoch`     | `true` | 每 epoch 执行 |
| `min_rule_use_count` | `1`    | 规则最小使用次数   |
| `max_rules`          | `40`   | 最大规则数      |


**attribution**


| 键                 | 默认     | 说明                    |
| ----------------- | ------ | --------------------- |
| `enabled`         | `true` | 规则归因                  |
| `inject_rule_ids` | `true` | 向 Skill 注入 rule_id 注释 |


**knowledge**


| 键                   | 默认     | 说明      |
| ------------------- | ------ | ------- |
| `enabled`           | `true` | 知识归纳    |
| `gate_tolerance`    | `0.05` | gate 容差 |
| `min_support_count` | `2`    | 最小支持数   |


### `settings.pipeline`（M1–M4 编排）


| 键                                  | 默认值     | 说明                           |
| ---------------------------------- | ------- | ---------------------------- |
| `write_artifact_contract`          | `true`  | 写入 artifact_contract.json    |
| `validate_context_refs`            | `true`  | 校验 benchmark context_refs    |
| `run_atoms_when_benchmark_present` | `false` | 有 benchmark 时仍跑 M3           |
| `run_docs_when_atoms_skipped`      | `false` | 跳过 M3 时仍跑 M2                 |
| `merge_atom_seeds_into_benchmark`  | `false` | 自动合并 atom 种子到 benchmark      |
| `append_atom_rules_to_skill`       | `false` | 自动追加 atom 规则到 skill          |
| `bootstrap_min_confidence`         | `0.8`   | bootstrap 最低置信度              |
| `use_evidence_index`               | `true`  | M4 消费 evidence_index sidecar |
| `use_entrypoints`                  | `true`  | M4 消费 entrypoints sidecar    |
| `use_role_index`                   | `true`  | M4 消费 role_index sidecar     |
| `auto_plot_training_curve`         | `true`  | 训练结束自动绘图                     |


### `settings.model_provider`（M5）

**backends** — 每个 backend 字段：`type`（`llm_api` | `local_llm` | `agent_cli` | `agent_service` | `mcp_agent` | `mock`）、`provider`、`base_url`、`api_key_env`、`model`、`context_window`、`max_output_tokens`、`timeout_seconds` 等。环境变量通过 `${VAR_NAME}` 内联引用。

**routes** — 按 role 选择 backend（`primary` + `fallback`）：


| role           | 用途                              |
| -------------- | ------------------------------- |
| `extractor`    | M3 Atom 抽取                      |
| `clusterer`    | M1 LLM 聚类                       |
| `optimizer`    | M4 reflect / select / aggregate |
| `target`       | M4 rollout / eval               |
| `judge`        | LLM Judge scorer                |
| `agent_worker` | 预留：外部 Agent CLI                 |
| `default`      | 兜底                              |


M4 可用 `skillopt.rollout_backend` / `optimizer_backend` / `judge_backend` 覆盖 `target` / `optimizer` / `judge`。

**policies**


| 键                            | 默认            | 说明          |
| ---------------------------- | ------------- | ----------- |
| `default_retries`            | `3`           | 默认重试次数      |
| `retry_backoff`              | `exponential` | 退避策略        |
| `trace_enabled`              | `true`        | 写入 traces/  |
| `cache_enabled`              | `false`       | 响应缓存        |
| `redact_secrets`             | `true`        | trace 脱敏    |
| `max_cost_per_run_usd`       | `20`          | 单次 run 成本上限 |
| `max_timeout_seconds`        | `900`         | 超时上限        |
| `structured_output_fallback` | `true`        | 结构化输出降级     |


### `settings.output` / `settings.approvals`


| 段           | 键                       | 默认      | 说明                             |
| ----------- | ----------------------- | ------- | ------------------------------ |
| `output`    | `root`                  | `runs/` | run 产物根目录                      |
| `output`    | `publish_target`        | `""`    | 发布目标（空则 CLI 默认 `skills/agent`） |
| `approvals` | `require_for`           | 见模板     | 需审批的动作类型                       |
| `approvals` | `auto_approve_in_batch` | `false` | 批处理自动批准                        |


### `project` 段


| 键                  | 默认              | 说明                                   |
| ------------------ | --------------- | ------------------------------------ |
| `name`             | `code-to-skill` | 项目名                                  |
| `domain`           | `agent-skill`   | 业务域                                  |
| `description`      | （见模板）           | 描述                                   |
| `initial_skill`    | `""`            | 初始 SKILL.md 路径                       |
| `benchmark`        | `""`            | benchmark 目录（含 train/selection/test） |
| `graph_role_hints` | `{}`            | 图谱 role 提示（按 task_type）              |
| `reflect_prompts`  | `{}`            | 自定义 reflect 提示（`error` / `success`）  |


**project.code_graph**


| 键                        | 默认   | 说明                                                         |
| ------------------------ | ---- | ---------------------------------------------------------- |
| `custom_patterns`        | `{}` | 框架自定义解析模式                                                  |
| `context_ref_path_rules` | `[]` | context_ref 路径展开规则（prefix / skip_if_contains / expansions） |


**project.sources.repos[]**


| 字段                   | 默认     | 说明      |
| -------------------- | ------ | ------- |
| `id`                 | （必填）   | 仓库标识    |
| `path`               | （必填）   | 本地路径    |
| `ref`                | `HEAD` | 快照 ref  |
| `include`            | `[]`   | 包含 glob |
| `exclude`            | `[]`   | 排除 glob |
| `framework_patterns` | `{}`   | 框架模式    |


**project.sources.docs[]**


| 字段            | 默认           | 说明                                                            |
| ------------- | ------------ | ------------------------------------------------------------- |
| `id`          | （必填）         | 文档标识                                                          |
| `path`        | （必填）         | 本地路径或 URI                                                     |
| `provider`    | `local_file` | `local_file` | `feishu_api` | …                               |
| `type`        | （必填）         | `markdown` | `pdf` | `wiki_export` | `html` | `docx` | `text` |
| `version`     | `latest`     | 版本标签                                                          |
| `authority`   | —            | 权威来源标签                                                        |
| `domain_tags` | `[]`         | 域标签                                                           |
| `ocr_enabled` | `false`      | 是否 OCR                                                        |


---

## 准确性优化建议

以下按 [00-overall-design.md](docs/design/00-overall-design.md) §3.1 与 [04-skillopt-loop.md](docs/design/04-skillopt-loop.md) §8、§12 整理，给出可操作的调优路径。

### P0：必须先做对

1. **use_llm_rollout: true** — 关闭后 rollout 走规则降级，train 可能全绿但 selection 不升，reflect 信号不可信。
2. **Benchmark 三 split 无 ID 重叠** — `train` 驱动编辑，`selection` 做 gate，`test` 仅最终报告；`validate_splits()` 不应有 warning。
3. **gate_metric 与 selection 规模匹配** — selection < 5 时即使用 `hard` 也会降级为 soft；5–19 建议 `mixed`；≥ 20 可用 `hard`。

### P1：显著提升收益

1. **Target / Optimizer 模型分离** — rollout 量大用较快模型（`routes.target` 或 `rollout_backend`）；reflect/select 用较强模型（`routes.optimizer` 或 `optimizer_backend`）。
2. **初始 Skill 质量** — `initial_skill.md` 不必完美（论文 154 token + 1 编辑即可 +29pt），但需有清晰 Workflow / Constraint / Checklist 结构；gate 长期 reject 时检查 `best_skill.md` 是否同步更新。
3. **expected_checks 设计** — 每条 benchmark item 须有可检查的断言；约束题配置合适的 `response_mode`；keyword scorer 配合 `check_aliases` 处理同义表述。

### P2：代码域专项

1. **enable_code_tools: true** — Reflect/Rollout 可读真实源码，避免纯 skill 回显。
2. **context_refs** — benchmark item 指向 M1 叶子或文档 chunk，提高证据命中。
3. **check_aliases** — 全局或 per-item 别名，减少 keyword scorer 误杀。
4. **context_ref_path_rules** — 将简短 ref 展开为仓库内可解析路径（见 `config.template.yaml` 示例）。

### Benchmark 设计


| 字段                | 建议                                                    |
| ----------------- | ----------------------------------------------------- |
| `expected_checks` | 动词开头、可客观验证；避免模糊「回答正确」                                 |
| `response_mode`   | 开放题 vs 约束题 vs 工具调用题分别配置                               |
| `scorer`          | 默认 keyword；语义/rubric 题用 `llm_judge`（走 `routes.judge`） |
| `context_refs`    | 代码题指向相关类/方法 leaf；文档题指向 chunk id                       |
| `check_aliases`   | 同一 check 的多种合法表述                                      |


目录结构：`benchmarks/<name>/{train,selection,test}/items.json`。

### 评分器配置

每条 benchmark item 通过 `scorer` 选择评分器；未指定时默认 `keyword`（同 `deterministic`）。

| `scorer` | 适用场景 | item 级配置 |
| -------- | -------- | ----------- |
| `keyword` | 可客观子串匹配的断言 | `expected_checks`、`check_aliases`、`response_mode` |
| `python_script` | 自定义规则（平衡验算、结构化解析等） | `scorer_config.script`（相对 `items.json` 所在 split 目录）、可选 `timeout_seconds` |
| `llm_judge` | 开放问答 / rubric 语义评分 | `rubric`；走 `routes.judge` backend |

**全局别名**（keyword 与 python_script 共用）：`settings.skillopt.check_aliases`，与 item 级 `check_aliases` 合并。

**Fineract demo** 全量 item 使用共享脚本：

```json
"scorer": "python_script",
"scorer_config": { "script": "../score_expected_checks.py" }
```

脚本位于 `demo-project/benchmarks/score_expected_checks.py`：在 keyword 匹配基础上，对含借贷分录的回答做**借贷平衡**验算；若回答明确「无法/不得生成凭证」，则跳过平衡类 check。扩展脚本须从 stdin 读 JSON、向 stdout 写单行 `{"hard", "soft", "passed_checks", "missed_checks", ...}`；详见 `src/code_to_skill/skillopt_loop/scoring.py` 模块文档。

### M4 训练超参：MVP vs 稳定版


| 参数                   | MVP（`config.template.yaml`） | 稳定版（论文消融推荐）                     |
| -------------------- | --------------------------- | ------------------------------- |
| `num_epochs`         | 3                           | 4–5（小数据集可 6–8）                  |
| `batch_size`         | 20                          | 32–40                           |
| `edit_budget`        | 3                           | 4                               |
| `gate_metric`        | `soft`                      | `hard`（selection ≥ 20）或 `mixed` |
| `enable_slow_update` | `false`                     | `true`（去掉可灾难性降分）                |
| `enable_meta_skill`  | `false`                     | `true`                          |
| `budget_strategy`    | `cosine`                    | `cosine`                        |


小数据集（如 Fineract fast 仅 5 条 train）应相应缩小 `batch_size`，而非机械套用论文 40。

### 自进化：`--trace-merge` vs `--self-evolve`


| 模式              | 适用场景                                                                          |
| --------------- | ----------------------------------------------------------------------------- |
| `--trace-merge` | 轻量轨迹归纳，不改严格 gate；快速试验 trace 聚类                                                |
| `--self-evolve` | 完整路径：trace pool → proposals → **严格 gate**（reject_ties）→ attribution → hygiene |


项目定制：调整 `self_evolution.proposals.success_ignore_checks`（忽略泛化 check）、`success_rule_tail`（成功规则写法）、`hygiene.max_rules`（控制 Skill 体积）。

### 诊断命令

```bash
# 自进化产物完整性
skill-lab inspect run <run_id> --validate-self-evolution --trace-pool

# held-out 评测
skill-lab eval <run_id> --split test

# 训练曲线与 gate 历史
skill-lab run training-curve plot <run_id>
skill-lab inspect run <run_id>   # history.json 近 5 步 gate
```

### 常见陷阱


| 现象                       | 可能原因                                | 对策                                                |
| ------------------------ | ----------------------------------- | ------------------------------------------------- |
| train 全绿、selection 不升    | `use_llm_rollout: false` 或假 rollout | 开启 LLM rollout，检查 traces                          |
| 编辑长期 reject              | selection 过小仍期望 hard gate           | 改用 soft/mixed，或扩充 selection                       |
| reflect 质量差              | target 与 optimizer 同一弱模型            | 分离 backend                                        |
| Skill 堆砌重复「必须」           | edit_budget 过大、无 hygiene            | 降 edit_budget，启用 hygiene                          |
| context_ref 解析率低         | 路径规则缺失                              | 配置 `context_ref_path_rules`，inspect run 查看 report |
| 关闭 slow/meta 后 epoch 间遗忘 | MVP 默认关闭                            | 稳定版开启两者                                           |


---

## Fineract 示例（fast benchmark）

仓库内 `config.yaml` 已配置 Apache Fineract 与 **fineract-fast**（train/selection/test = 5/6/3，完整集备份在 `fineract-full`）。

```bash
export SKILL_LAB_CONFIG_PATH=$PWD/config.yaml   # 可选

# 完整 M1→M4（推荐首次冒烟）
skill-lab run all --config-path config.yaml --with-atoms

# 仅 M4 重训（需 run 目录内已有 graph.db）
skill-lab run optimize-skill \
  --config-path config.yaml \
  -o demo-project/runs/<run_id>/optimization

# M4 + 轨迹归纳（--trace-merge，不改严格 gate）
skill-lab run optimize-skill --trace-merge ...

# M4 + 完整自进化（严格 gate、归因、hygiene）
skill-lab run optimize-skill --self-evolve ...
```

产物目录：`settings.output.root/<run_id>/`（示例 `demo-project/runs/`）。

## 准备指南

### 1. 代码仓库

`project.sources.repos` 指向本地 clone（示例：`demo-project/sources/repos/fineract`）。源码**不纳入 git**，首次或更新时执行：

```bash
./demo-project/fetch-sources.sh
```

仓库 URL 见 [demo-project/sources/repos.manifest.yaml](demo-project/sources/repos.manifest.yaml)。

### 2. 知识文档

置于 `demo-project/sources/docs/<project>/` 并在 `config.yaml` 的 `sources.docs` 注册。

### 3. 初始 Skill

`demo-project/initial_skill.md`：Workflow / Constraint / Failure Mode / Checklist。

### 4. Benchmark

`benchmarks/<name>/{train,selection,test}/items.json`，每条含 `id`、`question`、`expected_checks`、`context_refs`（可选）。

快速子集生成：`python demo-project/benchmarks/build_fast_subset.py`（产出 `fineract-fast`）。

### 5. API 与 `.env`

在项目根目录配置 `.env`；`model_provider` 通过 `${VAR}` 引用。勿将 key 提交到 git。

## 技术栈

- **语言**: Python 3.10+
- **核心依赖**: pydantic, pyyaml, tree-sitter, openai, tiktoken, rich, click
- **文档解析**: pdfplumber, python-docx, beautifulsoup4, markdown-it-py
- **离线部署**: `pip download -d vendor/` → `pip install --no-index --find-links=vendor/`

## 项目结构

```
code-to-skill/
├── docs/design/          # 00–06 模块与整合设计
├── docs/references/      # API 参考与编码规范
├── config.template.yaml  # 配置权威模板（带注释）
├── config.yaml           # 本地配置（Fineract 示例）
├── src/code_to_skill/
│   ├── cli/              # M6
│   ├── code_graph/       # M1
│   ├── document_normalizer/
│   ├── atom_extractor/   # M3
│   ├── skillopt_loop/    # M4（含 self_evolution）
│   └── model_provider/   # M5
├── demo-project/            # Fineract 示例（benchmark/docs 入库；runs/repos clone 忽略）
└── tests/
```

## 开发

```bash
pip install -e ".[lxml]"
python -m pytest tests/ -q

# 使用源码 CLI
PYTHONPATH=src skill-lab run all --config-path config.yaml --dry-run
```

## License

MIT