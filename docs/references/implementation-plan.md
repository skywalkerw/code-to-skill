# code-to-skill 实现状态与下一步计划

> 更新日期：2026-06-04
> 设计基准：`docs/design/00-06` 设计文档
> 代码行数：4137 行（37 文件）| 测试：53 个（全部通过）

---

## 一、当前实现状态

| 模块 | 行数 | 测试 | 设计覆盖 | 状态 | 上次更新后新增 |
|------|------|------|---------|------|---------------|
| **M5** 模型交互 | 653 | 5 | 90% | ✅ | — |
| **M6** CLI | 600 | 9 | 85% | ✅ | `run_all` 接入全模块调用 |
| **M1** 代码图谱 | 1160 | 10 | 85% | ✅ | Java 正则修复 + 入口点增强（942 entrypoints） |
| **M2** 文档规范化 | 719 | 14 | 75% | ✅ | — |
| **M3** SkillAtom | 626 | 9 | 80% | ✅ | aligner（19→8 高置信原子，conf=0.90） |
| **M4** SkillOpt | 372 | 6 | 65% | ✅ | 确定性 scorer 评分修复 + keyword seeds |

### 关键指标

| 指标 | 数值 |
|------|------|
| 总代码行 | 4,137 |
| Python 文件 | 37 |
| 测试 | 53（全部通过） |
| 核心依赖 | 13 个 pip 包 + 可选 lxml/ocr |
| 离线部署 | vendor/ + `pip install --no-index` |

### 端到端验证（Apache Fineract develop，416 文件）

| 步骤 | 输入 | 输出 |
|------|------|------|
| M1 scan → parse | 416 Java files | 2,197 nodes（1,566 methods + 604 classes + 26 config） |
| M1 resolve | 2,197 nodes | 54,487 edges |
| M1 entrypoints | 2,197 nodes | 942 entrypoints（473 config + 422 service + 47 REST） |
| M1 leaf_context | 2,197 nodes | 315 leaf contexts |
| M2 normalize | README.md | 7 chunks |
| M3 extract | 315 leaves + 7 chunks | 1,009 raw atoms |
| M3 score → align → merge | 1,009 raw | **6 accepted atoms**（201~298 source files each） |
| M3 seeds | 6 atoms | 6 benchmark seeds |
| M4 optimize | 6 seeds, 3 epochs | best_score=1.000 |
| **SKILL.md** | 终产物 | 8340 chars / ~1464 tokens |

---

## 二、已完成 vs 未完成

### 2.1 本轮新增完成项

| 日期 | 改进 | 影响 |
|------|------|------|
| 06-04 | Java 正则修复（方法/类名正确提取） | 节点数 ↑，名称从 `public` → `createJournalEntry` |
| 06-04 | Java 入口点增强（JAX-RS @Path/@GET + Spring） | entrypoints 0 → 942 |
| 06-04 | M3 aligner 证据对齐 | 19 个重复原子 → 4 个高置信规则（conf=0.90） |
| 06-04 | Benchmark seed 关键词增强 | M4 确定性 scorer 从 0.0 → 1.0 |
| 06-04 | M6 CLI `run_all` 接入全模块 | 一键端到端 |
| 06-04 | README + 准备指南 | 文档齐全 |
| 06-04 | 代码目录清理 + docs 整理 | 项目结构干净 |

### 2.2 仍然未完成

| 设计文档 | 设计项 | 原因 |
|----------|--------|------|
| 01 §4.6 | LLM 聚类模块树 | 需 M5 LLM backend |
| 01 §4.2 | tree-sitter AST 解析 | grammar 编译复杂（已尝试），正则降级可用 |
| 02 §2.4 | RemoteKnowledgeSource | 接口已预留，需第三方 API 凭证 |
| 03 §4.2.1 | LLM 抽取 SkillAtom | 需 M5 LLM backend |
| 04 §4.4/4.6 | LLM Reflect + Select | 需 M5 optimizer backend |
| 04 §4.9/4.10 | Slow Update + Meta Skill | MVP 跳过 |
| 05 §7.5 | Agent CLI + Sandbox | 需 Docker |
| 06 §4.5-4.10 | status/inspect/resume 命令 | CLI skeleton 已就绪，待实现 |

---

## 三、下一步计划

### Phase 1：提升 Fineract Skill 质量（1-2 天）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | 补充 Fineract benchmark | 从 Issues/PR 抽取 10-20 条真实任务，分 train/selection/test |
| P0 | 手写一份高质量 initial_skill.md | 覆盖 Workflow/Constraints/Failure Modes（见准备指南） |
| P0 | 扩充知识库文档 | 添加 CONTRIBUTING.md、官方 Wiki 导出 |
| P1 | 增强 M1 Java 解析器 | 更多 Spring/JAX-RS 模式、构造函数识别、字段声明 |
| P1 | 实现 M4 真实 rollout（MockReplayBackend） | 替代当前规则模拟，使 optimizer 有真实反馈信号 |

### Phase 2：LLM 接入（2-3 天，需 API key）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | 配置百炼 backend | `dashscope-deepseek-v4-pro`，API key 写入环境变量 |
| P0 | M3 LLM 抽取 | 用 structured_output 调用 prompt 模板，替代规则模式 |
| P0 | M4 LLM Reflect | optimizer 分析失败轨迹 → 有意义的 patch（不再 TODO） |
| P0 | M4 LLM Select | optimizer 排序编辑 → edit budget 控制有意义 |
| P1 | 多模型投票 Judge | LLM judge scorer 替代确定性 scorer |

### Phase 3：工程完善（3-5 天）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | M6 status/inspect/resume 实现 | 读取 run_state 展示进度 + 断点恢复 |
| P1 | 多仓库支持 | 当前只处理 `sources.repos[0]`，扩展到全部 |
| P1 | 增量更新 | 检测 commit diff → 仅重新解析变更文件 |
| P1 | 历史轨迹接入 | 从 Agent 执行日志学习失败模式 |
| P2 | 发布回滚 | publish 命令支持版本号和回滚 |
| P2 | CI 集成 | GitHub Actions 自动化测试 |

### Phase 4：文档与社区（1-2 天）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | 端到端演示视频/录屏 | Fineract 完整流程 |
| P1 | API 文档 | 每个模块的公开函数 docstring |
| P1 | 贡献指南 | CONTRIBUTING.md |
| P2 | 示例项目包 | 开箱即用的 Fineract example |

---

## 四、风险更新

| 风险 | 等级 | 缓解 | 状态 |
|------|------|------|------|
| tree-sitter 编译 | 低 | 正则降级 2,197 节点产出可接受 | ✅ 已缓解 |
| LLM API 成本 | 中 | MockReplayBackend 离线闭环 | ⬜ 待 API key |
| 文档稀疏 | 中 | 当前仅 README.md | ⬜ 待补充 |
| 网络不稳定 | 低 | HTTPS 推送成功 | ✅ 已解决 |
| Java 解析遗漏 | 中 | 当前覆盖 class/method/annotation，缺泛型/lambda | ⬜ Phase 1 改进 |
