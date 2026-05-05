from __future__ import annotations

import os
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import streamlit as st
from docling.document_converter import DocumentConverter
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(override=True)

from docling_openai_vlm import (
    DEFAULT_VLM_MAX_COMPLETION_TOKENS,
    DEFAULT_VLM_REASONING_EFFORT,
    DEFAULT_VLM_SCALE,
    DEFAULT_VLM_TIMEOUT_SECONDS,
    build_openai_vlm_converter,
    check_openai_chat_access,
)
from routing_pipeline import RoutedPdfOptions, run_routed_pdf

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2")
DEFAULT_SECONDARY_MODEL = os.getenv("OPENAI_SECONDARY_MODEL", "gpt-5.4-mini")
MAX_LLM_CONTEXT_CHARS = int(os.getenv("MAX_LLM_CONTEXT_CHARS", "40000"))

ConversionMode = Literal["standard", "openai_vlm", "routed"]
ResponseFormatMode = Literal["markdown", "html"]


st.set_page_config(
    page_title="Docling PDF Table Extractor",
    layout="wide",
)


def get_openai_client() -> OpenAI | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return OpenAI()


def build_converter(
    mode: ConversionMode,
    model: str,
    max_completion_tokens: int,
    reasoning_effort: str,
    timeout_seconds: float,
    scale: float,
    response_format: ResponseFormatMode,
) -> DocumentConverter:
    if mode == "openai_vlm":
        return build_openai_vlm_converter(
            model,
            max_completion_tokens,
            reasoning_effort,
            timeout_seconds,
            scale,
            response_format,
        )
    return DocumentConverter()


@st.cache_data(show_spinner=False)
def convert_pdf(
    pdf_bytes: bytes,
    filename: str,
    mode: ConversionMode,
    model: str,
    max_completion_tokens: int,
    reasoning_effort: str,
    timeout_seconds: float,
    scale: float,
    response_format: ResponseFormatMode,
) -> dict[str, Any]:
    suffix = Path(filename).suffix or ".pdf"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"uploaded{suffix}"
        input_path.write_bytes(pdf_bytes)

        if mode == "openai_vlm":
            check_openai_chat_access(
                model=model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=min(timeout_seconds, 60),
            )

        converter = build_converter(
            mode=mode,
            model=model,
            max_completion_tokens=max_completion_tokens,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            scale=scale,
            response_format=response_format,
        )
        result = converter.convert(input_path)
        document = result.document

        tables: list[dict[str, Any]] = []
        for table_index, table in enumerate(document.tables, start=1):
            dataframe: pd.DataFrame = table.export_to_dataframe(doc=document)
            tables.append(
                {
                    "index": table_index,
                    "rows": len(dataframe),
                    "columns": len(dataframe.columns),
                    "dataframe": dataframe,
                    "csv": dataframe.to_csv(index=False),
                    "html": table.export_to_html(doc=document),
                }
            )

        markdown = document.export_to_markdown()
        if mode == "openai_vlm" and not markdown.strip():
            raise RuntimeError(
                "OpenAI VLM returned no Markdown. Check the Streamlit log for the "
                "upstream API error; common causes are insufficient quota, an invalid "
                "model name, or unsupported API parameters."
            )

        return {
            "markdown": markdown,
            "table_count": len(tables),
            "tables": tables,
            "mode": mode,
            "model": model if mode == "openai_vlm" else None,
            "response_format": response_format if mode == "openai_vlm" else None,
        }


def build_llm_context(markdown: str, tables: list[dict[str, Any]]) -> str:
    chunks = ["# Extracted document markdown", markdown.strip()]

    if tables:
        chunks.append("# Extracted tables as CSV")
        for table in tables:
            chunks.append(f"## Table {table['index']}")
            chunks.append(table["csv"].strip())

    context = "\n\n".join(part for part in chunks if part)
    if len(context) <= MAX_LLM_CONTEXT_CHARS:
        return context

    return (
        context[:MAX_LLM_CONTEXT_CHARS]
        + "\n\n[Context truncated because MAX_LLM_CONTEXT_CHARS was reached.]"
    )


