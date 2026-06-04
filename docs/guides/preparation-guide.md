# 准备指南：初始知识库、草稿 SKILL 与测评案例

> 适用场景：开始一个新项目前，准备 code-to-skill 流水线需要的最小输入。
> 以 Apache Fineract 为示例，但所有步骤可推广到任意仓库。

---

## 一、知识库准备

code-to-skill 从两个来源提取 SkillAtom：**代码仓库**（M1）和 **知识文档**（M2）。代码仓库准备好 `.test-data/fineract-develop` 即可（见 README），下面对知识文档做详细说明。

### 1.1 文档类型与优先级

| 优先级 | 文档类型 | 示例 | 对 Skill 的贡献 |
|--------|----------|------|-----------------|
| 最高 | 官方编码规范/API 文档 | CONTRIBUTING.md, API reference | constraint, coding_convention |
| 高 | 故障处理手册/运维 SOP | runbook.md, incident-guide.md | procedure, failure_mode |
| 中 | README/架构设计文档 | README.md, architecture.md | concept, tool_policy |
| 低 | FAQ/讨论帖 | wiki-export.md | failure_mode, concept |

### 1.2 准备步骤

**Step 1**：创建知识库目录 `kb/<project>/`：

```bash
mkdir -p kb/fineract
```

**Step 2**：收集 Markdown 文档（最推荐，解析最准确）：

```bash
# 从目标仓库直接复制
cp .test-data/fineract-develop/README.md kb/fineract/
cp .test-data/fineract-develop/CONTRIBUTING.md kb/fineract/

# 从官方 Wiki 导出
# - GitHub Wiki: git clone https://github.com/apache/fineract.wiki.git kb/fineract/wiki
# - Confluence: Space settings → Export → HTML
# - 飞书: 文档 → 导出 → Markdown
```

**Step 3**：收集 PDF 文档（如官方手册）：

```bash
# 下载或复制 PDF
cp ~/Downloads/apache-fineract-user-manual.pdf kb/fineract/
```

**Step 4**：在 `project.yaml` 中注册文档：

```yaml
sources:
  docs:
    - id: fineract-readme
      path: kb/fineract/README.md
      provider: local_file
      type: markdown
      version: "1.10"
      authority: official_doc

    - id: fineract-contrib
      path: kb/fineract/CONTRIBUTING.md
      provider: local_file
      type: markdown
      version: "1.10"
      authority: official_doc

    - id: fineract-manual
      path: kb/fineract/user-manual.pdf
      provider: local_file
      type: pdf
      ocr_enabled: false
      authority: official_doc
```

### 1.3 文档质量要求

| 要求 | 说明 |
|------|------|
| 有明确版本 | 文档文件名或 YAML 中标注版本号 |
| 可追溯来源 | 知道是从哪个系统/时间导出的 |
| 不包含敏感信息 | API key、密码等会在 M2 中自动脱敏，但最好预先清理 |
| UTF-8 编码 | Markdown/文本文件统一 UTF-8 |

### 1.4 最少知识库

对于第一个 MVP，只需要 **一份 README.md** 即可跑通流水线：

```yaml
sources:
  docs:
    - id: repo-readme
      path: .test-data/fineract-develop/README.md
      provider: local_file
      type: markdown
```

验证知识库加载：

```bash
# 方式 1: config validate 会检查所有 source 是否可达
skill-lab config --config-path project.yaml

# 方式 2: 直接调用 M2
python -c "
from code_to_skill.document_normalizer import normalize_document
r = normalize_document('kb/fineract/README.md', 'test')
print(f'{len(r[\"chunks\"])} chunks')
"
```

---

## 二、草稿 SKILL 编写

在运行 M4 SkillOpt 优化之前，需要一份 **初始 Skill**（`initial_skill.md`）。如果从零开始，M3 产出的 atoms 会自动拼接为初始 Skill。但手写一份高质量草稿能让优化起点更高。

### 2.1 SKILL.md 模板

