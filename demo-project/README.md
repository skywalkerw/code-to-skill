# demo-project

Fineract 示例项目数据（纳入版本库）。

| 路径 | 版本库 | 说明 |
|------|--------|------|
| `benchmarks/` | ✅ | train/selection/test benchmark |
| `initial_skill.md` | ✅ | 初始 Skill 草稿 |
| `sources/docs/` | ✅ | 示例知识文档（Markdown） |
| `sources/repos.manifest.yaml` | ✅ | 源码仓库 git URL 清单 |
| `sources/repos/` | ❌ | 本地 clone，运行 `fetch-sources` 拉取 |
| `runs/` | ❌ | 流水线动态产物（M1–M4 输出） |

## 拉取源码仓库

在仓库根目录执行（读取 `config.yaml` 的 `project.sources.repos`）：

```bash
./demo-project/fetch-sources.sh
```

或：

```bash
python3 demo-project/fetch_sources.py --config-path config.yaml
```

默认 shallow clone Apache Fineract `develop` 分支到 `demo-project/sources/repos/fineract`。已存在时执行 `fetch` + `checkout` + `pull`。

可选环境变量：`SKILL_LAB_CONFIG_PATH` 指定配置文件；`--depth 0` 完整 clone。
