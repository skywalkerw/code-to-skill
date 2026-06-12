# 08：代码诊断驱动的 SkillOpt 优化设计

> 状态: 已实现  
> 日期: 2026-06-12  
> 关联文档: `03-skillatom-extraction.md`、`04-skillopt-loop.md`、`07-skillopt-run-quality-optimization.md`  
> 目标: 让 SkillOpt 从“分数筛 patch”升级为“代码诊断 -> 规则归纳 -> 稳定记忆 -> replay 验证”的优化闭环

## 1. 背景

最近几轮 Fineract benchmark 暴露出一个核心问题：SkillOpt 虽然能调用代码工具，也能通过 selection gate 保护 best 不退化，但它仍不能稳定达到 Codex/Cursor 那种“快速读代码并找出技能优化点”的效果。

对比样本：

| run | best score | test hard | test soft | 主要失败 |
|---|---:|---:|---:|---|
| `20260612-105003/optimization-07` | 0.778 | 0.875 | 0.938 | `jv_overdraft_001` |
| `20260612-141940/optimization` | 0.673 | 0.500 | 0.916 | `jv_purchase_002`、`jv_refund_001`、`jv_transfer_001`、`jv_disburse_fee_001` |

`optimization-07` 的有效规则是：

- 明确交易类型 + 金额时，必须生成凭证，不因日期、loanId、支付方式等辅助字段缺失拒绝。
- 费用、手续费、罚金类交易要显式包含 `Charge`。
- 摘要保留用户输入中的动作词。

最新 run 没有稳定复现这些规则，而只接受了 Charge-Off 和 Accrual 两条较窄规则。说明当前系统缺少跨 run 记忆和代码驱动诊断，不能像 Codex/Cursor 一样把一次失败稳定归纳成长期有效的 skill 规则。

## 2. 问题定义

### 2.1 Codex/Cursor 为什么更快

Codex/Cursor 面对失败项时通常执行的是定向诊断流程：

1. 观察失败现象。
2. 主动搜索和阅读相关代码。
3. 找到关键方法、常量、分支和业务语义。
4. 归纳通用规则。
5. 直接修改 skill 或代码。
6. 用工程判断过滤 case 过拟合和坏输出。

例如 `jv_overdraft_001`：

- 读 `SavingsTransactionDTO.isOverdraftTransaction()`。
- 读 `CashBasedAccountingProcessorForSavings` 的 overdraft 分支。
- 读 `CashAccountsForSavings.OVERDRAFT_PORTFOLIO_CONTROL`。
- 得出规则：题面明确全部为 overdraft amount 时，应生成借 `OVERDRAFT_PORTFOLIO_CONTROL`、贷 `SAVINGS_REFERENCE` 的凭证。

### 2.2 SkillOpt 当前为什么不稳定

当前 M4 主循环更像黑盒分数优化：

```text
rollout -> scorer -> missed_checks -> reflect patch -> selection gate -> best_skill
```

这个闭环有三个弱点：

| 弱点 | 表现 | 后果 |
|---|---|---|
| 诊断弱 | optimizer 多数看到的是 `missed_checks`，不是结构化代码事实 | patch 容易只补关键词或窄场景 |
| 记忆弱 | 有效规则只存在本 run 的 `best_skill.md` | 下一轮重新探索，可能学不到同一规则 |
| 卫生弱 | prompt/context echo 可能仍拿到较高 soft | gate 被脏输出干扰 |

M3/M4 产物中已经有 `context_refs`、`evidence_index`、`trace_pool`、`run_quality_report`，但它们还没有形成一等的“代码诊断 -> skill 规则”管线。

## 3. 设计目标

1. 失败项必须先经过代码诊断，再进入 reflect patch。
2. 生成的 skill 规则必须有代码证据来源，而不是只来自 `missed_checks`。
3. 跨 run 保留已验证有效规则，避免每次重新发现。
4. 对 prompt echo、context echo、tool residue 进行 hard fail 或 retry。
5. 候选 skill 不只跑 selection，还要 replay 最近失败项和历史黄金失败项。
6. 保持 07 的质量门禁：best 单调、无 benchmark id、无 scorer 泄露、无 skill 膨胀。

