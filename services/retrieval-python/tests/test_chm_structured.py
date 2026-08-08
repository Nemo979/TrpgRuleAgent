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

    def test_splits_word_anchor_chapters_into_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "combat.html"
            path.write_text(
                """
                <html><body>
                <a name="战斗中的数据计算">战斗中的数据计算</a>
                <p>先攻取决于敏捷。</p>
                <a name="战斗中的动作">战斗中的动作</a>
                <p>标准动作可以进行攻击。</p>
                <a name="OLE_LINK1">OLE_LINK1</a>
                <p>域代码说明。</p>
                </body></html>
                """,
                encoding="utf-8",
            )

            blocks = extract_blocks(path)
            sections = partition_sections(blocks)

        heading_paths = [section.heading_path for section in sections]
        self.assertEqual(
            heading_paths,
            [["战斗中的数据计算"], ["战斗中的动作"]],
        )
        self.assertIn("先攻取决于敏捷", sections[0].content())
        self.assertNotIn("域代码说明", sections[0].content())

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

    def test_splits_word_anchor_chapters_via_heading_and_table_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            html = root / "combat.html"
            first = "先攻取决于敏捷修正。" * 20
            second = "标准动作可以发起攻击。" * 20
            html.write_text(
                f"<a name=\"战斗中的数据计算\">战斗中的数据计算</a><p>{first}</p>"
                f"<a name=\"战斗中的动作\">战斗中的动作</a><p>{second}</p>",
                encoding="utf-8",
            )
            document = self.document("combat.html", f"战斗中的数据计算\n{first}\n{second}")
            audit = {
                "documents": [
                    {"id": document.id, "strategy": "split_headings_and_tables"}
                ]
            }

            values, report = transform_documents([document], root, audit)

        titles = [value.title for value in values]
        self.assertEqual(titles, ["战斗中的数据计算", "战斗中的动作"])
        self.assertEqual(
            [value.metadata["headingPath"] for value in values],
            [
                ["核心规则", "战斗动作", "战斗中的数据计算"],
                ["核心规则", "战斗动作", "战斗中的动作"],
            ],
        )
        self.assertTrue(all(value.metadata["legacyParentId"] == document.id for value in values))
        self.assertEqual(report["splitParents"], 1)

    def test_splits_oversized_entry_on_level_field_paragraphs(self) -> None:
        from trpg_retrieval.chm_structured import (
            EntryCandidate,
            HtmlBlock,
            _field_split_entry,
        )

        document = self.document("spells.html", "诅咒集")
        blocks = [
            HtmlBlock("paragraph", "诅咒法术\n简介。"),
            HtmlBlock("paragraph", "等级：法师 3\n施放时间：标准动作"),
            HtmlBlock("paragraph", "描述文字。"),
            HtmlBlock("paragraph", "元素诅咒\n简介。"),
            HtmlBlock("paragraph", "等级：德鲁伊 3\n施放时间：1轮"),
            HtmlBlock("paragraph", "描述文字。"),
        ]
        candidate = EntryCandidate(
            heading_path=("法术", "诅咒集"),
            blocks=tuple(blocks),
            block_start=0,
            block_end=len(blocks),
            entry_type="spell",
        )

        values = _field_split_entry(document, candidate)

        self.assertEqual([value.title for value in values], ["诅咒法术", "元素诅咒"])
        self.assertEqual(values[0].metadata["entryType"], "spell")
        self.assertIn("等级：法师 3", values[0].content)
        self.assertIn("等级：德鲁伊 3", values[1].content)

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

    def test_drops_tiny_navigation_label_repeated_as_loose_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "classes.html").write_text(
                "<h2>职业变体</h2><p>职业变体</p>"
                "<h3>混血术士</h3><p>这是有实质内容的职业变体规则。</p>"
                "<h3>探求者</h3><p>这是另一个有实质内容的职业变体规则。</p>",
                encoding="utf-8",
            )
            document = self.document(
                "classes.html",
                "职业变体\n混血术士\n这是有实质内容的职业变体规则。\n"
                "探求者\n这是另一个有实质内容的职业变体规则。",
            )
            audit = {
                "documents": [
                    {"id": document.id, "strategy": "split_headings"}
                ]
            }

            values, report = transform_documents([document], root, audit)

        self.assertEqual([value.title for value in values], ["混血术士", "探求者"])
        self.assertEqual(report["splitParents"], 1)
        self.assertEqual(report["fallbackParents"], 0)

    def test_splits_generic_pf1e_entries_without_spell_name_special_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "catalog.html").write_text(
                """
                <h2>法术</h2>
                <h3>职业法术列表</h3><p>一级：油腻术、护盾术。</p>
                <h3>油腻术 (Grease)</h3>
                <p>School conjuration; Level sorcerer/wizard 1</p>
                <p>Casting Time 1 standard action; Range close; Duration 1 min./level</p>
                <p>Saving Throw Reflex partial; Spell Resistance yes</p>
                <h2>专长</h2>
                <h3>敏捷专长 (Agile Feat)</h3>
                <p>Prerequisites Dexterity 13.</p><p>Benefit You move quickly.</p>
                <h2>魔法物品</h2>
                <h3>银月戒指 (Silvermoon Ring)</h3>
                <p>Aura faint; Caster Level 5th; Slot ring; Price 12,000 gp</p>
                <h2>职业变体</h2>
                <h3>混血术士</h3><p>Replaces the normal bloodline feature.</p>
                <h2>职业能力</h2>
                <h3>勇猛</h3><p>Level 3; Class Skills Acrobatics and Climb.</p>
                <p>Description The character gains a bonus.</p>
                """,
                encoding="utf-8",
            )
            document = self.document("catalog.html", "目录正文" * 2_000)
            audit = {"documents": [{"id": document.id, "strategy": "split_catalog_entries"}]}

            values, report = transform_documents([document], root, audit)

        self.assertEqual(
            {value.metadata["entryType"] for value in values},
            {"spell", "feat", "magic_item", "archetype", "class_ability"},
        )
        grease = next(value for value in values if value.metadata["entryType"] == "spell")
        self.assertIn("油腻术", grease.content)
        self.assertEqual(grease.metadata["nameZh"], "油腻术")
        self.assertEqual(grease.metadata["nameEn"], "Grease")
        self.assertEqual(grease.metadata["legacyParentId"], document.id)
        self.assertEqual(grease.metadata["sourcePosition"]["blockStart"], 3)
        self.assertNotIn("职业法术列表", [value.title for value in values])
        self.assertEqual(report["splitParents"], 1)

    def test_creates_independent_rule_table_and_filters_navigation_shells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tables.html").write_text(
                """
                <h2>职业变体</h2><p>职业变体</p>
                <h3>职业变体</h3><p>职业变体</p>
                <h3>守望者</h3><p>Replaces one class feature with a watchful ability.</p>
                <h2>战斗修正</h2>
                <table><tr><th>情况</th><th>修正</th></tr>
                <tr><td>隐蔽</td><td>—2</td></tr></table>
                """,
                encoding="utf-8",
            )
            document = self.document("tables.html", "表格与职业变体" * 500)
            audit = {"documents": [{"id": document.id, "strategy": "split_tables_with_context"}]}

            values, _report = transform_documents([document], root, audit)

        self.assertEqual([value.metadata["entryType"] for value in values], ["archetype", "rule_table"])
        self.assertEqual(values[0].title, "守望者")
        self.assertIn("隐蔽", values[1].content)
        self.assertTrue(all(value.metadata["legacyParentId"] == document.id for value in values))


if __name__ == "__main__":
    unittest.main()
