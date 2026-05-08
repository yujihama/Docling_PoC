from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from routing_pipeline import ProgressCallback, RoutedPdfOptions, run_routed_pdf
from settings import (
    AppSettings,
    SettingsSource,
    load_settings,
    settings_to_safe_dict,
)


SettingsSourceInput = (
    SettingsSource | Mapping[str, str | None] | Callable[[str], str | None] | None
)
PdfInput = str | Path | bytes | bytearray | memoryview


def load_docling_poc_settings(
    *,
    config_path: str | Path | None = None,
    settings_source: SettingsSourceInput = None,
) -> AppSettings:
    return load_settings(config_path=config_path, settings_source=settings_source)


def routed_options_from_settings(
    settings: AppSettings,
    *,
    invocation: str = "python_api",
    overrides: dict[str, Any] | None = None,
) -> RoutedPdfOptions:
    return RoutedPdfOptions.from_settings(
        settings,
        resolved_settings=settings_to_safe_dict(settings),
        config_sources=list(settings.config_sources),
        invocation=invocation,
        **(overrides or {}),
    )


def convert_pdf(
    input_pdf: PdfInput,
    *,
    filename: str = "input.pdf",
    settings: AppSettings | None = None,
    config_path: str | Path | None = None,
    settings_source: SettingsSourceInput = None,
    options_overrides: dict[str, Any] | None = None,
    run_id: str | None = None,
    output_dir: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if isinstance(input_pdf, (bytes, bytearray, memoryview)):
        return convert_pdf_bytes(
            bytes(input_pdf),
            filename=filename,
            settings=settings,
            config_path=config_path,
            settings_source=settings_source,
            options_overrides=options_overrides,
            run_id=run_id,
            output_dir=output_dir,
            progress_callback=progress_callback,
        )
    return convert_pdf_file(
        input_pdf,
        settings=settings,
        config_path=config_path,
        settings_source=settings_source,
        options_overrides=options_overrides,
        run_id=run_id,
        output_dir=output_dir,
        progress_callback=progress_callback,
    )


def convert_pdf_file(
    pdf_path: str | Path,
    *,
    settings: AppSettings | None = None,
    config_path: str | Path | None = None,
    settings_source: SettingsSourceInput = None,
    options_overrides: dict[str, Any] | None = None,
    run_id: str | None = None,
    output_dir: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or load_docling_poc_settings(
        config_path=config_path,
        settings_source=settings_source,
    )
    options = routed_options_from_settings(
        resolved_settings,
        overrides=options_overrides,
    )
    return run_routed_pdf(
        Path(pdf_path),
        options=options,
        run_id=run_id,
        output_dir=Path(output_dir) if output_dir is not None else None,
        progress_callback=progress_callback,
    )


def convert_pdf_bytes(
    pdf_bytes: bytes,
    *,
    filename: str = "input.pdf",
    settings: AppSettings | None = None,
    config_path: str | Path | None = None,
    settings_source: SettingsSourceInput = None,
    options_overrides: dict[str, Any] | None = None,
    run_id: str | None = None,
    output_dir: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    suffix = Path(filename).suffix or ".pdf"
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"uploaded{suffix}"
        input_path.write_bytes(pdf_bytes)
        return convert_pdf_file(
            input_path,
            settings=settings,
            config_path=config_path,
            settings_source=settings_source,
            options_overrides=options_overrides,
            run_id=run_id,
            output_dir=output_dir,
            progress_callback=progress_callback,
        )
