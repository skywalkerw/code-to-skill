# CodeGraph 复刻计划：M1 vs external/codegraph

> 日期：2026-06-06  
> 参考：`external/codegraph/src/`（TypeScript）、`docs/references/codegraph-gap-analysis.md`

---

## 1. 架构对比

| 维度 | external/codegraph | 本项目 M1（当前） |
|------|-------------------|------------------|
| 定位 | Agent 实时代码智能服务（MCP） | 离线批处理 → SkillAtom / SkillOpt 证据 |
| 存储 | SQLite + FTS5 + unresolved_refs | SQLite 简化版 + graph.json |
| 解析 | tree-sitter WASM，28 语言 | tree-sitter 可选 + 正则，6 语言 |
| 解析精度 | AST 级 + 框架插件（20+） | 正则为主，Spring 注解补充 |
| 引用解析 | 多层：import + FQN + 框架 + callback 合成 | import + 方法体 calls 启发式 |
| 调用边 | 全仓库 calls + 动态派发合成 | 方法体 calls 启发式 ✅ |
| 遍历/搜索 | GraphTraverser + QueryBuilder + FTS 混合排序 | GraphTraverser + GraphQueryEngine + FTS ✅ |
| 上下文 | ContextBuilder（1200+ 行启发式） | 精简 ContextBuilder + LeafContext ✅ |
| 增量 | mtime/hash sync + 文件监听 | hash 检测 + pipeline 接入 ✅ |
| MCP | 10 工具 + daemon | 无；SkillOpt code_tools 图+文件双通道 ✅ |
| 独有能力 | — | 模块树、入口点、多仓库、benchmark 链路 |

---

## 2. 差距分级（历史 → 已关闭）

> 下列 P0–P2 项已在 Phase 1–9 中实现；保留原文供对照 external/codegraph。

### P0 — 阻塞 Agent 按需查代码 ✅ Phase 1

| 项 | 原差距 | 状态 | 代码 |
|----|--------|------|------|
| 图索引查询 | code_tools 仅 grep | ✅ | `code_tools.py` + `GraphRegistry` |
| DB 搜索 | 无 FTS | ✅ | `db.py` nodes_fts + `search_nodes()` |
| 上下文构建 | 无 TaskContext | ✅ | `context_builder.py` |
| 增量索引 | 每次全量 | ✅ | `get_changed_files()` + pipeline |
| 方法调用边 | resolver 无 calls | ✅ | `resolver.py` |

### P1 — 解析质量与查询完备性 ✅ Phase 2

| 项 | 原差距 | 状态 | 代码 |
|----|--------|------|------|
| tree-sitter | 未落地 | ✅ 可选 | `parser.py` + `[codegraph]` extra |
| qualified_name | 仅 file::name | ✅ | `GraphNode.qualified_name` + DB 列 |
| unresolved_refs | 仅内存 | ✅ | `db.save_unresolved_refs()` + 二阶段 |
| 查询语法 | 无 kind:/file: | ✅ | `query_parser.py` |
| manifest.json | 未写 | ✅ | `code_graph/__init__.py` |

### P2 — MCP 与运行时 parity-lite ✅ Phase 3–7

| 项 | 原差距 | 状态 | 代码 |
|----|--------|------|------|
| MCP 工具 | 无 | ✅ 6 工具 | `codegraph_mcp/__init__.py` |
| 框架解析 | 仅 Spring 注解 | ✅ 部分 | `framework.py` + `mybatis_xml.py` |
| 文件监听 | 无 | ✅ | `watcher.py` + `code-graph-watch` |
| 生成代码检测 | 无 | ✅ | `generated_detection.py` |

### P3 — 完整 parity（长期） ⬜ 未做

- 20+ 框架 resolver、28 语言、完整 callback 合成 parity  
- 预估 50k+ LOC，**非 M1 阻塞项**

---

## 3. 执行阶段

### Phase 1（已完成）— 图驱动代码工具 + DB 查询层

```
graph.db ──► GraphQueryEngine ──► ContextBuilder
                │                      │
                └──────► CodeToolsHandler（search_symbol / get_code_context / trace_symbol）
                │
run pipeline ──► 增量 parse + FTS 索引
resolver ──► 方法 calls 边
```

