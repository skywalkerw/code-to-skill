# code-to-skill

从知识库和代码仓库中提取、生成并持续优化 **Agent Skill** 的离线流水线。

> Agent Skill 不是知识库摘要，而是告诉 Agent 在什么条件下执行什么流程、调用什么工具、遵守什么约束的可复用能力包。

## 快速开始

```bash
# 安装
pip install -e .
# 可选: OCR 支持
pip install -e ".[ocr]"

# 初始化项目
skill-lab init --workspace ./my-project --domain fintech

# 编辑 project.yaml 配置数据源后，校验
skill-lab config --config-path project.yaml

# 运行全流程
skill-lab run all --config-path project.yaml
```

## 流水线

```
代码仓库 ─→ M1 代码图谱 ─→ M3 SkillAtom ─→ M4 SkillOpt ─→ SKILL.md
知识文档 ─→ M2 文档规范化 ─┘                              (best_skill.md)
  ↑                                                      
M5 模型/Agent 交互层 (基础设施)
M6 CLI 编排层 (贯穿)
```

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| M1 | 代码图谱与模块树 | Git 仓库/本地目录 | `graph.json`、`module_tree.json`、`leaf_contexts/` |
| M2 | 知识库文档规范化 | Markdown/PDF/HTML/DOCX | `chunks.jsonl`、`tables.jsonl` |
| M3 | SkillAtom 抽取 | M1 + M2 产物 | `merged_atoms.jsonl`、`benchmark_seeds.jsonl` |
| M4 | SkillOpt 优化循环 | initial_skill.md + benchmark | `best_skill.md`、`history.json` |
| M5 | 模型与 Agent 交互 | InteractionRequest | ModelResponse / AgentResponse |
| M6 | CLI 人机交互 | 命令行 / project.yaml | `run_manifest.json`、`run_state.json` |

## 产出示例

对 Apache Fineract（Java 金融核心系统，416 文件）运行后生成的 `SKILL.md`：

```markdown
# Fineract Agent Skill
> 来源: Apache Fineract (develop)，416 文件，2197 节点

## 核心规则
### ⚡ 必须遵守的约束
**规则**: 费用/罚金计算 → 确认费用类型、计算基数和上限
- ✅ Do: 修改费用计算前确认费用类型、计算基数和上限
- ❌ Do NOT: 不得新增未授权的费用类型或修改罚金上限
- 📁 来源: 201 个文件 (conf=0.90)

### 📋 操作流程
**规则**: 利率/计提计算 → 确认计息方式和精度
- ✅ Do: 确认计息方式（declining balance/flat/等额本息）和精度要求
- ❌ Do NOT: 不得随意修改利率精度而不更新相关摊销逻辑
- 📁 来源: 298 个文件 (conf=0.90)
```

## 技术栈

- **语言**: Python 3.10+
- **核心依赖**: pydantic, pyyaml, tree-sitter, openai, tiktoken, rich, click
- **文档解析**: pdfplumber, python-docx, beautifulsoup4, markdown-it-py
- **离线部署**: `pip download -d vendor/` → `pip install --no-index --find-links=vendor/`

## 项目结构

```
code-to-skill/
├── docs/
│   ├── design/           # 6 模块详细设计文档
│   └── implementation-plan.md
├── src/code_to_skill/    # 主代码
│   ├── model_gateway/    # M5 模型交互层
│   ├── cli/              # M6 CLI 编排
│   ├── code_graph/       # M1 代码图谱
│   ├── document_normalizer/ # M2 文档规范化
│   ├── atom_extractor/   # M3 SkillAtom 抽取
│   └── skillopt_loop/    # M4 SkillOpt 优化
├── tests/                # 53 个测试
├── project.yaml          # 项目配置模板 (Fineract 示例)
├── pyproject.toml
└── requirements.txt
```

## CLI 命令

```bash
skill-lab init                  # 初始化项目
skill-lab config                # 校验配置
skill-lab run all               # 运行全流程
skill-lab run code-graph        # 仅代码图谱
skill-lab run normalize-docs    # 仅文档规范化
skill-lab run extract-atoms     # 仅 Atom 抽取
skill-lab run optimize-skill    # 仅 Skill 优化
skill-lab status <run_id>       # 查看状态
skill-lab inspect <artifact>    # 查看产物
skill-lab eval <skill>          # 评测 Skill
skill-lab publish <run_id>      # 发布 Skill
skill-lab resume <run_id>       # 恢复运行
```

## 配置

`project.yaml` 完整示例见 `.test-data/project.yaml`。核心配置项：

```yaml
project:
  name: my-project
  domain: fintech

sources:
  repos:
    - id: my-repo
      path: /path/to/repo
      ref: main
      include: ["src/**"]
      exclude: ["**/test/**"]
  docs:
    - id: my-docs
      path: docs/readme.md
      provider: local_file

skillopt:
  num_epochs: 3
  batch_size: 20
  edit_budget: 3
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[lxml]"
pip install pytest

# 运行测试
python -m pytest tests/ -v

# 全流程端到端测试 (需要 Fineract 在 external/ 下)
python -c "
from code_to_skill.code_graph import run_code_graph_pipeline
# ... 见 tests/ 和 runs/ 目录
"
```

## License

MIT
