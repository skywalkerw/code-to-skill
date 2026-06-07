"""CLI 帮助文案（供 Click -h / --help 使用）。"""

MAIN_EPILOG = """
\b
查看帮助:
  skill-lab -h                  本页：顶层命令
  skill-lab run -h              流水线子命令列表
  skill-lab run all -h          完整流水线参数说明
  skill-lab codegraph -h        代码图谱查询子命令

\b
示例:
  skill-lab init --name my-skill
  skill-lab doctor --config-path config.yaml
  skill-lab config --config-path config.yaml
  skill-lab run all --config-path config.yaml
  skill-lab run optimize-skill -o runs/latest/optimization --epochs 3
  skill-lab resume 20260607-120000 --config-path config.yaml
  skill-lab eval <run_id> --split test
"""

RUN_EPILOG = """
\b
查看帮助:
  skill-lab run -h                    本子命令列表
  skill-lab run <子命令> -h           该子命令的参数说明

\b
示例:
  skill-lab run all --config-path config.yaml
  skill-lab run all --dry-run
  skill-lab run all --resume-run-id 20260607-120000
  skill-lab run code-graph --repo fineract
  skill-lab run optimize-skill -o test-data/runs/xxx/optimization \\
      --epochs 1 --batch-size 15 --resume
"""

RUN_ALL_DOC = """\b
运行完整流水线 M1→M4（一次 run 目录）。

\b
步骤:
  [1/4] code-graph      构建 graph.db（多仓库支持 custom_patterns）
  [2/4] normalize-docs  规范化 project.sources.docs
  [3/4] extract-atoms   从代码/文档抽取 SkillAtom
  [4/4] optimize-skill  SkillOpt 训练（initial_skill + benchmark）

\b
产物目录:
  <settings.output.root>/<run_id>/
    sources/code/<repo_id>/<ref>/graph.db
    atoms/
    optimization/best_skill.md
    logs/run.log

\b
续训:
  --resume-run-id <id>  复用已有 run 目录，跳过 M1–M3（若 graph.db 已存在）

\b
模块边界（--from-step / --to-step，实验性）:
  code-graph | normalize-docs | extract-atoms | optimize-skill
  别名: m1|m2|m3|m4 或 1|2|3|4
"""

RUN_CODE_GRAPH_DOC = """\b
仅运行 M1：扫描仓库、解析 AST、框架元数据，写入 graph.db。

输出默认在 settings.output.root/sources/code/<repo_id>/<ref>/。
project.code_graph.custom_patterns 会在此阶段生效。
"""

RUN_NORMALIZE_DOCS_DOC = """\b
仅运行 M2：将 project.sources.docs 规范化为 chunks。

输出在 settings.output.root/sources/docs/<doc_id>/。
"""

RUN_EXTRACT_ATOMS_DOC = """\b
仅运行 M3：从 leaf_contexts + document_chunks 抽取 SkillAtom。

通常需先完成 M1/M2；``--from`` 指定含 sources/ 与 atoms/ 的 run 目录。
"""

RUN_CODE_GRAPH_DAEMON_DOC = """\b
启动 CodeGraph MCP daemon（stdio + 可选文件监听）。

需已存在 graph.db；Cursor 通过 MCP 接入后可在 IDE 内查图谱。
"""

RUN_CODE_GRAPH_WATCH_DOC = """\b
监听仓库文件变更，防抖后增量更新 graph.db（前台运行，Ctrl+C 退出）。
"""

RUN_OPTIMIZE_SKILL_DOC = """\b
仅运行 M4：SkillOpt 优化循环（rollout → reflect → gate → evaluate）。

\b
常用参数:
  -o / --output     训练目录（含 runtime_state.json 时可 --resume 续训）
  --epochs          覆盖 config.settings.skillopt.num_epochs
  --batch-size      每 epoch 训练 batch 大小
  --benchmark       覆盖 config.project.benchmark 目录

\b
依赖:
  graph.db（run all 或 run code-graph 产出，或 run 目录内 sources/code/...）
  config.project.initial_skill 与 benchmark（train/selection/test）
"""
