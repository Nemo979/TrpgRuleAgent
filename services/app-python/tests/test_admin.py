import json
import tempfile
import unittest
from pathlib import Path

from trpg_app.admin import build_jsonl, publish_revision


class AdminWorkflowTest(unittest.TestCase):
    def test_builds_then_publishes_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = root / "input.jsonl"
            documents.write_text(
                json.dumps(
                    {
                        "id": "coc7:skills",
                        "rulesetId": "coc7",
                        "sourceId": "core",
                        "sourceTitle": "Core",
                        "title": "Skills",
                        "fullPath": "Core > Skills",
                        "content": "A unique rule body.",
                        "version": "1",
                        "priority": 0,
                        "metadata": {"page": 10},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            revision = build_jsonl(
                root=root / "libraries",
                library_id="coc7",
                name="CoC 7E",
                system="Call of Cthulhu",
                edition="7E",
                documents=documents,
                index_dir=None,
                aliases=[" CoC7 ", "克苏鲁7版", "CoC7"],
            )
            self.assertFalse((root / "libraries/coc7/current").exists())

            publish_revision(root / "libraries", "coc7", revision)

            manifest = json.loads(
                (root / "libraries/coc7/current/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["revision"], revision)
            self.assertEqual(manifest["aliases"], ["CoC7", "克苏鲁7版"])

    def test_build_rejects_empty_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = self._write_documents(root, library_id="coc7")

            with self.assertRaisesRegex(ValueError, "aliases must be non-empty"):
                build_jsonl(
                    root=root / "libraries",
                    library_id="coc7",
                    name="CoC 7E",
                    system="Call of Cthulhu",
                    edition="7E",
                    documents=documents,
                    index_dir=None,
                    aliases=[""],
                )

            self.assertFalse((root / "libraries").exists())

    def test_build_preserves_extraction_report_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = self._write_documents(root, library_id="coc7")
            import_report = root / "import-report.json"
            warnings = [{"type": "image_dominant_page", "page": 3}]
            import_report.write_text(
                json.dumps(
                    {
                        "libraryId": "coc7",
                        "sourceFile": "coc7.pdf",
                        "pageCount": 12,
                        "documentCount": 999,
                        "tableCount": 4,
                        "warnings": warnings,
                        "revision": "stale-revision",
                        "exactDuplicates": [["old", "duplicate"]],
                    }
                ),
                encoding="utf-8",
            )

            revision = build_jsonl(
                root=root / "libraries",
                library_id="coc7",
                name="CoC 7E",
                system="Call of Cthulhu",
                edition="7E",
                documents=documents,
                import_report=import_report,
                index_dir=None,
            )

            report = json.loads(
                (
                    root
                    / "libraries"
                    / "coc7"
                    / "builds"
                    / revision
                    / "import-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("coc7.pdf", report["sourceFile"])
            self.assertEqual(12, report["pageCount"])
            self.assertEqual(4, report["tableCount"])
            self.assertEqual(warnings, report["warnings"])
            self.assertEqual(revision, report["revision"])
            self.assertEqual(1, report["documentCount"])
            self.assertEqual([], report["exactDuplicates"])

    def test_build_rejects_import_report_for_different_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = self._write_documents(root, library_id="coc7")
            import_report = root / "import-report.json"
            import_report.write_text(
                json.dumps({"libraryId": "pf1e", "warnings": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "import report uses a different library id"
            ):
                build_jsonl(
                    root=root / "libraries",
                    library_id="coc7",
                    name="CoC 7E",
                    system="Call of Cthulhu",
                    edition="7E",
                    documents=documents,
                    import_report=import_report,
                    index_dir=None,
                )
            self.assertFalse((root / "libraries").exists())

    @staticmethod
    def _write_documents(root: Path, *, library_id: str) -> Path:
        documents = root / "input.jsonl"
        documents.write_text(
            json.dumps(
                {
                    "id": f"{library_id}:skills",
                    "rulesetId": library_id,
                    "sourceId": "core",
                    "sourceTitle": "Core",
                    "title": "Skills",
                    "fullPath": "Core > Skills",
                    "content": "A unique rule body.",
                    "version": "1",
                    "priority": 0,
                    "metadata": {"page": 10},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return documents


if __name__ == "__main__":
    unittest.main()
