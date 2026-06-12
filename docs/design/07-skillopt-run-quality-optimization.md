# 07：SkillOpt Run 质量优化设计

> 状态: 设计中  
> 日期: 2026-06-12  
> 关联模块: M3 SkillAtom、M4 SkillOpt、自进化 self_evolution、Benchmark scorer、run inspect  
> 主要代码路径: `src/code_to_skill/skillopt_loop/`、`demo-project/benchmarks/`、`tests/`

## 1. 背景

当前 pipeline 已能完成 M1-M4 全流程，并且 M3 产物、M4 trace pool、Python scorer、最终 test eval 报告都已落盘。但最近 run 暴露出一个更深的问题：系统可以提升局部评分，却没有稳定产出“可泛化、可部署、不过拟合 benchmark”的 skill。

最新样本 run：`demo-project/runs/20260612-105003`。

| 指标 | 结果 |
|---|---:|
| M3 `artifact_quality.passed` | true |
| M3 `source_ref_resolve_rate` | 1.0 |
| M3 `evidence_exact_hit_rate` | 1.0 |
| M3 `generic_expected_checks` | 0 |
| M4 `best_score` | 0.728 |
| final test soft | 0.937 |
| final test hard | 0.625 |
| final test hard passed | 5/8 |
| trace missing | 0 |
| initial skill 大小 | 924 bytes |
| best skill 大小 | 10999 bytes |

与上一轮 `20260612-085122` 对比：

| run | selection best | test soft | test hard | best skill 大小 |
|---|---:|---:|---:|---:|
| `20260612-085122` | 0.8065 | 0.854 | 0.750 | 4645 bytes |
| `20260612-105003` | 0.728 | 0.937 | 0.625 | 10999 bytes |

结论：新 run 的 soft 提升主要来自 scorer 变宽和输出覆盖率提升，但 hard 降低，且 `best_skill.md` 明显膨胀并含 benchmark/scorer 面向文本。下一阶段优化目标应从“能变”转为“稳定变好、少过拟合、可解释、可回滚”。

## 2. 当前问题

### 2.1 `knowledge_accept` 会降低 best

当前 M4 主循环中，普通 gate 失败后会尝试 success-derived knowledge merge。若 `knowledge_gate >= best_score - tolerance`，系统将其视为 accept，并直接写回：

- `best_score = knowledge_gate`
- `best_skill = knowledge_content`
- `best_step = step_counter`

在 `20260612-105003` 中出现了连续降级：

| step | gate reason | selection gate | best 变化 |
|---:|---|---:|---|
| 1 | `accept_new_best` | 0.702 | 0.646 -> 0.702 |
| 2 | `knowledge_accept (0.669 >= 0.702-0.050)` | 0.669 | 0.702 -> 0.669 |
| 3 | `knowledge_accept (0.646 >= 0.669-0.050)` | 0.646 | 0.669 -> 0.646 |

这违反了 best 的语义。`knowledge_accept` 可以推进探索态或 current skill，但不能降低历史最优产物。

### 2.2 Skill 污染了 benchmark 和 scorer 语言

最新 `best_skill.md` 中出现以下内容：

- `Scenario rules (by benchmark case)`
- `jv_chargeoff_001`、`jv_purchase_001` 等 benchmark id
- `cover verified checks [...]`
- `校验程序`
- 为了命中 `expected_checks` 而强制输出固定 token

这类内容能提高局部 keyword 命中，但会降低 skill 的可部署性：

- 将训练集 case id 变成通用规则，迁移到真实项目任务时不可用。
- 让 target agent 学会讨好 scorer，而不是学习业务判断。
- 对 test 中同义表达不稳。例如 latest run 失败项都只漏一个 token：`资产`、`费用`、`Charge`。

### 2.3 场景兜底规则过于 case 化

`scenario_rules.py` 当前从失败 case 生成规则时会拼入：

- benchmark id
- question 摘要
- missed checks
- `must satisfy verification checks`

这会把评分器私有信息写入 skill。场景规则可以存在，但应归纳为业务触发条件和处理策略，而不是 benchmark case 索引。

### 2.4 评分机制已经支持扩展，但质量语义仍偏弱

当前 `score_benchmark_item()` 已支持：

- keyword/deterministic
- python_script
- llm_judge

`demo-project/benchmarks/score_expected_checks.py` 已将 Fineract 的无凭证场景特殊处理放在 benchmark 侧，而不是通用代码侧，这个方向是正确的。

