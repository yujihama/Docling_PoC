from __future__ import annotations

import base64
import json
import os
import threading
from io import BytesIO
from typing import Any

import requests
from docling.datamodel.base_models import (
    InputFormat,
    OpenAiApiResponse,
    OpenAiChatMessage,
    VlmStopReason,
)
from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
from docling.datamodel.pipeline_options_vlm_model import ResponseFormat
from docling.datamodel.stage_model_specs import VlmModelSpec
from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions, VlmEngineType
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.models.inference_engines.vlm import api_openai_compatible_engine
from docling.pipeline.vlm_pipeline import VlmPipeline


DEFAULT_VLM_MAX_COMPLETION_TOKENS = int(
    os.getenv("OPENAI_VLM_MAX_COMPLETION_TOKENS", "12000")
)
DEFAULT_VLM_REASONING_EFFORT = os.getenv("OPENAI_VLM_REASONING_EFFORT", "none")
DEFAULT_VLM_TIMEOUT_SECONDS = float(os.getenv("OPENAI_VLM_TIMEOUT_SECONDS", "180"))
DEFAULT_VLM_SCALE = float(os.getenv("OPENAI_VLM_SCALE", "2.0"))
DEFAULT_VLM_PROMPT_VARIANT = os.getenv(
    "OPENAI_VLM_PROMPT_VARIANT", "strict_preserve"
)
DEFAULT_VLM_IMAGE_DETAIL = os.getenv("OPENAI_VLM_IMAGE_DETAIL", "auto")
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
SUPPORTED_VLM_PROMPT_VARIANTS = {"strict_preserve", "table_first"}
SUPPORTED_VLM_IMAGE_DETAILS = {"auto", "low", "high"}

VLM_MARKDOWN_PROMPT = """Convert this document page to GitHub-flavored Markdown.

Rules:
- Output only Markdown. Do not wrap the answer in code fences.
- Preserve reading order, headings, paragraphs, lists, footnotes, and visible labels.
- Convert every visible table into a Markdown table.
- Preserve numbers, dates, IDs, symbols, punctuation, units, and column names exactly.
- If a cell is blank, leave it blank.
- A blank table cell means there are no visible characters, numbers, or symbols in that cell; keep it empty and do not write [[読み取り不明]].
- If any visible character, number, date, ID, amount, or table cell is unreadable, do not guess. Write [[読み取り不明]].
- Use [[読み取り不明]] only when visible content exists but cannot be read.
- Do not summarize or invent missing content.
"""

VLM_HTML_PROMPT = """Convert this document page to clean HTML.

Rules:
- Output only an HTML fragment. Do not wrap the answer in code fences.
- Preserve reading order, headings, paragraphs, lists, footnotes, and visible labels.
- Convert every visible table into a semantic <table> with <thead>, <tbody>, <tr>, <th>, and <td> where appropriate.
- Preserve numbers, dates, IDs, symbols, punctuation, units, and column names exactly.
- If a cell is blank, leave it blank.
- A blank table cell means there are no visible characters, numbers, or symbols in that cell; keep it empty and do not write [[読み取り不明]].
- If any visible character, number, date, ID, amount, or table cell is unreadable, do not guess. Write [[読み取り不明]].
- Use [[読み取り不明]] only when visible content exists but cannot be read.
- Do not summarize or invent missing content.
"""

VLM_TABLE_FIRST_MARKDOWN_SUFFIX = """
Table priority:
- Prioritize table fidelity over prose style when there is a conflict.
- Preserve every table row and column even when cells are visually sparse.
- Keep merged or grouped headers visible by repeating the visible header text in the Markdown table when needed.
- Do not collapse adjacent numeric columns.
"""

VLM_TABLE_FIRST_HTML_SUFFIX = """
Table priority:
- Prioritize table fidelity over prose style when there is a conflict.
- Preserve every table row and column even when cells are visually sparse.
- Use colspan or rowspan only when it is visibly present; otherwise keep a rectangular table.
- Do not collapse adjacent numeric columns.
"""


_DOCLING_OPENAI_PATCHED = False
_VLM_USAGE_LOCAL = threading.local()


