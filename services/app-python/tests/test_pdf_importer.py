import unittest

from trpg_app.importers.pdf import table_to_markdown


class PdfTableTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
