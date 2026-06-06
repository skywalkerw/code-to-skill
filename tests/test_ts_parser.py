"""tree-sitter Query 解析测试。"""
from __future__ import annotations

import os
import tempfile

import pytest

from code_to_skill.code_graph import parser as p
from code_to_skill.code_graph.ts_backend import get_parser_bundle, backend_status


def test_backend_status():
    st = backend_status()
    assert "tree_sitter_version" in st


def test_java_component_annotation_does_not_steal_class_name():
    """@Component 在 class 声明前时，类名须为真实类型而非 Component。"""
    java = """
package org.example;
import org.springframework.stereotype.Component;
@Component
public class CashBasedAccountingProcessorForLoan implements AccountingProcessorForLoan {
    public void createJournalEntriesForDisbursements() {}
}
"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "Processor.java")
        with open(path, "w", encoding="utf-8") as f:
            f.write(java)
        graph, errs = p.parse_files(["Processor.java"], d)
        assert not errs
        class_names = [n.name for n in graph.nodes if n.kind.value == "class"]
        assert "CashBasedAccountingProcessorForLoan" in class_names
        assert "Component" not in class_names
        stats = p.get_last_parse_stats()
        assert stats.files.get("Processor.java") in ("tree-sitter-query", "tree-sitter-walk", "regex")


def test_java_parse_finds_class_and_method():
    java = """
package org.example;
public class LoanService {
    public void disburse() {}
    class Inner {
        void helper() {}
    }
}
"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "LoanService.java")
        with open(path, "w", encoding="utf-8") as f:
            f.write(java)
        graph, errs = p.parse_files(["LoanService.java"], d)
        assert not errs
        names = {
            (n.name, n.kind.value)
            for n in graph.nodes
            if n.kind.value != "file"
        }
        assert ("LoanService", "class") in names
        stats = p.get_last_parse_stats()
        backend = stats.files.get("LoanService.java", "regex")
        bundle = get_parser_bundle("java")
        if bundle and backend in ("tree-sitter-query", "tree-sitter-walk"):
            assert ("disburse", "method") in names or ("helper", "method") in names
        else:
            assert ("LoanService", "class") in names
            assert backend in ("regex", "tree-sitter-query", "tree-sitter-walk")


def test_kotlin_or_rust_gets_nodes():
    """扩展语言：至少 regex 能提取符号。"""
    with tempfile.TemporaryDirectory() as d:
        kt = os.path.join(d, "Foo.kt")
        with open(kt, "w") as f:
            f.write("class Foo { fun bar() {} }")
        graph, _ = p.parse_files(["Foo.kt"], d)
        names = [n.name for n in graph.nodes if n.kind.value != "file"]
        assert "Foo" in names or "bar" in names
