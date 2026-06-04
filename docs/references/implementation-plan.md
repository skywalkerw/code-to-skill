# code-to-skill 实现状态与下一步计划

> 更新日期：2026-06-04
> 设计基准：`docs/design/00-06` 设计文档

---

## 一、当前实现状态

| 模块 | 行数 | 测试 | 设计覆盖 | 状态 | Phase 关键新增 |
|------|------|------|---------|------|---------------|
| **M5** 模型交互 | 750 | 5 | 95% | ✅ | `llm_backend.py`(DeepSeek API 工厂+降级)，L1 structured output |
| **M6** CLI | 600 | 9 | 85% | ✅ | — |
| **M1** 代码图谱 | 1160 | 10 | 85% | ✅ | — |
| **M2** 文档规范化 | 719 | 14 | 80% | ✅ | kb/fineract/ 含 README+CONTRIBUTING |
| **M3** SkillAtom | 830 | 9 | 85% | ✅ | `llm_extractor.py`(LLM 抽取+降级) |
| **M4** SkillOpt | 630 | 6 | 75% | ✅ | `llm_components.py`(LLM Reflect+Select+降级) |

### 关键指标

| 指标 | 数值 |
|------|------|
| 总代码行 | ~4,700 |
| Python 文件 | 40 |
| 测试 | 53（全部通过） |
| LLM 能力 | M3 抽取✅ + M4 Reflect✅ + M4 Select✅ |
| 离线降级 | 全部 LLM 组件支持规则模式回退 |

### Phase 完成状态

| Phase | 主题 | 状态 |
|-------|------|------|
| Phase 1 | 知识库+Skill+Benchmark | ✅ 完成 |
| Phase 2 | LLM 接入 | ✅ 完成 |
| Phase 3 | 工程完善 | ✅ 大部分完成 |
| Phase 4 | 文档与社区 | ✅ 大部分完成 |

---

## 二、Phase 1 完成项

### 知识库
- `kb/fineract/README.md` — 项目架构文档
- `kb/fineract/CONTRIBUTING.md` — 贡献指南
- `project.yaml` 注册 2 份文档，M2 产出 14 chunks

### 草稿 Skill（`initial_skill.md`）
- 4 个 Workflow（会计分录/利率计算/费用类型/互操作支付）
- 7 条 Constraints（审计日志/借贷平衡/费用授权/罚金上限等）
- 7 条 Do NOT（禁止事项）
- 5 种 Failure Modes（含症状/根因/修复方向）
- 8 项 Validation Checklist

### 测评案例（`benchmarks/fineract/train/items.json`）
- 12 条基于真实 Fineract 场景
- loan/journal/savings/interop/charge/constraint/transaction 全覆盖
- 全部 deterministic scorer

---

## 三、Phase 2 完成项

### LLM Backend（`model_gateway/llm_backend.py`）
- `is_llm_available()` — 检测 `DEEPSEEK_API_KEY` 环境变量
- `create_llm_backend()` — 自动创建 DeepSeek API 或降级 MockBackend
- 配置：`deepseek-api` / `deepseek-v4-pro`（base_url=https://api.deepseek.com）

### M3 LLM 抽取（`atom_extractor/extractor/llm_extractor.py`）
- `extract_from_code_llm()` — 调用 L1 structured_output
- `extract_from_docs_llm()` — 从文档抽取
- Prompt 模板完全对齐设计文档 §4.2.1
- 降级：LLM 不可用时返回空 → 规则模式补全
- 实测：AccountingProcessorHelper → "JournalEntry 必须先 validate 再 save"

### M4 LLM Reflect/Select（`skillopt_loop/llm_components.py`）
- `reflect_llm()` — 分析失败轨迹 → 具体 edit 建议
- `select_edits_llm()` — 按 budget 排序候选编辑
- 实测：2 条失败 case → 2 条编辑建议（"state-changing ops 前加 audit" / "重试前检查幂等"）
- Select 实测：3 选 2（balance check 0.80 > idempotency 0.70）

---

## 四、端到端验证数据

| 指标 | 数值 |
|------|------|
| Fineract 文件 | 416 |
| 图谱节点 | 2,197 |
| 入口点 | 942 |
| M2 文档 chunks | 14（README + CONTRIBUTING） |
| M3 atoms | 规则 1,009 + LLM 补充 |
| M3 aligned | 6 accepted（conf=0.90） |
| M4 benchmark | 12 条 hand-crafted |
| M4 评分 | best_score=1.000（规则模式）/ LLM 模式等待真实 rollout |
| Skill 产物 | initial_skill.md（2,727 chars / 手工） + SKILL.md（M3 自动） |

---

## 五、下一步计划

### Phase 3：工程完善

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | M6 status/inspect/resume 实现 | 读取 run_state 展示进度 + 断点恢复 |
| P1 | 多仓库支持 | 当前只处理 `sources.repos[0]`，扩展到全部 |
| P1 | 真实 LLM rollout（替换规则模拟） | 接入 DeepSeek API 做 M4 rollout |
| P1 | selection/test split 补充 | 当前仅 train，缺少 held-out gate |
| P2 | 增量更新 | 检测 commit diff → 仅重新解析变更文件 |
| P2 | CI 集成 | GitHub Actions 自动化测试 |

### Phase 4：文档与社区

| 优先级 | 任务 | 
|--------|------|
| P0 | 编码规范（`docs/coding-standards.md` ✅ 已完成） |
| P1 | 端到端演示 |
| P1 | API 文档 |
| P1 | 贡献指南 |
| P2 | 示例项目包 |

---

## 六、风险更新

| 风险 | 等级 | 缓解 | 状态 |
|------|------|------|------|
| tree-sitter 编译 | 低 | 正则降级 2,197 节点可接受 | ✅ 已缓解 |
| LLM API 成本 | 低 | 降级模式 + prompt 长度控制 | ✅ 已缓解 |
| 文档稀疏 | 低 | kb/fineract 已含 2 份文档 | ✅ 已解决 |
| 网络不稳定 | 低 | HTTPS 推送成功 | ✅ 已解决 |
| Java 解析遗漏 | 中 | 当前覆盖 class/method/annotation，缺泛型/lambda | ⬜ 待改进 |
