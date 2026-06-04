# code-to-skill 实现状态与下一步计划

> 更新日期：2026-06-04
> 设计基准：`docs/design/00-06` 设计文档

---

## 一、当前实现状态

| 模块 | 行数 | 测试 | 状态 | 设计覆盖度 | 关键缺失 |
|------|------|------|------|-----------|----------|
| **M5** 模型交互 | 653 | 5 | ✅ 完成 | 90% | Agent CLI backend 未实现（预留接口），sandbox 模式未实现 |
| **M6** CLI | 600 | 9 | ✅ 完成 | 85% | status/inspect/eval/publish/resume 仅 skeleton，交互式审批未实现 |
| **M1** 代码图谱 | 1160 | 10 | ✅ 完成 | 80% | tree-sitter 语法库未编译安装，LLM 聚类未接入，Java 入口点识别偏弱 |
| **M2** 文档规范化 | 719 | 14 | ✅ 完成 | 75% | RemoteKnowledgeSource 未实现（接口已预留），OCR 为可选依赖 |
| **M3** SkillAtom | 522 | 9 | ✅ 完成 | 70% | LLM 抽取未接入（当前规则模式），aligner 未实现，checks 仅基于规则 |
| **M4** SkillOpt | 372 | 6 | ✅ 完成 | 60% | LLM reflect/aggregate/select 未接入（当前规则 patch），slow_update 未实现，meta_skill 未实现，state_manager 仅有基础保存 |

### 端到端验证（Apache Fineract）

| 步骤 | 输入 | 输出 | 状态 |
|------|------|------|------|
| scan → parse → resolve | Fineract accounting+portfolio (1063 files) | 853 nodes (380 methods + 86 classes) | ✅ |
| cluster → leaf_context | 853 nodes | 111 leaf contexts | ✅ |
| normalize | README.md | 7 chunks | ✅ |
| extract → score → merge | 111 leaves + 7 chunks | 295 raw → 10 merged (8 accepted) | ✅ |
| rollout → evaluate | 8 benchmark seeds | best_score=1.0, 4 training steps | ✅ |

---

## 二、设计文档对照检查

### 2.1 已实现且匹配设计的

- M5: `InteractionRequest/Response` schema，`InteractionBackend` 接口，`OpenAICompatibleBackend`，`Router`（role/stage + fallback），`Tracer`，`structured_output` 三级降级
- M6: `RunManifest/RunState/Event` schema，`ProjectConfig`（完整 project.yaml schema），11 个 click 命令骨架，`init` + `config validate` 完整实现
- M1: `CodeGraph/GraphNode/GraphEdge` schema，`FileInventory`，多语言扫描+glob 过滤，6 语言正则解析器，import 引用解析，目录级模块树聚类，叶子上下文+token 控制
- M2: `DocumentManifest/Chunk/Table` schema，`KnowledgeSource` 接口+`LocalFileKnowledgeSource`，Markdown/HTML/PDF/DOCX 解析器，清洗脱敏，chunk 切分
- M3: `SkillAtom/RawAtom/SourceRef` schema，代码/文档规则抽取，三层分层制 scoring，合并去重，benchmark 种子生成
- M4: `BenchmarkItem/RolloutResult/EditOp` schema，确定性 scorer，edit apply（append/replace/delete），6 阶段训练循环，语义 hash，断点状态保存

### 2.2 设计中有但未实现

| 设计文档 | 设计项 | 原因 |
|----------|--------|------|
| 01 §4.6 | LLM 聚类模块树 | 需要 M5 LLM backend + prompt 模板（已设计） |
| 02 §2.4 | RemoteKnowledgeSource（飞书/Confluence API） | 接口已预留，需要第三方 API 凭证 |
| 03 §4.2.1 | LLM 抽取 SkillAtom（prompt 模板已设计） | 需要 M5 LLM backend，当前规则模式可独立运行 |
| 03 §4.3 | 证据对齐（evidence alignment） | 需要同时持有代码+文档来源做交叉验证 |
| 04 §4.4 | LLM Reflect（轨迹分析生成 patch） | 需要 M5 optimizer backend |
| 04 §4.6 | LLM Select（编辑排序） | 需要 M5 optimizer backend |
| 04 §4.9 | Slow Update（epoch 级纵向更新） | MVP 阶段跳过 |
| 04 §4.10 | Meta Skill（优化器侧记忆） | MVP 阶段跳过 |
| 05 §7.5 | Agent CLI backend | 需要 Codex/Claude Code 安装配置 |
| 05 §7.5 | Sandbox 安全隔离 | 需要 Docker 环境 |

### 2.3 设计与实现差异

| 位置 | 设计 | 实现 | 差异说明 |
|------|------|------|----------|
| M1 parser | tree-sitter 为主 | 正则降级为主 | tree-sitter 语言 grammar 需单独编译，当前以正则模式运行 |
| M3 extractor | LLM 抽取 | 规则启发式 | LLM 模式可通过 M5 structured_output 接入，规则模式保证离线可运行 |
| M4 rollout | target Agent 执行 | 规则模拟回答 | 接入 M5 backend 后切换为真实 LLM rollout |
| M4 reflect | optimizer 分析轨迹 | 规则 patch | 当前生成 TODO patch，接入 LLM 后产生有意义的编辑 |

---

## 三、下一步计划

### 3.1 短期（提升单模块质量）

| 优先级 | 模块 | 任务 | 预估工时 |
|--------|------|------|----------|
| P0 | M1 | 安装 tree-sitter Java grammar，切换为 AST 解析 | 2h |
| P0 | M1 | 增强 Java 入口点识别（Spring annotations 覆盖率） | 1h |
| P1 | M3 | 实现证据对齐 `aligner.py`（代码/文档 source_ref 交叉匹配） | 2h |
| P1 | M4 | 接入 M5 MockReplayBackend，实现 fixture 驱动的 rollout | 2h |
| P1 | M3/M4 | 补充 Fineract 相关 benchmark（从 Issues/PR 抽取 10+ 真实任务） | 3h |

### 3.2 中期（LLM 接入）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | M5 配置百炼 backend | 用户提供 API key，替换 MockBackend |
| P0 | M3 LLM 抽取 | 用 M5 structured_output 调用 LLM 执行 §4.2.1 的 prompt 模板 |
| P0 | M4 LLM reflect | 用 M5 optimizer backend 分析失败轨迹生成 patch |

### 3.3 长期（生产化）

| 任务 | 说明 |
|------|------|
| 多仓库支持 | 当前 M1 只处理第一个 repo |
| 增量更新 | 检测代码变化，仅重新解析变更文件 |
| 历史轨迹接入 | M3 从 Agent 执行日志中学习失败模式 |
| 发布回滚 | M6 publish 支持版本管理和回滚 |
| Web UI / TUI 仪表盘 | 当前仅 CLI |

---

## 四、风险提示

| 风险 | 等级 | 缓解 |
|------|------|------|
| tree-sitter grammar 编译失败 | 低 | 正则降级已可工作，853 节点产出可接受 |
| LLM API 成本不可控 | 中 | MockReplayBackend 保证离线闭环可测 |
| Fineract 无配套文档导致 M2 输入稀疏 | 中 | 当前仅 README.md 作为文档源，需手动补充 |
| 网络不稳定影响推送 | 低 | 本地仓库完整，改日推送即可 |