交付：
- `graph_queries.py`、`context_builder.py`
- `db.py` 扩展 FTS / search / stats
- `code_tools.py` 图索引优先
- `__init__.py` 增量流水线
- `resolver.py` calls 启发式
- 测试 + 更新 gap-analysis 状态

### Phase 2（已完成）— 解析与 schema 对齐

- `tree-sitter-languages` 可选集成（`pip install code-to-skill[codegraph]`）
- `GraphNode` + DB：`qualified_name` / `signature` / `docstring`
- `query_parser.py`：`kind:` / `file:` 过滤
- `unresolved_refs` 落库 + FQN 二阶段匹配
- `manifest.json` + `file_inventory.json` 产物

### Phase 3（已完成）— MCP 暴露

- `code_to_skill.codegraph_mcp`：**6 工具**（search / context / node / trace / impact / status）
- `codegraph-mcp` CLI + `docs/references/codegraph-mcp-config.json` 模板

### Phase 4（已完成）— 框架与运维

- Spring `@Autowired` / `@Bean` / `@Transactional` + MyBatis `@Mapper` 边
- Java `extends` / `implements` + FQN import 解析
- `evidence.py`：`EvidenceBuilder` → M3 `evidence_index.json` + `edge_path`
- `watcher.py` + `skill-lab run code-graph-watch` 增量监听

### Phase 5（已完成）— 派发合成与多仓库

- `generated_detection.py`：路径 + 内容标记，写入 `diagnostics/generated_files.json`
- `callback_synthesis.py`：interface → implementor 合成 calls 边
- `resolver`：`resolve_unresolved_second_pass` 二阶段 import 重试
- `registry.py`：`GraphRegistry` 多 graph.db 聚合
- SkillOpt / MCP：`graph_sources` 多仓库 + `codegraph_impact` 工具

### Phase 6（已完成）— 框架插件与运行时

- `mybatis_xml.py`：Mapper XML `namespace` / statement id 节点 + Java 引用边
- `js_callbacks.py`：addEventListener / promise.then 等回调 calls 边
- SkillOpt 图谱工具：`impact_symbol` + `graph_status`
- `codegraph-daemon`：watch + MCP stdio 一体
- `tool_loop`：工具轮次用尽后空响应重试一次

### Phase 7（已完成）— SkillOpt Rollout 可靠性

- `rollout_max_tool_rounds`（默认 2）与 reflect `max_tool_rounds`（5）分离
- `rollout_helpers.py`：输出模板 + tool 结果降级凭证
- `tool_snippets` 回传 + 阶段化 `synthesis_hint`
- React/JSX：`onClick={handler}` 等回调边扩展

### Phase 8（已完成）— M4 断点续训

- `resume_state.py`：`runtime_state.json` v1.1（epoch / next_batch_start / skill 路径）
- `run_skillopt_loop(resume=True)` + `skill-lab resume` / `optimize-skill --resume`
- `step_checkpoint.json` bootstrap（兼容无 runtime_state 的旧 run）
- Reflect 工具轮次后 `REFLECT_SYNTHESIS_HINT` 强制 JSON

### Phase 12（已完成）— 深度上下文 + 会计链接 + React RENDERS

- `react_renders.py`：JSX `<Component />` → `references` 边；接入 `run_code_graph_pipeline`
- `accounting_linker.py`：benchmark id / missed_checks → 图谱搜索 query
- `context_builder.build_deep()`：`search` + `explore` 顶层符号 + markdown 摘要
- `registry.build_context(..., deep=True)`；MCP `codegraph_context(deep=)`；SkillOpt `get_code_context(deep=)`
- `code_evidence.py`：reflect 无 context_refs 时用 accounting_linker 预取图谱块
- `rollout_helpers`：解析 `explore_symbol` JSON（`source` / `explored` 字段）
- CLI：`skill-lab doctor`（tree-sitter 版本、配置、数据源可达性）
- 测试：`test_phase12.py`

### Phase 11（已完成）— MCP/工具 parity + 实时图谱 + rollout 代码注入

