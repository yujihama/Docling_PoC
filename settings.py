from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "docling_poc.default.toml"
DEFAULT_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"


class SettingsSource:
    name = "settings_source"

    def load(self) -> None:
        return None

    def get(self, name: str, default: str | None = None) -> str | None:
        return default


@dataclass
class DotEnvSettingsSource(SettingsSource):
    env_path: Path = ROOT / ".env"
    override: bool = True
    name: str = "dotenv"

    def load(self) -> None:
        load_dotenv(self.env_path, override=self.override)

    def get(self, name: str, default: str | None = None) -> str | None:
        return os.getenv(name, default)


@dataclass
class MappingSettingsSource(SettingsSource):
    values: Mapping[str, str | None]
    name: str = "mapping"

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self.values.get(name, default)
        return str(value) if value is not None else None


@dataclass
class CallableSettingsSource(SettingsSource):
    getter: Callable[[str], str | None]
    name: str = "callable"

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self.getter(name)
        return default if value is None else value


def coerce_settings_source(
    source: SettingsSource | Mapping[str, str | None] | Callable[[str], str | None] | None,
) -> SettingsSource:
    if source is None:
        return DotEnvSettingsSource()
    if isinstance(source, SettingsSource):
        return source
    if isinstance(source, Mapping):
        return MappingSettingsSource(source)
    return CallableSettingsSource(source)


@dataclass
class ModelSettings:
    primary: str = "gpt-5.2"
    secondary: str = "gpt-5.4-mini"
    table_vlm: str = "gpt-5.4-mini"
    large_table_vlm: str = "gpt-5.4"
    reconcile_table_fallback: str = "gpt-5.4"


@dataclass
class VlmSettings:
    max_completion_tokens: int = 12000
    reasoning_effort: str = "none"
    timeout_seconds: float = 180.0
    scale: float = 2.0
    response_format: str = "markdown"
    prompt_variant: str = "strict_preserve"
    image_detail: str | None = "auto"


@dataclass
class RoutingSettings:
    reconcile_compare_mode: str = "ocr_vlm"
    text_chars_threshold: int = 200
    text_quality_threshold: float = 0.80
    table_text_quality_threshold: float = 0.70
    table_score_threshold: float = 0.40
    complex_table_score_threshold: float = 0.60
    image_area_threshold: float = 0.40
    enable_embedded_visual_append: bool = True
    embedded_visual_min_area_ratio: float = 0.08
    embedded_visual_min_width_ratio: float = 0.30
    embedded_visual_min_height_ratio: float = 0.06
    embedded_visual_text_overlap_threshold: float = 0.05
    embedded_visual_complexity_threshold: float = 0.35
    embedded_visual_force_area_ratio: float = 0.25
    embedded_visual_crop_margin_points: float = 8.0
    parallel_reconcile_candidates: bool = True
    max_parallel_table_groups: int = 2
    use_coordinate_table_reconstruction: bool = False
    enable_table_vlm_fallback: bool = False
    table_vlm_prompt_variant: str = "table_first"
    table_vlm_reasoning_effort: str = "none"
    enable_reconcile_table_fallback: bool = True
    reconcile_table_fallback_prompt_variant: str = "table_first"
    reconcile_table_fallback_reasoning_effort: str = "none"
    enable_vlm_coordinate_quality_check: bool = True
    enable_vlm_coordinate_auto_correct: bool = True
    coordinate_min_span_coverage: float = 0.98
    coordinate_max_cell_chars: int = 160
    coordinate_max_cell_char_ratio: float = 8.0
    table_vlm_large_min_columns: int = 12
    table_vlm_large_min_area_ratio: float = 0.60


@dataclass
class OutputSettings:
    root: Path = ROOT / "outputs"
    routing_runs_subdir: str = "docling_routing_runs"
    benchmark_subdir: str = "docling_benchmark"
    poc_runs_subdir: str = "docling_poc_runs"
    save_outputs: bool = True

    @property
    def routing_runs_dir(self) -> Path:
        return self.root / self.routing_runs_subdir

    @property
    def benchmark_dir(self) -> Path:
        return self.root / self.benchmark_subdir

    @property
    def poc_runs_dir(self) -> Path:
        return self.root / self.poc_runs_subdir


@dataclass
class RuntimeSettings:
    max_llm_context_chars: int = 40000
    streamlit_port: int = 8501


@dataclass
class ProviderSettings:
    name: str = "openai"
    api_key: str | None = None
    chat_completions_url: str = DEFAULT_OPENAI_CHAT_COMPLETIONS_URL
    azure_endpoint: str | None = None
    azure_deployment: str | None = None
    azure_api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    max_retries: int = 2
    initial_backoff_seconds: float = 1.0


