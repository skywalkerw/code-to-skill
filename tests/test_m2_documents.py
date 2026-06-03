"""M2 文档规范化测试。"""
import pytest
from pathlib import Path

from code_to_skill.document_normalizer.types import (
    DocumentManifest, DocumentChunk, RawDocument,
)
from code_to_skill.document_normalizer.knowledge_source import (
    LocalFileKnowledgeSource, get_provider,
)
from code_to_skill.document_normalizer.parsers import (
    parse_markdown, parse_html, parse_text, parse_raw_document,
)
from code_to_skill.document_normalizer.structure import build_structure
from code_to_skill.document_normalizer.cleaner import redact_text, clean_text
from code_to_skill.document_normalizer.chunker import chunk_blocks
from code_to_skill.document_normalizer import normalize_document


FINERACT_ROOT = "external/fineract-develop"
HAS_FINERACT = Path(FINERACT_ROOT).exists()


class TestTypes:
    def test_manifest(self):
        m = DocumentManifest(source_id="test", source_uri="test.md", source_type="markdown")
        assert m.schema_version == "1.0"

    def test_chunk(self):
        c = DocumentChunk(chunk_id="t:1", source_id="test", text="Hello")
        assert c.chunk_id == "t:1"


class TestKnowledgeSource:
    def test_local_file_found(self):
        src = LocalFileKnowledgeSource(FINERACT_ROOT if HAS_FINERACT else ".")
        # Use README if it exists
        raw = src.fetch_raw_content("README.md" if HAS_FINERACT else "pyproject.toml")
        assert raw.source_type in ("markdown", "text")

    def test_local_file_not_found(self):
        src = LocalFileKnowledgeSource(".")
        with pytest.raises(FileNotFoundError):
            src.fetch_raw_content("nonexistent_12345.md")

    def test_get_provider(self):
        provider = get_provider("local_file")
        assert provider.healthcheck()


class TestParsers:
    def test_markdown_blocks(self):
        md = "# Title\n\nSome paragraph.\n\n- item 1\n- item 2"
        blocks = parse_markdown(md)
        assert len(blocks) >= 3

    def test_html_blocks(self):
        html = "<h1>Title</h1><p>Text</p>"
        blocks = parse_html(html)
        assert len(blocks) >= 2

    def test_text_blocks(self):
        blocks = parse_text("line1\n\nline2")
        assert len(blocks) == 2

    def test_markdown_code_fence(self):
        md = '```python\nprint("hi")\n```'
        blocks = parse_markdown(md)
        assert blocks[0]["type"] == "code_block"
        assert "print" in blocks[0]["text"]


class TestCleaner:
    def test_redact_api_key(self):
        text = 'api_key = "sk-1234567890abcdef1234567890abcdef"'
        clean, count = redact_text(text)
        assert count >= 1
        assert "sk-" not in clean or "REDACTED" in clean

    def test_clean_whitespace(self):
        text = "a\n\n\nb"
        assert clean_text(text) == "a\n\nb"


class TestChunker:
    def test_chunk_basic(self):
        blocks = [
            {"type": "heading", "level": 1, "text": "Title"},
            {"type": "paragraph", "text": "Some content here."},
        ]
        chunks = chunk_blocks(blocks, "test")
        assert len(chunks) >= 1
        assert chunks[0].text == "Some content here."


class TestFullPipeline:
    def test_markdown_normalize(self, tmp_path):
        # Create a temp markdown file
        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello\n\nWorld content.\n\n## Section 2\n\nMore text.")

        result = normalize_document(
            source_uri=str(md_file),
            source_id="test-md",
            output_root=str(tmp_path / "output"),
        )
        assert len(result["chunks"]) >= 1

    @pytest.mark.skipif(not HAS_FINERACT, reason="Fineract not available")
    def test_fineract_readme(self):
        result = normalize_document(
            source_uri="external/fineract-develop/README.md",
            source_id="fineract-readme",
        )
        assert result["manifest"].source_type == "markdown"
        assert len(result["chunks"]) > 0
