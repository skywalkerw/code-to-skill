# code-to-skill 代码实现计划

> 对照基准：`docs/design/00-06` 设计文档
> 技术选型：Python 3.10+，依赖见 `requirements.txt`

---

## 执行顺序

```
M5（基础设施）→ M6（CLI）→ M1+M2（并行）→ M3 → M4
```

| 阶段 | 模块 | 预估工期 | 产出 |
|------|------|------|------|
| 1 | M5 模型/Agent 交互 | 2-3 天 | `src/model_gateway/` |
| 2 | M6 CLI 编排 | 2 天 | `src/cli/` |
| 3 | M1 代码图谱 | 3 天 | `src/code_graph/` |
| 4 | M2 文档规范化 | 2 天 | `src/document_normalizer/` |
| 5 | M3 SkillAtom 抽取 | 3 天 | `src/atom_extractor/` |
| 6 | M4 SkillOpt 优化 | 4 天 | `src/skillopt_loop/` |

---

## 阶段 1：M5 基础设施

### 1.1 项目骨架
- `pyproject.toml` + `requirements.txt`
- `src/__init__.py`
- `src/model_gateway/__init__.py`

### 1.2 数据结构 (`types.py`)
- `InteractionRequest`, `InteractionResponse`
- `ModelResponse`, `AgentResponse`
- `HealthStatus`

### 1.3 抽象接口 (`backends/__init__.py`)
- `InteractionBackend(ABC)`：`capabilities()`, `invoke()`, `healthcheck()`

### 1.4 OpenAI 兼容后端 (`backends/openai_compatible.py`)
- 适配百炼 / vLLM / Ollama

### 1.5 路由 (`router.py`)
- role/stage → backend 映射
- fallback 链 + tenacity 重试

### 1.6 结构化输出降级 (`structured_output.py`)
- L3 (native) → L2 (tool_calling) → L1 (prompt+parse)

### 1.7 追踪 (`tracer.py`)
- trace.json + token_usage.jsonl + cost_usage.jsonl

### 1.8 Mock 后端 (`backends/mock.py`)
- 从 fixture 回放，用于测试

---

## 阶段 2：M6 CLI

### 2.1 数据结构 (`types.py`)
- `RunManifest`, `RunState`, `Event`, `Approval`

### 2.2 命令
- `init`, `config validate`, `run all`, `run <module>`
- `status`, `resume`, `inspect`, `approve`, `eval`, `publish`

---

## 阶段 3：M1 代码图谱

### 3.1 文件扫描 (`scanner.py`)
### 3.2 tree-sitter 解析 (`parser.py`)
### 3.3 引用解析 (`resolver.py`)
### 3.4 入口点识别 (`entrypoints.py`)
### 3.5 模块树聚类 (`cluster.py`)
### 3.6 叶子上下文 (`leaf_context.py`)

---

## 阶段 4：M2 文档规范化

### 4.1 KnowledgeSource 接口 + LocalFileKnowledgeSource (`knowledge_source.py`)
### 4.2 Markdown/HTML/DOCX/PDF 解析 (`parsers/`)
### 4.3 结构恢复 (`structure.py`)
### 4.4 清洗脱敏 (`cleaner.py`)
### 4.5 Chunk 切分 (`chunker.py`)
### 4.6 内容类型识别 (`classifier.py`)

---

## 阶段 5：M3 SkillAtom 抽取

### 5.1 从代码抽取 (`extractor/from_code.py`)
### 5.2 从文档抽取 (`extractor/from_docs.py`)
### 5.3 证据对齐 (`aligner.py`)
### 5.4 分层评分 (`scorer.py`)
### 5.5 合并聚类 (`merger.py`)
### 5.6 验证断言 (`checks.py`)

---

## 阶段 6：M4 SkillOpt 优化

### 6.1 Adapter (`adapter.py`)
### 6.2 Scorer (`scorer.py`)
### 6.3 Rollout (`rollout.py`)
### 6.4 Reflect (`reflect.py`)
### 6.5 Aggregate (`aggregate.py`)
### 6.6 Select (`select.py`)
### 6.7 Update (`updater.py`)
### 6.8 Evaluate + Gate (`evaluator.py`)
### 6.9 缓存 (`cache.py`)
### 6.10 断点续训 (`state_manager.py`)

---

## 目录结构

```
code-to-skill/
├── pyproject.toml
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── model_gateway/          # M5
│   ├── cli/                    # M6
│   ├── code_graph/             # M1
│   ├── document_normalizer/    # M2
│   ├── atom_extractor/         # M3
│   └── skillopt_loop/          # M4
├── tests/
├── vendor/                     # 离线依赖
└── docs/
```
