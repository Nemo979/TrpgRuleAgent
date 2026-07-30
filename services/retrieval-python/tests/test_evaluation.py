import json
import tempfile
import unittest
from pathlib import Path

from trpg_retrieval.domain import RuleDocument, SearchHit
from trpg_retrieval.evaluation import RetrievalCase, evaluate, load_cases
from trpg_retrieval.repository import RuleRepository


class RecordingRetriever:
    def __init__(self, hit):
        self.hit = hit
        self.seen_rulesets = []

    def search(self, query, documents, limit, source_ids=None):
        del query, limit, source_ids
        values = list(documents)
        self.seen_rulesets.append({item.ruleset_id for item in values})
        return [self.hit]


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

    def test_evaluates_the_repository_library_instead_of_pf1e_constant(self) -> None:
        document = RuleDocument(
            id="coc7:combat",
            ruleset_id="coc7",
            source_id="core",
            source_title="Core",
            title="Combat",
            full_path="Core > Combat",
            content="Combat order.",
            version="7e",
            priority=0,
        )
        retriever = RecordingRetriever(SearchHit(document, "Combat order.", 1.0))

        report = evaluate(
            RuleRepository([document]),
            retriever,
            [RetrievalCase("combat", "战斗顺序", ["coc7:combat"])],
            5,
        )

        self.assertEqual(report["hitRate"], 1.0)
        self.assertEqual(retriever.seen_rulesets, [{"coc7"}])


if __name__ == "__main__":
    unittest.main()
