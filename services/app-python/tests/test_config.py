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
        model = self.load().models[0]

        self.assertEqual(model.request_timeout_seconds, 45)
        self.assertEqual(model.max_retries, 2)

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


if __name__ == "__main__":
    unittest.main()
