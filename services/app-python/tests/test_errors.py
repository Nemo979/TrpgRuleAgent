import unittest

import httpx
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from trpg_app.errors import classify_chat_error


class ChatErrorClassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.request = httpx.Request("POST", "https://model.example/v1/chat/completions")

    def response(self, status: int) -> httpx.Response:
        return httpx.Response(status, request=self.request)

    def test_timeout_and_rate_limit_are_retryable(self) -> None:
        timeout = classify_chat_error(APITimeoutError(self.request))
        limited = classify_chat_error(
            RateLimitError("limited", response=self.response(429), body=None)
        )

        self.assertEqual(timeout.code, "model_timeout")
        self.assertTrue(timeout.retryable)
        self.assertEqual(limited.code, "model_rate_limited")
        self.assertTrue(limited.retryable)

    def test_authentication_error_is_not_retryable(self) -> None:
        result = classify_chat_error(
            AuthenticationError("invalid", response=self.response(401), body=None)
        )

        self.assertEqual(result.code, "model_configuration_error")
        self.assertFalse(result.retryable)

    def test_server_error_is_retryable_without_exposing_provider_message(self) -> None:
        result = classify_chat_error(
            InternalServerError(
                "provider response includes sensitive details",
                response=self.response(500),
                body=None,
            )
        )

        self.assertEqual(result.code, "model_unavailable")
        self.assertTrue(result.retryable)
        self.assertNotIn("sensitive", result.message)


if __name__ == "__main__":
    unittest.main()
