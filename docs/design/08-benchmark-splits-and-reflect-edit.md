# Benchmark Split 接入与 Reflect/Edit 改进

> 版本: 1.1  
> 状态: **已实现**（2026-06-06 代码对照完成）  
> 关联: [04-skillopt-loop.md](./04-skillopt-loop.md)

## 实现对照总览

| 章节 | 内容 | 状态 | 主要代码 |
|------|------|------|----------|
| §3 | Benchmark Split 接入 | ✅ 完成 | `benchmark_splits.py`, `__init__.py`, `cli/main.py` |
| §4 | Reflect/Edit 改进 | ✅ 完成 | `scoring.py`, `envs/base.py`, `llm_components.py`, `edit_validator.py` |
| §5 P0–P4 | 实施分期 | ✅ 全部完成 | 见 §5 明细 |
| §6 | Reflect 第二轮改进 | ✅ 完成 | `llm_components.py`, `envs/base.py` |
| §7 | E2E 第三轮改进 | ✅ 完成 | `tool_loop.py`, `edit_validator.py`, `llm_components.py`, `envs/base.py` |
| §9 | Step Artifact 追溯 | ✅ 完成 | `edit_traceability.py`, `types.py`, `__init__.py` |
| §10 | 场景规则兜底 | ✅ 完成 | `scenario_rules.py`, `__init__.py` |
| §8 | 验收标准 | ✅ 机制就绪 | `test-data/benchmarks/fineract/`, `tests/test_benchmark_reflect.py` |

---

## 1. 背景

### 1.1 Selection 未真正接入 ~~（历史问题）~~

> ✅ **已修复**：`BenchmarkSplits.from_dir()` 加载三份 split；`run_skillopt_loop()` 通过 `resolve()` 使用显式或 ratio 切分，不再仅从 train 尾部切 selection。

### 1.2 Reflect/Edit 产出无意义改动 ~~（历史问题）~~

> ✅ **已修复**：规则降级改为语义化规则；Reflect prompt 含 `missed_checks`；`EditValidator` 过滤 meta 注释与重复内容。

## 2. 目标

| # | 目标 | 状态 | 说明 |
|---|------|------|------|
| 1 | train / selection / test 三份独立文件，训练与 gate 严格分离 | ✅ | `fineract` benchmark: train=7, selection=3, test=2 |
| 2 | Reflect 产出可执行的 skill 规则，禁止占位注释 | ✅ | `_REFLECT_SYSTEM_PROMPT` + `EditValidator` + `_sanitize_llm_edits` |
| 3 | 每步编辑可追溯到具体失败 case 和 missed checks | ✅ | `EditOp.related_*` + `steps/step_N/rollout_summary.json` + `edit_proposals.json` |

## 3. Benchmark Split 接入

### 3.1 目录结构 ✅

```
<project.benchmark>/
├── train/items.json       # 训练 rollout（必须）
├── selection/items.json   # validation gate（可选）
└── test/items.json        # 最终评测（可选）
```

实现：`BenchmarkSplits.from_dir()` → `benchmark_splits.py:35`

### 3.2 BenchmarkSplits ✅

```python
@dataclass
class BenchmarkSplits:
    train: list[dict]
    selection: list[dict]
    test: list[dict]

    @classmethod
    def from_dir(cls, path: str) -> "BenchmarkSplits": ...
```

附加：`ResolvedBenchmarkSplits` dataclass、`validate_splits()` / `log_validation()`（ID 重叠检测）。

### 3.3 切分策略 ✅

| 条件 | 行为 | 状态 |
|------|------|------|
| 存在 `selection/items.json` 或 `test/items.json` | 使用显式 split，不做 ratio 切分 | ✅ `has_explicit_splits` / `force_explicit` |
| 仅有 train | 回退到 `selection_split_ratio` / `test_split_ratio` 切分（向后兼容） | ✅ `n_train = total - n_sel - n_test`（修复浮点精度） |

实现：`BenchmarkSplits.resolve()` → `benchmark_splits.py:52`  
接入：`run_skillopt_loop()` → `__init__.py:150`；CLI `_load_benchmark_splits()` → `cli/main.py:318`

### 3.4 Gate 策略 ✅

| 项 | 状态 | 代码 |
|----|------|------|
| `gate_metric` 从 `settings.skillopt.gate_metric` 传入 CLI | ✅ | `cli/main.py` optimize-skill / run all |
| selection < 5 条时自动降级为 `soft` gate | ✅ | `__init__.py:195` |
| `select_gate_score(hard/soft/mixed)` | ✅ | `gate.py` |