- MCP 新增：`codegraph_files`、`codegraph_callers`、`codegraph_callees`（共 10 工具）
- `registry_holder.py`：MCP registry 单例 + db mtime 失效；daemon 同步后 `invalidate_registry`
- SkillOpt 工具：`list_graph_files`、`find_callers`、`find_callees`（图谱工具共 10 个）
- `build_rollout_item_context`：rollout 自动注入 benchmark `context_refs` 源码
- CLI：`skill-lab run code-graph-daemon`（watch + MCP stdio）

### Phase 10（已完成）— tree-sitter Query 深度解析 + SkillOpt 代码证据

- `ts_backend.py`：统一 grammar 加载（tree-sitter-languages / language-pack）
- `ts_queries.py`：Query 提取 + 全树遍历兜底，14 语言 grammar 映射
- `parser.py`：per-file `parse_backend` 统计 → `diagnostics/parse_stats.json`
- `pyproject.toml`：固定 `tree-sitter>=0.21,<0.22` + 默认 `tree-sitter-languages`
- `registry.explore_symbol` / `get_symbol_source`；MCP `codegraph_explore`
- `code_evidence.py`：reflect 预取 benchmark `context_refs` 对应真实源码
- SkillOpt 工具：`explore_symbol`、`get_symbol_source`

### Phase 9（已完成）— E2E 质量兜底

- `initial_skill.md` §2.3 会计凭证输出模板（`## 会计凭证` + 借/贷表 + 借贷校验）
- `scenario_rules.py`：generic 规则全部 duplicate 时按 benchmark case 生成场景规则
- `run all --resume-run-id`：复用 run 目录、跳过 M1–M3、M4 断点续训
- 测试：`test_resume_state.py`、`test_scenario_rules.py`

---

## 4. 验收标准

### Phase 1（核心图查询） ✅

| # | 标准 | 状态 | 验证 |
|---|------|------|------|
| 1 | `use_cache=True` 二次运行只解析变更文件 | ✅ | `test_m1_code_graph.py` |
| 2 | `GraphQueryEngine.search("JournalEntry")` 返回符号 | ✅ | `test_graph_queries.py` |
| 3 | `ContextBuilder.build(...)` 返回片段 + 符号 | ✅ | `test_graph_queries.py` |
| 4 | SkillOpt `search_symbol` 走 graph.db | ✅ | `test_code_tools.py` |
| 5 | trace callers/callees | ✅ | `test_code_tools.py` |

### Phase 2–9（扩展） ✅

| 能力 | 测试 |
|------|------|
| query_parser kind:/file: | `test_graph_queries.py` |
| MCP 6 工具 | `test_codegraph_mcp.py` |
| Spring/MyBatis 边 | `test_phase4_framework.py` |
| callback / generated / registry | `test_phase5.py`, `test_phase6.py` |
| watcher | `test_watcher.py` |
| rollout 降级凭证 | `test_rollout_helpers.py` |
| 断点续训 | `test_resume_state.py`, `test_resume_bootstrap.py` |
| 场景规则兜底 | `test_scenario_rules.py` |

### 深度检查（2026-06-06）

- **单元测试**：codegraph + skillopt 相关 **73 passed**（含 phase/resume/scenario）
- **已知缺口**：`test_m5_types.py` 曾引用已重命名的 `ProjectConfig` → 已改为 `TargetProjectConfig` 别名路径
- **文档联动**：Phase 9 场景规则见 [08-benchmark-splits-and-reflect-edit.md §10](./08-benchmark-splits-and-reflect-edit.md)
- **仍依赖 LLM 质量**：E2E 优化分数非确定性；机制（synthesis / scenario / traceability）已就绪

---

## 5. 与 SkillOpt 的关系

| 能力 | SkillOpt 消费方式 |
|------|------------------|
| graph.db | `run all` M1 产物 → M4 `code_tools.graph_db_path` |
| search_symbol | reflect / rollout optimizer 自主查规则 |
| get_code_context | 失败 case 根因分析时拉上下文 |
| trace_symbol | 验证调用链是否覆盖 benchmark check |
| LeafContext | M3 atom 抽取（不变） |

图查询与文件读取**并存**：符号级优先，文件级兜底。
