# 贡献指南

欢迎为 code-to-skill 做贡献。请先阅读本文。

---

## 快速开始

```bash
git clone git@github.com:skywalkerw/code-to-skill.git
cd code-to-skill
pip install -e ".[lxml]"
pip install pytest
python -m pytest tests/ -v  # 应全部通过
```

## 项目结构

详见 [`docs/coding-standards.md`](docs/coding-standards.md)。关键规则：

- 代码只在 `src/code_to_skill/` 下，按 M1-M6 模块组织
- 新文件必须归入对应模块目录，不要放在根目录
- 使用绝对导入 `from code_to_skill.xxx import ...`
- 所有数据模型使用 pydantic，必须含 `schema_version`
- LLM 调用必须支持降级（`is_llm_available()` 检查）

## 开发流程

1. Fork 仓库，从 `master` 创建 feature 分支
2. 实现功能，添加测试（`tests/` 目录下）
3. 确保 `python -m pytest tests/ -v` 全部通过
4. Commit 格式：`<模块>: <简短描述>`
5. 发起 Pull Request

## 测试

```bash
# 全部测试
python -m pytest tests/ -v

# 单模块
python -m pytest tests/test_m3_m4.py -v

# 需要 Fineract 的集成测试（external/fineract-develop 存在时自动运行）
```

## 添加新模块

1. 在 `src/code_to_skill/` 下创建模块目录
2. 创建 `__init__.py`（包含 `run_*_pipeline()` 主函数）
3. 创建 `types.py`（pydantic models）
4. 在 `tests/` 下创建 `test_mX_*.py`
5. 在 M6 CLI 的 `main.py` 中添加 `run <module>` 命令

## 添加 LLM 后端

在 `model_gateway/llm_backend.py` 的 `_ENV_MAP` 中添加新条目：

```python
"my-backend": {
    "base_url_env": "MY_BASE_URL",
    "api_key_env": "MY_API_KEY",
    "model": "model-name",
    "default_base_url": "https://...",
}
```

## 添加新语言解析器

在 `code_graph/parser.py` 的 `_PATTERNS` 中添加新语言的 regex 模式。

## 文档

- 设计讨论：先看 `docs/design/00-overall-design.md`
- 新功能：先在 `docs/references/implementation-plan.md` 记录计划
- API 变更：更新 `docs/api-reference.md`

## License

MIT
