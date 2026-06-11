# Benchmark 数据集

| 目录 | 说明 | train / selection / test |
|------|------|--------------------------|
| `fineract` | 默认完整集（测试与文档引用） | 15 / 22 / 8 |
| `fineract-full` | 完整集备份（与 `fineract` 相同） | 15 / 22 / 8 |
| `fineract-fast` | 快速冒烟集（`config.yaml` 默认） | 5 / 6 / 3 |

## 快速跑

`config.yaml` 已指向 `fineract-fast`，并配合较小的 `batch_size` / `num_epochs`。

恢复完整 benchmark：

```yaml
project:
  benchmark: demo-project/benchmarks/fineract
```

从完整集重新生成 fast 子集：

```bash
python demo-project/benchmarks/build_fast_subset.py
```

修正 `context_refs` 简写路径（`fineract-accounting/...` → Maven 源码路径）：

```bash
python demo-project/benchmarks/normalize_context_refs.py
```