def ask_llm(client: OpenAI, model: str, prompt: str, context: str) -> str:
    response = client.responses.create(
        model=model,
        instructions=(
            "You analyze PDF content extracted by Docling. "
            "Answer in Japanese. Be precise about tables, column names, units, "
            "and uncertainty. If the extracted context does not contain enough "
            "information, say so clearly."
        ),
        input=f"{prompt}\n\n--- Extracted context ---\n{context}",
        store=False,
    )
    return response.output_text


st.title("Docling PDF Table Extractor")

with st.sidebar:
    st.header("Settings")
    conversion_label = st.radio(
        "Conversion pipeline",
        ["Standard Docling", "OpenAI VLM", "Routed OCR/VLM Reconcile"],
        index=0,
        help=(
            "OpenAI VLM sends rendered PDF page images to the OpenAI Chat Completions API "
            "through Docling's VlmPipeline."
        ),
    )
    if conversion_label == "OpenAI VLM":
        conversion_mode: ConversionMode = "openai_vlm"
    elif conversion_label == "Routed OCR/VLM Reconcile":
        conversion_mode = "routed"
    else:
        conversion_mode = "standard"
    model = st.text_input("OpenAI model", value=DEFAULT_MODEL)

    if conversion_mode in {"openai_vlm", "routed"}:
        st.caption(
            "VLM settings are used by OpenAI VLM mode and by IMAGE_RECONCILE / IMAGE_RECONCILE_APPEND pages."
        )
        max_completion_tokens = st.number_input(
            "VLM max completion tokens",
            min_value=1024,
            max_value=128000,
            value=DEFAULT_VLM_MAX_COMPLETION_TOKENS,
            step=1024,
        )
        reasoning_effort = st.selectbox(
            "VLM reasoning effort",
            ["none", "low", "medium", "high", "xhigh"],
            index=["none", "low", "medium", "high", "xhigh"].index(
                DEFAULT_VLM_REASONING_EFFORT
                if DEFAULT_VLM_REASONING_EFFORT
                in ["none", "low", "medium", "high", "xhigh"]
                else "none"
            ),
        )
        response_format: ResponseFormatMode = st.selectbox(
            "VLM response format",
            ["markdown", "html"],
            index=0,
        )
        prompt_variant = "strict_preserve"
        timeout_seconds = st.number_input(
            "VLM timeout seconds",
            min_value=30.0,
            max_value=900.0,
            value=DEFAULT_VLM_TIMEOUT_SECONDS,
            step=30.0,
        )
        vlm_scale = st.slider(
            "VLM image scale",
            min_value=0.5,
            max_value=3.0,
            value=DEFAULT_VLM_SCALE,
            step=0.5,
        )
        force_reconcile_pages = ""
        enable_embedded_visual_append = True
        if conversion_mode == "routed":
            compare_label = st.selectbox(
                "Reconcile comparison",
                ["OCR vs VLM", "VLM vs VLM"],
                index=0,
                help="Choose the source pair used for IMAGE_RECONCILE and embedded visual append pages.",
            )
            reconcile_compare_mode = (
                "vlm_vlm" if compare_label == "VLM vs VLM" else "ocr_vlm"
            )
            secondary_model = DEFAULT_SECONDARY_MODEL
            if reconcile_compare_mode == "vlm_vlm":
                secondary_model = st.text_input(
                    "Secondary OpenAI model",
                    value=DEFAULT_SECONDARY_MODEL,
                    help="Compared against the primary OpenAI model above.",
                )
            force_reconcile_pages = st.text_input(
                "Force IMAGE_RECONCILE pages",
                value="",
                placeholder="例: 2,5-7",
                help="Optional comma-separated pages/ranges to force OCR/VLM reconciliation.",
            )
            enable_embedded_visual_append = st.checkbox(
                "Detect embedded visual regions",
                value=True,
                help=(
                    "When a text-layer page contains a large low-text-overlap visual region, "
                    "run an additional OCR/VLM reconciliation pass for that page."
                ),
            )
            parallel_reconcile_candidates = st.checkbox(
                "Parallel reconcile candidates",
                value=True,
                help="Run OCR/VLM or primary/secondary VLM candidates concurrently for each reconciled page.",
            )
            max_parallel_table_groups = st.number_input(
                "Max parallel table groups",
                min_value=1,
                max_value=4,
                value=2,
                step=1,
                help="Runs independent standard Docling table groups concurrently. Higher values use more CPU and memory.",
            )
            use_coordinate_table_reconstruction = st.checkbox(
                "Coordinate grid reconstruction",
                value=False,
                help=(
                    "Try PDF line/text coordinates for text-table pages. "
                    "Column headers are not inferred; PDF grid rows are preserved under col_001, col_002, ..."
                ),
            )
            enable_table_vlm_fallback = st.checkbox(
                "Auto VLM fallback for tables",
                value=False,
                help=(
                    "When coordinate-grid reconstruction looks unreliable, rerun that page with "
                    "a table-focused OpenAI VLM model."
                ),
            )
            table_vlm_model = st.text_input(
                "Table VLM model",
                value="gpt-5.4-mini",
                help="Used for normal table VLM fallback.",
            )
            large_table_vlm_model = st.text_input(
                "Large table VLM model",
                value="gpt-5.4",
                help="Used when the table is wide, dense, large, or coordinate collapse is detected.",
            )
            table_vlm_prompt_variant = st.selectbox(
                "Table VLM prompt",
                ["table_first", "strict_preserve"],
                index=0,
            )
            table_vlm_reasoning_effort = st.selectbox(
                "Table VLM reasoning effort",
                ["none", "low", "medium", "high", "xhigh"],
                index=0,
            )
            enable_reconcile_table_fallback = st.checkbox(
                "Local table fallback on reconcile warnings",
                value=True,
                help=(
                    "When OCR/VLM or VLM/VLM reconciliation creates unknown cells or "
                    "detects table-structure mismatch, rerun only the reconciled page/crop "
                    "with a table-focused VLM and replace the table if it improves."
                ),
            )
            reconcile_table_fallback_model = st.text_input(
                "Reconcile table fallback model",
                value="gpt-5.4",
                help="Used only after reconcile warnings such as unknown cells or column mismatch.",
            )
            reconcile_table_fallback_prompt_variant = st.selectbox(
                "Reconcile table fallback prompt",
                ["table_first", "strict_preserve"],
                index=0,
            )
            reconcile_table_fallback_reasoning_effort = st.selectbox(
                "Reconcile table fallback reasoning effort",
                ["none", "low", "medium", "high", "xhigh"],
                index=0,
            )
            enable_vlm_coordinate_quality_check = st.checkbox(
                "Validate table VLM with coordinate evidence",
                value=True,
                help=(
                    "For TEXT_TABLE_VLM pages, compare VLM critical values with "
                    "coordinate-grid extraction and fallback or mask unsupported values."
                ),
            )
            enable_vlm_coordinate_auto_correct = st.checkbox(
                "Auto-correct table VLM cells from coordinates",
                value=True,
                help=(
                    "Only corrects uniquely aligned numeric cells where coordinate evidence "
                    "identifies a single replacement value."
                ),
            )
    else:
        max_completion_tokens = DEFAULT_VLM_MAX_COMPLETION_TOKENS
        reasoning_effort = DEFAULT_VLM_REASONING_EFFORT
        response_format = "markdown"
        prompt_variant = "strict_preserve"
        timeout_seconds = DEFAULT_VLM_TIMEOUT_SECONDS
        vlm_scale = DEFAULT_VLM_SCALE
        force_reconcile_pages = ""
        enable_embedded_visual_append = True
        parallel_reconcile_candidates = True
        max_parallel_table_groups = 2
        use_coordinate_table_reconstruction = False
        enable_table_vlm_fallback = False
        table_vlm_model = "gpt-5.4-mini"
        large_table_vlm_model = "gpt-5.4"
        table_vlm_prompt_variant = "table_first"
        table_vlm_reasoning_effort = "none"
        enable_reconcile_table_fallback = True
        reconcile_table_fallback_model = "gpt-5.4"
        reconcile_table_fallback_prompt_variant = "table_first"
        reconcile_table_fallback_reasoning_effort = "none"
        enable_vlm_coordinate_quality_check = True
        enable_vlm_coordinate_auto_correct = True
        reconcile_compare_mode = "ocr_vlm"
        secondary_model = DEFAULT_SECONDARY_MODEL

