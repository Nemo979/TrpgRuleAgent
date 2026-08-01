import tempfile
import unittest
from pathlib import Path

from trpg_retrieval.chm_structure_audit import (
    HtmlSignals,
    attach_evaluation_targets,
    audit_documents,
    inspect_html,
    recommend_strategy,
    render_markdown,
)


class ChmStructureAuditTest(unittest.TestCase):
    def test_extracts_headings_tables_anchors_and_non_table_bold_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rules.html"
            path.write_text(
                """
                <html><body>
                <h2 id="combat">战斗动作</h2>
                <p><b>全防御</b>提供闪避加值。</p>
                <table><tr><th><b>动作</b></th></tr><tr><td>全防御</td></tr></table>
                <a name="withdraw"></a><h3>撤退</h3>
                </body></html>
                """,
                encoding="utf-8",
            )

            signals = inspect_html(path)

        self.assertEqual(signals.headings, ("战斗动作", "撤退"))
        self.assertEqual(signals.table_count, 1)
        self.assertEqual(signals.table_row_count, 2)
        self.assertEqual(signals.anchor_count, 2)
        self.assertEqual(signals.bold_labels, ("全防御",))

    def test_recommends_catalog_and_heading_strategies(self) -> None:
        catalog = HtmlSignals(("法术",), 2, 10, 0, tuple(f"法术{i}" for i in range(30)))
        rules = HtmlSignals(("战斗", "全防御"), 0, 0, 0, ())

        self.assertEqual(
            recommend_strategy(
                content_length=50_000,
                full_path="法术 > CRB",
                signals=catalog,
            ),
            "split_catalog_entries",
        )
        self.assertEqual(
            recommend_strategy(
                content_length=12_000,
                full_path="核心规则 > 战斗",
                signals=rules,
            ),
            "split_headings",
        )

    def test_audit_and_markdown_do_not_modify_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.html").write_text(
                "<h2>属性</h2><h3>敏捷</h3><p>规则正文</p>",
                encoding="utf-8",
            )
            documents = [
                {
                    "id": "pf:test",
                    "fullPath": "核心规则 > 属性",
                    "content": "规则" * 3_000,
                    "metadata": {"sourceFile": "one.html"},
                }
            ]

            report = audit_documents(documents, root)

        self.assertEqual(documents[0]["content"], "规则" * 3_000)
        self.assertEqual(report["documentCount"], 1)
        self.assertEqual(report["documents"][0]["strategy"], "split_headings")
        markdown = render_markdown(report, top=1)
        self.assertIn("核心规则 > 属性", markdown)
        self.assertIn("`split_headings`", markdown)

    def test_attaches_evaluation_ranks_and_target_strategies(self) -> None:
        report = {
            "documents": [
                {
                    "id": "pf:combat",
                    "fullPath": "核心规则 > 战斗 > 全防御",
                    "contentLength": 12000,
                    "strategy": "split_headings",
                }
            ]
        }

        attach_evaluation_targets(
            report,
            [{"id": "total-defense", "query": "全防御加值", "relevantIds": ["pf:combat"]}],
            {"cases": [{"id": "total-defense", "rank": None}]},
        )

        target = report["evaluationTargets"][0]
        self.assertIsNone(target["rank"])
        self.assertEqual(target["documents"][0]["strategy"], "split_headings")


if __name__ == "__main__":
    unittest.main()
