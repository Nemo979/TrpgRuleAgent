import json
import tempfile
import unittest
from pathlib import Path

from trpg_retrieval.evaluation import load_cases


class EvaluationTest(unittest.TestCase):
    def test_loads_jsonl_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                json.dumps({
                    "id": "aao-trigger",
                    "query": "什么时候会触发借机攻击？",
                    "relevantIds": ["pf1e-example"],
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            cases = load_cases(path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].id, "aao-trigger")
        self.assertEqual(cases[0].relevant_ids, ["pf1e-example"])

    def test_rejects_case_without_relevant_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                '{"id":"broken","query":"test","relevantIds":[]}\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_cases(path)


if __name__ == "__main__":
    unittest.main()
