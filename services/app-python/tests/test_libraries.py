from __future__ import annotations

import unittest

from trpg_app.libraries import _search_hit_for_model


class SearchToolProjectionTest(unittest.TestCase):
    def test_keeps_selection_fields_and_drops_large_retrieval_metadata(self) -> None:
        projected = _search_hit_for_model(
            {
                "id": "doc-1",
                "sourceId": "crb",
                "sourceTitle": "核心规则",
                "title": "借机攻击",
                "fullPath": "核心规则 > 战斗 > 借机攻击",
                "excerpt": "离开威胁方格",
                "score": 0.9,
                "metadata": {
                    "entryType": "section",
                    "headingPath": ["战斗", "借机攻击"],
                    "legacyParentId": "legacy-1",
                    "structuralBlocks": [{"content": "x" * 50_000}],
                    "searchBlocks": ["y" * 50_000],
                },
            }
        )

        self.assertEqual(projected["id"], "doc-1")
        self.assertEqual(projected["excerpt"], "离开威胁方格")
        self.assertEqual(
            projected["metadata"],
            {
                "entryType": "section",
                "headingPath": ["战斗", "借机攻击"],
                "legacyParentId": "legacy-1",
            },
        )
        self.assertNotIn("structuralBlocks", projected["metadata"])


if __name__ == "__main__":
    unittest.main()
