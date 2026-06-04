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
    echo ">> 端到端全链路 (M1→M2→M3→M4)"
    python3 -c "
from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.document_normalizer import normalize_document
from code_to_skill.atom_extractor import run_atom_extraction
from code_to_skill.skillopt_loop import run_skillopt_loop
import os, shutil

SKILL_DIR = '.test-data/e2e-test'
os.makedirs(SKILL_DIR, exist_ok=True)

print('[1/4] M1 代码图谱...')
m1 = run_code_graph_pipeline(
    repo_root='.test-data/fineract-develop',
    include=['fineract-provider/src/main/java/org/apache/fineract/accounting/**'],
    exclude=['**/test/**','**/target/**'],
    output_root=f'{SKILL_DIR}/sources', use_cache=False, max_leaf_tokens=8000,
)

print('[2/4] M2 文档规范化...')
r = normalize_document(source_uri='.test-data/kb/fineract/README.md', source_id='test')
chunks = [c.model_dump() for c in r['chunks']]

print('[3/4] M3 Atom抽取...')
m3 = run_atom_extraction(leaf_contexts=[ctx.model_dump() for ctx in m1['leaf_contexts']], document_chunks=chunks)
accepted = sum(1 for a in m3['merged_atoms'] if a.status in ('accepted','candidate'))

print('[4/4] M4 SkillOpt...')
lines = ['# Test Skill'] + [f'- {a.claim}' for a in m3['merged_atoms'] if a.status in ('accepted','candidate')]
m4 = run_skillopt_loop(initial_skill='\n'.join(lines), benchmark_items=m3['benchmark_seeds'],
                       output_dir=f'{SKILL_DIR}/m4', num_epochs=1, batch_size=4)

shutil.rmtree(SKILL_DIR)
print(f'  ✅ 全链路通过: {len(m1[\"graph\"].nodes)} nodes → {accepted} atoms → score={m4[\"best_score\"]:.2f}')
"
else
    echo "用法: $0 [M1|M2|M3|M4|M5|M6|e2e] [-v]"
    exit 1
fi

echo ""
echo "========================================"
echo "  ✅ 完成"
echo "========================================"
