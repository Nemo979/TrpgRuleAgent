import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trpg_app.importers.pdf import (
    detect_bulleted_page_headings,
    detect_page_headings,
    import_pdf,
    table_to_markdown,
)


def fake_page(
    *,
    text: str | None,
    tables: list[list[list[str | None]]],
    images: list[object] | None = None,
) -> MagicMock:
    page = MagicMock()
    page.extract_text.return_value = text
    page.extract_tables.return_value = tables
    page.images = images or []
    return page


class PdfTableTest(unittest.TestCase):
    def test_does_not_treat_numeric_table_rows_as_headings(self) -> None:
        self.assertEqual(detect_page_headings("\n5以上 失去意识晕倒在地\n"), [])
        self.assertEqual(detect_page_headings("\n20円\n"), [])
        self.assertEqual(detect_page_headings("\n5.最后要做的事\n"), ["5.最后要做的事"])

    def test_accepts_short_bulleted_section_but_not_rule_description(self) -> None:
        self.assertEqual(
            detect_page_headings("\n⚫ 吓一跳\n紧接着的规则正文。"),
            ["吓一跳"],
        )
        self.assertEqual(
            detect_bulleted_page_headings("   ⚫   吓一跳\n紧接着的规则正文。"),
            ["吓一跳"],
        )
        self.assertEqual(
            detect_page_headings("\n⚫ 一团毛球（4）：通过嬉戏让别人敞开心扉。\n"),
            [],
        )

    def test_converts_regular_table_to_markdown(self) -> None:
        markdown, reliable = table_to_markdown(
            [["等级", "加值"], ["1", "+1"], ["2", "+2"]]
        )
        self.assertTrue(reliable)
        self.assertIn("| 等级 | 加值 |", markdown)
        self.assertIn("| 2 | +2 |", markdown)

    def test_marks_ragged_table_as_unreliable(self) -> None:
        _, reliable = table_to_markdown([["A", "B"], ["only-one"]])
        self.assertFalse(reliable)