剩余问题：

- hard 仍严格依赖 token 全命中。
- 同义词、业务等价表达、输出模式错误的解释还不够结构化。
- scorer 结果虽写入 final eval，但训练 step 的 selection 详情仍不足以直接解释某次 gate 为什么接受。

### 2.5 可观测性提升了，但还未形成质量闭环

`final_eval/test_eval_report.json` 已有 `schema_version=1.1`，包含：

- `predicted_answer`
- `expected_checks`
- `passed_checks`
- `missed_checks`
- `fail_reason`
- `score_type`
- `scorer_justification`
- `trace_request_id`
- `trace_calls`

但训练期间仍缺少三个关键闭环：

- step 级 selection per-item 评分明细。
- skill 内容质量指标，例如泄露词命中、规则数、重复规则数、token 估算。
- best/current/knowledge 三种状态的明确变更记录。

## 3. 设计目标

1. `best_skill` 必须单调不退化。任何探索接受都不能降低 `best_score`。
2. Skill 不应包含 benchmark id、expected_checks、scorer、校验程序等泄露词。
3. 通用代码只实现抽象机制，不内置 Fineract 专用业务词或目标项目 case。
4. Benchmark 侧可通过 Python scorer 表达领域评分逻辑，且训练和最终评测走同一 scorer 路由。
5. 每次 run 能回答四个问题：
   - 哪个 step 让 skill 变好或变差？
   - 变更来自失败、成功、M3 evidence 还是 fallback？
   - gate 接受的是 new best、current exploration 还是 knowledge-only？
   - 最终失败是输出质量问题、评分词问题、证据检索问题还是 skill 污染问题？

## 4. 非目标

- 不把 Fineract 的科目映射、业务词别名、会计特殊规则写入 `src/code_to_skill` 通用代码。
- 不把 hard pass 放宽为“只要 soft 高就算通过”。hard 仍表示 benchmark 定义的完整通过。
- 不在 M4 中直接修正目标项目答案。M4 只优化 skill，领域判定属于 benchmark/scorer 或目标项目 skill 内容。
- 不依赖 test split 做训练选择。test 只用于最终 held-out 报告。

## 5. 总体方案

将 M4 的状态和质量控制拆成五层：

```text
rollout
  -> reflect/proposal
  -> edit validation
  -> candidate eval on selection
  -> state transition
       ├─ best: 只接受严格不退化的新最优
       ├─ current: 可接受持平或 tolerance 内探索
       └─ knowledge: success-derived 规则先进入探索态
  -> hygiene/leak guard
  -> final test eval
```

核心变化：

| 层 | 改造点 |
|---|---|
| 状态机 | 分离 `best_skill`、`current_skill`、`exploration_skill` |
| Gate | `knowledge_accept` 不再覆盖 best，history 显式记录状态目标 |
| Edit hygiene | 引入 benchmark/scorer leakage guard 和 size/duplicate guard |
| Scenario rules | 从 case-id 规则改为业务触发条件规则 |
| Scoring | 保持通用 scorer 路由，领域逻辑放 benchmark Python 脚本 |
| Observability | step 级 selection 明细、skill 质量报告、run 级对比报告 |

## 6. 详细设计

### 6.1 状态语义

定义三种 skill 状态：

| 状态 | 含义 | 可被导出 | 可退化 |
|---|---|---|---|
| `best_skill` | selection gate 历史最优产物 | 是 | 否 |
| `current_skill` | 下一步 rollout 使用的当前策略 | 否，除非也是 best | 可在策略允许下轻微退化 |
| `exploration_skill` | knowledge merge / frontier / slow update 的候选探索态 | 否 | 可 |

`best_score` 必须满足单调不下降：

```python
assert new_best_score >= old_best_score
```

`current_score` 可以低于 `best_score`，但必须显式记录原因和来源，避免误认为 best 回退。

### 6.2 Gate 状态转移

候选更新后先计算：

```text
candidate_gate = select_gate_score(candidate_hard, candidate_soft, metric)
```

状态转移：

| 条件 | 动作 | best | current |
|---|---|---|---|
| `candidate_gate > best_score + delta` | `accept_new_best` | 更新 | 更新 |
| `candidate_gate > current_score + delta` | `accept_current` | 不变 | 更新 |
| `candidate_gate == current_score` 且允许 tie | `accept_current_tie` | 不变 | 更新 |
| train 明显提升且 selection 不降 | `accept_current_train_improved` | 不变 | 更新 |
| 其他 | `reject` | 不变 | 不变 |