## 4. 非目标

- 不把 Fineract 专用业务规则写入通用 `src/code_to_skill`。
- 不把 test split 用作训练选择；test 仍只做最终评估。
- 不用单纯增加 epoch 替代诊断和记忆。
- 不让 scorer 变成“宽松同义词兜底”来掩盖 skill 问题。

## 5. 总体方案

新增一层 **Code Diagnosis Layer**，插入在 rollout 失败和 reflect 之间：

```text
rollout failures
  -> code diagnosis
       -> read context_refs
       -> extract code facts
       -> infer failure cause
       -> propose general rule
  -> rule memory lookup
  -> reflect / proposal merge
  -> selection eval
  -> replay gate
  -> quality gate
  -> best/current update
  -> persistent rule bank
```

从“让 LLM 自己在 reflect 里随缘读代码”，改为“系统强制为失败项生成结构化诊断记录，再让 LLM 基于诊断记录写规则”。

## 6. 新增核心产物

### 6.1 `code_diagnosis.jsonl`

位置：

```text
optimization/code_diagnosis/step_000N/code_diagnosis.jsonl
```

每条失败 case 一条诊断：

```json
{
  "schema_version": "1.0",
  "step": 4,
  "item_id": "jv_overdraft_001",
  "question": "储蓄透支（overdraft）取款 200.00，其中 200.00 全部为透支金额",
  "missed_checks": ["借", "贷", "overdraft"],
  "context_refs": [
    "CashBasedAccountingProcessorForSavings.java#isOverdraftTransaction",
    "AccountingConstants.java#CashAccountsForSavings.OVERDRAFT_PORTFOLIO_CONTROL"
  ],
  "code_facts": [
    {
      "ref": "SavingsTransactionDTO.java#isOverdraftTransaction",
      "fact": "overdraftAmount != null && overdraftAmount > 0 时为透支交易"
    },
    {
      "ref": "CashBasedAccountingProcessorForSavings.java",
      "fact": "透支取款非账户转账时，使用 OVERDRAFT_PORTFOLIO_CONTROL 与 SAVINGS_REFERENCE 创建现金制分录"
    }
  ],
  "failure_cause": "当前 skill 将透支取款视为需要拆分信息的模糊请求；题面已说明 200 全部为透支金额，应直接生成凭证。",
  "general_rule": "当储蓄取款题面明确全部金额为 overdraft/透支金额时，应生成透支取款凭证，借透支组合控制科目，贷储蓄参考/现金银行科目，金额取用户输入。",
  "rule_scope": {
    "task_type": "journal_entry",
    "trigger_terms": ["透支", "overdraft", "全部为透支金额"],
    "applies_to": ["savings_withdrawal"],
    "does_not_apply_to": ["未说明透支金额且总金额需拆分为正常余额和透支金额"]
  },
  "confidence": 0.86,
  "diagnosis_source": "context_refs",
  "status": "ready"
}
```

### 6.2 `rule_bank.jsonl`

位置：

```text
runs/rule_bank/<project_name>/rules.jsonl
```

也可以放在项目目录：

```text
demo-project/rule_bank/rules.jsonl
```

每条规则是跨 run 可复用的长期记忆：

```json
{
  "rule_id": "fineract.journal_entry.sufficient_amount_generate_voucher",
  "text": "当用户输入包含明确交易类型和金额时，默认信息充分，应生成会计凭证；不得因日期、loanId、支付方式等辅助字段缺失拒绝，除非代码证据显示必须拆分金额或会计期间关闭。",
  "source_runs": ["20260612-105003"],
  "source_items": ["jv_purchase_001", "jv_repayment_interest_001"],
  "evidence_refs": [
    "CashBasedAccountingProcessorForLoan.java#createJournalEntriesForRepayments"
  ],
  "support_count": 2,
  "accepted_count": 1,
  "regression_count": 0,
  "last_seen_at": "2026-06-12T12:05:21+08:00",
  "status": "active"
}
```

