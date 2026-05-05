# Docling PoC

Streamlit proof of concept for PDF extraction with Docling.

The app supports three conversion paths:

- `Standard Docling`: Docling's default PDF pipeline. No LLM or VLM is used for conversion.
- `OpenAI VLM`: Docling's `VlmPipeline` renders each PDF page as an image, sends the page image plus a prompt to an OpenAI vision-capable model, then reparses the returned Markdown or HTML into a `DoclingDocument`.
- `Routed OCR/VLM Reconcile`: Runs a lightweight PDF preflight, routes text-layer pages to CPU-oriented Docling modes, and sends weak text-layer image pages through both OCR and VLM. If a good text-layer page still contains a large low-text-overlap embedded visual region, it keeps the text route and appends an OCR/VLM reconciliation pass for that page. OCR/VLM disagreements are emitted as warnings and unsafe values are masked as `[[読み取り不明]]` in the safe output.

The `Ask GPT` tab is separate from conversion. It sends already extracted Markdown and table CSV text to the OpenAI Responses API for summarization or Q&A.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set your OpenAI API key:

```env
OPENAI_API_KEY=sk-your-api-key
OPENAI_MODEL=gpt-5.2
MAX_LLM_CONTEXT_CHARS=40000
OPENAI_VLM_MAX_COMPLETION_TOKENS=12000
OPENAI_VLM_REASONING_EFFORT=none
OPENAI_VLM_TIMEOUT_SECONDS=180
OPENAI_VLM_SCALE=2.0
```

Do not commit `.env`. The repository includes only `.env.example`.

## Run The App

```powershell
streamlit run app.py
```

On Windows, this helper starts Streamlit on port `8501`:

```powershell
.\run_streamlit.cmd
```

Then open:

```text
http://localhost:8501/
```

## VLM Implementation Notes

In the OpenAI VLM mode, Docling is not using its standard OCR/table model stack as the main extractor. The flow is:

1. Load the PDF page.
2. Render each page to a page image.
3. Optionally resize the image with `scale` and `max_size`.
4. Send image plus prompt to the OpenAI Chat Completions API.
5. Ask the model to return either Markdown or HTML.
6. Reparse the returned text through Docling's Markdown or HTML backend.
7. Assemble the final `DoclingDocument`, including structured tables when Docling can infer them from the returned text.

This means VLM accuracy depends strongly on the selected OpenAI model, prompt, image scale, and output format.

The helper in `docling_openai_vlm.py` also patches Docling's OpenAI-compatible request path so that:

- GPT-5 family models do not receive unsupported `temperature` parameters.
- `reasoning_effort` is sent only for GPT-5 family models.
- upstream OpenAI API errors are raised clearly instead of becoming empty Markdown.

## Benchmark Commands

Generate the synthetic benchmark PDFs:

```powershell
python benchmarks\generate_complex_pdfs.py
```

Run standard Docling:

```powershell
python benchmarks\run_docling_benchmark.py
```

Run OpenAI VLM:

```powershell
python benchmarks\run_openai_vlm_benchmark.py --model gpt-5.4-mini --response-format markdown --scale 2.0
```

Run routed CPU-oriented processing with OCR/VLM reconciliation:

```powershell
python run_routed_pdf.py --pdf outputs\docling_benchmark\pdfs\case01_clean_financial_2p.pdf --run-id routed_case01
```

Useful routed options:

- `--force-reconcile-pages 2,5-7`: force selected pages through OCR/VLM reconciliation.
- `--disable-embedded-visual-append`: disable the extra OCR/VLM append pass for embedded visual regions on text-layer pages.
- `--embedded-visual-min-area-ratio`: adjust the minimum page-area ratio for embedded visual detection.
- `--model`: OpenAI model used for `IMAGE_RECONCILE` and `IMAGE_RECONCILE_APPEND` pages.
- `--compare-mode ocr-vlm|vlm-vlm`: compare OCR against the VLM model, or compare two VLM models.
- `--secondary-model`: second OpenAI model used when `--compare-mode vlm-vlm`.
- `--disable-parallel-reconcile-candidates`: run reconcile candidates sequentially instead of concurrently.
- `--max-parallel-table-groups`: maximum number of independent standard table groups to run concurrently.
- `--use-coordinate-table-reconstruction`: experimental PDF line/text-coordinate grid reconstruction for `TEXT_TABLE_ACCURATE` pages. Column headers are not inferred; PDF grid rows are preserved under `col_001`, `col_002`, ...
- `--vlm-scale`: rendered image scale for VLM.
- `--response-format`: `markdown` or `html`.

Routed outputs are written to:

```text
outputs/docling_routing_runs/<run_id>/
```

Key files:

- `safe_output.md`: user-facing output with low-confidence values masked as `[[読み取り不明]]`.
- `raw_output.md`: merged unmasked extraction output.
- `raw_ocr_output.md` and `raw_vlm_output.md`: source outputs for reconciled pages.
- `raw_candidate_a_output.md` and `raw_candidate_b_output.md`: comparison candidates. In `vlm-vlm` mode these are primary and secondary VLM outputs.
- `preflight.csv`: page routing signals and selected mode.
- `warnings.csv`: warning code, level, evidence, and suggested action. Text-layer table repair can emit `ROW_HEADER_SPAN_MISSED` or `TABLE_TEXT_COVERAGE_LOSS` when PDF text exists in a table region but Docling did not place it into structured cells.

`metadata.json` includes `candidate_timing_seconds`, `segment_timing_seconds_by_mode`, and `page_timing_estimates` for bottleneck analysis. `IMAGE_RECONCILE_APPEND` uses cropped embedded visual regions as the OCR/VLM input instead of rerunning the whole page.

