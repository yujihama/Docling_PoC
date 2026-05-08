from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from routing_pipeline import RoutedPdfOptions, safe_options_dict
from settings import MappingSettingsSource, load_settings, settings_to_safe_dict


class SettingsTests(unittest.TestCase):
    def write_config(self, directory: Path) -> Path:
        config_path = directory / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[models]",
                    'primary = "model-from-config"',
                    'secondary = "secondary-from-config"',
                    'table_vlm = "table-from-config"',
                    "",
                    "[vlm]",
                    "max_completion_tokens = 321",
                    'reasoning_effort = "low"',
                    "timeout_seconds = 42.5",
                    "scale = 1.5",
                    "",
                    "[routing]",
                    "max_parallel_table_groups = 3",
                    "enable_table_vlm_fallback = true",
                    "",
                    "[outputs]",
                    'root = "custom_outputs"',
                    'routing_runs_subdir = "routing_runs"',
                    "save_outputs = false",
                    "",
                    "[openai]",
                    'name = "openai"',
                    "max_retries = 5",
                    "initial_backoff_seconds = 0.25",
                ]
            ),
            encoding="utf-8",
        )
        return config_path

    @patch("settings.load_dotenv", lambda *args, **kwargs: None)
    def test_load_settings_uses_toml_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(Path(tmpdir))
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(config_path)

        self.assertEqual(settings.models.primary, "model-from-config")
        self.assertEqual(settings.models.secondary, "secondary-from-config")
        self.assertEqual(settings.models.table_vlm, "table-from-config")
        self.assertEqual(settings.vlm.max_completion_tokens, 321)
        self.assertEqual(settings.routing.max_parallel_table_groups, 3)
        self.assertTrue(settings.routing.enable_table_vlm_fallback)
        self.assertFalse(settings.outputs.save_outputs)
        self.assertTrue(str(settings.outputs.root).endswith("custom_outputs"))
        self.assertEqual(settings.outputs.routing_runs_subdir, "routing_runs")
        self.assertEqual(settings.openai.max_retries, 5)
        self.assertEqual(settings.openai.initial_backoff_seconds, 0.25)
        self.assertEqual(settings.config_sources, [str(config_path)])

    @patch("settings.load_dotenv", lambda *args, **kwargs: None)
    def test_environment_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config_path = self.write_config(tmp_path)
            output_root = tmp_path / "env_outputs"
            env = {
                "OPENAI_MODEL": "model-from-env",
                "OPENAI_SECONDARY_MODEL": "secondary-from-env",
                "OPENAI_VLM_MAX_COMPLETION_TOKENS": "999",
                "OPENAI_VLM_REASONING_EFFORT": "medium",
                "OPENAI_VLM_TIMEOUT_SECONDS": "88",
                "OPENAI_VLM_SCALE": "2.5",
                "DOCLING_OUTPUT_ROOT": str(output_root),
                "DOCLING_SAVE_OUTPUTS": "true",
                "OPENAI_MAX_RETRIES": "7",
                "OPENAI_INITIAL_BACKOFF_SECONDS": "1.75",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_settings(config_path)

        self.assertEqual(settings.models.primary, "model-from-env")
        self.assertEqual(settings.models.secondary, "secondary-from-env")
        self.assertEqual(settings.vlm.max_completion_tokens, 999)
        self.assertEqual(settings.vlm.reasoning_effort, "medium")
        self.assertEqual(settings.vlm.timeout_seconds, 88.0)
        self.assertEqual(settings.vlm.scale, 2.5)
        self.assertEqual(settings.outputs.root, output_root)
        self.assertTrue(settings.outputs.save_outputs)
        self.assertEqual(settings.openai.max_retries, 7)
        self.assertEqual(settings.openai.initial_backoff_seconds, 1.75)

    def test_mapping_source_supports_azure_provider_without_dotenv(self) -> None:
        source = MappingSettingsSource(
            {
                "DOCLING_LLM_PROVIDER": "azure",
                "AZURE_OPENAI_API_KEY": "secret-key",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
                "AZURE_OPENAI_DEPLOYMENT": "deployment-a",
                "AZURE_OPENAI_API_VERSION": "2024-10-21",
                "OPENAI_MODEL": "gpt-5.2",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(Path(tmpdir))
            settings = load_settings(config_path, settings_source=source)

        self.assertEqual(settings.provider.name, "azure")
        self.assertEqual(settings.provider.api_key, "secret-key")
        self.assertEqual(
            settings.provider.azure_endpoint,
            "https://example.openai.azure.com",
        )
        self.assertEqual(settings.provider.azure_deployment, "deployment-a")
        payload = settings_to_safe_dict(settings)
        self.assertEqual(payload["provider"]["api_key"], "***")

    @patch("settings.load_dotenv", lambda *args, **kwargs: None)
    def test_safe_dict_and_routed_options_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(Path(tmpdir))
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(config_path)

        payload = settings_to_safe_dict(settings)
        options = RoutedPdfOptions.from_settings(settings)

        self.assertIsInstance(payload["outputs"]["root"], str)
        self.assertEqual(options.model, "model-from-config")
        self.assertEqual(options.secondary_model, "secondary-from-config")
        self.assertEqual(options.table_vlm_model, "table-from-config")
        self.assertEqual(options.max_completion_tokens, 321)
        self.assertEqual(options.max_parallel_table_groups, 3)
        self.assertEqual(options.output_root, str(settings.outputs.root))
        self.assertEqual(options.routing_runs_subdir, "routing_runs")
        self.assertEqual(options.openai_max_retries, 5)
        self.assertIsNone(options.llm_api_key)
        secret_options = RoutedPdfOptions.from_settings(settings, llm_api_key="secret")
        self.assertEqual(safe_options_dict(secret_options)["llm_api_key"], "***")


if __name__ == "__main__":
    unittest.main()