def _current_vlm_usage_events() -> list[dict[str, Any]]:
    events = getattr(_VLM_USAGE_LOCAL, "events", None)
    if events is None:
        events = []
        _VLM_USAGE_LOCAL.events = events
    return events


def supports_reasoning_effort(model: str) -> bool:
    return model.startswith("gpt-5")


def resolve_response_format(response_format: str | ResponseFormat) -> ResponseFormat:
    if isinstance(response_format, ResponseFormat):
        return response_format
    normalized = response_format.strip().lower()
    if normalized == "markdown":
        return ResponseFormat.MARKDOWN
    if normalized == "html":
        return ResponseFormat.HTML
    raise ValueError(f"Unsupported VLM response format: {response_format}")


def resolve_prompt_variant(prompt_variant: str) -> str:
    normalized = prompt_variant.strip().lower()
    if normalized not in SUPPORTED_VLM_PROMPT_VARIANTS:
        raise ValueError(f"Unsupported VLM prompt variant: {prompt_variant}")
    return normalized


def resolve_image_detail(image_detail: str | None) -> str | None:
    if image_detail is None:
        return None
    normalized = image_detail.strip().lower()
    if normalized not in SUPPORTED_VLM_IMAGE_DETAILS:
        raise ValueError(f"Unsupported VLM image detail: {image_detail}")
    return normalized


def prompt_for_response_format(
    response_format: ResponseFormat,
    prompt_variant: str = DEFAULT_VLM_PROMPT_VARIANT,
) -> str:
    resolved_variant = resolve_prompt_variant(prompt_variant)
    if response_format == ResponseFormat.HTML:
        base_prompt = VLM_HTML_PROMPT
        suffix = VLM_TABLE_FIRST_HTML_SUFFIX
    else:
        base_prompt = VLM_MARKDOWN_PROMPT
        suffix = VLM_TABLE_FIRST_MARKDOWN_SUFFIX
    if resolved_variant == "table_first":
        return f"{base_prompt.rstrip()}\n{suffix.strip()}\n"
    return base_prompt


def clear_vlm_usage_events() -> None:
    _current_vlm_usage_events().clear()


def get_vlm_usage_events() -> list[dict[str, Any]]:
    return list(_current_vlm_usage_events())


def patch_docling_openai_gpt5_params() -> None:
    """Make Docling's OpenAI API calls strict and GPT-5-compatible."""
    global _DOCLING_OPENAI_PATCHED
    if _DOCLING_OPENAI_PATCHED:
        return

    original_streaming_request = api_openai_compatible_engine.api_image_request_streaming

    def should_drop_temperature(params: dict[str, Any]) -> bool:
        model = str(params.get("model", ""))
        return model.startswith("gpt-5")

    def extract_generated_text(message: OpenAiChatMessage) -> str:
        if message.content is not None:
            return message.content.strip()
        for tool_call in message.tool_calls or []:
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                continue
            try:
                payload = json.loads(arguments)
            except json.JSONDecodeError:
                return arguments.strip()
            if isinstance(payload, dict):
                text = payload.get("text")
                if isinstance(text, str):
                    return text.strip()
        return ""

    def map_stop_reason(finish_reason: str | None) -> VlmStopReason:
        if finish_reason == "content_filter":
            return VlmStopReason.CONTENT_FILTERED
        if finish_reason == "length":
            return VlmStopReason.LENGTH
        return VlmStopReason.END_OF_SEQUENCE

    def patched_request(
        image: Any,
        prompt: str,
        url: Any,
        timeout: float = 20,
        headers: dict[str, str] | None = None,
        **params: Any,
    ) -> tuple[str, int | None, VlmStopReason]:
        if should_drop_temperature(params):
            params.pop("temperature", None)
        image_detail = resolve_image_detail(params.pop("image_detail", None))

        img_io = BytesIO()
        image = image.copy().convert("RGBA")
        image.save(img_io, "PNG")
        image_base64 = base64.b64encode(img_io.getvalue()).decode("utf-8")
        image_url: dict[str, Any] = {"url": f"data:image/png;base64,{image_base64}"}
        if image_detail:
            image_url["detail"] = image_detail
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": image_url,
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            **params,
        }

        response = requests.post(
            str(url),
            headers=headers or {},
            json=payload,
            timeout=timeout,
        )
        if not response.ok:
            try:
                error = response.json().get("error", {})
            except Exception:
                error = {"message": response.text}
            message = error.get("message", response.text)
            code = error.get("code") or error.get("type") or response.status_code
            raise RuntimeError(f"OpenAI VLM API error ({code}): {message}")

        api_resp = OpenAiApiResponse.model_validate_json(response.text)
        choice = api_resp.choices[0]
        generated_text = extract_generated_text(choice.message)
        usage = api_resp.usage
        usage_payload: dict[str, Any] = {}
        if usage is not None:
            usage_payload = usage.model_dump(mode="json")
        _current_vlm_usage_events().append(
            {
                "model": params.get("model"),
                "image_detail": image_detail,
                "prompt_chars": len(prompt),
                "generated_chars": len(generated_text),
                "finish_reason": choice.finish_reason,
                **usage_payload,
            }
        )
        return (
            generated_text,
            usage.total_tokens if usage is not None else None,
            map_stop_reason(choice.finish_reason),
        )

    def patched_streaming_request(*args: Any, **kwargs: Any) -> Any:
        if should_drop_temperature(kwargs):
            kwargs.pop("temperature", None)
        return original_streaming_request(*args, **kwargs)

    api_openai_compatible_engine.api_image_request = patched_request
    api_openai_compatible_engine.api_image_request_streaming = patched_streaming_request
    _DOCLING_OPENAI_PATCHED = True