### 6.3 `replay_eval_report.json`

位置：

```text
optimization/steps/step_000N/replay_eval_report.json
```

候选 skill 通过 selection 后，还要 replay：

- 当前 run 最近 hard failures。
- rule bank 关联的历史 failure exemplars。
- 本轮被 sanitizer/reject 的高风险输出。

报告示例：

```json
{
  "schema_version": "1.0",
  "step": 5,
  "candidate_hash": "abc123",
  "replay_items": 8,
  "hard": 0.875,
  "soft": 0.944,
  "fixed_ids": ["jv_overdraft_001", "jv_transfer_001"],
  "regressed_ids": [],
  "prompt_echo_ids": [],
  "passed": true
}
```

### 6.4 `output_hygiene_report.json`

位置：

```text
optimization/steps/step_000N/output_hygiene_report.json
```

用于捕捉 target 输出污染：

```json
{
  "schema_version": "1.0",
  "step": 5,
  "prompt_echo_count": 1,
  "tool_residue_count": 0,
  "bad_outputs": [
    {
      "id": "jv_disburse_fee_001",
      "reason": "prompt_echo",
      "matched_patterns": ["Task:", "Skill reference:", "Code context:"]
    }
  ]
}
```

## 7. Code Diagnosis Layer 设计

### 7.1 输入

每个 hard fail 的 rollout result：

```json
{
  "id": "jv_disburse_fee_001",
  "question": "贷款发放时扣收手续费 50.00",
  "missed_checks": ["借"],
  "context_refs": [
    "CashBasedAccountingProcessorForLoan.java#isRepaymentAtDisbursement"
  ],
  "predicted_answer": "...",
  "trace_request_id": "req-..."
}
```

### 7.2 证据读取顺序

沿用现有 artifact contract，但把它提升为诊断强制流程：

1. 精确 `context_refs`。
2. `evidence_index` 精确命中。
3. `role_index` / `entrypoints`。
4. `get_code_context` fallback。
5. 如果仍无证据，诊断状态为 `needs_review`，不得生成高置信规则。

### 7.3 诊断 prompt 约束

诊断模型输出必须是 JSON，且区分四类失败：

| 类型 | 说明 | 示例 |
|---|---|---|
| `missing_business_rule` | skill 缺业务规则 | 不知道 overdraft 全额时应生成凭证 |
| `output_format_error` | 输出格式不满足要求 | 没有 `## 会计凭证` |
| `prompt_echo` | 输出粘贴 prompt/context | 吐出 `Task:` / `Code context:` |
| `scorer_alias_gap` | 业务正确但 scorer 缺同义词 | `超额还款` vs `overpayment` |

这一步的价值是把 `missed_checks` 转成可行动诊断，而不是让 reflect 从关键词猜原因。

## 8. 规则归纳设计

### 8.1 从诊断到规则

每条诊断可以产生一个候选规则，但必须满足：

- 有 `code_facts` 支撑。
- 有 `trigger_terms` 和 `does_not_apply_to`。
- 不包含 benchmark id。
- 不包含 `expected_checks`、`scorer`、`verified checks` 等评分器语言。
- 不是单纯“输出某关键词”。

### 8.2 规则类型

| 类型 | 写入位置 | 示例 |
|---|---|---|
| `business_mapping` | 科目对/业务规则 | 透支取款全额 overdraft 时的借贷科目 |
| `sufficiency_policy` | 约束 | 明确交易类型+金额时生成凭证 |
| `output_policy` | 输出要求 | 固定以 `## 会计凭证` 开头 |
| `terminology_policy` | 输出要求/术语 | 费用类交易保留 `Charge` |
| `hygiene_policy` | 运行时检查，不写 skill | 禁止 prompt echo |

### 8.3 支持度

规则接受不只看单 case：

```text
support_score = code_confidence * 0.4
              + replay_fix_rate * 0.4
              + no_regression_score * 0.2
```

