# 09：代码优先的精准检索与 SkillOpt 利用设计

> 状态: 已实现  
> 日期: 2026-06-12  
> 关联文档: `03-skillatom-extraction.md`、`04-skillopt-loop.md`、`07-skillopt-run-quality-optimization.md`、`08-code-diagnosis-driven-skillopt.md`  
> 目标: 让代码成为 SkillOpt 的主信息源，通过确定性检索、结构化代码事实和证据约束，稳定生成可复用 skill 规则

## 1. 背景

最近几轮 Fineract runs 说明：系统已经能读取代码、构建 graph、验证 `context_refs`，但“代码被充分利用”的程度仍不够。

最新 run `20260612-182126` 中：

- `context_ref_report.resolve_rate = 1.0`，说明 benchmark 引用路径都能解析。
- final test 达到 `test_hard=1.0`、`test_soft=1.0`，说明当前闭环已经能修复测试失败。
- 但 `run_quality_report.diagnosis_metrics.code_facts_rate = 0.286`，说明多数诊断仍来自 missed checks / scorer 反馈，而不是代码事实。
- M3 仍会抽到 `Command handlers`、`@Service`、`审计日志` 等实现结构规则；虽然已通过配置过滤避免注入 skill，但源头检索与筛选仍不够精准。

这说明当前系统的问题不是“完全没有代码”，而是代码证据没有成为优化决策的中心。

## 2. 问题定义

### 2.1 当前代码利用链路

现有链路大致为：

```text
benchmark context_refs
  -> validate_context_refs
  -> rollout inline / tool_loop
  -> failed_results
  -> code_diagnosis
  -> reflect prompt
  -> candidate skill
  -> selection / replay / quality gate
```

当前已有能力：

- `CodeToolsHandler` 提供 `search_code`、`read_code_file`、`search_symbol`、`get_code_context`、`explore_symbol`、`trace_symbol` 等工具。
- `build_rollout_item_context()` 能按 `context_refs` 为 rollout 注入代码片段。
- `build_reflect_code_evidence()` 能为失败 case 预取部分代码证据。
- `code_diagnosis` 能记录失败类型、missed checks、context refs 和少量 code facts。
- `rule_bank` 能持久化已验证规则。

### 2.2 主要缺口

| 缺口 | 表现 | 影响 |
|---|---|---|
| 检索入口被动 | 主要依赖已有 `context_refs` 和 missed checks | 没有找到真正业务逻辑时仍继续优化 |
| 查询词粗糙 | 直接用 question / missed checks 搜 | 容易搜到 handler、swagger、configuration |
| rerank 不足 | 搜到的代码按工具原始顺序使用 | 业务 processor/service/domain 可能排在低位 |
| 代码事实弱 | reflect 看到源码片段或 missed checks，而非结构化 fact | 规则像关键词补丁，不像代码归纳 |
| 证据门槛弱 | `require_code_facts_for_rules=false` 时仍可从 missed checks 生成业务规则 | rule bank 可能收录无代码支撑规则 |
| rollout 工具发散 | LLM 在 rollout 中多轮随机搜索 | 成本高、延迟高、且可能找错方向 |

## 3. 设计目标

1. 代码是业务规则的主信息源；scorer/missed checks 只作为失败信号。
2. 每个失败 case 先生成确定性 `CodeQueryPlan`，再执行检索。
3. 检索结果经过角色感知 rerank，优先业务逻辑代码，降低 glue code 权重。
4. reflect 输入使用结构化 `CodeFact`，而不是长源码堆叠。
5. 没有代码事实的业务规则默认不进入 rule bank。
6. rollout 阶段减少自由工具调用，优先使用预取代码事实。
7. M3 atom accepted 与 M4 skill rule 都必须带证据质量指标。

## 4. 非目标

- 不把 Fineract 业务规则硬编码到通用代码。
- 不让 scorer keyword 替代代码事实。
- 不把所有源码塞进 prompt。
- 不要求所有任务都有代码事实；输出格式、prompt echo 等通用问题可以无需代码证据修复。

## 5. 总体方案

新增一条 **Code-First Retrieval Pipeline**：

```text
failure / benchmark item
  -> CodeQueryPlan
  -> multi-channel retrieval
  -> role-aware rerank
  -> CodeFact extraction
  -> evidence-gated reflect
  -> candidate rules with evidence_refs
  -> replay + quality gate
  -> rule bank
```