@dataclass
class AppSettings:
    models: ModelSettings = field(default_factory=ModelSettings)
    vlm: VlmSettings = field(default_factory=VlmSettings)
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    outputs: OutputSettings = field(default_factory=OutputSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    config_sources: list[str] = field(default_factory=list)

    @property
    def openai(self) -> ProviderSettings:
        return self.provider


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _settings_kwargs(data: dict[str, Any], section_name: str, settings_type: type) -> dict[str, Any]:
    allowed = {item.name for item in fields(settings_type)}
    return {
        key: value
        for key, value in _section(data, section_name).items()
        if key in allowed
    }


def _source_get(source: SettingsSource, name: str, default: str | None = None) -> str | None:
    value = source.get(name, default)
    return default if value is None else value


def _first_source_get(
    source: SettingsSource,
    names: tuple[str, ...],
    default: str | None = None,
) -> str | None:
    for name in names:
        value = source.get(name)
        if value not in (None, ""):
            return value
    return default


def _bool_env(source: SettingsSource, name: str, default: bool) -> bool:
    value = source.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(source: SettingsSource, name: str, default: int) -> int:
    value = source.get(name)
    return int(value) if value not in (None, "") else default


def _float_env(source: SettingsSource, name: str, default: float) -> float:
    value = source.get(name)
    return float(value) if value not in (None, "") else default


def _path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _load_toml(path: Path | None) -> tuple[dict[str, Any], list[str]]:
    if path is None or not path.exists():
        return {}, []
    with path.open("rb") as handle:
        return tomllib.load(handle), [str(path)]


def load_settings(
    config_path: str | Path | None = None,
    settings_source: SettingsSource | Mapping[str, str | None] | Callable[[str], str | None] | None = None,
) -> AppSettings:
    source = coerce_settings_source(settings_source)
    source.load()
    selected_config = config_path or source.get("DOCLING_POC_CONFIG")
    selected_path = _path(selected_config) if selected_config else DEFAULT_CONFIG_PATH
    data, sources = _load_toml(selected_path)

    models = ModelSettings(**_settings_kwargs(data, "models", ModelSettings))
    vlm = VlmSettings(**_settings_kwargs(data, "vlm", VlmSettings))
    routing = RoutingSettings(**_settings_kwargs(data, "routing", RoutingSettings))
    outputs = OutputSettings(**_settings_kwargs(data, "outputs", OutputSettings))
    runtime = RuntimeSettings(**_settings_kwargs(data, "runtime", RuntimeSettings))
    provider = ProviderSettings(
        **{
            **_settings_kwargs(data, "openai", ProviderSettings),
            **_settings_kwargs(data, "provider", ProviderSettings),
        }
    )

    models.primary = _source_get(source, "OPENAI_MODEL", models.primary) or models.primary
    models.secondary = (
        _source_get(source, "OPENAI_SECONDARY_MODEL", models.secondary) or models.secondary
    )
    vlm.max_completion_tokens = _int_env(
        source,
        "OPENAI_VLM_MAX_COMPLETION_TOKENS",
        int(vlm.max_completion_tokens),
    )
    vlm.reasoning_effort = (
        _source_get(source, "OPENAI_VLM_REASONING_EFFORT", vlm.reasoning_effort)
        or vlm.reasoning_effort
    )
    vlm.timeout_seconds = _float_env(
        source, "OPENAI_VLM_TIMEOUT_SECONDS", float(vlm.timeout_seconds)
    )
    vlm.scale = _float_env(source, "OPENAI_VLM_SCALE", float(vlm.scale))
    vlm.prompt_variant = (
        _source_get(source, "OPENAI_VLM_PROMPT_VARIANT", vlm.prompt_variant)
        or vlm.prompt_variant
    )
    vlm.image_detail = _source_get(source, "OPENAI_VLM_IMAGE_DETAIL", vlm.image_detail or "")
    runtime.max_llm_context_chars = _int_env(
        source,
        "MAX_LLM_CONTEXT_CHARS",
        int(runtime.max_llm_context_chars),
    )
    runtime.streamlit_port = _int_env(source, "STREAMLIT_PORT", int(runtime.streamlit_port))
    outputs.root = _path(
        _source_get(source, "DOCLING_OUTPUT_ROOT", str(outputs.root)) or outputs.root
    )
    outputs.save_outputs = _bool_env(source, "DOCLING_SAVE_OUTPUTS", bool(outputs.save_outputs))

    provider.name = (
        _first_source_get(source, ("DOCLING_LLM_PROVIDER", "LLM_PROVIDER"), provider.name)
        or provider.name
    ).strip().lower()
    provider.api_key = _first_source_get(
        source,
        (
            "AZURE_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        )
        if provider.name == "azure"
        else ("OPENAI_API_KEY",),
        provider.api_key,
    )
    provider.chat_completions_url = (
        _first_source_get(
            source,
            ("OPENAI_CHAT_COMPLETIONS_URL", "OPENAI_BASE_CHAT_COMPLETIONS_URL"),
            provider.chat_completions_url,
        )
        or provider.chat_completions_url
    )
    provider.azure_endpoint = _first_source_get(
        source,
        ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL"),
        provider.azure_endpoint,
    )
    provider.azure_deployment = _first_source_get(
        source,
        ("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_CHAT_DEPLOYMENT"),
        provider.azure_deployment,
    )
    provider.azure_api_version = (
        _source_get(source, "AZURE_OPENAI_API_VERSION", provider.azure_api_version)
        or provider.azure_api_version
    )
    provider.max_retries = _int_env(source, "OPENAI_MAX_RETRIES", int(provider.max_retries))
    provider.initial_backoff_seconds = _float_env(
        source,
        "OPENAI_INITIAL_BACKOFF_SECONDS",
        float(provider.initial_backoff_seconds),
    )

    return AppSettings(
        models=models,
        vlm=vlm,
        routing=routing,
        outputs=outputs,
        runtime=runtime,
        provider=provider,
        config_sources=sources,
    )


def settings_to_safe_dict(settings: AppSettings) -> dict[str, Any]:
    payload = asdict(settings)
    payload["outputs"]["root"] = str(settings.outputs.root)
    if payload.get("provider", {}).get("api_key"):
        payload["provider"]["api_key"] = "***"
    return payload
