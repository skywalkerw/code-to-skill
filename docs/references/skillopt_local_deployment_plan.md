# SkillOpt 本地化部署方案

## 从知识库和代码提取并优化 Agent Skill 的完整实施方案

> 基于论文 [SkillOpt: Executive Strategy for Self-Evolving Agent Skills](https://arxiv.org/abs/2605.23904) (arXiv: 2605.23904, Microsoft, May 2026)
> 
> 开源仓库: https://github.com/microsoft/SkillOpt (MIT License)

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [SkillOpt 方法论](#2-skillopt-方法论)
3. [环境与架构](#3-环境与架构)
4. [实施步骤](#4-实施步骤)
5. [自定义 Benchmark 开发](#5-自定义-benchmark-开发)
6. [评测数据集构建](#6-评测数据集构建)
7. [训练与调优](#7-训练与调优)
8. [部署与集成](#8-部署与集成)
9. [成本估算](#9-成本估算)
10. [FAQ](#10-faq)

---

## 1. 背景与目标

### 1.1 问题定义

当前 Agent Skill 的创建方式有三种，但各有缺陷：

| 方式 | 问题 |
|------|------|
| **手写** | 依赖专家经验，无法随反馈持续改进 |
| **一次性 LLM 生成** | 不能从执行失败中学习 |
| **松散自修订** | 无受控优化过程，可能越改越差 |

SkillOpt 改变了这个范式：**把 Skill 文档当作可训练的外部状态，像训练神经网络一样训练它**。

### 1.2 目标

从飞书知识库文档和代码仓库中提取领域知识，构建初始 Skill，通过 SkillOpt 的优化循环产出可部署的 `best_skill.md` 文件（300-2000 token），实现：

- 知识库问答准确率提升
- 代码理解/审查/生成能力增强
- Skill 跨模型、跨工具链可迁移
- 部署时零额外推理开销

### 1.3 论文核心结果

| 指标 | 数值 |
|------|------|
| 评测覆盖 | 6 benchmarks × 7 models × 3 harnesses |
| 全胜率 | **52/52 cells** 最优或并列最优 |
| GPT-5.5 direct chat 平均提升 | **+23.5 分** |
| GPT-5.5 Codex harness 提升 | **+24.8 分** |
| GPT-5.5 Claude Code 提升 | **+19.1 分** |
| 小模型 (Qwen3.5-4B) 平均提升 | **+19.2 分** |
| 部署 skill 体积 | **300-2000 tokens** |

---

## 2. SkillOpt 方法论

### 2.1 核心思想

> **把 Skill 文档视为冻结 Agent 的外部可训练状态，用一个独立的 Optimizer 模型做文本空间的受控优化。**

对应的深度学习类比：

| 深度学习概念 | SkillOpt 文本空间类比 |
|-------------|---------------------|
| 模型权重 | Skill 文档 (Skill document) |
| 梯度 | 轨迹分析得出的编辑方向 |
| 学习率 | 每步允许的编辑条数预算 (edit budget L) |
| Batch size | 每次 rollout 的任务数量 (B) |
| Mini-batch | 每次分析的轨迹数量 (Bm) |
| Validation set | Held-out selection split |
| Momentum | Epoch-wise slow/meta update |
| 负梯度 | Rejected-edit buffer |

### 2.2 优化循环

```
┌─────────────────────────────────────────────────────┐
│                    训练循环 (Offline)                  │
│                                                      │
│  ┌──────────┐    ┌──────────────┐    ┌────────────┐ │
│  │ 当前 Skill │───▶│ Target Model │───▶│  Rollout   │ │
│  │  S_t      │    │ (frozen)     │    │  Traces    │ │
│  └──────────┘    └──────────────┘    │  + Scores  │ │
│       ▲                              └─────┬──────┘ │
│       │                                    │        │
│       │        ┌──────────────────┐        │        │
│       │        │  Optimizer Model  │◀───────┘        │
│       │        │  (更强的前沿模型)   │                 │
│       │        └────────┬─────────┘                 │
│       │                 │                           │
│       │        ┌────────▼─────────┐                 │
│       │        │  Edit Proposals  │                 │
│       │        │  add/del/replace │                 │
│       │        │  合并 & 排序 & 裁剪│                 │
│       │        └────────┬─────────┘                 │
│       │                 │                           │
│       │        ┌────────▼─────────┐                 │
│       │        │  Validation Gate │                 │
│       └────────│  (Held-out Split)│                 │
│      accepted  │  score(S_{t+1})  │                 │
│                │  > score(S_t) ?  │                 │
│                └──────────────────┘                 │
│                                                      │
│  输出: best_skill.md (300-2000 tokens)                │
└─────────────────────────────────────────────────────┘
```

### 2.3 五个关键机制

1. **Rollout Batch & Minibatch Reflection**  
   用当前 skill 跑一批任务 (batch_size=40)，再把轨迹分成 minibatch (size=8) 送给 optimizer 分析成功/失败模式

2. **Bounded Textual Edit Budget**  
   每步最多接受 L=4 条 add/delete/replace 编辑，防止 skill 漂移过大，扮演文本学习率的角色

3. **Held-out Validation Gate**  
   候选 skill 必须在 held-out selection split 上**严格提升**分数才被接受；持平即拒绝

4. **Rejected-edit Buffer**  
   被拒编辑作为负反馈，防止 optimizer 反复提出相同的无效编辑方向

5. **Epoch-wise Slow/Meta Update**  
   每个 epoch 结束时，汇总长周期规律，写入 skill 的 protected region，步级编辑不可覆盖

---

## 3. 环境与架构

### 3.1 软件依赖

| 组件 | 版本/说明 |
|------|----------|
| Python | ≥ 3.10 |
| SkillOpt | `git clone https://github.com/microsoft/SkillOpt.git` |
| 可选: WebUI | `pip install -e ".[webui]"` (Gradio 监控面板) |
| 可选: ALFWorld | `pip install -e ".[alfworld]"` |

### 3.2 模型后端方案

提供三种方案，可根据实际条件选择：

#### 方案 A: 百炼 API（推荐，当前已有）

复用已配置的 8 个百炼模型，SkillOpt 通过 `openai_compatible` 模式对接：

```bash
# .env
export AZURE_OPENAI_ENDPOINT="https://coding.dashscope.aliyuncs.com/v1"
export AZURE_OPENAI_API_KEY="<你的百炼 API Key>"
export AZURE_OPENAI_AUTH_MODE="openai_compatible"
```

推荐分工：

| 角色 | 模型 | 理由 |
|------|------|------|
| **Optimizer** | `deepseek-v4-pro` | 强推理能力，适合分析轨迹和提议编辑 |
| **Target** | `glm-5` 或 `MiniMax-M2.5` | 作为执行模型被优化 |

#### 方案 B: 纯本地 vLLM（完全离线）

```bash
# 启动两个 vLLM 实例
vllm serve Qwen/Qwen3.6-35B-A3B --port 8001  # optimizer (强)
vllm serve Qwen/Qwen3.5-4B --port 8000        # target

# .env
export QWEN_CHAT_BASE_URL="http://localhost:8000/v1"
export QWEN_CHAT_MODEL="Qwen/Qwen3.5-4B"
```

```bash
python scripts/train.py \
    --optimizer_backend qwen_chat \
    --target_backend qwen_chat \
    --optimizer_model Qwen/Qwen3.6-35B-A3B \
    --target_model Qwen/Qwen3.5-4B \
    --optimizer_qwen_chat_base_url http://localhost:8001/v1 \
    --target_qwen_chat_base_url http://localhost:8000/v1
```

#### 方案 C: Ollama（轻量本地）

```bash
# 先拉取模型
ollama pull qwen3:14b   # optimizer (强)
ollama pull qwen3:4b    # target

# .env
export AZURE_OPENAI_ENDPOINT="http://localhost:11434/v1"
export AZURE_OPENAI_API_KEY="ollama"
export AZURE_OPENAI_AUTH_MODE="openai_compatible"
```

### 3.3 硬件建议

| 角色 | 模型规模 | 显存需求 |
|------|---------|---------|
| Optimizer (本地) | 14B-35B MoE | 24-48 GB |
| Target (本地) | 4B-7B | 8-16 GB |
| Target (API) | 任意 | 无本地需求 |

---

## 4. 实施步骤

### 4.1 整体流程

```
Phase 1: 安装 SkillOpt         (10 分钟)
Phase 2: 从 KB+Code 提取初始 Skill  (30 分钟)
Phase 3: 自动构建评测数据集      (1-2 小时，含人工校验)
Phase 4: 开发自定义 Benchmark   (1-2 小时)
Phase 5: 训练 & 调参            (取决于样本量和模型)
Phase 6: 评估 & 部署            (30 分钟)
```

### 4.2 Phase 1: 安装

```bash
# 克隆仓库
cd /home/node/.openclaw/workspace/tools
git clone https://github.com/microsoft/SkillOpt.git
cd SkillOpt

# 安装
pip install -e .

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入百炼 API 配置（见方案 A）

# 验证安装
python -c "import skillopt; print(skillopt.__version__)"
```

### 4.3 Phase 2: 从知识库和代码提取初始 Skill

用 LLM 自动分析知识库文档和代码仓库，生成结构化的 `initial.md`。

#### 输入源

| 来源 | 类型 | 示例 |
|------|------|------|
| 飞书知识库 | 文档/wiki | 操作手册、FAQ、规范文档 |
| 飞书文档 | 流程/指南 | 架构设计、API 文档 |
| 代码仓库 | 源码/配置 | GitHub/GitLab repos |
| INTERNAL docs | README/CONTRIBUTING | 项目规范 |

#### 提取 Prompt 模板

```
你是一个领域知识提取专家。请分析以下内容，提取可复用的 Agent Skill。

## 知识库文档
{document_content}

## 代码仓库
{code_summary}

## 输出格式

请输出一个结构化的 Skill 文档 (markdown)，包含：

1. **Core Concepts**: 领域核心概念和术语
2. **Procedures**: 标准操作流程 (SOP)
3. **Tool Policies**: 工具调用策略和限制
4. **Error Handling**: 常见错误及修复方案
5. **Output Constraints**: 输出格式要求
6. **Coding Conventions**: 代码规范和模式
7. **API Patterns**: API 调用链和依赖关系

要求：
- 控制在 500 token 以内
- 只写可复用的通用规则，不要写入具体实例
- 每条规则一句话，不要段落
```

#### 输出示例

```markdown
# Internal Platform Skill

## Core Concepts
- All API responses wrapped in Result<T> with code, data, message fields
- Error codes E001-E099: config errors; E100-E199: network errors

## Procedures
1. When querying user data, always join with permission table first
2. Before calling external API, acquire distributed lock via Redis

## Tool Policies
- Use mysql_tool for queries, never direct SQL injection
- File operations must go through file_service, not raw os module

## Error Handling
- E001 "config not found": check consul key then fallback to default config
- E100 "timeout": retry with exponential backoff, max 3 attempts

## Output Constraints
- Code responses must include type annotations
- API responses must include request_id from context

## Coding Conventions
- Use async/await for all I/O operations
- All db operations must be inside a transaction
- Log level: DEBUG for dev, INFO for prod
```

> 💡 论文关键发现：初始 Skill 不需要完美。LiveMath benchmark 只用 154 token 的初始 skill + 1 次优化编辑就提升了 29.3 分。

### 4.4 Phase 3: 自动构建评测数据集

这是最关键的一步——SkillOpt 强依赖 scored rollouts。

#### 数据分割

参考论文设置 (`split_seed=42`)：

| 集合 | 样本数 | 用途 |
|------|-------|------|
| **train** | 100-200 | 训练 rollout，为 optimizer 提供轨迹证据 |
| **val** (selection) | 30-50 | **仅用于 gate**，决定是否接受候选 skill |
| **test** | 50-100 | 最终评估，训练期间完全不可见 |

#### 从知识库生成 QA 对

对每篇知识库文档，用 LLM 自动生成问答对：

```
Prompt: 基于以下文档，生成 10 个问答对。每个问题应该测试对文档内容的理解，
标准答案应该可以从文档中直接提取或推断。

文档:
{document_text}

输出格式 (JSON):
[
  {"id": "kb_001", "question": "...", "context": "...", "answers": ["..."]},
  ...
]
```

#### 从代码生成评测样本

```
Prompt: 基于以下代码仓库信息，生成 10 个代码理解/审查任务。

仓库摘要:
{repo_summary}

输出格式 (JSON):
[
  {
    "id": "code_001",
    "question": "函数 process_order() 的返回值类型是什么？",
    "context": "def process_order(order_id: str) -> OrderResult: ...",
    "answers": ["OrderResult"]
  },
  {
    "id": "code_002",
    "question": "以下代码违反了哪条项目规范？",
    "context": "code: ...",
    "answers": ["缺少事务管理", "未使用 async/await"]
  }
]
```

#### 数据文件结构

```
data/mykb_split/
├── train/
│   └── items.json      # 100-200 条
├── val/
│   └── items.json      # 30-50 条（held-out gate）
└── test/
    └── items.json      # 50-100 条（final eval）
```

#### items.json 格式

```json
[
  {
    "id": "kb_001",
    "question": "当遇到错误码 E001 时应该如何处理？",
    "context": "参考文档: 操作手册 v2.3, 第 3 章...",
    "answers": ["重启服务并检查 consul 配置文件"]
  },
  {
    "id": "code_001",
    "question": "函数 calculate_price 缺少什么必要的错误处理？",
    "context": "def calculate_price(items):\n    total = sum(i.price for i in items)\n    return total",
    "answers": ["缺少空列表检查", "缺少类型校验"]
  }
]
```

### 4.5 Phase 4: 开发自定义 Benchmark

在 SkillOpt 项目中注册你的自定义 benchmark。

#### 文件结构

```
skillopt/envs/mykb/
├── __init__.py
├── dataloader.py        # 数据加载
├── rollout.py           # 任务执行逻辑
└── initial.md           # 初始 skill（Phase 2 的产出）
```

#### `dataloader.py`

```python
"""Data loader for custom knowledge-base + code skill benchmark."""

import json
from pathlib import Path
from typing import List, Dict, Any


def load_items(split_dir: str, split: str) -> List[Dict[str, Any]]:
    """Load task items from a split directory.
    
    Args:
        split_dir: Path to the data split root directory
        split: One of 'train', 'val', 'test'
    
    Returns:
        List of task items
    """
    path = Path(split_dir) / split / "items.json"
    if not path.exists():
        raise FileNotFoundError(f"Split file not found: {path}")
    
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    
    # Validate required fields
    for item in items:
        assert "id" in item, f"Missing 'id' in item"
        assert "question" in item, f"Missing 'question' in item {item.get('id')}"
        assert "answers" in item, f"Missing 'answers' in item {item.get('id')}"
    
    return items


def get_initial_skill() -> str:
    """Load the initial skill document."""
    skill_path = Path(__file__).parent / "initial.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return ""
```

#### `rollout.py`

```python
"""Rollout logic for the custom KB+Code benchmark."""

import re
from typing import Dict, Any
from skillopt.core.types import RolloutResult


def run_rollout(
    item: Dict[str, Any],
    target_model,
    skill: str,
    config: Dict[str, Any],
) -> RolloutResult:
    """Execute a single task rollout.
    
    Args:
        item: Task item with question, context, answers
        target_model: The frozen target model
        skill: Current skill document text
        config: Runtime configuration
    
    Returns:
        RolloutResult with trajectory and score
    """
    # Build prompt with skill + task
    system_prompt = f"{skill}\n\nAnswer questions based on domain knowledge."
    
    user_message = item["question"]
    if item.get("context"):
        user_message = f"Context:\n{item['context']}\n\nQuestion: {item['question']}"
    
    # Call target model
    response = target_model.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        **config.get("generation_kwargs", {}),
    )
    
    # Score the response
    score = compute_score(response, item["answers"], config.get("scorer", {}))
    
    return RolloutResult(
        item_id=item["id"],
        question=item["question"],
        response=response,
        expected_answers=item["answers"],
        score=score,
        trajectory={
            "system_prompt": system_prompt,
            "user_message": user_message,
            "response": response,
        },
    )


def compute_score(
    response: str,
    expected_answers: list,
    scorer_config: Dict[str, Any],
) -> float:
    """Score a model response against expected answers.
    
    Supports multiple scoring strategies.
    """
    metric = scorer_config.get("metric", "exact_match")
    
    if metric == "exact_match":
        # Check if any expected answer appears in response
        response_lower = response.lower()
        for answer in expected_answers:
            if answer.lower() in response_lower:
                return 1.0
        return 0.0
    
    elif metric == "partial_match":
        # Fraction of expected answers found
        response_lower = response.lower()
        matches = sum(
            1 for a in expected_answers if a.lower() in response_lower
        )
        return matches / len(expected_answers) if expected_answers else 0.0
    
    elif metric == "semantic":
        # Placeholder for LLM-as-judge scoring
        # Could use a separate model to evaluate semantic equivalence
        return exact_match_metric(response, expected_answers)
    
    return 0.0


def exact_match_metric(response: str, expected: list) -> float:
    """Simple exact/substring match."""
    resp = response.lower().strip()
    for ans in expected:
        if ans.lower().strip() in resp or resp in ans.lower().strip():
            return 1.0
    return 0.0
```

#### Benchmark 配置文件

```yaml
# configs/mykb/default.yaml
_base_: ../_base_/default.yaml

benchmark: mykb
env_name: mykb

# Optimizer settings
optimizer:
  epochs: 4                    # 训练 epoch 数
  batch_size: 40               # 每批 rollout 任务数
  mini_batch_size: 8           # 每次分析轨迹数
  edit_budget_L: 4             # 每步最大编辑数 (textual LR)
  schedule: cosine             # LR schedule: cosine/linear/constant
  gate_metric: hard            # Validation gate: hard/soft/mixed
  slow_update_gate_with_selection: true  # 论文原始设置
  rejected_buffer: true        # 启用被拒编辑缓冲
  slow_update_enabled: true    # 启用 epoch 级慢更新
  meta_skill_enabled: true     # 启用 optimizer 侧 meta skill

# Scorer
scorer:
  metric: exact_match          # exact_match / partial_match / semantic

# Generation
generation_kwargs:
  temperature: 0.0
  max_tokens: 1024
```

### 4.6 Phase 5: 训练

```bash
# 使用百炼 API
python scripts/train.py \
    --config configs/mykb/default.yaml \
    --split_dir data/mykb_split \
    --azure_openai_endpoint https://coding.dashscope.aliyuncs.com/v1 \
    --optimizer_model deepseek-v4-pro \
    --target_model glm-5 \
    --num_epochs 4 \
    --batch_size 40 \
    --workers 8 \
    --out_root outputs/mykb_v1

# 查看训练进度 (WebUI)
python -m skillopt_webui.app --port 7860 --host 0.0.0.0
```

#### 训练输出结构

```
outputs/mykb_v1/
├── config.json              # 扁平化运行时配置
├── history.json             # 每步训练历史
├── runtime_state.json       # 断点续训状态
├── best_skill.md            # ★ 最终最优 Skill 文档
├── skills/
│   ├── skill_v0001.md       # 版本快照
│   ├── skill_v0002.md
│   └── ...
├── steps/
│   └── step_0001/
│       ├── edit_proposals.json   # 候选编辑
│       ├── edit_apply_report.json # 接受/拒绝记录
│       └── eval_results.json     # 评分详情
├── slow_update/
│   └── epoch_01/
└── meta_skill/
    └── epoch_01/
```

### 4.7 Phase 6: 评估与部署

#### 独立评估

```bash
# 在 test split 上评估训练好的 skill
python scripts/eval_only.py \
    --config configs/mykb/default.yaml \
    --skill outputs/mykb_v1/best_skill.md \
    --split test \
    --split_dir data/mykb_split \
    --azure_openai_endpoint https://coding.dashscope.aliyuncs.com/v1 \
    --target_model glm-5

# 在全量数据上评估
python scripts/eval_only.py \
    --config configs/mykb/default.yaml \
    --skill outputs/mykb_v1/best_skill.md \
    --split all \
    --split_dir data/mykb_split \
    --azure_openai_endpoint https://coding.dashscope.aliyuncs.com/v1 \
    --target_model glm-5
```

#### 对比 Baseline

建议对比以下 baseline：

| Baseline | 说明 | 命令 |
|----------|------|------|
| No skill | 裸 target model | 清空 initial.md 或使用空 skill |
| One-shot LLM | 一次性生成、不优化 | 使用 Phase 2 的 initial.md，不训练 |
| Human skill | 人工编写 | 专家编写然后 eval_only |
| TextGrad | Prompt 梯度优化 | 见论文 baseline 配置 |

#### 部署

将 `best_skill.md` 内容集成到 System Prompt：

```python
# 部署示例
SYSTEM_PROMPT = f"""{best_skill_content}

{original_system_prompt}
"""

# 或直接在 OpenClaw 中将 skill 作为 skill 文件部署
# 将 best_skill.md 放入 ~/.openclaw/skills/mykb/SKILL.md
```

部署特点：
- **零额外推理开销**：skill 只有 300-2000 token
- **可审计**：纯文本，可直接读取和修改
- **可迁移**：跨模型、跨工具链使用

---

## 5. 自定义 Benchmark 开发

### 5.1 扩展 Scoring 策略

如果你的知识库任务不适合 exact match，可以实现更灵活的 scorer：

```python
# skillopt/envs/mykb/scorer.py

def semantic_score(response: str, expected: list, judge_model=None) -> float:
    """Use LLM-as-judge to evaluate semantic equivalence."""
    prompt = f"""Rate whether the response correctly answers the question.
    
Expected answers: {expected}
Actual response: {response}

Rate from 0.0 (completely wrong) to 1.0 (perfectly correct).
Output only the numeric score."""
    
    result = judge_model.chat([{"role": "user", "content": prompt}])
    try:
        return float(result.strip())
    except ValueError:
        return 0.0


def code_exec_score(response: str, test_cases: list) -> float:
    """Execute generated code against test cases."""
    import subprocess, tempfile, os
    
    # Extract code block from response
    code = extract_code_block(response)
    
    # Write to temp file
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(code + "\n\n")
        f.write("\n".join(test_cases))
        tmp = f.name
    
    # Execute
    try:
        result = subprocess.run(
            ["python", tmp],
            capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp)
        return 1.0 if result.returncode == 0 else 0.0
    except Exception:
        os.unlink(tmp)
        return 0.0
```

### 5.2 多模态文档支持

如果知识库包含图片/截图：

```python
def load_document_with_images(doc_path: str) -> Dict:
    """Load document including embedded images for multimodal models."""
    # Extract images from docx/pdf
    # Encode as base64 for multimodal target models
    pass
```

---

## 6. 评测数据集构建

### 6.1 从飞书知识库自动生成 QA

```python
# scripts/generate_eval_from_feishu.py

def generate_qa_pairs(doc_content: str, llm) -> list:
    """Generate QA pairs from a knowledge base document."""
    prompt = f"""You are generating evaluation data for an AI agent skill optimizer.

Based on the following document, generate 10 question-answer pairs.

Requirements:
- Questions should test understanding of document content
- Answers should be extractable or clearly inferable from the document
- Include both factual and procedural questions
- Answers should be concise (1-3 sentences)

Document:
{doc_content}

Output as JSON array:
[{{"id": "...", "question": "...", "context": "relevant excerpt", "answers": ["answer"]}}]
"""
    response = llm.chat([{"role": "user", "content": prompt}])
    return parse_json(response)


def generate_code_qa_pairs(repo_summary: str, code_snippets: list, llm) -> list:
    """Generate code-related QA pairs from repository analysis."""
    prompt = f"""Based on the following code repository information, generate 10 
code-understanding tasks.

Repository:
{repo_summary}

Sample code snippets:
{chr(10).join(code_snippets[:5])}

Generate tasks covering:
1. Function behavior questions
2. Bug identification
3. Code review / style violations
4. API usage patterns
5. Error handling gaps

Output as JSON array:
[{{"id": "code_001", "question": "...", "context": "code snippet", "answers": ["expected"]}}]
"""
    response = llm.chat([{"role": "user", "content": prompt}])
    return parse_json(response)
```

### 6.2 数据质量校验

```python
def validate_dataset(items: list) -> Dict[str, int]:
    """Validate dataset quality."""
    stats = {"total": len(items), "duplicate_ids": 0, "empty_answers": 0}
    
    ids = set()
    for item in items:
        if item["id"] in ids:
            stats["duplicate_ids"] += 1
        ids.add(item["id"])
        if not item.get("answers") or all(not a for a in item["answers"]):
            stats["empty_answers"] += 1
    
    return stats
```

---

## 7. 训练与调优

### 7.1 超参数参考（论文消融实验结果）

| 参数 | 推荐值 | 搜索范围 | 论文发现 |
|------|-------|---------|---------|
| `epochs` | 4 | 2-8 | 4 轮足够收敛 |
| `batch_size` | 40 | 8-full epoch | 8-40 之间稳定，全量略降 |
| `mini_batch_size` | 8 | 1-32 | 2-32 均稳定 |
| `edit_budget_L` | 4 | 1-16 | 4-8 最佳，过小欠拟合过大漂移 |
| `schedule` | cosine | constant/cosine/linear | 三者均优于无 budget |
| `gate_metric` | hard | hard/soft/mixed | hard 为默认，小 selection 集用 soft |

### 7.2 关键消融发现

| 去掉的组件 | SearchQA | Spreadsheet | LiveMath | 影响 |
|-----------|----------|-------------|----------|------|
| (baseline 全部) | 87.1 | 77.5 | 61.3 | — |
| - Learning Rate | 84.6 | 75.7 | 57.3 | ↓ 中度 |
| - Rejected Buffer | 85.5 | 72.9 | 58.9 | ↓↓ 显著 |
| - Meta + Slow Update | 86.3 | **55.0** | 59.7 | ↓↓↓ 灾难 |

> ⚠️ 最重要的组件：Meta Skill + Slow Update。去掉后 SpreadsheetBench 从 77.5 暴跌到 55.0。

### 7.3 小数据集调优

如果样本少于 50 条：

- 用 `gate_metric: soft` 代替 hard（硬匹配太严格）
- 调小 `batch_size` 到 16-20
- 增加 `epochs` 到 6-8 补偿证据不足

---

## 8. 部署与集成

### 8.1 与 OpenClaw 集成

```bash
# 将训练好的 skill 部署为 OpenClaw skill
mkdir -p /home/node/.openclaw/skills/mykb-domain

# 复制 best_skill.md
cp outputs/mykb_v1/best_skill.md \
   /home/node/.openclaw/skills/mykb-domain/SKILL.md
```

### 8.2 跨模型迁移

论文实验表明，优化后的 skill 可以跨模型迁移且大多数情况下获得正向收益：

| 迁移场景 | SpreadsheetBench | LiveMath |
|---------|-----------------|----------|
| GPT-5.4 → GPT-5.4-mini | +9.4 | +4.5 |
| GPT-5.4 → GPT-5.4-nano | +3.0 | +5.6 |

操作：直接在目标模型上用 `eval_only.py --skill` 加载已有 skill。

### 8.3 跨工具链迁移

```bash
# Codex 训练的 skill 迁移到 Claude Code
# SkillOpt 输出格式统一为 best_skill.md，两种 CLI 通用
python scripts/eval_only.py \
    --config configs/mykb/default.yaml \
    --skill outputs/mykb_codex/best_skill.md \
    --harness claude_code \
    ...
```

### 8.4 持续迭代

```
┌──────────────┐    新数据/新场景     ┌──────────────┐
│ best_skill.md │──────────────────▶│ 作为新 initial │
│   (v1.0)     │                    │ 再训练 v2.0   │
└──────────────┘                    └──────┬───────┘
       ▲                                  │
       └──────────────────────────────────┘
              持续优化循环
```

---

## 9. 成本估算

### 9.1 使用百炼 API

以训练一个 skill 为例（100 train + 30 val + 50 test）：

| 阶段 | 调用次数 | Token 估算 | 费用估算 (¥) |
|------|---------|-----------|-------------|
| 初始 Skill 生成 | 1 | 10-50K | ~0.01 |
| 评测数据生成 | 10-20 | 100-500K | ~0.1 |
| 4 epoch × 40 batch 训练 | ~160 rollout + 20 optimizer | 20-200M | **5-50** |
| 最终评估 | ~50 | 2-5M | ~0.5 |
| **总计** | | | **约 ¥6-51** |

> 实际费用取决于 benchmark 复杂度。短 QA（SearchQA 类）约 20M token/epoch；长上下文文档（DocVQA 类）约 50M token/epoch。

### 9.2 ROI 分析

- 一次性投入 ¥6-51
- 产出的 `best_skill.md` 永久可用
- 每次推理附加 300-2000 token（约 ¥0.001-0.005）
- 预期准确率提升 10-25 个百分点

---

## 10. FAQ

### Q1: 初始 Skill 写不好怎么办？

**A:** 论文证明初始 skill 不需要完美。LiveMath 只用 154 token 的初始 skill + 1 次编辑就提升 29.3 分。关键是让 SkillOpt 有轨迹数据可分析。

### Q2: 评测数据太少怎么办？

**A:** 
- 20-50 条就可以开始（论文的 1 example 实验仍然有效）
- 使用 LLM 自动扩充数据
- 改用 `gate_metric: soft` 避免 gate 过严

### Q3: 没有 8 卡 GPU 可以跑吗？

**A:** 完全可以。使用百炼 API（方案 A）无需任何本地 GPU。如果本地部署，Ollama + 7B 模型在单张消费级 GPU 上也能跑。

### Q4: Skill 会过拟合训练数据吗？

**A:** 不容易。因为：
- Validation gate 使用 held-out 数据严格把关
- Edit budget 限制每步只能做 4 条小修改
- Rejected buffer 防止循环改进
- 论文的 test set 评估证明了泛化性

### Q5: 如何确保 Skill 质量？

**A:**
- 输出 `best_skill.md` 是可审计的纯文本
- 每步有 `edit_apply_report.json` 记录所有修改来源
- 最终在 test set 上独立评估
- 建议人工 review 最终 skill 的合理性

---

## 附录

### A. 关键文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| 论文 | `papers/skillopt_2605.23904.pdf` | 27 页完整论文 |
| 代码仓库 | `tools/SkillOpt/` | MIT 开源实现 |
| 配置模板 | `tools/SkillOpt/.env.example` | 环境变量模板 |
| 自定义 benchmark | `tools/SkillOpt/skillopt/envs/mykb/` | 本文档的 benchmark 实现 |
| 评测数据 | `tools/SkillOpt/data/mykb_split/` | train/val/test 数据 |
| 训练输出 | `tools/SkillOpt/outputs/mykb_v1/` | 训练结果 |
| 部署 skill | `~/.openclaw/skills/mykb-domain/SKILL.md` | OpenClaw 集成 |

### B. 参考文献

1. Yang et al., "SkillOpt: Executive Strategy for Self-Evolving Agent Skills", arXiv:2605.23904, May 2026
2. Ni et al., "Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills", arXiv:2603.25158, 2026
3. Alzubi et al., "EvoSkill: Automated Skill Discovery for Multi-Agent Systems", arXiv:2603.02766, 2026
4. Agrawal et al., "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning", arXiv:2507.19457, 2025

---

> **文档版本**: v1.0  
> **最后更新**: 2026-06-02  
> **维护者**: OpenClaw Agent  
> **适用范围**: 基于 SkillOpt 的知识库与代码 Skill 提取与优化
