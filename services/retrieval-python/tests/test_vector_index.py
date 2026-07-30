import unittest

from trpg_retrieval.vector_index import embed_passages, embed_query


class RecordingEmbedding:
    def __init__(self):
        self.values = []

    def embed(self, values, **kwargs):
        self.values.append((list(values), kwargs))
        return [[0.0]]

    def passage_embed(self, values, **kwargs):
        raise AssertionError("E5 passages must use explicit passage prefix")

    def query_embed(self, query):
        raise AssertionError("E5 queries must use explicit query prefix")


class VectorIndexHelpersTest(unittest.TestCase):
    def test_multilingual_e5_uses_required_retrieval_prefixes(self) -> None:
        model = RecordingEmbedding()

        list(
            embed_passages(
                model,
                "intfloat/multilingual-e5-small",
                ["规则文本"],
                batch_size=1,
            )
        )
        list(embed_query(model, "intfloat/multilingual-e5-small", "规则问题"))

        self.assertEqual(model.values[0][0], ["passage: 规则文本"])
        self.assertEqual(model.values[1][0], ["query: 规则问题"])


if __name__ == "__main__":
    unittest.main()
