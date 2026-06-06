# M1 code_graph vs external/codegraph 差距分析

> 日期：2026-06-04

---

## 概况

| 维度 | external/codegraph | 我们的 M1 |
|------|-------------------|-----------|
| 语言 | TypeScript (Node.js) | Python |
| 代码量 | 118 个 .ts 文件 | 8 个 .py 文件（1166 行） |
| AST 引擎 | tree-sitter WASM（零编译） | 正则为主，tree-sitter 可选但需编译 |
| 部署方式 | npx 零安装 / 独立二进制 | pip install |

---

## 能力对比

### 1. 符号提取（parser）

| 能力 | CodeGraph | M1 | 差距 |
|------|----------|-----|------|
| 多语言支持 | ✅ 15+ 语言（通过 WASM grammar） | ✅ 6 语言（正则降级） | 语言覆盖不足 |
| 专用提取器 | ✅ Vue/MyBatis/Svelte/DFM/Liquid | ❌ | 缺少框架特定提取 |
| 精度 | 高（AST 级别） | 中（正则，有漏检） | 缺少真正的 AST |
| 生成代码检测 | ✅ `generated-detection.ts` | ❌ | 无法识别生成代码 |

### 2. 图谱构建（graph）

| 能力 | CodeGraph | M1 | 差距 |
|------|----------|-----|------|
| 节点/边模型 | ✅ types.ts | ✅ types.py | 基本对齐 |
| 图遍历 | ✅ `traversal.ts`（BFS/DFS/影响范围） | ✅ `traversal.py` | 基本对齐 |
| 图查询 | ✅ `queries.ts`（查询语言） | ✅ `graph_queries.py` | 子集对齐 |
| 持久化 | ✅ SQLite（db/） | ✅ `db.py` + JSON 产物 | 基本对齐 |

### 3. 引用解析（resolution）

| 能力 | CodeGraph | M1 | 差距 |
|------|----------|-----|------|
| import/export | ✅ | ✅ | 对齐 |
| 调用关系 | ✅ | ⚠️ 启发式 method call + import | 部分对齐 |
| 类型解析 | ✅ | ❌ | 不做类型系统 |

### 4. 搜索（search）

| 能力 | CodeGraph | M1 | 差距 |
|------|----------|-----|------|
| 符号搜索 | ✅ 查询语法 | ✅ FTS5 + `GraphQueryEngine.search` | 基本对齐 |
| 模糊匹配 | ✅ | ✅ `find_symbol` 模糊 | 基本对齐 |
| 影响分析 | ✅ traversal | ✅ `impact()` | 基本对齐 |

### 5. 上下文构建（context）

| 能力 | CodeGraph | M1 | 差距 |
|------|----------|-----|------|
| LLM 上下文包 | ✅ 专门模块 | ✅ leaf_context | 对齐 |
| Token 控制 | ✅ | ✅ token 预算 | 对齐 |
| 按需查询 | ✅ search-driven | ✅ `ContextBuilder` + SkillOpt 图谱工具 | 基本对齐 |

### 6. M1 独有能力

| 能力 | M1 | CodeGraph |
|------|-----|-----------|
| 入口点识别（REST/Job/CLI） | ✅ entrypoints.py | ❌ |
| 模块树聚类 | ✅ cluster.py | ❌ |
| 多仓库支持 | ✅ | ❌ |
| 自动 benchmark 生成 | ✅（by M3） | ❌ |

---

## 主要差距

### P0：缺少真正的 tree-sitter AST 解析

**现状**：正则降级，遗漏泛型、lambda、注解参数等。

**CodeGraph 方案**：WASM 编译的 tree-sitter，无需本地编译器。Python 也可通过 `tree-sitter` 包 + 预编译 `.so` 实现。

**建议**：预编译 Java/Python grammar 的 `.so` 文件放入 `vendor/` 目录，`parser.py` 自动加载。

### P0：缺少图遍历能力

**现状**：只有 `graph.nodes` 和 `graph.edges` 的原始存储，没有遍历 API。

**影响**：无法做"这个方法被谁调用"、"修改这个类影响哪些文件"的查询。M3 的 extractor 只能逐文件分析，无法跨文件追踪调用链。

**建议**：实现 `traverse_callees(node_id)` 和 `traverse_callers(node_id)`。

### P1：缺少持久化层

**现状**：每次运行重新解析全部文件，产物为 JSON 文件。

**影响**：416 文件需 30s 解析。增量更新无法实现。

**建议**：SQLite 存储节点和边，支持按文件 hash 判断是否需要重新解析。

### P1：缺少符号搜索

**现状**：无搜索能力，只能遍历全部节点。

**建议**：基于 `name_index`（已在 resolver.py 中部分实现）提供 `find_symbol(name)` 和 `find_by_kind(kind)` 接口。

### P2：语言/框架覆盖不足

**CodeGraph** 有 Vue/MyBatis/Svelte 等框架特定提取器，我们的 M1 只有通用 Java/Python regex。

---

## 建议优先级

| 优先级 | 改进 | 状态 | 对当前项目的影响 |
|--------|------|------|-----------------|
| P0 | 图遍历 API（callees/callers） | ✅ 已完成（traversal.py） | M3 可追踪调用链 |
| P0 | 符号搜索接口 | ✅ 已完成（traversal.py） | M3/M4 可按需查询 |
| P1 | SQLite 持久化 | ✅ 已完成（db.py） | 增量更新基础 |
| P2 | tree-sitter Query 深度解析 | ✅ Phase 10 | `ts_queries.py` + 14 grammar；默认 `tree-sitter-languages`；需 `tree-sitter<0.22` |
| P2 | 框架提取器 | ✅ 部分（Spring/MyBatis/Fineract） | `framework.py` + `mybatis_xml.py`；Vue/Svelte 等未覆盖 |
| P2 | SkillOpt explore_symbol + code_evidence | ✅ Phase 10 | reflect 预取 benchmark `context_refs` 真实源码 |
| P1 | 图查询 + ContextBuilder + SkillOpt 接线 | ✅ 已完成 | M4 可用 search_symbol / get_code_context / trace_symbol |
| P1 | 增量解析缓存（use_cache） | ✅ 已完成 | `run all` 默认启用 |
| P1 | qualified_name + manifest.json | ✅ 已完成 | 对齐设计文档 01 |
| P1 | kind:/file: 查询语法 | ✅ 已完成 | query_parser.py |
| P2 | MCP 工具暴露 | ✅ 已完成 | codegraph-mcp + **6 工具** |
| P2 | Spring/MyBatis 框架边 | ✅ 已完成 | framework.py + resolver inheritance |
| P2 | M3 evidence_index | ✅ 已完成 | EvidenceBuilder + edge_path |
| P3 | 文件监听增量 | ✅ 已完成 | watcher + code-graph-watch CLI |
| P2 | 生成代码检测 | ✅ 已完成 | generated_detection.py |
| P2 | 接口派发合成 | ✅ 已完成 | callback_synthesis.py |
| P2 | 多仓库图谱 | ✅ 已完成 | GraphRegistry + graph_sources |
| P2 | MCP impact 工具 | ✅ 已完成 | codegraph_impact |
| P2 | MyBatis XML 解析 | ✅ 已完成 | mybatis_xml.py |
| P2 | JS 回调合成 | ✅ 已完成 | js_callbacks.py |
| P2 | MCP daemon | ✅ 已完成 | codegraph-daemon |
| P2 | SkillOpt impact/status 工具 | ✅ 已完成 | impact_symbol / graph_status |