def check_openai_chat_access(
    model: str,
    reasoning_effort: str = DEFAULT_VLM_REASONING_EFFORT,
    timeout_seconds: float = 30,
) -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env.")

    params: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly OK."}],
        "max_completion_tokens": 32,
    }
    if reasoning_effort and supports_reasoning_effort(model):
        params["reasoning_effort"] = reasoning_effort

    response = requests.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=params,
        timeout=timeout_seconds,
    )
    if response.ok:
        return

    try:
        error = response.json().get("error", {})
    except Exception:
        error = {"message": response.text}
    message = error.get("message", response.text)
    code = error.get("code") or error.get("type") or response.status_code
    raise RuntimeError(f"OpenAI API preflight failed ({code}): {message}")


def build_openai_vlm_converter(
    model: str,
    max_completion_tokens: int = DEFAULT_VLM_MAX_COMPLETION_TOKENS,
    reasoning_effort: str = DEFAULT_VLM_REASONING_EFFORT,
    timeout_seconds: float = DEFAULT_VLM_TIMEOUT_SECONDS,
    scale: float = DEFAULT_VLM_SCALE,
    response_format: str | ResponseFormat = ResponseFormat.MARKDOWN,
    prompt_variant: str = DEFAULT_VLM_PROMPT_VARIANT,
    image_detail: str | None = DEFAULT_VLM_IMAGE_DETAIL,
    max_size: int | None = None,
) -> DocumentConverter:
    patch_docling_openai_gpt5_params()
    resolved_response_format = resolve_response_format(response_format)
    resolved_prompt_variant = resolve_prompt_variant(prompt_variant)
    resolved_image_detail = resolve_image_detail(image_detail)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env.")

    params: dict[str, Any] = {
        "model": model,
        "max_completion_tokens": max_completion_tokens,
    }
    if resolved_image_detail:
        params["image_detail"] = resolved_image_detail
    if reasoning_effort and supports_reasoning_effort(model):
        params["reasoning_effort"] = reasoning_effort

    engine_options = ApiVlmEngineOptions(
        engine_type=VlmEngineType.API_OPENAI,
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=timeout_seconds,
        concurrency=1,
    )
    vlm_options = VlmConvertOptions(
        engine_options=engine_options,
        model_spec=VlmModelSpec(
            name=f"OpenAI {model}",
            default_repo_id="openai",
            prompt=prompt_for_response_format(
                resolved_response_format, resolved_prompt_variant
            ),
            response_format=resolved_response_format,
            max_new_tokens=max_completion_tokens,
        ),
        scale=scale,
        max_size=max_size,
        batch_size=1,
    )
    pipeline_options = VlmPipelineOptions(
        enable_remote_services=True,
        vlm_options=vlm_options,
        generate_page_images=True,
        images_scale=scale,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=pipeline_options,
            )
        }
    )
