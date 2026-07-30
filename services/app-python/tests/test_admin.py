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
            )
            self.assertFalse((root / "libraries/coc7/current").exists())

            publish_revision(root / "libraries", "coc7", revision)

            manifest = json.loads(
                (root / "libraries/coc7/current/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["revision"], revision)


if __name__ == "__main__":
    unittest.main()