默认：

- `support_score >= 0.75` 才可进入 `best_skill`。
- `0.55 <= support_score < 0.75` 进入 `rule_bank` 的 `candidate` 状态。
- `< 0.55` 丢入 rejected buffer。

## 9. Rule Bank 设计

### 9.1 读入时机

M4 初始化时加载 rule bank：

```text
initial_skill
  + active rule bank rules
  + M3 accepted atoms
  -> current initial skill
```

需要有 token budget：

```yaml
settings:
  skillopt:
    rule_bank:
      enabled: true
      max_rules: 20
      min_support_count: 1
      exclude_regressed: true
```

### 9.2 写回时机

训练结束后写回：

- 新 best 中仍存在的新增规则。
- replay 后无 regression 的规则。
- 人工标记或 inspect 确认为有效的规则。

### 9.3 防止规则污染

rule bank 也必须过 07 quality gate：

- 无 benchmark id。
- 无 scorer 泄露。
- 无重复规则。
- 必须有 evidence refs 或人工确认。

## 10. Replay Gate 设计

### 10.1 为什么 selection 不够

最新 run 的 selection best 是 0.673，但 test hard 只有 0.5。selection gate 没能保证关键历史失败不回退。

Replay gate 用于回答：

> 这个候选 skill 是否仍能解决我们过去已经解决过的问题？

### 10.2 Replay 集合

每次候选通过 selection 后，构造 replay items：

1. 当前 step 的 hard failures。
2. 最近 N 个 run 的 hard failures。
3. rule bank 中每条 active rule 的 exemplars。
4. 最近出现 prompt echo 的 items。

Replay items 不等于 test split。它们是已知训练/验证失败样本和历史回归样本，可以用于 gate。

### 10.3 Gate 条件

候选必须满足：

- replay hard 不低于当前 best。
- 无新增 prompt echo。
- active rule exemplars 不回归。
- 若 selection 提升但 replay 回归，降级为 `accept_current` 或 reject。

伪代码：

```python
if selection_gate > best_score:
    replay = evaluate_replay(candidate)
    if replay.prompt_echo_count > 0:
        reject("prompt_echo")
    elif replay.regressed_ids:
        accept_current_or_reject("replay_regression")
    else:
        accept_new_best()
```

## 11. 输出卫生检查

### 11.1 Prompt Echo 模式

以下内容出现在最终 answer 中，应直接 retry 或 hard fail：

```text
Task:
Skill reference:
Code context:
Context references:
Follow the skill document
tool_calls
<｜｜DSML｜｜
```

### 11.2 处理策略

| 阶段 | 策略 |
|---|---|
| rollout target 输出后 | 若命中 echo，先用 synthesis hint retry |
| retry 后仍命中 | hard=0，fail_reason=`prompt_echo` |
| reflect 阶段 | prompt echo case 优先进入 diagnosis |
| final eval | report 中明确 `output_hygiene` 字段 |

### 11.3 为什么不能只靠 scorer

`jv_disburse_fee_001` 最新 run 中输出了 `Task / Skill reference / Code context`，但仍因命中部分关键词拿到 `soft=0.833`。这会让 gate 误判候选质量。因此 prompt echo 必须是 scorer 之前的硬质量信号。

## 12. 配置设计

权威模板见 `config.template.yaml` 中 `settings.skillopt` 下 `output_hygiene` / `code_diagnosis` / `warm_start` / `rule_bank` / `replay_gate` 段。示例：