uploaded_file = st.file_uploader("Upload a PDF", type=["pdf"])

if uploaded_file is None:
    st.info("Upload a PDF to extract Markdown and tables with the selected pipeline.")
    st.stop()

if conversion_mode == "openai_vlm" and not os.getenv("OPENAI_API_KEY"):
    st.error("OPENAI_API_KEY is not set in .env. VLM mode requires an OpenAI API key.")
    st.stop()

pdf_bytes = uploaded_file.getvalue()


def parse_page_ranges(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            pages.update(range(int(start_text), int(end_text) + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def routed_table_dataframe(table: dict[str, Any]) -> pd.DataFrame:
    csv_text = str(table.get("csv") or "")
    if not csv_text.strip():
        return pd.DataFrame()
    return pd.read_csv(StringIO(csv_text))


if conversion_mode == "routed":
    suffix = Path(uploaded_file.name).suffix or ".pdf"
    progress_placeholder = st.empty()
    spinner_text = (
        "Running routed conversion with VLM/VLM reconciliation..."
        if reconcile_compare_mode == "vlm_vlm"
        else "Running routed conversion with OCR/VLM reconciliation..."
    )
    with st.spinner(spinner_text):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = Path(tmpdir) / f"uploaded{suffix}"
                input_path.write_bytes(pdf_bytes)
                routed = run_routed_pdf(
                    input_path,
                    options=RoutedPdfOptions(
                        model=model,
                        reconcile_compare_mode=reconcile_compare_mode,
                        secondary_model=secondary_model,
                        max_completion_tokens=int(max_completion_tokens),
                        reasoning_effort=reasoning_effort,
                        timeout_seconds=float(timeout_seconds),
                        vlm_scale=float(vlm_scale),
                        response_format=response_format,
                        prompt_variant=prompt_variant,
                        enable_embedded_visual_append=enable_embedded_visual_append,
                        parallel_reconcile_candidates=parallel_reconcile_candidates,
                        max_parallel_table_groups=int(max_parallel_table_groups),
                        use_coordinate_table_reconstruction=use_coordinate_table_reconstruction,
                        enable_table_vlm_fallback=enable_table_vlm_fallback,
                        table_vlm_model=table_vlm_model,
                        large_table_vlm_model=large_table_vlm_model,
                        table_vlm_prompt_variant=table_vlm_prompt_variant,
                        table_vlm_reasoning_effort=table_vlm_reasoning_effort,
                        enable_reconcile_table_fallback=enable_reconcile_table_fallback,
                        reconcile_table_fallback_model=reconcile_table_fallback_model,
                        reconcile_table_fallback_prompt_variant=reconcile_table_fallback_prompt_variant,
                        reconcile_table_fallback_reasoning_effort=reconcile_table_fallback_reasoning_effort,
                        enable_vlm_coordinate_quality_check=enable_vlm_coordinate_quality_check,
                        enable_vlm_coordinate_auto_correct=enable_vlm_coordinate_auto_correct,
                        force_reconcile_pages=parse_page_ranges(force_reconcile_pages),
                        save_outputs=True,
                    ),
                    progress_callback=lambda message: progress_placeholder.info(message),
                )
        except Exception as exc:
            st.error("Routed PDF conversion failed.")
            st.exception(exc)
            st.stop()
        finally:
            progress_placeholder.empty()

    markdown = routed["safe_markdown"]
    tables = routed["tables"]
    metadata = routed["metadata"]
    warnings = routed["warnings"]
    preflight = routed["preflight"]

    summary_cols = st.columns(6)
    summary_cols[0].metric("Safe Markdown chars", f"{len(markdown):,}")
    summary_cols[1].metric("Tables", len(tables))
    summary_cols[2].metric("Warnings", len(warnings))
    summary_cols[3].metric("Needs retry", metadata["warning_level_counts"].get("needs_retry", 0))
    summary_cols[4].metric(
        "Embedded visuals",
        metadata.get("extra_action_counts", {}).get("IMAGE_RECONCILE_APPEND", 0),
    )
    summary_cols[5].metric("Sec/page", metadata.get("seconds_per_page"))

    tab_safe, tab_raw, tab_routing, tab_warnings, tab_tables, tab_llm = st.tabs(
        ["Safe Markdown", "Raw Output", "Routing", "Warnings", "Tables", "Ask GPT"]
    )

    with tab_safe:
        st.caption(f"Run directory: {routed['run_dir']}")
        st.download_button(
            "Download Safe Markdown",
            data=markdown,
            file_name=f"{Path(uploaded_file.name).stem}-safe.md",
            mime="text/markdown",
        )
        st.text_area("Safe Markdown", value=markdown, height=600)

    with tab_raw:
        comparison = metadata.get("comparison", {})
        source_a_label = comparison.get("source_a_label", "ocr")
        source_b_label = comparison.get("source_b_label", "vlm")
        raw_tabs = st.tabs(["Raw merged", f"Raw {source_a_label}", f"Raw {source_b_label}"])
        with raw_tabs[0]:
            st.text_area("Raw merged Markdown", value=routed["raw_markdown"], height=500)
        with raw_tabs[1]:
            st.text_area(
                f"Raw {source_a_label} Markdown",
                value=routed["raw_ocr_markdown"],
                height=500,
            )
        with raw_tabs[2]:
            st.text_area(
                f"Raw {source_b_label} Markdown",
                value=routed["raw_vlm_markdown"],
                height=500,
            )

    with tab_routing:
        timing_cols = st.columns(2)
        with timing_cols[0]:
            st.caption("Candidate timing seconds")
            st.json(metadata.get("candidate_timing_seconds", {}))
        with timing_cols[1]:
            st.caption("Mode timing seconds")
            st.json(metadata.get("segment_timing_seconds_by_mode", {}))
        page_timings = metadata.get("page_timing_estimates", [])
        if page_timings:
            st.caption("Estimated page timings")
            st.dataframe(pd.DataFrame(page_timings), use_container_width=True)
        st.dataframe(pd.DataFrame(preflight), use_container_width=True)

    with tab_warnings:
        if not warnings:
            st.success("No warnings were emitted.")
        else:
            st.dataframe(pd.DataFrame(warnings), use_container_width=True)

    with tab_tables:
        if not tables:
            st.warning("No tables were detected as structured Docling tables.")
        else:
            for table in tables:
                st.subheader(f"Table {table['index']}")
                st.caption(
                    f"{table.get('rows')} rows x {table.get('columns')} columns, "
                    f"pages {table.get('source_start_page')}-{table.get('source_end_page')}, "
                    f"mode {table.get('mode')}"
                )
                dataframe = routed_table_dataframe(table)
                if dataframe.empty:
                    st.text(str(table.get("csv", "")))
                else:
                    st.dataframe(dataframe, use_container_width=True)

    with tab_llm:
        client = get_openai_client()
        if client is None:
            st.warning("Set OPENAI_API_KEY in .env to summarize or ask questions.")
            st.stop()

        context = build_llm_context(markdown, tables)
        col_summary, col_context = st.columns([1, 1])
        with col_summary:
            if st.button("Generate Summary", use_container_width=True):
                with st.spinner(f"Summarizing with {model}..."):
                    answer = ask_llm(
                        client,
                        model,
                        "文書全体を要約し、検出された表の内容を箇条書きで説明してください。",
                        context,
                    )
                st.markdown(answer)
        with col_context:
            st.caption(f"LLM context length: {len(context):,} characters")

        question = st.text_area(
            "Question about the document or tables",
            placeholder="例: 表1の主要な列と数値の意味を説明して",
            height=120,
        )
        if st.button("Ask", type="primary", use_container_width=True):
            if not question.strip():
                st.warning("Enter a question first.")
            else:
                with st.spinner(f"Answering with {model}..."):
                    answer = ask_llm(client, model, question.strip(), context)
                st.markdown(answer)
    st.stop()

spinner_text = (
    f"Converting with OpenAI VLM ({model}). This sends rendered PDF page images to OpenAI..."
    if conversion_mode == "openai_vlm"
    else "Converting with standard Docling..."
)
with st.spinner(spinner_text):
    try:
        extracted = convert_pdf(
            pdf_bytes,
            uploaded_file.name,
            conversion_mode,
            model,
            int(max_completion_tokens),
            reasoning_effort,
            float(timeout_seconds),
            float(vlm_scale),
            response_format,
        )
    except Exception as exc:
        st.error("PDF conversion failed.")
        st.exception(exc)
        st.stop()

markdown = extracted["markdown"]
tables = extracted["tables"]

summary_cols = st.columns(4)
summary_cols[0].metric("Markdown chars", f"{len(markdown):,}")
summary_cols[1].metric("Tables", extracted["table_count"])
summary_cols[2].metric("File size", f"{len(pdf_bytes) / 1024 / 1024:.2f} MB")
summary_cols[3].metric("Pipeline", "OpenAI VLM" if conversion_mode == "openai_vlm" else "Standard")

tab_markdown, tab_tables, tab_llm = st.tabs(["Markdown", "Tables", "Ask GPT"])

with tab_markdown:
    st.download_button(
        "Download Markdown",
        data=markdown,
        file_name=f"{Path(uploaded_file.name).stem}.md",
        mime="text/markdown",
    )
    st.text_area("Extracted Markdown", value=markdown, height=600)

with tab_tables:
    if not tables:
        st.warning("No tables were detected as structured Docling tables.")
    else:
        for table in tables:
            st.subheader(f"Table {table['index']}")
            st.caption(f"{table['rows']} rows x {table['columns']} columns")
            st.dataframe(table["dataframe"], use_container_width=True)

            table_download_cols = st.columns(2)
            table_download_cols[0].download_button(
                "Download CSV",
                data=table["csv"],
                file_name=f"{Path(uploaded_file.name).stem}-table-{table['index']}.csv",
                mime="text/csv",
                key=f"csv-{table['index']}",
            )
            table_download_cols[1].download_button(
                "Download HTML",
                data=table["html"],
                file_name=f"{Path(uploaded_file.name).stem}-table-{table['index']}.html",
                mime="text/html",
                key=f"html-{table['index']}",
            )

with tab_llm:
    client = get_openai_client()
    if client is None:
        st.warning("Set OPENAI_API_KEY in .env to summarize or ask questions.")
        st.stop()

    context = build_llm_context(markdown, tables)

    col_summary, col_context = st.columns([1, 1])
    with col_summary:
        if st.button("Generate Summary", use_container_width=True):
            with st.spinner(f"Summarizing with {model}..."):
                answer = ask_llm(
                    client,
                    model,
                    "文書全体を要約し、検出された表の内容を箇条書きで説明してください。",
                    context,
                )
            st.markdown(answer)

    with col_context:
        st.caption(f"LLM context length: {len(context):,} characters")

    question = st.text_area(
        "Question about the document or tables",
        placeholder="例: 表1の主要な列と数値の意味を説明して",
        height=120,
    )
    if st.button("Ask", type="primary", use_container_width=True):
        if not question.strip():
            st.warning("Enter a question first.")
        else:
            with st.spinner(f"Answering with {model}..."):
                answer = ask_llm(client, model, question.strip(), context)
            st.markdown(answer)