核心变化：

- 从“让 LLM 在 tool loop 里自己找代码”改为“系统先确定性找出相关代码事实”。
- 从“missed checks -> 规则”改为“missed checks -> 查询计划 -> 代码事实 -> 规则”。
- 从“源码片段注入”改为“代码事实 + 最小证据片段注入”。

### 5.1 模块边界

纯代码能力必须归到 `tool` 层，M4 只做 SkillOpt 接线。

```text
src/code_to_skill/tool/
  code_tools.py          # 文件搜索、文件读取、图谱查询 handler
  code_retrieval.py      # CodeQueryPlan / CodeCandidate / CodeFact / rerank

src/code_to_skill/cli/tool_cmds.py
  skill-lab tool code ... # 直接命令行调用纯代码工具

src/code_to_skill/skillopt_loop/
  code_evidence.py       # 将 tool 产物转成 rollout/reflect 上下文
  code_diagnosis.py      # 将失败样本 + CodeFact 转成诊断和候选规则
```

边界原则：

- `tool` 不依赖 SkillOpt、benchmark、gate、rule bank。
- `tool` 可以被 CLI、MCP、M4、测试脚本共同调用。
- M4 不实现通用代码搜索算法，只调用 `tool` 并处理 SkillOpt-specific 的上下文注入、reflect prompt、replay gate、rule bank 写回。
- `codegraph_mcp` 只保留 MCP/兼容导出，不作为纯代码工具的主实现位置。

命令行入口：

```bash
skill-lab tool code search-code JournalEntry --config-path config.yaml
skill-lab tool code read-code-file path/to/Foo.java --repo-root /path/to/repo
skill-lab tool code search-symbol JournalEntry --db /path/to/graph.db --repo-root /path/to/repo
skill-lab tool code context "loan disbursement" --run-id 20260612-182126 --config-path config.yaml
skill-lab tool code trace processCommand --to createJournalEntry --db /path/to/graph.db
```

兼容入口：

```bash
skill-lab codegraph search JournalEntry --config-path config.yaml
```

`skill-lab codegraph` 保持为图谱专用兼容 CLI；新增能力优先放入 `skill-lab tool code`。

## 6. 核心数据结构

### 6.1 `CodeQueryPlan`

位置建议：

```text
src/code_to_skill/skillopt_loop/code_retrieval.py
```

结构：

```json
{
  "schema_version": "1.0",
  "case_id": "jv_loan_disburse_001",
  "question": "贷款发放 50000",
  "intent_terms": ["loan disbursement", "disburse", "cash accounting"],
  "anchor_refs": [
    "fineract-provider/.../CashBasedAccountingProcessorForLoan.java#createJournalEntriesForLoan"
  ],
  "symbol_hints": ["CashBasedAccountingProcessorForLoan", "LoanTransactionDTO"],
  "trace_targets": [
    {
      "from": "CreateLoanTransactionCommandHandler",
      "to": "createJournalEntriesForLoan"
    }
  ],
  "include_roles": ["processor", "service", "domain", "dto", "enum"],
  "exclude_roles": ["swagger", "handler_only", "configuration"],
  "missed_checks": ["贷款", "现金", "发放"],
  "scorer_failure_type": "missing_business_rule"
}
```

来源：

- `context_refs`
- `question`
- `missed_checks`
- `scorer_diagnostics`
- `atom_ids` / `source_atom_ids`
- `entrypoint_id`
- `graph_sidecars.evidence_index`

### 6.2 `CodeCandidate`

```json
{
  "ref": "CashBasedAccountingProcessorForLoan.java#createJournalEntriesForLoan",
  "path": "fineract-provider/src/main/java/...",
  "symbol": "createJournalEntriesForLoan",
  "kind": "method",
  "role": "processor",
  "source": "context_ref|symbol_search|trace|evidence_index|content_search",
  "score": 0.91,
  "score_reasons": [
    "context_ref_hit",
    "business_logic_role",
    "contains_amount_mapping",
    "called_by_entrypoint"
  ],
  "snippet": "...",
  "call_chain": "JournalEntriesApiResource -> service -> createJournalEntriesForLoan"
}
```

### 6.3 `CodeFact`