`knowledge_accept` 改为：

| 条件 | 动作 |
|---|---|
| `knowledge_gate > best_score + delta` | 提升为 `accept_new_best` |
| `knowledge_gate >= current_score - tolerance` | `accept_current_knowledge`，只更新 current |
| 否则 | `reject_knowledge` |

伪代码：

```python
old_best_score = best_score

if action == "accept_new_best":
    best_skill = candidate
    best_score = candidate_gate
    current_skill = candidate
    current_score = candidate_gate

elif action.startswith("accept_current"):
    current_skill = candidate
    current_score = candidate_gate
    # best 保持不变

elif knowledge_accept:
    if knowledge_gate > best_score + delta:
        best_skill = knowledge_content
        best_score = knowledge_gate
        current_skill = knowledge_content
        current_score = knowledge_gate
        action = "accept_new_best_from_knowledge"
    else:
        current_skill = knowledge_content
        current_score = knowledge_gate
        action = "accept_current_knowledge"

assert best_score >= old_best_score
```

### 6.3 Finalize 语义

当前 final 阶段存在一个风险：如果 `current_skill` 与 `best_skill` selection 分数持平，也可能导出 current。

优化后：

- 默认只导出 `best_skill`。
- 允许 `export_current_on_tie`，但默认 false。
- 若启用 tie export，必须同时满足：
  - current 无 leakage。
  - current skill quality 不差于 best。
  - current token 数不超过 `max_skill_tokens`。
  - current 与 best 的 selection 明细没有新增 hard fail。

配置建议：

```yaml
settings:
  skillopt:
    finalize:
      export_current_on_tie: false
```

### 6.4 Edit 泄露防护

新增 `skill_quality.py` 或扩展 `edit_validator.py`，分两级：

1. edit 级拒绝：阻止污染进入 candidate。
2. skill 级拒绝：防止 slow update、hygiene、knowledge merge 后整体污染。

通用泄露模式：

```text
expected_checks
verified checks
cover verified checks
benchmark case
by benchmark case
scorer
校验程序
评分器
must satisfy verification checks
```

benchmark id 不写死在通用代码，而用通用启发式识别：

```text
连续出现多个 snake_case id，且周围有 case/check/benchmark 语义
```

可配置补充：

```yaml
settings:
  skillopt:
    hygiene:
      leakage_patterns:
        - "cover verified checks"
        - "benchmark case"
        - "校验程序"
      benchmark_id_patterns:
        - "\\bjv_[a-z0-9_]+\\b"
```

注意：`benchmark_id_patterns` 属于项目配置，不应写入通用默认规则。通用默认只拒绝明显 scorer 语义。

### 6.5 Scenario rule 归纳

`scenario_rules.py` 需要从“case 复述”改为“业务条件归纳”。

当前形式：

```text
- **jv_purchase_001** (...): cover verified checks [库存、银行]
```

目标形式：

```text
- 当用户描述“买入/采购 + 物品 + 金额”且未声明为费用消耗时，按资产或存货类采购处理；贷方使用现金/银行类资金来源，金额只取用户输入。
```

改造规则：

| 输入 | 输出 |
|---|---|
| `id` | 仅用于 trace，不写入 skill |
| `question` | 提取触发条件，不逐字写入 |
| `missed_checks` | 生成业务结果要求，不写 `checks` |
| `context_refs` | 可写代码/模块来源，但不写 scorer 语言 |

落盘产物仍保留 case 追溯：

```json
{
  "rule_text": "...",
  "source_case_ids": ["jv_purchase_001"],
  "source_missed_checks": ["库存", "银行"],
  "written_to_skill": true
}
```

### 6.6 Skill 体积与重复控制

当前 latest run 从 924 bytes 膨胀到 10999 bytes，远超 `max_skill_tokens=2000` 的意图。

新增质量指标：

| 指标 | 计算方式 | 默认阈值 |
|---|---|---:|
| `estimated_tokens` | `len(skill) / 4` | `max_skill_tokens` |
| `rule_count` | bullet/table 规则数 | `max_rules` |
| `duplicate_rule_count` | 规范化后重复 bullet | 0 |
| `leakage_count` | 泄露模式命中数 | 0 |
| `case_id_count` | 项目配置 pattern 命中数 | 0 |

