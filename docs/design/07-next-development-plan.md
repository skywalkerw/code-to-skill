# 下一阶段开发计划：对齐 SkillOpt 论文与参考实现

> 基于 2026-06-05 差距分析，列出待实现模块及实现顺序。

## 目标

将 code-to-skill 的 `skillopt_loop` 从当前简化版（骨架正确但缺关键组件）
升级为与 external/SkillOpt 论文级实现对齐的完整优化循环。

## 分阶段计划

### P0 — 架构基础（先做，后续模块依赖这些）

| 序号 | 模块 | 新建文件 | 说明 |
|:---:|------|----------|------|
| P0-1 | **EnvAdapter 抽象层** | `skillopt_loop/envs/base.py` | 定义 `EnvAdapter` ABC（`rollout`/`reflect`/`build_env`），提供默认实现 `DEFAULTAdapter`，解耦 benchmark 与训练循环 |
| P0-2 | **Separation 模块** | `skillopt_loop/separation.py` | optimizer/target backend 分离 + accumulation 支持 |

### P1 — 梯度质量与收敛稳定性

| 序号 | 模块 | 新建文件 | 说明 |
|:---:|------|----------|------|
| P1-1 | **Gradient 模块** | `skillopt_loop/gradient/__init__.py`、`skillopt_loop/gradient/aggregate.py` | 分层 merge（failure→success→final），buffer→reflect 闭环 |
| P1-2 | **SelectionCache + Scheduler 接入** | `skillopt_loop/cache.py` | 语义 hash 索引的 selection score 缓存，EditBudgetScheduler 接入主 loop |

### P2 — 完整优化能力

| 序号 | 模块 | 新建文件 | 说明 |
|:---:|------|----------|------|
| P2-1 | **Slow update + Meta skill** | `skillopt_loop/slow_update.py`、`skillopt_loop/meta_skill.py` | epoch 级 longitudinal 对比 + optimizer 侧记忆 |
| P2-2 | **Scorer 增强 + Test eval + Checkpoint** | 加强已有 `scoring.py`、新建 `skillopt_loop/test_eval.py` | LLM Judge scorer、test split 最终评估、step 内 checkpoint 恢复 |

### P3 — 串联

| 序号 | 说明 |
|:---:|------|
| P3 | 更新 `run_skillopt_loop` 串联所有新模块，确保端到端可运行 |

---

## 详细规格

见各模块源代码注释与类型定义。