`CodeFact` 是进入 reflect / rule bank 的基本单位：

```json
{
  "fact_id": "fact_loan_disbursement_cash_direction",
  "case_id": "jv_loan_disburse_001",
  "statement": "贷款发放的现金制分录应借记贷款组合/贷记资金来源或现金科目，金额来自交易 DTO。",
  "evidence_refs": [
    "CashBasedAccountingProcessorForLoan.java#createJournalEntriesForLoan"
  ],
  "evidence_quotes": [
    "createDebitJournalEntryForLoan(...); createCreditJournalEntryForLoan(...);"
  ],
  "confidence": 0.86,
  "source": "trace+symbol",
  "role": "business_mapping"
}
```

约束：

- `statement` 必须是可复用业务事实，不是 benchmark keyword。
- `evidence_quotes` 只保留短片段，避免长源码污染 prompt。
- `confidence` 由来源、角色、调用链、snippet 命中综合计算。

## 7. 检索策略

### 7.1 Query Plan 生成

新增：

```python
build_code_query_plan(item_or_result, graph_sidecars, scorer_diagnostics) -> CodeQueryPlan
```

规则：

1. `context_refs` 是第一优先级 anchor。
2. `source_atom_ids` 可反查 `evidence_index`。
3. missed checks 中只保留可搜索业务词，过滤通用格式词。
4. question 中提取 CamelCase、英文业务词、中文动作词。
5. scorer diagnostics 若提供 `required_concepts` / `failure_type`，加入查询计划。

通用格式词过滤示例：

```text
会计凭证、借、贷、金额、表格、Markdown、借贷平衡
```

这些是输出要求，不应直接作为代码搜索主查询。

### 7.2 多路召回

新增聚合工具：

```text
find_relevant_code
```

输入：

```json
{
  "query_plan": {...},
  "max_candidates": 8,
  "include_source": true,
  "max_snippet_chars": 1200
}
```

内部并行召回：

| 通道 | 工具 | 目的 |
---|---|---|
| anchor refs | `explore_symbol` / `read_code_file` | 读取 benchmark 明确引用 |
| evidence index | `lookup_ref` / `lookup_atom` | 复用 M3 证据 |
| symbol search | `search_symbol` | 找类/方法 |
| context search | `get_code_context` | 找相关代码块 |
| trace | `trace_symbol` | 从入口到业务方法 |
| content search | `search_code` | 查常量、枚举、字段名 |
| file pattern | `list_graph_files` | 找同目录 processor/service |

### 7.3 角色感知 rerank

高权重：

- `service`
- `processor`
- `domain`
- `dto`
- `enum`
- 包含金额字段、GL account mapping、业务分支、交易类型判断的代码
- 与 `context_refs`、`entrypoint_id`、`source_atom_ids` 直接关联的代码

低权重：

- `*ApiResourceSwagger`
- 只委派的 `*CommandHandler`
- `Configuration` / `Starter`
- `Swagger` / DTO response wrapper
- 无业务分支的 glue code

建议评分：

```text
score =
  0.35 * anchor_score
+ 0.25 * role_score
+ 0.20 * semantic_match
+ 0.10 * call_chain_score
+ 0.10 * evidence_index_score
- 0.25 * glue_code_penalty
```

## 8. SkillOpt-loop 接线

### 8.1 Rollout 前

对每个 item：

1. 生成 `CodeQueryPlan`。
2. 执行 `find_relevant_code`，得到 `CodeFact`。
3. 将 top facts 写入 item 的 transient context：

```python
item["_code_facts"] = [...]
item["_code_candidates"] = [...]
```

`build_rollout_item_context()` 优先使用 `_code_facts`：

```text
--- Project code facts ---
- [fact] ...
  Evidence: ...
```

rollout 阶段默认不再让 LLM 自由搜索 5 轮；若已有 `_code_facts`，`rollout_max_tool_rounds` 可降为 1 或 0。

### 8.2 Failure diagnosis

`diagnose_failure()` 改为：

```text
failure
  -> build_code_query_plan
  -> find_relevant_code
  -> extract_code_facts
  -> classify_failure_with_facts
```

当 `failure_type=missing_business_rule` 时：

- 有 `CodeFact`：生成 `candidate_rule`。
- 无 `CodeFact` 且 `require_code_facts_for_rules=true`：标记 `needs_review`，不生成业务规则。