Run case-level hybrid selection. This first runs Standard Docling for each case, then reruns the whole case with VLM only when the Docling case is marked low confidence:

```powershell
python benchmarks\run_hybrid_benchmark.py --limit 1 --model gpt-5.4-mini
```

## Broad PoC Matrix

The broad PoC runner keeps Standard Docling and OpenAI VLM results under a
run-specific directory:

```powershell
python benchmarks\run_poc_matrix.py --dry-run --matrix all
python benchmarks\run_poc_matrix.py --matrix standard --phase full
python benchmarks\run_poc_matrix.py --matrix vlm --phase pilot --budget-usd 30
python benchmarks\report_poc.py --run-id <run_id>
```

Outputs are written to:

```text
outputs/docling_poc_runs/<run_id>/
```

The runner regenerates the benchmark corpus before execution unless
`--skip-generate-corpus` is passed. The corpus now includes C01-C05, optional
local `case6.pdf` as C06 when present, and generated cases C07-C11 for Japanese
multi-column content, forms, skewed scans, irregular tables, and chart/formula
content.

Standard Docling focused mode covers these settings:

- `do_table_structure`: on/off.
- `table_structure_options.mode`: `fast` and `accurate`.
- `table_structure_options.do_cell_matching`: on/off.
- `do_ocr`, `ocr_options.force_full_page_ocr`, and `force_backend_text`.
- `images_scale=2.0` with page image generation.
- `ocr_batch_size`, `layout_batch_size`, and `table_batch_size` small/default/large profiles.

OpenAI VLM full mode covers:

- Models: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.2`.
- Response formats: `markdown`, `html`.
- Scale: `1.0`, `2.0`, `2.5`.
- Reasoning effort: `none`, `low`, `medium`.
- Prompt variants: `strict_preserve`, `table_first`.
- `max_completion_tokens=12000` fixed.

Pilot mode runs a smaller VLM probe set, estimates full-matrix cost from the
measured token usage, and records whether the full VLM phase is within the
`--budget-usd` gate.

Useful VLM benchmark options:

- `--model`: OpenAI model, for example `gpt-5.2`, `gpt-5.4`, or `gpt-5.4-mini`.
- `--response-format`: `markdown` or `html`.
- `--scale`: page image scaling before VLM inference.
- `--max-completion-tokens`: completion cap for long page outputs.
- `--reasoning-effort`: `none`, `low`, `medium`, `high`, or `xhigh` for GPT-5 models.
- `--timeout-seconds`: request timeout.
- `--limit`: run only the first N benchmark cases.

## Benchmark Results

All measurements below use 5 generated PDFs, 36 total pages. The score is a deterministic string-recall benchmark:

```text
0.35 * text_anchor_recall + 0.55 * table_cell_recall + 0.10 * table_detection_ratio
```

`table_cell_recall` and `structured_table_cell_recall` are measured against exported structured table text, not the full Markdown body. Additional case-level metrics include `table_detection_precision`, `table_detection_f1`, `duplicate_table_rate`, `over_detection_penalty`, `row_count_present_rate`, `column_count_present_rate`, `table_structure_present_rate`, `case_confidence_score`, and `low_confidence`.

The current hybrid benchmark is intentionally case-level: a low-confidence case is rerun as a whole with VLM. Page-level selection and page-only VLM reruns remain future work for the broader Issue #1 plan.

| Run | Success | Pages | Total sec | Sec/page | Mean overall |
|---|---:|---:|---:|---:|---:|
| Standard Docling | 5/5 | 36 | 142.697 | 3.964 | 0.9128 |
| gpt-5.2 markdown scale2.0 | 5/5 | 36 | 377.728 | 10.492 | 0.9922 |
| gpt-5.4-mini markdown scale2.0 | 5/5 | 36 | 200.868 | 5.580 | 0.9679 |
| gpt-5.4-mini markdown scale2.5 | 5/5 | 36 | 228.379 | 6.344 | 0.9651 |
| gpt-5.4-mini html scale2.0 | 5/5 | 36 | 311.071 | 8.641 | 0.9692 |
| gpt-4.1 markdown scale2.0 | 5/5 | 36 | 272.548 | 7.571 | 0.8605 |
| gpt-4.1 html scale2.0 | 5/5 | 36 | 460.046 | 12.779 | 0.8327 |

Case-level overall scores:

| Run | C01 | C02 | C03 | C04 | C05 |
|---|---:|---:|---:|---:|---:|
| Standard Docling | 0.912 | 1.000 | 0.651 | 1.000 | 1.000 |
| gpt-5.2 markdown scale2.0 | 1.000 | 1.000 | 0.961 | 1.000 | 1.000 |
| gpt-5.4-mini markdown scale2.0 | 1.000 | 0.927 | 0.929 | 0.983 | 1.000 |
| gpt-5.4-mini markdown scale2.5 | 1.000 | 0.929 | 0.931 | 0.965 | 1.000 |
| gpt-5.4-mini html scale2.0 | 1.000 | 0.913 | 0.933 | 1.000 | 1.000 |
| gpt-4.1 markdown scale2.0 | 0.956 | 0.747 | 0.889 | 0.786 | 0.924 |
| gpt-4.1 html scale2.0 | 0.956 | 0.602 | 0.901 | 0.781 | 0.924 |

Summary:

- Best accuracy: `gpt-5.2 markdown scale2.0`.
- Best speed/accuracy balance: `gpt-5.4-mini markdown scale2.0`.
- `gpt-5.4-mini html scale2.0` slightly improved mean score versus Markdown, but was much slower.
- `scale=2.5` did not improve this benchmark enough to justify the extra latency.
- `gpt-4.1` was weaker for dense table cell recall in this test set.

Detailed outputs are committed under `outputs/docling_benchmark/`.