```yaml
settings:
  skillopt:
    output_hygiene:
      enabled: true
      retry_on_prompt_echo: true
      hard_fail_on_persistent_echo: true
      patterns:
        - "^Task:\\s"
        - "Skill reference:"
        - "Code context:"
        - "^Context references:"
        - "Follow the skill document"

    code_diagnosis:
      enabled: true
      max_context_files: 2
      max_snippet_chars: 800
      max_cases_per_step: 8
      write_jsonl: true
      require_code_facts_for_rules: false

    warm_start:
      from_best_skill: ""   # 可选：上一 run 的 best_skill.md 路径

    rule_bank:
      enabled: true
      path: demo-project/rule_bank/rules.jsonl
      max_active_rules: 20
      min_support_count: 1
      min_support_score: 0.55
      exclude_regressed: true
      write_back: true

    replay_gate:
      enabled: true
      pool_max_items: 12
      min_hard_pass_rate: 1.0
      reject_on_prompt_echo: true
      reject_on_regression: true
      on_regression: reject   # reject | accept_current
      include_rule_exemplars: true
      include_prompt_echo_cases: true
      external_pool_paths: []
      pool_path: ""
```

## 13. 代码改造范围

| 文件/模块 | 改造 |
|---|---|
| `skillopt_loop/code_diagnosis.py` | 新增失败项代码诊断器 |
| `skillopt_loop/diagnosis_rules.py` | 诊断 → 候选规则、quality scan |
| `skillopt_loop/eval_hygiene.py` | selection/test 统一 hygiene 评估 |
| `skillopt_loop/rule_bank.py` | 新增持久规则库读写 |
| `skillopt_loop/replay_gate.py` | 新增 replay 集构造与 gate |
| `skillopt_loop/output_hygiene.py` | 新增 prompt echo 检测 |
| `skillopt_loop/__init__.py` | 在 reflect 前插入 diagnosis，在 accept 前插入 replay gate |
| `skillopt_loop/llm_components.py` | reflect prompt 接收 `code_diagnosis` |
| `skillopt_loop/envs/base.py` | target 输出后执行 output hygiene/retry；backend 异常走 fallback |
| `skillopt_loop/test_eval.py` | final/selection 报告增加 hygiene 字段 |
| `cli/inspect_run.py` | 展示 diagnosis、rule bank、replay gate 指标；`--promote-rules-to-bank` |
| `cli/main.py` | `run all` / `optimize-skill` 支持 `--warm-start-rule-bank` |
| `demo-project/benchmarks/score_expected_checks.py` | scorer `diagnostics.failure_type` / `suggested_rule` |
| `demo-project/rule_bank/rules.jsonl` | Fineract 示例规则库种子 |
| `tests/` | 增加 diagnosis/rule_bank/replay/hygiene 回归测试 |

## 14. CLI 体验

### 14.1 查看诊断

```bash
skill-lab inspect run 20260612-141940 \
  --config-path config.yaml \
  --show-diagnosis
```

输出示例：

```text
Code diagnosis:
  jv_transfer_001: output_format_error
    cause: skill treated account transfer as needing account IDs
    rule: explicit amount transfer should still generate voucher with business-level accounts
  jv_disburse_fee_001: prompt_echo
    cause: target pasted Task/Skill/Code context
    action: add output hygiene retry
```

### 14.2 写入 rule bank

```bash
skill-lab inspect run 20260612-105003 \
  --optimization-dir optimization-07 \
  --promote-rules-to-bank
```

默认只提升：

- active best skill 中的新增规则。
- 无 leakage。
- 有 replay support。
- 无 regression。

### 14.3 Warm start

```bash
skill-lab run all \
  --config-path config.yaml \
  --warm-start-rule-bank
```

或：

```yaml
settings:
  skillopt:
    warm_start:
      from_best_skill: demo-project/runs/20260612-105003/optimization-07/best_skill.md
```

## 15. 实施计划

> **状态（2026-06-12）**：阶段 A–E 均已落地，见 §20 实现记录。以下为各阶段目标与验收标准。

### 阶段 A：输出卫生硬化 ✅

1. 新增 `output_hygiene.py`。
2. target rollout 后检测 prompt echo。
3. echo 时 retry；仍 echo 则 hard fail。
4. final/selection report 记录 `output_hygiene_reason`。

验收：

- `Task:`、`Skill reference:`、`Code context:` 不再以 soft 0.8+ 进入 gate。
- `jv_disburse_fee_001` 这类输出会 retry 或 hard fail。

