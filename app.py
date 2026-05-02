from __future__ import annotations

import os
import tempfile
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

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2")
MAX_LLM_CONTEXT_CHARS = int(os.getenv("MAX_LLM_CONTEXT_CHARS", "40000"))

ConversionMode = Literal["standard", "openai_vlm"]
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
        ["Standard Docling", "OpenAI VLM"],
        index=0,
        help=(
            "OpenAI VLM sends rendered PDF page images to the OpenAI Chat Completions API "
            "through Docling's VlmPipeline."
        ),
    )
    conversion_mode: ConversionMode = (
        "openai_vlm" if conversion_label == "OpenAI VLM" else "standard"
    )
    model = st.text_input("OpenAI model", value=DEFAULT_MODEL)

    if conversion_mode == "openai_vlm":
        st.caption(
            "VLM mode uses Docling VlmPipeline and OpenAI's Chat Completions endpoint."
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
    else:
        max_completion_tokens = DEFAULT_VLM_MAX_COMPLETION_TOKENS
        reasoning_effort = DEFAULT_VLM_REASONING_EFFORT
        response_format = "markdown"
        timeout_seconds = DEFAULT_VLM_TIMEOUT_SECONDS
        vlm_scale = DEFAULT_VLM_SCALE

uploaded_file = st.file_uploader("Upload a PDF", type=["pdf"])

if uploaded_file is None:
    st.info("Upload a PDF to extract Markdown and tables with the selected pipeline.")
    st.stop()

if conversion_mode == "openai_vlm" and not os.getenv("OPENAI_API_KEY"):
    st.error("OPENAI_API_KEY is not set in .env. VLM mode requires an OpenAI API key.")
    st.stop()

pdf_bytes = uploaded_file.getvalue()

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
