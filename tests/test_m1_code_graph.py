"""M1 代码图谱测试。"""
import pytest
from pathlib import Path

from code_to_skill.code_graph.scanner import scan_repo, _infer_language, _infer_kind
from code_to_skill.code_graph.types import (
    CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind,
    FileInventory, FileEntry, Entrypoint, ModuleTree, ModuleTreeNode, LeafContext,
)
from code_to_skill.code_graph.parser import parse_files, _infer_language as _parser_lang
from code_to_skill.code_graph.resolver import resolve_references, _extract_imports
from code_to_skill.code_graph import run_code_graph_pipeline


FINERACT_ROOT = "external/fineract-develop"
HAS_FINERACT = Path(FINERACT_ROOT).exists()

_ACCTG_INCLUDE = ["fineract-provider/src/main/java/org/apache/fineract/accounting/**"]
_NO_TEST_EXCLUDE = ["**/test/**", "**/integration-tests/**", "**/target/**"]


class TestTypes:
    def test_code_graph_defaults(self):
        g = CodeGraph()
        assert g.schema_version == "1.0"
        assert g.nodes == []

    def test_graph_node(self):
        n = GraphNode(id="a::b", kind=NodeKind.function, name="test", language="java")
        assert n.id == "a::b"

    def test_file_inventory(self):
        fi = FileInventory(files=[FileEntry(path="a.java", language="java", kind="source")])
        assert len(fi.files) == 1


class TestScanner:
    def test_language_inference(self):
        assert _infer_language("Foo.java") == "java"
        assert _infer_language("bar.py") == "python"
        assert _infer_language("app.js") == "javascript"
        assert _infer_language("image.png") == ""

    def test_kind_inference(self):
        assert _infer_kind("src/test/Foo.java", "Foo.java", "java") == "test"
        assert _infer_kind("src/main/Foo.java", "Foo.java", "java") == "source"

    def test_kind_binary(self):
        assert _infer_kind("lib/app.jar", "app.jar", "") == "binary"

    @pytest.mark.skipif(not HAS_FINERACT, reason="Fineract not available")
    def test_scan_fineract_accounting(self):
        inv = scan_repo(FINERACT_ROOT, include=_ACCTG_INCLUDE, exclude=_NO_TEST_EXCLUDE)
        assert len(inv.files) > 0
        # All should be Java
        for f in inv.files:
            assert f.language in ("java", "")


class TestParser:
    def test_java_regex_parse(self):
        code = """public class LoanRepaymentSchedule {
    public void calculateInterest() {
        return;
    }
}"""
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".java", delete=False) as f:
            f.write(code)
            fpath = f.name
        try:
            graph, errs = parse_files([os.path.basename(fpath)], os.path.dirname(fpath))
            assert len(errs) == 0
            # Should find at least the class
            assert len(graph.nodes) >= 1
        finally:
            os.unlink(fpath)


class TestResolver:
    def test_java_import_extraction(self):
        code = """import org.apache.fineract.accounting.gljournal.JournalEntry;
import java.math.BigDecimal;
import org.springframework.stereotype.Service;"""
        imports = _extract_imports(code, "java")
        assert "JournalEntry" in imports
        assert "BigDecimal" in imports
        assert "Service" in imports


class TestFullPipeline:
    @pytest.mark.skipif(not HAS_FINERACT, reason="Fineract not available")
    def test_run_on_fineract(self):
        results = run_code_graph_pipeline(
            repo_root=FINERACT_ROOT,
            include=_ACCTG_INCLUDE,
            exclude=_NO_TEST_EXCLUDE,
            max_leaf_tokens=8000,
        )
        assert len(results["inventory"].files) > 0
        assert len(results["graph"].nodes) > 0
