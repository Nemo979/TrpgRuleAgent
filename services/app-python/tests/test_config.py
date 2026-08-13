import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trpg_app.config import load_config


BASE_CONFIG = """
shared_password_env: TEST_SHARED_PASSWORD
session_secret_env: TEST_SESSION_SECRET
library_root: data/libraries
models:
  - id: test
    label: Test
    base_url: https://model.example/v1
    model: test-model
    api_key_env: TEST_MODEL_KEY
    request_timeout_seconds: 45
    max_retries: 2
"""


class AppConfigTest(unittest.TestCase):
    def load(self, content: str = BASE_CONFIG):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.yaml"
            path.write_text(content, encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "TEST_SHARED_PASSWORD": "shared",
                    "TEST_SESSION_SECRET": "secret",
                    "TEST_MODEL_KEY": "model-key",
                },
                clear=False,
            ):
                return load_config(path)

    def test_loads_model_reliability_settings(self) -> None:
        config = self.load()
        model = config.models[0]

        self.assertEqual(model.request_timeout_seconds, 45)
        self.assertEqual(model.max_retries, 2)
        self.assertFalse(config.enable_dynamic_evidence_budget)
        self.assertFalse(config.enable_query_decomposition)
        self.assertFalse(config.enable_complex_planner)
        self.assertFalse(config.enable_fact_ledger)

    def test_loads_dynamic_evidence_budget_feature_flag(self) -> None:
        config = self.load(
            BASE_CONFIG.replace(
                "library_root: data/libraries",
                "library_root: data/libraries\nenable_dynamic_evidence_budget: true",
            )
        )

        self.assertTrue(config.enable_dynamic_evidence_budget)

    def test_loads_query_decomposition_feature_flag(self) -> None:
        config = self.load(
            BASE_CONFIG.replace(
                "library_root: data/libraries",
                "library_root: data/libraries\nenable_query_decomposition: true",
            )
        )

        self.assertTrue(config.enable_query_decomposition)

    def test_loads_complex_planner_feature_flag(self) -> None:
        config = self.load(
            BASE_CONFIG.replace(
                "library_root: data/libraries",
                "library_root: data/libraries\nenable_complex_planner: true",
            )
        )

        self.assertTrue(config.enable_complex_planner)

    def test_loads_fact_ledger_feature_flag(self) -> None:
        config = self.load(
            BASE_CONFIG.replace(
                "library_root: data/libraries",
                "library_root: data/libraries\nenable_fact_ledger: true",
            )
        )

        self.assertTrue(config.enable_fact_ledger)

    def test_rejects_unbounded_retry_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_retries"):
            self.load(BASE_CONFIG.replace("max_retries: 2", "max_retries: 4"))

    def test_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "request_timeout_seconds"):
            self.load(
                BASE_CONFIG.replace(
                    "request_timeout_seconds: 45",
                    "request_timeout_seconds: 0",
                )
            )

    def test_rejects_string_feature_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.load(
                BASE_CONFIG.replace(
                    "library_root: data/libraries",
                    'library_root: data/libraries\nenable_dynamic_evidence_budget: "false"',
                )
            )

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.load(
                BASE_CONFIG.replace(
                    "library_root: data/libraries",
                    'library_root: data/libraries\nenable_query_decomposition: "false"',
                )
            )

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.load(
                BASE_CONFIG.replace(
                    "library_root: data/libraries",
                    'library_root: data/libraries\nenable_complex_planner: "false"',
                )
            )

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.load(
                BASE_CONFIG.replace(
                    "library_root: data/libraries",
                    'library_root: data/libraries\nenable_fact_ledger: "false"',
                )
            )


if __name__ == "__main__":
    unittest.main()
