#!/bin/bash
# code-to-skill 端到端全链路测试
# 用法: ./run_tests.sh

set -e

cd "$(dirname "$0")"

echo "========================================"
echo "  code-to-skill 端到端测试"
echo "========================================"
echo ""

# 确保 CLI 已安装
python -m pip install -e . 

skill-lab run all --config-path config.yaml --with-atoms

echo ""
echo "========================================"
echo "  ✅ 完成"
echo "========================================"
