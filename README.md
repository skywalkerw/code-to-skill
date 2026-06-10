# code-to-skill

从知识库和代码仓库中提取、生成并持续优化 **Agent Skill** 的离线流水线。

> Agent Skill 不是知识库摘要，而是告诉 Agent 在什么条件下执行什么流程、调用什么工具、遵守什么约束的可复用能力包。

## 快速开始

```bash
# 安装（建议在仓库根目录）
pip install -e .
# 可选: OCR
pip install -e ".[ocr]"

# API Key：在项目根目录放置 .env（启动时自动加载）
#   DEEPSEEK_API_KEY=...
#   DEEPSEEK_BASE_URL=https://api.deepseek.com

# 复制并编辑配置
cp config.template.yaml config.yaml

# 校验配置
skill-lab config --config-path config.yaml

# 完整流水线 M1→M4（fineract-fast 示例见 config.yaml）
skill-lab run all --config-path config.yaml --with-atoms
```

若已配置 `initial_skill` + benchmark，默认会**跳过 M2/M3**；要跑齐四段模块请加 **`--with-atoms`**。

开发中若 CLI 未重装，可在命令前加 `PYTHONPATH=src`。

## 流水线

```
代码仓库 ─→ M1 代码图谱 ─→ M3 SkillAtom ─→ M4 SkillOpt ─→ SKILL.md
知识文档 ─→ M2 文档规范化 ─┘         │              (best_skill.md)
  ↑                                  │
M5 模型/Agent 交互层 (基础设施)       └─ Design 08 自进化（可选）
M6 CLI 编排层 (贯穿)
```

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| M1 | 代码图谱与模块树 | Git 仓库/本地目录 | `graph.db`、`graph.json`、`leaf_contexts/` |
| M2 | 知识库文档规范化 | Markdown/PDF/HTML/DOCX | `chunks.jsonl`、`tables.jsonl` |
| M3 | SkillAtom 抽取 | M1 + M2 产物 | `merged_atoms.jsonl`、`artifact_quality.json`、`benchmark_seeds.jsonl` |
| M4 | SkillOpt 优化循环 | initial_skill + benchmark | `best_skill.md`、`history.json`、`trace_pool/`（可选） |
| M5 | 模型与 Agent 交互 | InteractionRequest | `traces/`、ModelResponse |
| M6 | CLI 人机交互 | 命令行 / config.yaml | `run_manifest.json`、`logs/run.log` |

设计文档见 [`docs/design/`](docs/design/)（含 [07 流水线整合](docs/design/07-pipeline-integration-optimization.md)、[08 Skill 自进化](docs/design/08-skill-self-evolution-optimization.md)）。

## Fineract 示例（fast benchmark）

仓库内 `config.yaml` 已配置 Apache Fineract 与 **`fineract-fast`**（train/selection/test = 5/6/3，完整集备份在 `fineract-full`）。

```bash
# 环境变量
export SKILL_LAB_CONFIG_PATH=$PWD/config.yaml   # 可选

# 完整 M1→M4（推荐首次冒烟）
skill-lab run all --config-path config.yaml --with-atoms

# 仅 M4 重训（需 run 目录内已有 graph.db）
skill-lab run optimize-skill \
  --config-path config.yaml \
  -o test-data/runs/<run_id>/optimization

# M4 + 轨迹归纳（Design 08，不改严格 gate）
skill-lab run optimize-skill --trace-merge ...

# M4 + 完整自进化（严格 gate、归因、hygiene）
skill-lab run optimize-skill --self-evolve ...
```

产物目录：`settings.output.root/<run_id>/`（默认 `test-data/runs/`）。

## CLI 命令

```bash
skill-lab init                  # 初始化项目与 config.yaml 模板
skill-lab doctor                # 环境诊断
skill-lab config                # 校验 config.yaml（L1）
skill-lab run all               # 完整流水线 M1→M4
skill-lab run code-graph        # 仅 M1
skill-lab run normalize-docs    # 仅 M2
skill-lab run extract-atoms     # 仅 M3
skill-lab run optimize-skill    # 仅 M4（支持 --resume、--trace-merge、--self-evolve）
skill-lab run skill-hygiene     # 离线 hygiene + gate
skill-lab run bootstrap-benchmark  # M3 种子 → benchmark train
skill-lab status [run_id]       # 运行状态
skill-lab inspect run <run_id>  # run 摘要（--trace-pool、--validate-self-evolution 等）
skill-lab inspect file <path>   # 单文件产物预览
skill-lab eval <run_id>         # 独立评测 best_skill
skill-lab publish <run_id>      # 发布 SKILL.md（--strip-rule-ids 可选）
skill-lab resume <run_id>       # M4 断点续训
skill-lab codegraph             # 图谱查询（MCP / CLI）
```

## 配置

主配置文件为 **`config.yaml`**（模板：`config.template.yaml`）。两段结构：

```yaml
settings:
  output:
    root: test-data/runs/
  skillopt:
    num_epochs: 2
    batch_size: 5
    rollout_workers: 4
    use_llm_rollout: true
  self_evolution:          # Design 08，默认关闭
    enabled: false
  model_provider:
    backends: { ... }
    routes:
      target: { primary: deepseek-flash }    # rollout
      optimizer: { primary: deepseek }       # reflect/select

project:
  name: fineract-finance-skill
  initial_skill: test-data/initial_skill.md
  benchmark: test-data/benchmarks/fineract-fast
  sources:
    repos: [ ... ]
    docs: [ ... ]
```

## 准备指南

### 1. 代码仓库

`project.sources.repos` 指向本地 clone（示例：`test-data/sources/repos/fineract`）。

### 2. 知识文档

置于 `test-data/sources/docs/<project>/` 并在 `config.yaml` 的 `sources.docs` 注册。

### 3. 初始 Skill

`test-data/initial_skill.md`：Workflow / Constraint / Failure Mode / Checklist。

### 4. Benchmark

`benchmarks/<name>/{train,selection,test}/items.json`，每条含 `id`、`question`、`expected_checks`、`context_refs`（可选）。

快速子集生成：`python test-data/benchmarks/build_fast_subset.py`（产出 `fineract-fast`）。

### 5. API 与 `.env`

在项目根目录配置 `.env`；`model_provider` 通过 `${VAR}` 引用。勿将 key 提交到 git。

## 技术栈

- **语言**: Python 3.10+
- **核心依赖**: pydantic, pyyaml, tree-sitter, openai, tiktoken, rich, click
- **文档解析**: pdfplumber, python-docx, beautifulsoup4, markdown-it-py
- **离线部署**: `pip download -d vendor/` → `pip install --no-index --find-links=vendor/`

## 项目结构

```
code-to-skill/
├── docs/design/          # 00–08 模块与整合设计
├── docs/references/      # 论文 PDF
├── config.template.yaml
├── config.yaml           # 本地配置（Fineract 示例）
├── src/code_to_skill/
│   ├── cli/              # M6
│   ├── code_graph/       # M1
│   ├── document_normalizer/
│   ├── atom_extractor/   # M3
│   ├── skillopt_loop/    # M4 + Design 08
│   └── model_provider/   # M5
├── test-data/            # 示例数据与 runs（通常 gitignore）
└── tests/
```

## 开发

```bash
pip install -e ".[lxml]"
python -m pytest tests/ -q

# 使用源码 CLI
PYTHONPATH=src skill-lab run all --config-path config.yaml --dry-run
```

## License

MIT
