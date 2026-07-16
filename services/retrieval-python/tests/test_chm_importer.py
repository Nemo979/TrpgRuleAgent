import tempfile
import unittest
from pathlib import Path

from trpg_retrieval.importers.chm import import_documents, parse_sitemap


class ChmImporterTest(unittest.TestCase):
    def test_parses_nested_sitemap_and_gb18030_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sitemap = """<html><body><ul>
            <li><object type='text/sitemap'><param name='Name' value='核心规则书 CRB'></object>
              <ul><li><object type='text/sitemap'><param name='Name' value='战斗'><param name='Local' value='combat.htm'></object></li></ul>
            </li></ul></body></html>"""
            (root / "rules.hhc").write_bytes(sitemap.encode("gb18030"))
            html = """<html><head><meta charset='gb2312'><title>战斗</title></head>
            <body><h1>借机攻击</h1><p>测试规则正文</p><script>ignore()</script></body></html>"""
            (root / "combat.htm").write_bytes(html.encode("gb18030"))

            entries = parse_sitemap(root / "rules.hhc")
            self.assertEqual(["核心规则书 CRB", "战斗"], entries[0]["path"])

            documents, report = import_documents(root)
            self.assertEqual(1, len(documents))
            self.assertEqual("crb", documents[0].source_id)
            self.assertIn("借机攻击", documents[0].content)
            self.assertNotIn("ignore", documents[0].content)
            self.assertEqual(1, report["documentCount"])


if __name__ == "__main__":
    unittest.main()