## 4. Reflect/Edit 改进

### 4.1 Rollout 反馈增强 ✅

- ✅ `score_rollout_result()` 返回 `passed_checks` / `missed_checks` → `scoring.py:45`
- ✅ rollout result 含 `question`、`expected_checks`、`missed_checks`、`passed_checks` → `envs/base.py:265`
- ✅ `fail_reason` 具体化为 `missed: 库存, 银行` → `envs/base.py:288`
- ✅ LLM 空响应时 skill 关键词模板降级 → `envs/base.py:243`（§7 补充）

### 4.2 Reflect Prompt ✅

| 要求 | 状态 | 代码 |
|------|------|------|
| 失败 case 含 question、passed/missed checks、answer excerpt | ✅ | `_format_failure_cases()` |
| 禁止 meta 编辑（`# Verify`、`need improvement`） | ✅ | `_REFLECT_SYSTEM_PROMPT` + `EditValidator` |
| 优先 `insert_after` 定位到 skill 章节 | ✅ | prompt 指令 + `_skill_section_index()` |
| step_buffer / rejected edits 防重复 | ✅ | `_build_buffer_summary()` |
| meta_skill 注入 | ✅ | `reflect_llm(meta_skill_context=...)` |
| 代码工具 hint（可选） | ✅ | `_CODE_TOOLS_REFLECT_HINT` |

### 4.3 规则降级 ✅

| 要求 | 状态 | 代码 |
|------|------|------|
| 基于 `missed_checks` 聚合生成具体规则 | ✅ | `_rule_based_patches()` |
| 插入相关章节（如 `### 2.3 生成会计凭证`） | ✅ | `_find_insert_target()` |
| 语义映射非关键词堆砌 | ✅ | `_CHECK_SEMANTIC_RULES` |
| 按 `task_type` 分组，最多 5 条 | ✅ | `_group_failures_by_task_type()` + `_MAX_RULE_CHECKS` |
| 跳过 skill 已有规则，增量追加 | ✅ | `_rule_bullet_in_skill()` + `_last_line_in_section()`（§7 补充） |

### 4.4 EditValidator ✅

在 `apply_edits` 前过滤（`filter_valid_edits` → `__init__.py:322`）：

| 规则 | 状态 | 代码 |
|------|------|------|
| content < 20 字符 | ✅ | `MIN_CONTENT_LEN` |
| meta 注释模式 | ✅ | `_META_PATTERNS` |
| 与 current_skill 重复（整段或全部 bullet 已存在） | ✅ | `_content_already_in_skill()` |
| 缺少可执行标记（必须/不得/输出等） | ✅ | `_ACTIONABLE_MARKERS` |
| reflect 阶段预过滤 LLM edits | ✅ | `_sanitize_llm_edits()` |

### 4.5 Select 覆盖度排序 ✅

- ✅ 无 LLM 或 LLM 失败时按 missed checks 覆盖度排序 → `_rank_edits_by_coverage()`
- ✅ insert_after/replace 定位加分 → `_score_edit_coverage()` loc_bonus=0.15
- ✅ LLM select 可用时 structured output，失败回退覆盖度排序 → `select_edits_llm()`

## 5. 实施分期

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | BenchmarkSplits + 显式 split + gate_metric | ✅ |
| P1 | scoring missed/passed + rollout 丰富化 | ✅ |
| P2 | 规则降级 + reflect prompt + edit_validator | ✅ |
| P3 | insert_after 定位 + select 覆盖度排序 | ✅ |
| P4 | test_evaluate 接入（显式 test split） | ✅ `__init__.py:517` + CLI `eval` 命令 |

## 6. Reflect 第二轮改进（2026-06-06）

针对 e2e 暴露的问题：

| 问题 | 修复 | 状态 | 代码 |
|------|------|------|------|
| Step 1 reflect 返回空 content | `_invoke_reflect_with_retry` + `_parse_reflect_response` 从 content 解析 JSON | ✅ | `llm_components.py:78,128` |
| 规则降级关键词堆砌 | `_CHECK_SEMANTIC_RULES` 语义映射 + 按 `task_type` 分组 + 最多 5 条 | ✅ | `llm_components.py:23,497` |
| LLM 无效 edit 进入 pipeline | `_sanitize_llm_edits` 在 reflect 阶段预过滤 | ✅ | `llm_components.py:89` |
| Rollout system prompt 不匹配任务 | target 改为 accounting agent + 要求输出完整会计凭证 | ✅ | `envs/base.py:215` |
| Reflect tool loop 耗尽无 JSON | tool loop 后 structured synthesis pass | ✅ | `_invoke_reflect_with_retry` attempt 0 分支 |
| token budget 可配置 | `token_budgets.py` + config | ✅ | `configure_token_budgets()` |

