#!/bin/bash
# code-to-skill 测试脚本
# 用法: ./run_tests.sh          # 所有测试
#       ./run_tests.sh -v       # 详细输出
#       ./run_tests.sh M1       # 仅 M1 相关测试

set -e

cd "$(dirname "$0")"

echo "========================================"
echo "  code-to-skill 测试"
echo "========================================"
echo ""

# 检查环境
python3 -c "import code_to_skill" 2>/dev/null || {
    echo "❌ 未安装 code-to-skill，正在安装..."
    pip install -e ".[lxml]" -q
}

# 运行测试
if [ -z "$1" ] || [ "$1" = "-v" ]; then
    echo ">> 全部测试"
    python3 -m pytest tests/ "$@"
elif [ "$1" = "M1" ]; then
    echo ">> M1 代码图谱"
    python3 -m pytest tests/test_m1_code_graph.py "${@:2}"
elif [ "$1" = "M2" ]; then
    echo ">> M2 文档规范化"
    python3 -m pytest tests/test_m2_documents.py "${@:2}"
elif [ "$1" = "M3" ] || [ "$1" = "M4" ]; then
    echo ">> M3/M4 抽取与优化"
    python3 -m pytest tests/test_m3_m4.py "${@:2}"
elif [ "$1" = "M5" ] || [ "$1" = "M6" ]; then
    echo ">> M5/M6 模型交互与CLI"
    python3 -m pytest tests/test_m5_types.py "${@:2}"
elif [ "$1" = "e2e" ]; then
    echo ">> 端到端全链路 (skill-lab CLI)"

    # 确保 CLI 已安装
    pip install -e . -q 2>/dev/null

    python3 -m code_to_skill.cli.main run all --config-path ".test-data/project.yaml"
    echo "  ✅ 全链路通过"
else
    echo "用法: $0 [M1|M2|M3|M4|M5|M6|e2e] [-v]"
    exit 1
fi

echo ""
echo "========================================"
echo "  ✅ 完成"
echo "========================================"