```markdown
# <项目名> Agent Skill

> 版本: 0.1.0
> 适用: <目标 Agent> 在执行 <任务类型> 时
> Token 预算: <目标 500-2000>

## 工作流程 (Workflow)

### <场景 1>
1. 步骤 1
2. 步骤 2
3. 验证: <检查项>

### <场景 2>
...

## 必须遵守的约束 (Constraints)

- <约束 1>: <具体规则>
- <约束 2>: <具体规则>

## 禁止行为 (Do NOT)

- ❌ <禁止项 1>
- ❌ <禁止项 2>

## 常见失败模式 (Failure Modes)

| 症状 | 根因 | 修复方向 |
|------|------|----------|
| <症状 1> | <根因> | <修复> |

## 验证检查清单 (Validation)

- [ ] <检查项 1>
- [ ] <检查项 2>
```

### 2.2 以 Fineract 为例的草稿 SKILL

```markdown
# Fineract Agent Skill

> 版本: 0.1.0 (草稿)
> 适用: Codex/Claude Code 在执行 Fineract 代码审查和代码修改时
> Token 预算: 800

## 工作流程

### 修改会计分录逻辑
1. 确认涉及的 GL Account（总账科目）
2. 检查 JournalEntry 类型（DEBIT/CREDIT）
3. 确认会计分录的触发条件（贷款发放/还款/费用扣款）
4. 验证: 借貸平衡 (Debits = Credits)

### 修改利率计算
1. 确认计息方式: declining balance / flat / 等额本息
2. 检查利率精度要求
3. 更新相关摊销逻辑
4. 验证: 还款计划重新计算后金额一致

## 必须遵守的约束

- 所有涉及金额变更的操作必须写入 JournalEntry（审计日志）
- 费用类型必须在系统允许列表中
- 罚金计算必须有上限

## 禁止行为

- ❌ 不得新增未授权的费用类型
- ❌ 不得修改罚金上限而不经审批
- ❌ 不得跨过 JournalEntry 直接修改账户余额

## 验证检查清单

- [ ] JournalEntry 记录完整
- [ ] 借貸金额平衡
- [ ] 费用类型在许可列表内
- [ ] 利率精度与配置一致
```

### 2.3 如何快速生成草稿

**方法 A**：先在目标仓库跑一次 M1→M3，用产出自动拼接：

```bash
skill-lab run all --config-path project.yaml
# 初始 Skill 在 runs/<run_id>/SKILL.md
```

**方法 B**：手动整理，问自己 4 个问题：

1. **Agent 在这个仓库最容易出什么错？** → 写进 Failure Modes
2. **哪些操作绝对不能做？** → 写进 Do NOT
3. **代码里有哪些强制检查（assert/validate/require）？** → 写进 Constraints
4. **修复 bug 或新增功能的正确步骤是什么？** → 写进 Workflow

---

## 三、测评案例（Benchmark）准备

Benchmark 是 M4 SkillOpt 优化的核心驱动力。每条 Benchmark 是一个问题 + 预期检查项 + 评分标准。

### 3.1 案例来源

| 来源 | 数量建议 | 获取方式 |
|------|----------|----------|
| GitHub Issues (bug fix) | 10-20 个 | 按 label:bug 过滤，提取修复描述 |
| PR Review Comments | 10-20 个 | 按 "changes requested" 过滤 |
| 代码中的 assert/validate 语句 | 自动生成 | M3 的 `benchmark_seeds` 自动抽取 |
| 人工编写的"陷阱"案例 | 5-10 个 | 故意构造容易出错的场景 |

### 3.2 Benchmark 格式

每条案例是一个 JSON 对象：

