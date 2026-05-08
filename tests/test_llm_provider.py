from __future__ import annotations

import unittest

from docling_openai_vlm import (
    azure_chat_completions_url,
    resolve_llm_provider_request,
)


class LlmProviderTests(unittest.TestCase):
    def test_openai_provider_request_uses_bearer_auth(self) -> None:
        request = resolve_llm_provider_request(
            provider="openai",
            api_key="openai-key",
            model="gpt-5.2",
        )

        self.assertEqual(request.provider, "openai")
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer openai-key",
        )
        self.assertFalse(request.strip_model_from_payload)

    def test_azure_provider_request_uses_deployment_url_and_api_key(self) -> None:
        request = resolve_llm_provider_request(
            provider="azure",
            api_key="azure-key",
            model="gpt-5.2",
            azure_endpoint="https://example.openai.azure.com/",
            azure_deployment="deployment a",
            azure_api_version="2024-10-21",
        )

        self.assertEqual(request.provider, "azure")
        self.assertEqual(request.headers["api-key"], "azure-key")
        self.assertTrue(request.strip_model_from_payload)
        self.assertEqual(
            request.url,
            "https://example.openai.azure.com/openai/deployments/"
            "deployment%20a/chat/completions?api-version=2024-10-21",
        )

    def test_azure_url_helper_trims_endpoint(self) -> None:
        self.assertEqual(
            azure_chat_completions_url(
                endpoint="https://example.openai.azure.com/",
                deployment="dep",
                api_version="2024-10-21",
            ),
            "https://example.openai.azure.com/openai/deployments/"
            "dep/chat/completions?api-version=2024-10-21",
        )


if __name__ == "__main__":
    unittest.main()