### 8.3 Reflect prompt

reflect prompt 新增硬约束：

```text
Only propose business rules grounded in Code Facts.
If a failure has no Code Facts, propose an investigation step or scorer/format fix, not a business mapping rule.
Do not convert missed_checks directly into skill text.
```

输入结构：

```markdown
## Failure
- id: jv_purchase_001
- missed checks: 库存, 借贷校验

## Code Facts
- `CashBasedAccountingProcessor...`: ...
  Evidence: ...

## Allowed Edits
- Add a general rule only if backed by at least one Code Fact.
```

### 8.4 Rule bank

`upsert_candidate_rules()` 要求业务规则包含：

```json
{
  "evidence_refs": ["..."],
  "code_fact_ids": ["..."],
  "support_count": 1
}
```

配置：

```yaml
settings:
  skillopt:
    code_diagnosis:
      require_code_facts_for_rules: true
    rule_bank:
      require_evidence_refs: true
```

没有 evidence 的业务规则不进入 active rule bank。

## 9. M3 atom 抽取改造

M3 也应使用相同的 role-aware 策略。

### 9.1 accepted atom 门槛

accepted atom 必须满足至少一项：

- source ref 指向 service/processor/domain/dto/enum。
- evidence summary 包含调用链或业务分支。
- 与 benchmark task domain 匹配。
- 非 glue code，或 glue code 只作为入口证据，不作为规则主体。

### 9.2 降权规则

以下 atom 默认降权到 `needs_review` 或 `rejected`：

- command handler 仅委派服务。
- Swagger/API response model。
- configuration bean wiring。
- constructor injection / `@Service` 等编码 convention，除非目标 skill 是“如何扩展代码”。

### 9.3 配置项

```yaml
settings:
  atom_extractor:
    code_first:
      enabled: true
      role_rerank: true
      accepted_roles:
        - service
        - processor
        - domain
        - dto
        - enum
      downrank_roles:
        - handler
        - swagger
        - configuration
```

## 10. 配置设计

新增：

```yaml
settings:
  skillopt:
    code_retrieval:
      enabled: true
      max_candidates: 8
      max_facts_per_case: 4
      max_snippet_chars: 1200
      require_code_facts_for_business_rules: true
      query_plan:
        use_context_refs: true
        use_missed_checks: true
        use_scorer_diagnostics: true
        use_atom_refs: true
      rerank:
        enabled: true
        prefer_roles:
          - processor
          - service
          - domain
          - dto
          - enum
        downrank_path_patterns:
          - "**/*Swagger*.java"
          - "**/*CommandHandler.java"
          - "**/*Configuration.java"
      rollout:
        use_prefetched_facts: true
        disable_free_search_when_facts_exist: true
      observability:
        write_query_plan: true
        write_code_candidates: true
        write_code_facts: true
```

## 11. 产物设计

每个 step 新增：

```text
optimization/code_retrieval/step_000N/query_plans.jsonl
optimization/code_retrieval/step_000N/candidates.jsonl
optimization/code_retrieval/step_000N/code_facts.jsonl
optimization/code_retrieval/step_000N/summary.json
```

summary 示例：

```json
{
  "schema_version": "1.0",
  "step": 13,
  "cases": 1,
  "query_plans": 1,
  "candidates": 8,
  "facts": 3,
  "cases_with_facts": 1,
  "code_facts_rate": 1.0,
  "top_sources": {
    "context_ref": 1,
    "trace": 1,
    "evidence_index": 1
  },
  "downranked_glue_hits": 4
}
```

## 12. 观测指标

新增到 `run_quality_report.json`：

```json
{
  "code_retrieval_metrics": {
    "query_plan_count": 12,
    "cases_with_code_facts": 10,
    "code_facts_rate": 0.833,
    "business_rules_with_evidence_rate": 0.9,
    "glue_code_top1_rate": 0.05,
    "avg_candidates_per_case": 6.7,
    "avg_facts_per_case": 2.4
  }
}
```

关键目标：

- `code_facts_rate >= 0.7`
- `business_rules_with_evidence_rate >= 0.8`
- `glue_code_top1_rate <= 0.1`
- final skill 中无 benchmark id / scorer leakage

## 13. 实施计划

