# code-to-skill API 参考

> 6 模块的公开函数签名与说明。完整代码见 `src/code_to_skill/`。

---

## M5：模型与智能体交互（`model_gateway/`）

### Backend 工厂

```python
from code_to_skill.model_gateway.llm_backend import create_llm_backend, is_llm_available

# 检查 LLM 是否可用（DEEPSEEK_API_KEY 已设置）
is_llm_available(backend_id="deepseek") -> bool

# 创建 backend 实例（有 API key → OpenAI 兼容，无 → MockBackend）
create_llm_backend(backend_id="deepseek") -> InteractionBackend
```

### 类型

```python
from code_to_skill.model_gateway.types import InteractionRequest, InteractionResponse, ModelResponse
# InteractionRequest(role, stage, messages, response_format, max_output_tokens, temperature)
# → InteractionResponse(content, parsed, status, usage, latency_ms)
```

### Backend 接口

```python
from code_to_skill.model_gateway.backends import InteractionBackend
# backend.capabilities() -> dict   # chat, json_schema, tool_calling, structured_output_level
# backend.invoke(request) -> InteractionResponse
# backend.healthcheck() -> HealthStatus
```

### 路由

```python
from code_to_skill.model_gateway.router import Router
# Router(route_config, backends).invoke(request) -> InteractionResponse
```

### 结构化输出

```python
from code_to_skill.model_gateway.structured_output import invoke_with_structured_output
# L3(原生)→L2(tool_calling)→L1(prompt+parse) 自动降级
invoke_with_structured_output(backend, request, target_schema=None) -> InteractionResponse
```

---

## M6：CLI 编排（`cli/`）

### 配置加载

```python
from code_to_skill.cli.config_loader import load_project_config
cfg = load_project_config("project.yaml")  # → ProjectConfig
cfg.validate_sources_exist()                # → list[str] warnings
```

### 命令行

```bash
skill-lab init --workspace ./proj --domain fintech
skill-lab config --config-path project.yaml [--dry-run-level config-only]
skill-lab run all --config-path project.yaml
skill-lab run code-graph --repo <path>
skill-lab run normalize-docs --config-path project.yaml
skill-lab run extract-atoms --from <sources_dir>
skill-lab run optimize-skill --benchmark <path>
skill-lab status [run_id]
skill-lab inspect <artifact.json|.jsonl|.md>
skill-lab eval <run_id> --split test
skill-lab resume <run_id>
```

---

## M1：代码图谱（`code_graph/`）

### 主流水线

```python
from code_to_skill.code_graph import run_code_graph_pipeline

results = run_code_graph_pipeline(
    repo_root="test-data/fineract-develop",
    include=["fineract-provider/.../**"],
    exclude=["**/test/**"],
    max_leaf_tokens=8000,
    max_module_depth=3,
    output_root=None,  # 不写文件时为 None
)
# → {"inventory": FileInventory, "graph": CodeGraph, "entrypoints": list,
#    "module_tree": ModuleTree, "leaf_contexts": list, "errors": list}
```

### 子模块

```python
from code_to_skill.code_graph.scanner import scan_repo
inv = scan_repo("repo/root", include=["src/**"], exclude=["**/test/**"])

from code_to_skill.code_graph.parser import parse_files
graph, errors = parse_files(["a.java", "b.java"], "repo/root")

from code_to_skill.code_graph.resolver import resolve_references
unresolved = resolve_references(graph, "repo/root")

from code_to_skill.code_graph.entrypoints import find_entrypoints
eps = find_entrypoints(graph, "repo/root")

from code_to_skill.code_graph.cluster import build_module_tree
tree = build_module_tree(graph, "repo/root")

from code_to_skill.code_graph.leaf_context import generate_leaf_contexts
contexts = generate_leaf_contexts(graph, tree, "repo/root", max_leaf_tokens=8000)
```

---

## M2：文档规范化（`document_normalizer/`）

### 主流水线

```python
from code_to_skill.document_normalizer import normalize_document

result = normalize_document(
    source_uri="kb/fineract/README.md",
    source_id="fineract-readme",
    source_provider="local_file",
    output_root=None,
    max_chunk_tokens=2000,
)
# → {"manifest": DocumentManifest, "index": DocumentIndex,
#    "chunks": list[DocumentChunk], "tables": list}
```

### KnowledgeSource

```python
from code_to_skill.document_normalizer.knowledge_source import (
    LocalFileKnowledgeSource, get_provider, register_provider,
)
ks = LocalFileKnowledgeSource(workspace_root=".")
raw = ks.fetch_raw_content("path/to/doc.md")
```

---

## M3：SkillAtom 抽取（`atom_extractor/`）

### 主流水线

```python
from code_to_skill.atom_extractor import run_atom_extraction

result = run_atom_extraction(
    leaf_contexts=[...],       # M1 产出的 leaf context dicts
    document_chunks=[...],     # M2 产出的 chunk dicts
    output_root=None,
)
# → {"raw_atoms": list[RawAtom], "merged_atoms": list[SkillAtom],
#    "benchmark_seeds": list, "clusters": dict}
```

### LLM 抽取

```python
from code_to_skill.atom_extractor.extractor.llm_extractor import (
    extract_from_code_llm, extract_from_docs_llm,
)
# DEEPSEEK_API_KEY 设置时自动启用，否则返回空（规则模式补全）
atoms = extract_from_code_llm(leaf_contexts)
atoms = extract_from_docs_llm(chunks)
```

---

## M4：SkillOpt 优化（`skillopt_loop/`）

### 主训练循环

```python
from code_to_skill.skillopt_loop import run_skillopt_loop

result = run_skillopt_loop(
    initial_skill="# Initial Skill\n...",
    benchmark_items=[{"id": "t1", "task_template": "...", "expected_checks": ["..."]}],
    output_dir="outputs/",
    num_epochs=3,
    batch_size=20,
    edit_budget=3,
    selection_split_ratio=0.25,
    use_llm_rollout=False,  # True → 真实LLM回答benchmark
)
# → {"best_skill": str, "history": list, "best_score": float}
```

### Scorer

```python
from code_to_skill.skillopt_loop import score_rollout_result
scores = score_rollout_result(predicted_answer, ["check1", "check2"])
# → {"hard": 0|1, "soft": 0.0-1.0, "passed": int, "total": int}

from code_to_skill.skillopt_loop import apply_edits, compute_semantic_hash
new_skill = apply_edits(skill_content, edits)
h = compute_semantic_hash(skill_content)
```

### LLM Reflect / Select

```python
from code_to_skill.skillopt_loop.llm_components import reflect_llm, select_edits_llm
patches = reflect_llm(rollout_results, current_skill)
ranked = select_edits_llm(edits, current_skill, budget=3)
```