### 阶段 B：Rule Bank MVP ✅

1. 从 `optimization-07/best_skill.md` 提取无污染规则。
2. 写入 `demo-project/rule_bank/rules.jsonl`。
3. M4 初始化时注入 active rules。
4. inspect 显示 rule bank 命中数。

验收：

- 新 run 初始 skill 包含 `optimization-07` 的有效规则。
- 不再依赖 LLM 每轮重新发现“明确交易类型+金额应生成凭证”。

### 阶段 C：Code Diagnosis MVP ✅

1. 对 hard failures 读取 `context_refs`。
2. 生成 `code_diagnosis.jsonl`。
3. reflect prompt 强制引用 diagnosis。
4. 规则没有 `code_facts` 时只能进入 candidate，不可直接 best（`require_code_facts_for_rules` 可配置，默认 `false`）。

验收：

- 每个 hard fail 至少有 diagnosis status。
- diagnosis 能区分 `missing_business_rule`、`output_format_error`、`prompt_echo`、`scorer_alias_gap`。

### 阶段 D：Replay Gate ✅

1. 构建 recent failure replay pool。
2. 通过 selection 后运行 replay。
3. replay regression 阻止 new best（或按 `on_regression: accept_current` 降级）。

验收：

- 后续 run 不应从 0.875 hard 回退到 0.5 hard 而仍认为正常。
- `run_quality_report` 增加 replay hard / regression ids。

### 阶段 E：完整闭环 ✅

1. diagnosis -> candidate rule。
2. candidate rule -> replay。
3. replay pass -> best skill。
4. best skill -> rule bank。
5. next run warm start。

## 16. 验收指标

### 16.1 稳定性指标

| 指标 | 目标 |
|---|---:|
| 连续 3 次 run 的 test hard 方差 | <= 0.125 |
| rule bank active rule regression | 0 |
| best_score_monotonic | true |
| leakage / case id | 0 |

### 16.2 诊断指标

| 指标 | 目标 |
|---|---:|
| hard failure diagnosis coverage | >= 90% |
| diagnosis with code facts | >= 80% |
| prompt echo detection precision | >= 95% |
| replay regression caught | 100% |

### 16.3 Fineract 当前目标

以最新 run `20260612-141940` 为基线：

| 指标 | 当前 | 目标 |
|---|---:|---:|
| test hard | 0.500 | >= 0.875 |
| test soft | 0.916 | >= 0.938 |
| prompt echo hard failures | 1 | 0 |
| rule bank active rules | 0 | >= 3 |

## 17. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 诊断层增加 token 和耗时 | run 成本上升 | 只对 hard failures、最多 N 条诊断 |
| rule bank 固化错误规则 | 长期污染 | replay gate + regression_count + 人工 inspect promote |
| 过度依赖历史失败 | 泛化不足 | replay 只做防回归，不替代 selection |
| prompt echo 检测误伤 | 正常答案被 retry | 模式限定为结构化 prompt 标记，允许项目配置 |
| 代码事实提取不准 | 错误规则进入候选 | `require_code_facts_for_rules` + confidence 阈值 |

## 18. 与 07 的关系

07 解决的是“接受什么”：best 单调、quality gate、无泄露、可观测。

08 解决的是“怎么产生更好的候选”：代码诊断、持久规则记忆、replay 防回归、输出卫生。

两者关系：

```text
08 产生更可靠候选
07 控制候选是否可接受
```

因此 08 不替代 07，而是在 07 之前增加更强的候选生成与验证层。

## 19. 推荐下一步（实现后）

代码已合入 `master`（commit `f8d1428`）。后续工作重点从「实现管线」转为「实跑验证与迭代」：