> **状态（2026-06-13）**：Phase 1–5 均已落地。以下为各阶段目标与当前实现。

### Phase 1：确定性 QueryPlan 与聚合检索 ✅

`tool/code_retrieval.py` 已实现：`CodeQueryPlan`、`CodeCandidate`、`CodeFact`、`build_code_query_plan()`、`find_relevant_code()`。
多路并行召回：anchor refs、symbol search、trace、content search、evidence_index。
产物写入 `code_retrieval/step_NNNN/{query_plans,candidates,code_facts}.jsonl` + `summary.json`。

验收：
- 对已有 Fineract benchmark，失败 case 至少 70% 能产生 code facts。
- `jv_purchase_001` 不再只输出 "根据 missed checks 补库存"，而是带代码或明确 `needs_review`。

### Phase 2：Role-aware rerank ✅

`_classify_code_role()` + `_role_aware_rerank()` 已实现。评分公式：
`0.35*anchor + 0.25*role + 0.20*semantic + 0.10*callchain + 0.10*evidence_idx - 0.25*glue_penalty`。

验收：
- M3 `atom_extractor.code_first.enabled=true` 时，accepted atoms 不再以 command handler / constructor injection 为主体。
- reflect evidence top1 大多是业务实现代码。

### Phase 3：Reflect 与 rule bank 证据门禁 ✅

`REFLECT_SYSTEM_PROMPT` 新增 Code-Fact Grounding 约束。
`upsert_candidate_rules()` → `remove_no_evidence_business_rules()` 过滤无代码证据业务规则。
配置默认 `code_diagnosis.require_code_facts_for_rules: true`，`rule_bank.require_evidence_refs: true`。

验收：
- rule bank 中业务规则都有 `evidence_refs`。
- 无代码证据的 missed-check 规则不会进入 active rule bank。

### Phase 4：Rollout 预取事实与工具收敛 ✅

`prefetch_code_facts_for_items()` 在 rollout 前写入 `_code_facts`。
`build_rollout_item_context()` 三路优先级：预取 _code_facts → context_refs → question hints。
有 facts 时减少 rollout_max_tool_rounds。

验收：
- rollout 平均 tool rounds 降低。
- final eval 中 backend/tool-loop error 不再导致 hard fail。

### Phase 5：Run quality 与 inspect 增强 ✅

`skill-lab inspect run --show-diagnosis` 展示 code_retrieval 末步指标。
`run_quality_report.json` 含 `code_retrieval_metrics`。
M3 `atom_extractor.code_first` 配置 + `_apply_role_aware_filter()` 实现。

## 14. 测试计划

新增测试：

```text
tests/test_code_retrieval.py
tests/test_code_retrieval_rerank.py
tests/test_code_fact_gate.py
tests/test_m3_role_filter.py
```

覆盖：

- QueryPlan 从 context_refs/question/missed_checks/scorer diagnostics 生成。
- 通用格式词不会成为主搜索词。
- handler/swagger/config 被降权。
- service/processor/domain 被升权。
- 无 CodeFact 时不生成 business rule。
- 有 CodeFact 时 candidate rule 带 `evidence_refs`。
- rollout 有 `_code_facts` 时不再强依赖自由工具搜索。

## 15. 风险与缓解

| 风险 | 缓解 |
|---|---|
| rerank 规则过拟合 Java/Fineract | role 推断用 path/symbol 通用启发，项目特殊词只放 config |
| 代码事实抽取错误 | fact 必须带 evidence_refs 和短 quote；低 confidence 标记 needs_review |
| prompt 变长 | reflect 只注入 top facts，不注入长源码 |
| 召回成本增加 | 聚合检索结果按 case 缓存；rollout 复用预取 facts |
| 无代码证据导致规则少 | output_format/prompt_echo/scorer_alias_gap 仍可无代码修复；业务 mapping 才强制证据 |

## 16. 预期效果

完成后，SkillOpt 的优化路径应从：

```text
missed_checks -> LLM 猜规则 -> selection gate
```

升级为：

```text
failure -> query plan -> relevant code -> code facts -> evidence-backed rule -> replay gate
```

目标不是单轮分数更高，而是让有效规则更稳定、更可解释、更可跨 run 复用，并接近 Codex/Cursor 在目标代码库中快速定位业务知识的能力。