class PdfImportWorkflowTest(unittest.TestCase):
    def test_bulleted_heading_is_local_and_does_not_leak_to_next_page(self) -> None:
        pages = [
            fake_page(text="\n其他\n\n⚫ 吓一跳\n紧接着的规则正文。", tables=[]),
            fake_page(text="下一页延续其他章节。", tables=[]),
        ]
        opened_pdf = MagicMock()
        opened_pdf.__enter__.return_value.pages = pages
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "rules.pdf"
            source.write_bytes(b"pdf")
            output = root / "built"
            with patch(
                "trpg_app.importers.pdf.pdfplumber.open",
                return_value=opened_pdf,
            ):
                import_pdf(
                    pdf_path=source,
                    output_dir=output,
                    library_id="gss",
                    source_title="夕妖晚谣",
                    edition="1.2",
                )
            documents = [
                json.loads(line)
                for line in (output / "documents.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(documents[0]["metadata"]["localHeadings"], ["吓一跳"])
            self.assertEqual(
                documents[0]["metadata"]["structuralBlocks"][-1]["headingPath"],
                ["夕妖晚谣", "其他", "吓一跳"],
            )
            self.assertEqual(
                documents[1]["metadata"]["headingPath"],
                ["夕妖晚谣", "其他"],
            )

    def test_imports_text_tables_and_warnings_without_a_real_pdf(self) -> None:
        pages = [
            fake_page(
                text=(
                    "当角色进行检定时，掷出六面骰并加上对应能力值。"
                    "若最终结果达到或超过难度，则本次行动成功。"
                ),
                tables=[
                    [["难度", "说明"], ["4", "普通"], ["6", "困难"]],
                ],
            ),
            fake_page(
                text="本页规则主要由插图展示。",
                tables=[],
                images=[object(), object()],
            ),
            fake_page(text=None, tables=[]),
            fake_page(
                text="",
                tables=[
                    [["能力", "效果"], ["夜行"]],
                ],
            ),
        ]
        opened_pdf = MagicMock()
        opened_pdf.__enter__.return_value.pages = pages

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "夕妖晚谣1.2(1).pdf"
            source_bytes = b"not-a-real-pdf"
            source.write_bytes(source_bytes)
            output = root / "built"

            with patch(
                "trpg_app.importers.pdf.pdfplumber.open",
                return_value=opened_pdf,
            ) as pdf_open:
                report = import_pdf(
                    pdf_path=source,
                    output_dir=output,
                    library_id="golden-sky-stories-zh-1-2",
                    source_title="夕妖晚谣",
                    edition="1.2",
                )

            pdf_open.assert_called_once_with(source)
            for page in pages:
                page.extract_text.assert_called_once_with(layout=True)
                page.extract_tables.assert_called_once_with()

            copied_pdf = output / "originals" / source.name
            self.assertEqual(copied_pdf.read_bytes(), source_bytes)

            document_lines = (
                output / "documents.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(document_lines), 3)
            documents = [json.loads(line) for line in document_lines]

            self.assertEqual(
                documents[0],
                {
                    "id": "golden-sky-stories-zh-1-2:pdf:af1124c3d57bed82",
                    "rulesetId": "golden-sky-stories-zh-1-2",
                    "sourceId": "source-5f7faab84f83",
                    "sourceTitle": "夕妖晚谣",
                    "title": "夕妖晚谣 · 第 1 页",
                    "fullPath": "夕妖晚谣 > 第 1 页",
                    "content": (
                        "当角色进行检定时，掷出六面骰并加上对应能力值。"
                        "若最终结果达到或超过难度，则本次行动成功。"
                        "\n\n### 表格 1\n\n"
                        "| 难度 | 说明 |\n"
                        "| --- | --- |\n"
                        "| 4 | 普通 |\n"
                        "| 6 | 困难 |"
                    ),
                    "version": "1.2",
                    "priority": 0,
                    "metadata": {
                        "format": "pdf",
                        "sourceFile": "夕妖晚谣1.2(1).pdf",
                        "page": 1,
                        "tableCount": 1,
                        "structureVersion": 2,
                        "headingPath": ["夕妖晚谣"],
                        "detectedHeadings": [],
                        "localHeadings": [],
                        "inheritedHeadings": [],
                        "structuralBlocks": [
                            {
                                "headingPath": ["夕妖晚谣"],
                                "content": (
                                    "当角色进行检定时，掷出六面骰并加上对应能力值。"
                                    "若最终结果达到或超过难度，则本次行动成功。"
                                    "\n\n### 表格 1\n\n"
                                    "| 难度 | 说明 |\n"
                                    "| --- | --- |\n"
                                    "| 4 | 普通 |\n"
                                    "| 6 | 困难 |"
                                ),
                            }
                        ],
                    },
                },
            )
            self.assertEqual(
                documents[1]["id"],
                "golden-sky-stories-zh-1-2:pdf:03c5cdeb9789d1e9",
            )
            self.assertEqual(documents[1]["metadata"]["page"], 2)
            self.assertEqual(documents[1]["metadata"]["tableCount"], 0)
            self.assertEqual(
                documents[2]["id"],
                "golden-sky-stories-zh-1-2:pdf:a696916d33330b20",
            )
            self.assertEqual(documents[2]["metadata"]["page"], 4)
            self.assertEqual(documents[2]["metadata"]["tableCount"], 1)
            self.assertIn("| 能力 | 效果 |", documents[2]["content"])
            self.assertIn("| 夜行 |  |", documents[2]["content"])

            expected_report = {
                "libraryId": "golden-sky-stories-zh-1-2",
                "sourceFile": "夕妖晚谣1.2(1).pdf",
                "pageCount": 4,
                "documentCount": 3,
                "tableCount": 2,
                "warnings": [
                    {
                        "type": "image_dominant_page",
                        "page": 2,
                        "imageCount": 2,
                    },
                    {"type": "empty_page", "page": 3},
                    {"type": "unreliable_table", "page": 4, "table": 1},
                ],
            }
            self.assertEqual(report, expected_report)
            self.assertEqual(
                json.loads(
                    (output / "import-report.json").read_text(encoding="utf-8")
                ),
                expected_report,
            )

    def test_carries_section_heading_to_continuation_page(self) -> None:
        pages = [
            fake_page(
                text="\n猫\n\n猫的基本特技。\n\n弱点和追加【特技】\n",
                tables=[],
            ),
            fake_page(
                text="好动天性\n猫看到移动的小东西时会追上去。",
                tables=[],
            ),
            fake_page(text="\n狗\n\n狗的基本特技。", tables=[]),
        ]
        opened_pdf = MagicMock()
        opened_pdf.__enter__.return_value.pages = pages

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "rules.pdf"
            source.write_bytes(b"pdf")
            output = root / "built"
            with patch(
                "trpg_app.importers.pdf.pdfplumber.open",
                return_value=opened_pdf,
            ):
                import_pdf(
                    pdf_path=source,
                    output_dir=output,
                    library_id="gss",
                    source_title="夕妖晚谣",
                    edition="1.2",
                )

            documents = [
                json.loads(line)
                for line in (output / "documents.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                documents[1]["metadata"]["headingPath"],
                ["夕妖晚谣", "猫", "弱点和追加【特技】"],
            )
            self.assertEqual(
                documents[1]["metadata"]["inheritedHeadings"],
                ["猫", "弱点和追加【特技】"],
            )
            self.assertIn("夕妖晚谣 > 猫 > 弱点和追加【特技】", documents[1]["fullPath"])
            self.assertEqual(
                documents[2]["metadata"]["headingPath"],
                ["夕妖晚谣", "狗"],
            )


if __name__ == "__main__":
    unittest.main()