skill 级 gate：

```text
candidate 必须同时通过 selection gate 和 quality gate。
```

若 selection 提升但 quality gate 失败：

- 不直接 reject 全部。
- 先尝试 sanitizer 删除泄露/重复片段。
- sanitizer 后重新跑 selection eval。
- sanitizer 仍失败，则 reject，并写入 `rejected_edit_buffer.jsonl`。

### 6.7 Benchmark scorer 边界

评分扩展原则：

- 通用代码只负责路由、超时、字段归一化和错误上报。
- 领域语义由 benchmark 的 `python_script` 决定。
- 同一 item 在 train/selection/test/fallback eval 中必须走同一 scorer。

当前 Fineract scorer 的方向保留：

- 输出无会计凭证时，不做借贷平衡金额判断。
- 输出“信息不足/需要补充/无法生成/缺少”等无凭证信号时，借贷专项 check 可通过。
- 其他情况只做 keyword/alias 判断，不引入通用会计凭证语义。

建议增强：

1. 将 `justification` 从单行变成可选结构化字段：

```json
{
  "justification": "keyword checks 5/6",
  "diagnostics": {
    "mode": "keyword",
    "no_voucher_response": false,
    "alias_hits": {"Charge": ["Fee"]}
  }
}
```

2. 支持 item 级别 `check_aliases`，处理 `Charge`/`Fee`、`资产`/`设备资产` 等可接受同义表达。
3. 不把别名写进通用代码。Fineract benchmark 可以在 `items.json` 或独立 alias 文件中维护。

### 6.8 Selection 观测性

新增 step 级 selection 明细：

```text
optimization/steps/step_000N/
├── selection_eval_report.json
├── skill_quality.json
└── gate_decision.json
```

`selection_eval_report.json`：

```json
{
  "schema_version": "1.0",
  "step": 5,
  "skill_hash": "abc123",
  "hard": 0.591,
  "soft": 0.865,
  "gate_score": 0.728,
  "per_item": [
    {
      "id": "jv_sel_fee_001",
      "hard": 0,
      "soft": 0.833,
      "passed_checks": ["会计凭证", "借", "贷"],
      "missed_checks": ["Charge"],
      "score_type": "python_script",
      "scorer_justification": "keyword checks 5/6"
    }
  ]
}
```

`skill_quality.json`：

```json
{
  "schema_version": "1.0",
  "step": 5,
  "estimated_tokens": 2749,
  "max_skill_tokens": 2000,
  "rule_count": 46,
  "duplicate_rule_count": 3,
  "leakage_hits": [
    {"pattern": "cover verified checks", "line": 11}
  ],
  "case_id_hits": [
    {"pattern": "\\bjv_[a-z0-9_]+\\b", "line": 11, "value": "jv_chargeoff_001"}
  ],
  "passed": false
}
```

`gate_decision.json`：

```json
{
  "schema_version": "1.0",
  "step": 5,
  "action": "accept_new_best",
  "state_target": "best",
  "reason": "new_best (0.649 -> 0.728) [mixed]",
  "before": {
    "best_score": 0.649,
    "current_score": 0.649
  },
  "after": {
    "best_score": 0.728,
    "current_score": 0.728
  },
  "quality_gate": {
    "passed": true,
    "leakage_count": 0
  }
}
```

### 6.9 Run 级对比报告

新增 `optimization/run_quality_report.json`，供 `inspect run` 展示。

```json
{
  "schema_version": "1.0",
  "run_id": "20260612-105003",
  "initial_skill_chars": 924,
  "best_skill_chars": 10999,
  "best_score": 0.728,
  "test_soft": 0.937,
  "test_hard": 0.625,
  "best_score_monotonic": false,
  "leakage_count": 19,
  "case_id_count": 16,
  "hard_failures": [
    {
      "id": "jv_purchase_002",
      "missed_checks": ["资产"],
      "soft": 0.833
    }
  ],
  "recommendations": [
    "Do not overwrite best during knowledge_accept.",
    "Sanitize benchmark/scorer-facing rules before gate."
  ]
}
```

`skill-lab inspect run --validate-self-evolution` 应优先读取该报告，若不存在则按历史产物即时计算。

## 7. 配置设计

建议新增或规范化配置：

