"""CLI 帮助文案（供 Click -h / --help 使用）。"""

MAIN_EPILOG = """
\b
查看帮助:
  skill-lab -h                  本页：顶层命令
  skill-lab run -h              流水线子命令列表
  skill-lab run all -h          完整流水线参数说明
  skill-lab codegraph -h        代码图谱查询子命令
  skill-lab inspect run -h      run 产物摘要与自进化校验

\b
示例:
  skill-lab init --name my-skill
  skill-lab doctor --config-path config.yaml
  skill-lab config --config-path config.yaml
  skill-lab run all --config-path config.yaml --with-atoms
  skill-lab run optimize-skill --trace-merge -o demo-project/runs/xxx/optimization
  skill-lab inspect run <run_id> --trace-pool --validate-self-evolution
  skill-lab resume <run_id> --config-path config.yaml
  skill-lab eval <run_id> --split test
"""

RUN_EPILOG = """
\b
查看帮助:
  skill-lab run -h                    本子命令列表
  skill-lab run <子命令> -h           该子命令的参数说明

\b
示例:
  skill-lab run all --config-path config.yaml --with-atoms
  skill-lab run all --dry-run
  skill-lab run all --resume-run-id 20260608-213005
  skill-lab run code-graph --repo fineract
  skill-lab run optimize-skill --self-evolve \\
      -o demo-project/runs/xxx/optimization --epochs 2 --batch-size 5
  skill-lab run skill-hygiene <run_id> --force
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
跳过策略（默认）:
  已配置 initial_skill + benchmark train 时，默认跳过 M2/M3，只跑 M1+M4。
  要跑齐四段请加: --with-atoms
  跳过 M3 但仍跑 M2: --with-docs

\b
产物目录:
  <settings.output.root>/<run_id>/
    sources/code/<repo_id>/<ref>/graph.db
    sources/docs/<doc_id>/
    atoms/merged_atoms.jsonl, artifact_quality.json
    optimization/best_skill.md, artifact_contract.json
    logs/run.log, traces/

\b
续训:
  --resume-run-id <id>  复用已有 run 目录，跳过 M1–M3（若 graph.db 已存在）

\b
Dry-run（不跑真实流水线）:
  --dry-run
  --dry-run-level config-only       # L1 配置校验（默认）
  --dry-run-level static-analysis   # L2 + M1 扫描/M2 解析
  --dry-run-level full-simulate     # L3 + M1–M4（MockReplayBackend）

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

通常需先完成 M1/M2；``--from-run`` 指定含 sources/ 的 run 目录。
产出含 merged_atoms.jsonl、artifact_quality.json、benchmark_seeds.jsonl。
"""

RUN_CODE_GRAPH_DAEMON_DOC = """\b
启动 CodeGraph MCP daemon（stdio + 可选文件监听）。

需已存在 graph.db；Cursor 通过 MCP 接入后可在 IDE 内查图谱。
"""

RUN_CODE_GRAPH_WATCH_DOC = """\b
监听仓库文件变更，防抖后增量更新 graph.db（前台运行，Ctrl+C 退出）。
"""

RUN_BOOTSTRAP_BENCHMARK_DOC = """\b
将 M3 高置信 atom 种子写入或合并进 benchmark train。

\b
常用参数:
  --from-run        含 atoms/merged_atoms.jsonl 的 run 目录
  --merge           追加到已有 train（默认仅在没有 train 时填充）
  --benchmark       覆盖 config.project.benchmark 目录
  --dry-run         预览条目数，不写文件
"""

RUN_OPTIMIZE_SKILL_DOC = """\b
仅运行 M4：SkillOpt 优化循环（rollout → reflect → gate → evaluate）。

\b
常用参数:
  -o / --output       训练目录（含 runtime_state.json 时可 --resume 续训）
  --epochs            覆盖 config.settings.skillopt.num_epochs
  --batch-size        每 epoch 训练 batch 大小
  --benchmark         覆盖 config.project.benchmark 目录
  --trace-merge       自进化：仅 trace 聚类归纳（不改严格 gate）
  --self-evolve       自进化：完整路径（严格 gate、归因、hygiene）
  --slow-update       启用 epoch 级 slow update
  --meta-skill        启用 meta skill

\b
依赖:
  graph.db（run all 或 run code-graph 产出；run 目录内 sources/code/...）
  config.project.initial_skill 与 benchmark（train/selection/test）

\b
自进化产物（--trace-merge / --self-evolve）:
  optimization/trace_pool/, proposals/, rejected_edit_buffer.jsonl
  rule_attribution.json（--self-evolve）
"""

RUN_SKILL_HYGIENE_DOC = """\b
对已有 run 的 optimization/best_skill.md 执行 hygiene pass。

经 selection gate 验证通过后才写回 best_skill.md。

\b
常用参数:
  --force     忽略 token/规则阈值，强制执行 hygiene
"""

INSPECT_RUN_DOC = """\b
汇总 run 目录：manifest、gate、test、context refs、训练曲线、run quality。

\b
Run quality（optimization/run_quality_report.json）:
  best_score_monotonic   best 是否单调不下降
  leakage_count          scorer/benchmark 泄露命中
  hard_failures          test 未通过项与 missed_checks

\b
自进化扩展:
  --trace-pool                 trace pool / proposals 摘要
  --rule-attribution           规则归因摘要
  --frontier                   前沿 Skill 池摘要
  --validate-self-evolution    校验 self_evolution 产物完整性
  --optimization-dir NAME      optimization 子目录（默认 optimization；对比重训用 optimization-07）
  --compare-optimization       对比 optimization 与 --optimization-dir 的质量指标

\b
单文件查看: skill-lab inspect file <path>
"""
