import unittest

from trpg_app.library_boundary import find_explicit_other_library


LIBRARIES = (
    {
        "id": "pathfinder-1e",
        "name": "Pathfinder 1E 中文规则库",
        "system": "Pathfinder",
        "edition": "1E",
        "revision": "pf-revision",
        "aliases": ["PF1E", "Pathfinder 1E"],
    },
    {
        "id": "golden-sky-stories-zh-1-2",
        "name": "夕妖晚谣 1.2",
        "system": "夕妖晚谣（Golden Sky Stories）",
        "edition": "中文 1.2",
        "revision": "gss-revision",
        "aliases": ["GSS"],
    },
)


class LibraryBoundaryTest(unittest.TestCase):
    def test_matches_other_library_in_both_directions(self) -> None:
        cases = (
            (
                "pathfinder-1e",
                "夕妖晚谣的化形规则是什么？",
                "golden-sky-stories-zh-1-2",
            ),
            (
                "golden-sky-stories-zh-1-2",
                "PF1E 的借机攻击如何判定？",
                "pathfinder-1e",
            ),
        )

        for current_id, message, expected_id in cases:
            with self.subTest(message=message):
                match = find_explicit_other_library(
                    message,
                    current_library_id=current_id,
                    libraries=LIBRARIES,
                )
                self.assertIsNotNone(match)
                self.assertEqual(match["id"], expected_id)

    def test_matches_english_parenthetical_system_alias(self) -> None:
        match = find_explicit_other_library(
            "How does a scene work in Golden Sky Stories?",
            current_library_id="pathfinder-1e",
            libraries=LIBRARIES,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "golden-sky-stories-zh-1-2")

    def test_does_not_match_current_ambiguous_or_broad_aliases(self) -> None:
        libraries = (
            *LIBRARIES,
            {
                "id": "generic-zh",
                "name": "中文规则库",
                "system": "中文规则库",
                "edition": "1",
                "revision": "generic-revision",
            },
            {
                "id": "pathfinder-2e",
                "name": "Pathfinder 2E",
                "system": "Pathfinder",
                "edition": "2E",
                "revision": "pf2-revision",
            },
        )
        messages = (
            "Pathfinder 的借机攻击如何判定？",
            "我的 mypf1ehelper 名字怎么写？",
            "中文规则库里通常有什么？",
            "这个能力什么时候恢复？",
        )

        for message in messages:
            with self.subTest(message=message):
                self.assertIsNone(
                    find_explicit_other_library(
                        message,
                        current_library_id="pathfinder-1e",
                        libraries=libraries,
                    )
                )

    def test_current_library_name_wins_for_comparison_questions(self) -> None:
        messages = (
            "Pathfinder 1E 里有没有类似夕妖晚谣化形的能力？",
            "PF1E 与 GSS 的行动机制有什么不同？",
        )

        for message in messages:
            with self.subTest(message=message):
                self.assertIsNone(
                    find_explicit_other_library(
                        message,
                        current_library_id="pathfinder-1e",
                        libraries=LIBRARIES,
                    )
                )


if __name__ == "__main__":
    unittest.main()
