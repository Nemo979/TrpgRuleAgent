import tempfile
import unittest
from pathlib import Path

from trpg_retrieval.chm_structured import (
    extract_blocks,
    partition_sections,
    transform_documents,
)
from trpg_retrieval.domain import RuleDocument


class ChmStructuredTest(unittest.TestCase):
    def document(self, source_file: str, content: str) -> RuleDocument:
        return RuleDocument(
            id="pf:combat",
            ruleset_id="pathfinder-1e",
            source_id="crb",
            source_title="核心规则",
            title="战斗动作",
            full_path="核心规则 > 战斗动作",
            content=content,
            version="test",
            priority=100,
            metadata={"sourceFile": source_file},
        )

    def test_extracts_heading_sections_and_markdown_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "combat.html"
            path.write_text(
                """
                <h2>战斗动作</h2><p>概述。</p>
                <h3>全防御</h3><p>获得+4闪避加值。</p>
                <table><tr><th>动作</th><th>加值</th></tr>
                <tr><td>全防御</td><td>+4</td></tr></table>
                <h3>撤退</h3><p>离开战斗。</p>
                """,
                encoding="utf-8",
            )

            blocks = extract_blocks(path)
            sections = partition_sections(blocks)

        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[1].heading_path, ["战斗动作", "全防御"])
        self.assertIn("| 动作 | 加值 |", sections[1].content())

    def test_splits_heading_documents_and_preserves_legacy_parent_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            html = root / "combat.html"
            first = "全防御规则。" * 30
            second = "撤退规则。" * 30
            html.write_text(
                f"<h2>战斗动作</h2><p>概述内容。</p><h3>全防御</h3><p>{first}</p>"
                f"<h3>撤退</h3><p>{second}</p>",
                encoding="utf-8",
            )
            document = self.document("combat.html", f"战斗动作\n{first}\n{second}")
            audit = {
                "documents": [
                    {"id": document.id, "strategy": "split_headings"}
                ]
            }

            values, report = transform_documents([document], root, audit)

        self.assertGreaterEqual(len(values), 2)
        self.assertTrue(all(value.metadata["legacyParentId"] == document.id for value in values))
        self.assertEqual(report["splitParents"], 1)

    def test_keeps_short_parent_and_adds_table_row_search_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "light.html").write_text(
                "<p>光源表</p><table><tr><th>光源</th><th>范围</th></tr>"
                "<tr><td>火把</td><td>20尺</td></tr></table>",
                encoding="utf-8",
            )
            document = self.document("light.html", "火把可以照亮20尺。")
            audit = {
                "documents": [
                    {"id": document.id, "strategy": "keep_table_aware"}
                ]
            }

            values, report = transform_documents([document], root, audit)

        self.assertEqual([value.id for value in values], [document.id])
        blocks = values[0].metadata["structuralBlocks"]
        self.assertTrue(any("光源 | 范围\n火把 | 20尺" in block["content"] for block in blocks))
        self.assertEqual(report["tableEnhancedParents"], 1)


if __name__ == "__main__":
    unittest.main()