```yaml
settings:
  skillopt:
    gate:
      strict_best_monotonic: true
      knowledge_updates_current_only: true
      export_current_on_tie: false

    quality_gate:
      enabled: true
      run_before_selection_eval: false
      run_after_selection_eval: true
      reject_on_leakage: true
      sanitize_then_reevaluate: true
      max_skill_tokens: 2000
      max_rules: 40
      leakage_patterns:
        - "expected_checks"
        - "verified checks"
        - "cover verified checks"
        - "benchmark case"
        - "scorer"
        - "校验程序"
        - "评分器"
      benchmark_id_patterns: []

    observability:
      write_selection_eval_report: true
      write_skill_quality_report: true
      write_gate_decision_report: true
      write_run_quality_report: true
```

兼容当前配置：

- `settings.self_evolution.edits.max_skill_tokens` 可作为 `quality_gate.max_skill_tokens` 的默认值。
- `settings.self_evolution.hygiene.max_rules` 可作为 `quality_gate.max_rules` 的默认值。
- 老配置不写 `quality_gate` 时默认开启基础泄露检测，但只 warning；下一版本再改为 hard reject。

## 8. 代码改造范围

| 文件 | 改造 |
|---|---|
| `src/code_to_skill/skillopt_loop/__init__.py` | 修正 best/current 状态转移、knowledge_accept 语义、finalize tie export |
| `src/code_to_skill/skillopt_loop/gate.py` | 可选增加 state target，或保持 GateDecision 纯比较逻辑 |
| `src/code_to_skill/skillopt_loop/edit_validator.py` | 增加 edit 级泄露检测 |
| `src/code_to_skill/skillopt_loop/skill_quality.py` | 新增 skill 级质量扫描、sanitizer、报告生成 |
| `src/code_to_skill/skillopt_loop/scenario_rules.py` | 去 case id、去 scorer 语汇，输出业务触发条件 |
| `src/code_to_skill/skillopt_loop/test_eval.py` | 复用报告结构到 selection eval |
| `src/code_to_skill/skillopt_loop/envs/base.py` | 确保 rollout result 透传 scorer diagnostics |
| `src/code_to_skill/skillopt_loop/scoring.py` | 保持 scorer 路由，透传 `diagnostics` 字段 |
| `src/code_to_skill/cli/inspect.py` 或等价 CLI | 展示 run quality report |
| `tests/test_self_evolution.py` | 覆盖 knowledge_accept 不降低 best |
| `tests/test_benchmark_reflect.py` | 覆盖 scorer diagnostics 和 no-voucher 行为 |
| `tests/test_skill_quality.py` | 新增 leakage、size、duplicate、sanitizer 测试 |

## 9. 实施计划

### 阶段 A：修正 best/current 状态

1. 修改 `run_skillopt_loop()` 中普通 accept 和 knowledge_accept 分支。
2. `accept` 只更新 current，不再在 `candidate_gate >= best_score` 以外覆盖 best。
3. `knowledge_accept` 默认更新 current；只有严格超过 best 才更新 best。
4. history 增加字段：
   - `state_target`
   - `best_score_before`
   - `best_score_after`
   - `current_score_before`
   - `current_score_after`
   - `best_monotonic`
5. 增加单测：构造 best=0.70、knowledge=0.66、tolerance=0.05，断言 best 仍为 0.70。

验收标准：

- 任意 history 中 `best_score` 单调不下降。
- `best_skill.md` 对应 `best_step` 的 skill，不被 lower-score current 覆盖。

### 阶段 B：质量门禁

1. 新增 `skill_quality.py`。
2. edit 级检测明显 scorer 泄露。
3. skill 级检测 size、rule count、duplicate、leakage。
4. gate 接受前写 `skill_quality.json`。
5. quality gate 失败时先 sanitize，再重新 evaluation。

验收标准：

- latest run 中类似 `cover verified checks`、`benchmark case`、`校验程序` 会被拒绝或清理。
- 通用代码不出现 Fineract 业务词或 `jv_` 固定规则。

### 阶段 C：场景规则归纳

1. 改写 `_scenario_rule_line()`。
2. edit proposal 保留 `related_task_ids`，skill 正文不写 task id。
3. missed checks 转为自然业务要求，不写 `checks`、`verified`、`benchmark`。

验收标准：

- `best_skill.md` 不含 benchmark id。
- `edit_proposals.json` 仍可追溯 source case。

### 阶段 D：评分观测增强