1. **实跑 M4**：在 `config.yaml` 启用 `rule_bank.path` 与 `warm_start.from_best_skill`，对比有无 rule bank 的 test hard 方差。
2. **replay 池扩展**：将历史黄金失败项写入 `replay_gate.external_pool_paths`，防止已解决 case 回退。
3. **inspect 巡检**：`skill-lab inspect run <run_id> --show-diagnosis`，关注 `diagnosis_metrics`、`replay_hard`、`replay_regressed_ids`。
4. **规则晋升**：好 run 结束后 `--promote-rules-to-bank`，或 `--warm-start-rule-bank` 从新 run 预热。
5. **可选增强**：独立 LLM 诊断 prompt（当前为启发式 + scorer `diagnostics`）；`require_code_facts_for_rules: true` 收紧候选规则。

`initial_skill.md` 仍是任务骨架；`rule_bank/rules.jsonl` 是跨 run 已验证规则记忆。两者分工见 §20.3。

## 20. 实现记录

### 20.1 新增模块

| 模块 | 路径 | 职责 |
|---|---|---|
| Output hygiene | `skillopt_loop/output_hygiene.py` | prompt echo / tool 残留检测、retry hint、step 报告 |
| Code diagnosis | `skillopt_loop/code_diagnosis.py` | 失败分类、证据链、`code_diagnosis.jsonl` |
| Diagnosis rules | `skillopt_loop/diagnosis_rules.py` | 诊断 → 候选规则、quality scan、`summary.json` |
| Rule bank | `skillopt_loop/rule_bank.py` | 跨 run 规则读写、inject、warm_start、write_back、`support_score` |
| Replay gate | `skillopt_loop/replay_gate.py` | replay pool、gate、fixed/regressed ids |
| Eval hygiene | `skillopt_loop/eval_hygiene.py` | `rollout_with_hygiene()` 统一 selection/test 评估 |

### 20.2 M4 主循环接线（`skillopt_loop/__init__.py`）

```text
M4 启动 → rule_bank warm_start + inject → initial_skill
每 step rollout → output_hygiene + 更新 replay_pool
reflect 前 → code_diagnosis + candidate rules → reflect prompt
selection eval → eval_hygiene
accept 前 → replay_gate（regression 时可 accept_current）
accept/finalize → rule_bank write_back
final → test eval hygiene + run_quality_report（含 diagnosis/replay 指标）
```

### 20.3 `initial_skill` 与 `rule_bank` 分工

| 产物 | 路径 | 角色 |
|---|---|---|
| 基线 Skill 骨架 | `demo-project/initial_skill.md` | 任务定义、科目对、输出格式；每 run 起点 |
| 跨 run 规则库 | `demo-project/rule_bank/rules.jsonl` | 已验证规则 + `support_count` / `regression_count` 元数据 |

M4 启动时将 active rules 注入 `initial_skill` 顶部 `## Rule bank (verified)` 段，而非覆盖骨架正文。

### 20.4 项目侧扩展（非通用 `src/`）

- `demo-project/benchmarks/score_expected_checks.py`：`diagnostics.failure_type` / `suggested_rule` 供诊断层消费。
- `demo-project/rule_bank/rules.jsonl`：5 条 active 规则（来自 `optimization-07` 与 echo 分析）。
- `settings.pipeline.atom_rule_include_keywords` / `exclude_keywords`：M3 atom 规则过滤，避免 Fineract 领域词固化进通用代码。

### 20.5 CLI

| 命令 / 选项 | 说明 |
|---|---|
| `inspect run --show-diagnosis` | 展示 diagnosis 步数、replay pool、hygiene/replay 末步报告 |
| `inspect run --promote-rules-to-bank` | 从指定 optimization 目录晋升规则到 `rule_bank.path` |
| `inspect run --warm-start-rule-bank` | 同 promote（预热规则库） |
| `run all` / `optimize-skill --warm-start-rule-bank` | 启动前从配置或 CLI 预热规则库 |

### 20.6 已知限制

- 代码诊断当前为**启发式 + scorer diagnostics**，非完整 LLM 诊断 prompt。
- `require_code_facts_for_rules` 默认 `false`，便于 MVP 迭代；生产可收紧。
- rule bank 依赖 `min_support_score` 与 `exclude_regressed` 过滤；错误规则需 inspect 人工审查或降级。