## 7. E2E 第三轮改进（2026-06-06）

针对 `20260606-143303` run 中 tool loop 耗尽 / 规则 duplicate 跳过 step 的问题：

| 问题 | 修复 | 状态 | 代码 |
|------|------|------|------|
| tool_loop 达 max_rounds 后空 content | 强制无 tools synthesis 回合 + 空响应重试 | ✅ | `tool_loop.py:82` |
| reflect 空 JSON 误判为可用 | `_reflect_response_usable` 仅接受含 edits 的解析结果 | ✅ | `llm_components.py:113` |
| 规则降级重复插入已有 bullet | 过滤已有规则 + section 内增量 append | ✅ | `_rule_bullet_in_skill()`, `_last_line_in_section()` |
| duplicate 校验仅整段匹配 | bullet 级重复检测 | ✅ | `edit_validator.py:20` |
| rollout LLM 空输出 hard=0 | skill 关键词模板凭证降级 | ✅ | `envs/base.py:243` |

测试：`tests/test_tool_loop.py`，`tests/test_benchmark_reflect.py`（22 项）

## 10. 场景规则兜底（2026-06-06，对齐 09-Phase 9）

当 generic 语义规则与 `_rule_based_patches` 均因 duplicate 被 reject 时，按**失败 benchmark case** 生成唯一场景规则：

| 项 | 状态 | 代码 |
|----|------|------|
| `build_scenario_edits()` 按 case id + question + missed_checks | ✅ | `scenario_rules.py` |
| 接入 validate 失败分支（在 skip 前尝试） | ✅ | `__init__.py:328` |
| `EditOp.related_task_ids` / `related_missed_checks` | ✅ | `scenario_rules.py:97` |
| `initial_skill.md` §2.3 凭证输出模板 | ✅ | `test-data/initial_skill.md` |
| 测试 | ✅ | `tests/test_scenario_rules.py` |

## 8. 验收标准

| 标准 | 状态 | 验证方式 |
|------|------|----------|
| `selection/items.json` 3 题参与 gate，不与 train 重叠 | ✅ | `fineract` + `validate_splits()` 无 warning |
| `best_skill.md` diff 含实质性规则，无 `# Verify` 占位行 | ✅ 机制 | `EditValidator` + 语义规则降级；E2E 质量依赖 LLM |
| `test_report.json` 在训练结束后生成 | ✅ | `__init__.py:535` → `{output_dir}/test_report.json` |
| CLI `--benchmark` 指向数据目录、`--output` 指向产物目录 | ✅ | `cli/main.py` optimize-skill |
| 独立 `skill-lab eval` 使用 test split | ✅ | `cli/main.py:814` → `test_evaluate()` |
| 每步 edit 可追溯到 task id 与 missed checks | ✅ | `steps/step_N/edit_proposals.json` + `rollout_summary.json` |

## 9. Step Artifact 追溯（2026-06-06 补全）

每步 `steps/step_NNNN/` 目录：

| 文件 | 内容 |
|------|------|
| `rollout_summary.json` | 失败 case 列表（id、question、missed/passed checks、fail_reason） |
| `reflect_patches.json` | Reflect 原始 patch（含 failure_summary） |
| `edit_proposals.json` | 选中编辑 + `related_task_ids` + `related_missed_checks` |
| `rejected_edits.json` | validate 拒绝的编辑（skip 时写入） |

实现：`edit_traceability.py`，`EditOp.related_task_ids` / `related_missed_checks`

---

## 附录：测试覆盖

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_benchmark_reflect.py` | split、scoring、edit_validator、rule patches、select coverage、edit traceability |
| `tests/test_scenario_rules.py` | 场景规则兜底 + duplicate 后 validator |
| `tests/test_rollout_helpers.py` | rollout synthesis hint + tool 降级凭证 |
| `tests/test_resume_state.py` | M4 断点续训 runtime_state |
| `tests/test_tool_loop.py` | tool loop synthesis 回合 |
| `tests/test_m3_m4.py` | M4 pipeline 集成（含 skillopt MVP） |