1. 训练 selection eval 写 `selection_eval_report.json`。
2. Python scorer 结果透传 `diagnostics`。
3. final eval 和 selection eval 使用同一 report builder。
4. `inspect run` 汇总 hard failures、missed checks、trace links、scorer counts。

验收标准：

- 不打开 trace 原文也能定位失败原因。
- fallback eval、selection eval、test eval 的 scorer 路由一致。

### 阶段 E：配置与回归

1. 更新 `config.yaml` 或 template 中的质量门禁配置。
2. 补充 README/CLI help 中的 inspect 命令说明。
3. 跑完整测试集和至少一轮 full benchmark。

建议测试命令：

```bash
python -m pytest tests/test_benchmark_reflect.py -q
python -m pytest tests/test_m3_m4.py tests/test_self_evolution.py -q
python -m pytest tests/test_slow_update.py -q
python -m pytest tests/test_skill_quality.py -q
python -m compileall src/code_to_skill/skillopt_loop demo-project/benchmarks
```

## 10. 验收指标

### 10.1 功能指标

| 指标 | 目标 |
|---|---:|
| `best_score_monotonic` | true |
| `trace_missing_ids` | 0 |
| `final_eval.schema_version` | >= 1.1 |
| `selection_eval_report` 覆盖率 | 每个 evaluated step 100% |
| scorer 路由一致性 | train/selection/test/fallback 一致 |

### 10.2 Skill 质量指标

| 指标 | 目标 |
|---|---:|
| `leakage_count` | 0 |
| `case_id_count` | 0，除非项目配置显式允许 |
| `duplicate_rule_count` | 0 |
| `estimated_tokens` | <= `max_skill_tokens`，默认 2000 |
| `rule_count` | <= `max_rules`，默认 40 |

### 10.3 效果指标

以 `20260612-105003` 为基线：

| 指标 | 基线 | 目标 |
|---|---:|---:|
| test hard | 0.625 | >= 0.750 |
| test soft | 0.937 | 不低于 0.900 |
| best skill chars | 10999 | 显著下降，目标 < 6000 |
| scorer 泄露命中 | 存在 | 0 |

## 11. 风险与取舍

| 风险 | 影响 | 应对 |
|---|---|---|
| quality gate 过严导致无编辑可接受 | 优化停滞 | 第一阶段 warning + sanitize，第二阶段 hard reject |
| 去掉 case id 后短期 hard 下降 | benchmark keyword 命中降低 | 通过 item aliases 和业务化 scorer 修正评分，而不是污染 skill |
| current 可探索导致训练曲线复杂 | inspect 更难读 | history 增加 `state_target` 和 before/after 字段 |
| sanitizer 删除有效规则 | 误伤 | 只处理泄露段和重复段，删除后必须重新 selection eval |
| 领域 scorer 维护成本上升 | benchmark 复杂 | 领域逻辑本就应在 benchmark 侧，通用代码保持稳定 |

## 12. 对 latest run 的直接建议

针对 `20260612-105003`：

1. 优先修复 `knowledge_accept`，避免 best 从 0.702 降到 0.669/0.646 这类回退。
2. 加质量门禁，拒绝 `cover verified checks`、`benchmark case`、`校验程序`。
3. 改造 `scenario_rules.py`，不要把 `jv_*` 写入 skill。
4. 为 test 失败项补 benchmark 侧 aliases，而不是在通用代码或 skill 中硬塞 token：
   - `资产`: 可接受 `设备资产`、`固定资产`，但 `库存（设备）` 是否通过需由 Fineract benchmark 决定。
   - `费用`: 可接受 `Fee`、`手续费`、`费用收入`。
   - `Charge`: 可接受 `Client charge`、`Fee charge`，但是否等价应由 benchmark item 配置。
5. 重跑后重点看：
   - `history.json` 的 best 是否单调。
   - `best_skill.md` 是否不含 scorer 泄露。
   - `final_eval/test_eval_report.json` hard 是否回到 0.750 以上。
   - `run_quality_report.json` 是否给出明确失败归因。

## 13. 推荐落地顺序

1. 先做状态机修复，因为它直接保护 `best_skill.md`。
2. 再做 skill quality gate，因为它阻断污染继续扩散。
3. 然后改 scenario rule 归纳，减少 case 级过拟合来源。
4. 最后补 selection/run 观测性，让后续每轮优化可直接定位问题。

这个顺序可以在不改变 benchmark 语义的情况下先保证产物质量，再逐步提高 hard 分。
