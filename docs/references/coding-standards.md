# code-to-skill 代码规范

> 项目统一编码与目录结构约定。新贡献者必读。

---

## 一、目录结构

```
src/code_to_skill/                    # 唯一 Python 包
├── __init__.py                       # 包描述
│
├── model_gateway/                    # M5：基础设施层（最先加载，被所有模块依赖）
│   ├── __init__.py
│   ├── types.py                      # InteractionRequest/Response schema
│   ├── llm_backend.py                # 环境变量→backend 工厂
│   ├── router.py                     # role/stage 路由 + fallback
│   ├── tracer.py                     # trace/token/cost 记录
│   ├── structured_output.py          # L3→L1 JSON Schema 降级
│   └── backends/                     # backend 实现（一个文件一个类）
│       ├── __init__.py               # InteractionBackend 抽象接口
│       ├── openai_compatible.py      # OpenAI 兼容后端
│       └── mock.py                   # 测试桩
│
├── cli/                              # M6：编排层
│   ├── __init__.py
│   ├── types.py                      # RunManifest/RunState/Event
│   ├── config_loader.py              # project.yaml → pydantic
│   └── main.py                       # 全部 click 命令
│
├── code_graph/                       # M1：代码图谱
│   ├── __init__.py                   # 主流水线 run_code_graph_pipeline()
│   ├── types.py                      # CodeGraph/GraphNode/GraphEdge...
│   ├── scanner.py                    # 文件扫描 + glob 过滤
│   ├── parser.py                     # tree-sitter(可选) + 正则降级
│   ├── resolver.py                   # import 引用解析
│   ├── entrypoints.py                # REST/CLI/Job 入口识别
│   ├── cluster.py                    # 模块树聚类 + leaf 细化
│   └── leaf_context.py               # 叶子上下文 + token 控制
│
├── document_normalizer/              # M2：文档规范化
│   ├── __init__.py                   # 主流水线 normalize_document()
│   ├── types.py                      # DocumentManifest/Chunk/Table...
│   ├── knowledge_source.py           # KnowledgeSource 接口 + LocalFile
│   ├── structure.py                  # heading tree 构建
│   ├── cleaner.py                    # 清洗 + 脱敏
│   ├── chunker.py                    # 语义 chunk 切分
│   └── parsers/                      # 格式解析器
│       └── __init__.py               # Markdown/HTML/PDF/DOCX/Text
│
├── atom_extractor/                   # M3：SkillAtom 抽取
│   ├── __init__.py                   # 主流水线 run_atom_extraction()
│   ├── types.py                      # SkillAtom/RawAtom/SourceRef...
│   ├── scorer.py                     # 三层分层制 confidence
│   ├── aligner.py                    # 跨来源证据对齐
│   ├── merger.py                     # 合并去重 + benchmark seeds
│   └── extractor/                    # 抽取器
│       ├── __init__.py               # 规则启发式抽取（5种金融模式）
│       └── llm_extractor.py          # LLM 抽取（自动降级）
│
└── skillopt_loop/                    # M4：SkillOpt 优化
    ├── __init__.py                   # 主训练循环 run_skillopt_loop()
    ├── types.py                      # BenchmarkItem/RolloutResult/EditOp...
    └── llm_components.py             # LLM Reflect + Select（自动降级）
```

---

## 二、模块规则

### 2.1 模块编号与依赖

```
M5（基础设施） ← 被所有模块依赖
M6（CLI 编排） ← 调用 M1-M4
M1（代码图谱） ← 产出给 M3
M2（文档规范） ← 产出给 M3
M3（Atom 抽取）← 消费 M1+M2，产出给 M4
M4（SkillOpt） ← 消费 M3
```

**依赖方向**：
- M5 → 被 M1/M2/M3/M4/M6 依赖 ✅
- M6 → 依赖 M1/M2/M3/M4/M5 ✅
- M1 → 依赖 M5 ✅
- M3 → 依赖 M1/M2/M5 ✅
- M4 → 依赖 M3/M5 ✅
- 禁止：M1→M2、M3→M4、M4→M2 ❌

### 2.2 模块内文件分类

| 后缀 | 用途 | 示例 |
|------|------|------|
| `types.py` | pydantic 数据模型，不含逻辑 | `SkillAtom`, `CodeGraph` |
| `__init__.py` | 模块入口 + 主流水线函数 | `run_code_graph_pipeline()` |
| `scanner.py` | 输入处理 | 文件扫描 |
| `parser.py` | 格式解析 | AST/正则 |
| `resolver.py` | 关系解析 | import 引用 |
| `scorer.py` | 评分逻辑 | confidence 计算 |
| `merger.py` | 合并聚类 | 去重 |
| `aligner.py` | 跨来源对齐 | 证据匹配 |
| `cleaner.py` | 清洗处理 | 文本清洗 |
| `chunker.py` | 切分处理 | chunk 切分 |

### 2.3 跨模块依赖方式

**允许的依赖**：
- `from code_to_skill.model_gateway.types import InteractionRequest` ✅
- `from code_to_skill.atom_extractor.types import SkillAtom` ✅
- `from code_to_skill.llm_backend import create_llm_backend` → 已废弃，应用 `model_gateway.llm_backend`