```json
{
  "id": "fineract_001",
  "question": "这段贷款利率计算代码有什么风险？\n\n```java\n// loan disbursement handler\npublic void disburse(Long loanId) {\n    Loan loan = loanRepo.findById(loanId);\n    BigDecimal interest = loan.getPrincipal().multiply(rate);\n    loan.setInterest(interest);\n    loanRepo.save(loan);\n}\n```",
  "task_type": "code_review",
  "context_refs": ["code://fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanDisbursementHandler.java"],
  "context_mode": "inline",
  "expected_checks": [
    "mentions JournalEntry or audit log",
    "mentions transaction boundary",
    "mentions interest calculation method"
  ],
  "scorer": "deterministic"
}
```

关键字段说明：

| 字段 | 说明 |
|------|------|
| `id` | 全局唯一标识 |
| `question` | 向 Agent 提出的问题（含上下文代码） |
| `task_type` | `code_review` / `qa` / `code_patch` |
| `context_refs` | 引用的代码节点 ID（M1 产出）或文件路径 |
| `context_mode` | `inline`（拼入 prompt）/ `agent_read`（Agent 自行读取）/ `none` |
| `expected_checks` | 正确回答应包含的关键词或条件 |
| `scorer` | `deterministic`（关键词匹配）或 `llm_judge`（LLM 评判） |

### 3.3 从真实 Issues 提取 Benchmark

以 Fineract Issue [#1234](https://github.com/apache/fineract/issues/1234) 为例：

**Issue 描述**：
> "Loan repayment schedule shows negative interest after prepayment"

**提取为 Benchmark**：
```json
{
  "id": "fineract_gh_1234",
  "question": "分析预还款后利息计算出现负值的原因，给出修复建议",
  "task_type": "code_review",
  "context_refs": ["code://fineract-provider/.../LoanRepaymentScheduleGenerator.java"],
  "context_mode": "inline",
  "expected_checks": [
    "prepayment",
    "interest recalculation",
    "negative amount check",
    "schedule regeneration"
  ],
  "scorer": "deterministic"
}
```

### 3.4 Benchmark 文件组织

```
benchmarks/fineract/
├── train/
│   └── items.json        # 训练用 (70%)
├── selection/
│   └── items.json        # 验证 gate 用 (15%)
└── test/
    └── items.json        # 最终报告用 (15%，训练期间不可见)
```

`items.json` 格式：
```json
{
  "schema_version": "1.0",
  "items": [
    { "id": "fineract_001", "question": "...", ... },
    { "id": "fineract_002", "question": "...", ... }
  ]
}
```

### 3.5 快速生成第一批 Benchmark

```bash
# 1. 跑一遍 M1→M3，自动生成种子
skill-lab run all --config-path project.yaml

# 2. 种子在 runs/<run_id>/atoms/benchmark_seeds.jsonl
# 3. 人工审核：检查 expected_checks 是否与问题匹配
# 4. 复制到 benchmarks/<project>/train/items.json
```

### 3.6 最少 Benchmark

对于第一个 MVP，**5-10 条**即可跑通 M4：

```bash
# 创建 benchmark 目录
mkdir -p benchmarks/fineract/{train,selection,test}

# 创建 8 条 training items（从 M3 种子人工筛选）
cat > benchmarks/fineract/train/items.json << 'EOF'
{
  "schema_version": "1.0",
  "items": [
    {
      "id": "fin_001",
      "question": "修改贷款利率计算时需要注意什么？",
      "task_type": "qa",
      "expected_checks": ["interest", "amortization", "schedule"],
      "scorer": "deterministic"
    },
    {
      "id": "fin_002",
      "question": "添加新的费用类型时有什么约束？",
      "task_type": "qa",
      "expected_checks": ["charge", "authorized", "limit"],
      "scorer": "deterministic"
    }
  ]
}
EOF
```

---

## 四、验证清单

跑全流程前，确认以下各项：

- [ ] `external/<repo>/` 存在且有 Java 源文件
- [ ] `kb/<project>/` 至少有 1 份 Markdown 文档
- [ ] `project.yaml` 的 `sources.repos[0].path` 指向正确路径
- [ ] `project.yaml` 的 `sources.docs` 至少注册了 1 份文档
- [ ] `skill-lab config --config-path project.yaml` 通过校验
- [ ] `benchmarks/<project>/train/items.json` 有 5+ 条训练案例
- [ ] （可选）`initial_skill.md` 在项目根目录

---

## 五、常见问题

**Q: 没有配套文档怎么办？**
A: 可以不要文档。M3 的规则抽取只需要代码（规则模式）。M2 跳过即可。

**Q: 不想手写 Benchmark 怎么办？**
A: M3 会自动从代码中生成 `benchmark_seeds`。虽然不是最优，但可以跑通 M4 闭环。

**Q: 如何判断 Benchmark 质量？**
A: 好的 Benchmark 满足：问题明确、有正确回答方向（expected_checks）、与代码实际行为相关、不会被 Skill 中直接抄到答案。

**Q: SKILL.md 写多长？**
A: MVP 目标 500-2000 tokens。太长 Agent 加载成本高，太短覆盖不全。优先保留约束和禁止行为，细节放 references/。