**禁止的依赖**：
- `from .xxx import ...` 在子目录中使用 `..` 相对导入 ❌（统一使用绝对导入 `from code_to_skill.xxx`）
- `from code_to_skill.cli.main import ...` ❌（CLI 只被用户调用，不被其他模块调用）
- 循环依赖 ❌

---

## 三、命名规范

### 3.1 文件名

- 全小写 + 下划线：`leaf_context.py`、`knowledge_source.py`
- 类型文件统称 `types.py`
- 每个模块一个 `__init__.py`，包含 `run_*_pipeline()` 主函数
- 禁止：驼峰 `KnowledgeSource.py`、连字符 `knowledge-source.py`

### 3.2 类名

- PascalCase：`SkillAtom`、`InteractionBackend`、`CodeGraph`
- pydantic model 继承 `BaseModel`
- 抽象接口用 `ABC` 后缀（可选）：`InteractionBackend(ABC)`

### 3.3 函数名

- snake_case：`run_code_graph_pipeline()`、`extract_from_code()`
- 公开 API：`run_*` 前缀（流水线入口）
- 内部函数：`_` 前缀（`_build_node()`、`_extract_imports()`）

### 3.4 常量

- UPPER_SNAKE：`_PATTERNS`、`_EXT_LANG`、`_REDACT_PATTERNS`
- 模块级私有常量加 `_` 前缀

---

## 四、Schema 规范

### 4.1 所有产物必须含 `schema_version`

```python
class CodeGraph(BaseModel):
    schema_version: str = "1.0"
```

### 4.2 Pydantic 优先

- 输入/输出一律用 pydantic `BaseModel`
- `dict` 只用于 LLM prompt 参数和临时中间值
- JSON/JSONL 序列化用 `model_dump()` / `model_dump_json()`

---

## 五、编码风格

### 5.1 注释

- 文件头：`"""模块 X：一句话职责。"""`
- 函数：docstring 描述 Args/Returns
- 设计文档引用：`# 见设计文档 §4.2.1`

### 5.2 类型

- 所有函数签名使用 type hints
- `from __future__ import annotations` 在文件头

### 5.3 日志

```python
import logging
logger = logging.getLogger(__name__)
logger.info("...")
logger.warning("...")
```

- 使用 `logging`，不要 `print`（CLI 输出除外）
- M1/M2/M3/M4 的流水线入口可用 `print` 报告进度

---

## 六、LLM 集成规范

### 6.1 Backend 配置

**文件**：`model_gateway/llm_backend.py`

```python
_ENV_MAP = {
    "backend-id": {
        "base_url_env": "ENV_VAR_NAME",
        "api_key_env": "ENV_VAR_NAME",
        "model": "model-name",
        "default_base_url": "https://...",
    },
}
```

### 6.2 降级策略

所有 LLM 调用必须遵守降级范式：

```python
if not is_llm_available():
    logger.info("LLM not available, falling back to rule-based")
    return rule_based_fallback()

backend = create_llm_backend()
try:
    response = backend.invoke(request)
except Exception as e:
    logger.warning("LLM call failed: %s", e)
    return rule_based_fallback()
```

### 6.3 Prompt 管理

- Prompt 模板放在使用它的模块文件中，作为模块级常量
- 命名：`_TASK_PROMPT`（私有常量）
- 格式：f-string 或 `str.format()`

---

## 七、测试规范

### 7.1 文件组织

```
tests/
├── test_m1_code_graph.py     # M1 测试
├── test_m2_documents.py      # M2 测试
├── test_m3_m4.py             # M3+M4 集成测试
└── test_m5_types.py          # M5+M6 测试
```

### 7.2 测试类命名

```python
class Test<Module><Feature>:
    def test_<scenario>(self):
```

### 7.3 外部依赖

```python
FINERACT_ROOT = "test-data/fineract-develop"
HAS_FINERACT = Path(FINERACT_ROOT).exists()

@pytest.mark.skipif(not HAS_FINERACT, reason="Fineract not available")
def test_run_on_fineract(self):
```

---

## 八、Git 规范

### 8.1 .gitignore

```
external/       # 第三方仓库（测试用，不提交）
vendor/         # 离线依赖缓存
runs/           # 运行产物
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
.DS_Store
*.log
```

### 8.2 Commit 格式

```
<模块>: <简短描述>

- 具体改动1
- 具体改动2
```

---

## 九、反模式（禁止事项）

| 反模式 | 说明 |
|--------|------|
| ❌ `..types` 相对导入 | 统一 `from code_to_skill.xxx.types` |
| ❌ 文件放在 `src/code_to_skill/` 根 | 必须归入某个模块目录 |
| ❌ `print` 在非 CLI 模块 | 用 `logging` |
| ❌ 硬编码 API key | 用环境变量 + `llm_backend.py` |
| ❌ pydantic model 无 `schema_version` | 所有模型必须有 |
| ❌ 跳过降级直接调用 LLM | 必须 `is_llm_available()` 检查 |
| ❌ 循环依赖 | M5 只被依赖，不依赖其他模块 |
