# Docling 2.92.0 オプション利用棚卸し

この棚卸しは `requirements.txt` の `docling==2.92.0` と、ローカル `.venv` にインストールされた Docling 2.92.0 の定義を基準にしている。

ステータスの意味:

- **本体で使用**: Streamlit UI、`run_routed_pdf.py`、または通常変換フローで明示的に使う。
- **ベンチのみ**: `benchmarks/` の評価マトリクスだけで明示的に使う。
- **暗黙デフォルト**: `DocumentConverter()` や `PdfPipelineOptions()` のデフォルトとして使われるが、現実装では値を制御していない。
- **未使用**: 現実装では指定も分岐もしていない。
- **独自**: Docling 標準ではなく、この PoC が追加している設定やモード。

## 現在の実装で使っている変換経路

| 経路 | 実装 | Docling 設定 |
|---|---|---|
| Standard Docling | `app.py` / `benchmarks/run_docling_benchmark.py` | `DocumentConverter()` のデフォルト。PDF は `StandardPdfPipeline`、`DoclingParseDocumentBackend`、`ThreadedPdfPipelineOptions`。 |
| OpenAI VLM | `docling_openai_vlm.py` | `InputFormat.PDF` に `PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=VlmPipelineOptions(...))` を指定。 |
| Routed OCR/VLM Reconcile | `routing_pipeline.py` / `run_routed_pdf.py` | ページ単位の独自ルーティングで、標準 PDF パイプラインと OpenAI VLM パイプラインを使い分ける。 |
| Standard option matrix | `benchmarks/run_poc_matrix.py` / `benchmarks/run_standard_pdf_poc.py` | 標準 PDF パイプラインの一部オプションを網羅的に振る。 |
| VLM option matrix | `benchmarks/run_poc_matrix.py` / `benchmarks/run_openai_vlm_benchmark.py` | OpenAI VLM の model / response_format / scale / reasoning_effort / prompt_variant を振る。 |

## 独自ルーティングモード

これは Docling 標準の `ProcessingPipeline` ではなく、この PoC 独自のモード。

| モード | ステータス | 主な Docling 設定 |
|---|---:|---|
| `TEXT_LIGHT` | 本体で使用 | `do_ocr=False`, `do_table_structure=False`, `force_backend_text=True`, `generate_page_images=False`, `images_scale=1.0` |
| `TEXT_TABLE_FAST` | 本体で使用 | `do_ocr=False`, `do_table_structure=True`, `TableFormerMode.FAST`, `do_cell_matching=True`, `force_backend_text=True` |
| `TEXT_TABLE_ACCURATE` | 本体で使用 | `do_ocr=False`, `do_table_structure=True`, `TableFormerMode.ACCURATE`, `do_cell_matching=True`, `force_backend_text=True` |
| `IMAGE_RECONCILE` | 本体で使用 | OCR 側は `do_ocr=True`, `force_full_page_ocr=True`, `do_table_structure=True`。同じページを VLM でも変換して突合する。 |
| `IMAGE_RECONCILE_APPEND` | 本体で使用 | テキストレイヤー抽出結果を維持しつつ、埋め込み画像領域に OCR/VLM 突合結果を追記する。 |

## DocumentConverter / 入力形式

Docling 2.92.0 の `InputFormat` は以下。

| InputFormat | ステータス | 備考 |
|---|---:|---|
| `PDF` | 本体で使用 | UI/CLI/ベンチの主対象。 |
| `MD`, `HTML` | 間接利用 | VLM の返却形式として Markdown/HTML を使う。トップレベル入力としては未使用。 |
| `DOCX`, `PPTX`, `IMAGE`, `ASCIIDOC`, `CSV`, `XLSX`, `XML_USPTO`, `XML_JATS`, `XML_XBRL`, `METS_GBS`, `JSON_DOCLING`, `AUDIO`, `VTT`, `LATEX` | 未使用 | `DocumentConverter()` のデフォルト allowed formats には含まれるが、アプリは PDF アップロードのみ。 |

`DocumentConverter` 初期化パラメータ:

| パラメータ | ステータス | 現状 |
|---|---:|---|
| `allowed_formats` | 暗黙デフォルト | 明示指定なし。Docling が `list(InputFormat)` を許可する。 |
| `format_options` | 本体で使用 | OpenAI VLM と Routed の PDF 変換で `InputFormat.PDF` だけ明示指定。Standard は未指定。 |

`convert()` / `convert_all()` のパラメータ:

| パラメータ | ステータス | 現状 |
|---|---:|---|
| `source` | 本体で使用 | ローカル PDF パス。 |
| `page_range` | 本体で使用 | Routed で同一モードの連続ページ範囲を変換。 |
| `headers` | 未使用 | URL/リモート取得用ヘッダーは使っていない。 |
| `raises_on_error` | 暗黙デフォルト | デフォルト `True`。 |
| `max_num_pages` | 未使用 | 制限なし。 |
| `max_file_size` | 未使用 | 制限なし。 |
| `convert_all()` | 未使用 | 複数入力の一括変換はしていない。 |
| `convert_string()` | 未使用 | 文字列からの Markdown/HTML 変換はしていない。 |

## FormatOption / バックエンド

| FormatOption | デフォルト pipeline/backend | ステータス |
|---|---|---:|
| `PdfFormatOption` | `StandardPdfPipeline` / `DoclingParseDocumentBackend` | 本体で使用 |
| `PdfFormatOption(pipeline_cls=VlmPipeline)` | `VlmPipeline` / PDF backend | 本体で使用 |
| `ImageFormatOption` | `StandardPdfPipeline` / `ImageDocumentBackend` | 未使用 |
| `WordFormatOption` | `SimplePipeline` / `MsWordDocumentBackend` | 未使用 |
| `PowerpointFormatOption` | `SimplePipeline` / `MsPowerpointDocumentBackend` | 未使用 |
| `HTMLFormatOption` | `SimplePipeline` / `HTMLDocumentBackend` | 未使用 |
| `MarkdownFormatOption` | `SimplePipeline` / `MarkdownDocumentBackend` | 未使用 |
| `AsciiDocFormatOption` | `SimplePipeline` / `AsciiDocBackend` | 未使用 |
| `CsvFormatOption` | `SimplePipeline` / `CsvDocumentBackend` | 未使用 |
| `ExcelFormatOption` | `SimplePipeline` / `MsExcelDocumentBackend` | 未使用 |
| XML / XBRL / METS / JSON / Audio / VTT / LaTeX 系 | 各専用 backend | 未使用 |

PDF backend enum:

| PdfBackend | ステータス | 備考 |
|---|---:|---|
| `DOCLING_PARSE` | 暗黙デフォルト | `PdfFormatOption` のデフォルト backend。 |
| `PYPDFIUM2` | 未使用 | 明示切り替えなし。 |
| `DLPARSE_V1`, `DLPARSE_V2`, `DLPARSE_V4` | 未使用 | Docling 側で deprecated、`DOCLING_PARSE` に正規化される。 |

Backend options:

| オプションモデル | 主なフィールド | ステータス |
|---|---|---:|
| `PdfBackendOptions` | `password`, `enable_remote_fetch`, `enable_local_fetch` | 未使用 |
| `HTMLBackendOptions` | `render_page`, viewport/wait/scale, `fetch_images`, `infer_furniture` など | 未使用 |
| `MarkdownBackendOptions` | `fetch_images`, `source_uri` | 未使用 |
| `MsExcelBackendOptions` | `treat_singleton_as_text`, `gap_tolerance`, `sheet_names` | 未使用 |
| `MetsGbsBackendOptions` | `password`, archive size/member limits | 未使用 |
| `LatexBackendOptions` | `parse_timeout` | 未使用 |
| `XBRLBackendOptions` | `taxonomy` | 未使用 |

## ProcessingPipeline enum

| ProcessingPipeline | ステータス | 現状 |
|---|---:|---|
| `STANDARD` | 本体で使用 | PDF 標準変換、Routed の CPU 側。 |
| `VLM` | 本体で使用 | OpenAI VLM 変換。 |
| `ASR` | 未使用 | Audio 入力なし。 |
| `LEGACY` | 未使用 | 旧パイプラインは使っていない。 |

## 非 PDF 用 pipeline options

| オプションモデル | 主なフィールド | ステータス | 現状 |
|---|---|---:|---|
| `ConvertPipelineOptions` | `document_timeout`, `accelerator_options`, `enable_remote_services`, `allow_external_plugins`, `artifacts_path`, picture/chart enrichment 系 | 暗黙デフォルト | PDF 以外の SimplePipeline 用。トップレベル入力としては使っていない。 |
| `PaginatedPipelineOptions` | `ConvertPipelineOptions` + `images_scale`, `generate_page_images`, `generate_picture_images` | 暗黙デフォルト | PDF/VLM の基底として間接的に使う。 |
| `AsrPipelineOptions` | base pipeline fields + `asr_options` | 未使用 | `AUDIO` 入力なし。 |
| `InlineAsrOptions` | `repo_id`, `verbose`, `timestamps`, `temperature`, `max_new_tokens`, `max_time_chunk`, `torch_dtype`, `supported_devices` | 未使用 | ASR 未使用。 |
| `InlineAsrNativeWhisperOptions` | `InlineAsrOptions` + `language`, `word_timestamps` | 未使用 | ASR 未使用。 |
| `InlineAsrMlxWhisperOptions` | `InlineAsrOptions` + `language`, `task`, `word_timestamps`, `no_speech_threshold`, `logprob_threshold`, `compression_ratio_threshold` | 未使用 | ASR 未使用。 |
| `InferenceAsrFramework.MLX` / `WHISPER` | ASR 推論 backend | 未使用 | ASR 未使用。 |
| `VlmExtractionPipelineOptions` | base pipeline fields + `vlm_options=InlineVlmOptions` | 未使用 | NuExtract 系の VLM extraction pipeline は使っていない。 |

## PdfPipelineOptions 全項目

| 項目 | デフォルト | ステータス | 現状 |
|---|---:|---:|---|
| `document_timeout` | `None` | 未使用 | 変換単位のタイムアウトは未設定。 |
| `accelerator_options` | `num_threads=4, device=auto` | 暗黙デフォルト | 明示指定なし。 |
| `enable_remote_services` | `False` | 未使用 | 標準 PDF 側では remote service を使わない。VLM 側だけ `True`。 |
| `allow_external_plugins` | `False` | 未使用 | 外部 plugin 許可なし。 |
| `artifacts_path` | `None` | 未使用 | モデル artifacts パス未指定。 |
| `do_picture_classification` | `False` | 未使用 | 画像分類 enrichment は使っていない。 |
| `picture_classification_options` | デフォルト classifier | 未使用 | `do_picture_classification=False` のため未使用。 |
| `do_picture_description` | `False` | 未使用 | 画像説明 enrichment は使っていない。 |
| `picture_description_options` | デフォルト VLM description options | 未使用 | `do_picture_description=False` のため未使用。 |
| `do_chart_extraction` | `False` | 未使用 | チャート抽出 enrichment は使っていない。 |
| `chart_extraction_options` | Granite Vision chart options | 未使用 | `do_chart_extraction=False` のため未使用。 |
| `images_scale` | `1.0` | 本体で使用 | Routed 標準側は `1.0` 固定。ベンチで `1.0/2.0`。VLM 側は別途 `scale` と同期。 |
| `generate_page_images` | `False` | 本体で使用 | Routed 標準側は `False`。ベンチで切替。VLM 側は `True`。 |
| `generate_picture_images` | `False` | 未使用 | 個別画像抽出なし。 |
| `do_table_structure` | `True` | 本体で使用 | Routed/ベンチでオンオフ。Standard default ではオン。 |
| `do_ocr` | `True` | 本体で使用 | Routed/ベンチでオンオフ。Standard default ではオン。 |
| `do_code_enrichment` | `False` | 未使用 | コード enrichment なし。 |
| `do_formula_enrichment` | `False` | 未使用 | 数式 enrichment なし。 |
| `force_backend_text` | `False` | 本体で使用 | テキストレイヤー系 Routed モードで `True`。ベンチでも切替。 |
| `table_structure_options` | `TableStructureOptions()` | 本体で使用 | `mode`, `do_cell_matching` を制御。 |
| `ocr_options` | `OcrAutoOptions()` | 本体で使用 | `force_full_page_ocr` だけ制御。エンジン/言語は未指定。 |
| `layout_options` | `LayoutOptions()` | 暗黙デフォルト | 明示制御なし。 |
| `code_formula_options` | デフォルト VLM options | 未使用 | enrichment が off。 |
| `generate_table_images` | `False` | 未使用 | Docling 側で deprecated。 |
| `generate_parsed_pages` | `False` | 未使用 | 中間 parsed page は保持しない。 |
| `ocr_batch_size` | `4` | 本体で使用 | Routed で `2`、ベンチで `1/4/8`。 |
| `layout_batch_size` | `4` | 本体で使用 | Routed で `4`、ベンチで `1/4/8`。 |
| `table_batch_size` | `4` | 本体で使用 | Routed で `2`、ベンチで `1/4/8`。 |
| `batch_polling_interval_seconds` | `0.5` | 暗黙デフォルト | 明示制御なし。 |
| `queue_max_size` | `100` | 暗黙デフォルト | 明示制御なし。 |

## TableStructureOptions

| 項目 / 値 | ステータス | 現状 |
|---|---:|---|
| `do_cell_matching` | 本体で使用 | Routed では `True` 固定。ベンチでは `True/False`。 |
| `mode=TableFormerMode.FAST` | 本体で使用 | `TEXT_TABLE_FAST` と一部 OCR 側。ベンチでも使用。 |
| `mode=TableFormerMode.ACCURATE` | 本体で使用 | `TEXT_TABLE_ACCURATE` と複雑表ページ。ベンチでも使用。 |
| `GraniteVisionTableStructureOptions` | 未使用 | TableFormer 以外の表構造 options は使っていない。 |

## OCR options / engines

| 項目 / エンジン | ステータス | 現状 |
|---|---:|---|
| `do_ocr` | 本体で使用 | Routed/ベンチで切替。 |
| `OcrAutoOptions` | 暗黙デフォルト | 明示的な engine 指定はなく、自動選択に任せている。 |
| `force_full_page_ocr` | 本体で使用 | `IMAGE_RECONCILE` の OCR 側とベンチで `True`。 |
| `lang` | 未使用 | OCR 言語は未指定。 |
| `bitmap_area_threshold` | 未使用 | デフォルト `0.05` のまま。 |
| `EasyOcrOptions` | 未使用 | `use_gpu`, `confidence_threshold`, `recog_network`, download 設定など未指定。 |
| `TesseractCliOcrOptions` | 未使用 | `tesseract_cmd`, `path`, `psm` 未指定。 |
| `TesseractOcrOptions` | 未使用 | Python binding 版 Tesseract 設定なし。 |
| `RapidOcrOptions` | 未使用 | backend `onnxruntime/openvino/paddle/torch`、model path、`rapidocr_params` など未指定。 |
| `OcrMacOptions` | 未使用 | macOS Vision OCR 設定なし。 |
| `KserveV2OcrOptions` | 未使用 | リモート OCR service 設定なし。 |

`OcrEngine` enum は `AUTO`, `EASYOCR`, `TESSERACT_CLI`, `TESSERACT`, `OCRMAC`, `RAPIDOCR`。ただしこの enum 自体は Docling 側で deprecated 扱いで、現実装も直接使っていない。

## Layout / accelerator

| 項目 | ステータス | 現状 |
|---|---:|---|
| `LayoutOptions.keep_empty_clusters` | 未使用 | デフォルト `False`。 |
| `LayoutOptions.skip_cell_assignment` | 未使用 | デフォルト `False`。 |
| `LayoutOptions.create_orphan_clusters` | 暗黙デフォルト | デフォルト `True`。 |
| `LayoutOptions.model_spec` | 暗黙デフォルト | デフォルトの layout model。切替なし。 |
| `LayoutObjectDetectionOptions` | 未使用 | カスタム object detection engine なし。 |
| `AcceleratorOptions.num_threads` | 暗黙デフォルト | Docling default `4`。Routed のバッチサイズは調整するが accelerator は未調整。 |
| `AcceleratorOptions.device` | 暗黙デフォルト | `auto`。`cpu/cuda/mps/xpu` の明示指定なし。 |
| `AcceleratorOptions.cuda_use_flash_attention2` | 未使用 | デフォルト `False`。 |

## Picture / chart / code / formula enrichment

| 項目 | ステータス | 現状 |
|---|---:|---|
| `do_picture_classification` / `picture_classification_options` | 未使用 | 画像分類なし。 |
| `do_picture_description` / `picture_description_options` | 未使用 | PDF 内画像への説明生成なし。 |
| `PictureDescriptionApiOptions` | 未使用 | 画像説明 API は使っていない。 |
| `PictureDescriptionVlmOptions` / `PictureDescriptionVlmEngineOptions` | 未使用 | 画像説明用 VLM は使っていない。 |
| `do_chart_extraction` / `chart_extraction_options` | 未使用 | chart2csv/chart2code/chart2summary は使っていない。 |
| `do_code_enrichment` / `do_formula_enrichment` | 未使用 | コード/数式抽出なし。 |
| `CodeFormulaVlmOptions` | 未使用 | enrichment off のため実行されない。 |

## VlmPipelineOptions / VLM conversion

| 項目 | デフォルト | ステータス | 現状 |
|---|---:|---:|---|
| `VlmPipelineOptions.enable_remote_services` | `False` | 本体で使用 | OpenAI VLM で `True`。 |
| `VlmPipelineOptions.generate_page_images` | `True` | 本体で使用 | OpenAI VLM で明示 `True`。 |
| `VlmPipelineOptions.images_scale` | `1.0` | 本体で使用 | OpenAI VLM で `scale` と同期。 |
| `VlmPipelineOptions.generate_picture_images` | `False` | 未使用 | 個別画像抽出なし。 |
| `VlmPipelineOptions.force_backend_text` | `False` | 未使用 | VLM 側では使っていない。 |
| `VlmPipelineOptions.vlm_options` | `VlmConvertOptions` | 本体で使用 | OpenAI 用 `VlmConvertOptions` を構築。 |
| `VlmConvertOptions.engine_options` | required | 本体で使用 | `ApiVlmEngineOptions(engine_type=API_OPENAI)`。 |
| `VlmConvertOptions.model_spec` | required | 本体で使用 | `VlmModelSpec(name, default_repo_id, prompt, response_format, max_new_tokens)`。 |
| `VlmConvertOptions.scale` | `2.0` | 本体で使用 | UI/CLI/ベンチで変更可能。 |
| `VlmConvertOptions.max_size` | `None` | 未使用 | helper 引数はあるが UI/CLI では露出なし。 |
| `VlmConvertOptions.batch_size` | `1` | 本体で使用 | OpenAI VLM は `1` 固定。 |
| `VlmConvertOptions.force_backend_text` | `False` | 未使用 | 使っていない。 |

## VLM engine / model options

| 項目 / 値 | ステータス | 現状 |
|---|---:|---|
| `VlmEngineType.API_OPENAI` | 本体で使用 | OpenAI Chat Completions。 |
| `VlmEngineType.API`, `API_OLLAMA`, `API_LMSTUDIO` | 未使用 | OpenAI 以外の API endpoint は使っていない。 |
| `VlmEngineType.TRANSFORMERS`, `MLX`, `VLLM`, `AUTO_INLINE` | 未使用 | ローカル VLM 実行なし。 |
| `ApiVlmEngineOptions.url` | 本体で使用 | `API_OPENAI` により `https://api.openai.com/v1/chat/completions`。 |
| `ApiVlmEngineOptions.headers` | 本体で使用 | `Authorization: Bearer ...`。 |
| `ApiVlmEngineOptions.params.model` | 本体で使用 | UI/CLI/ベンチで指定。 |
| `ApiVlmEngineOptions.params.max_completion_tokens` | 本体で使用 | UI/CLI/ベンチで指定。 |
| `ApiVlmEngineOptions.params.reasoning_effort` | 本体で使用 | GPT-5 系のみ付与。`none/low/medium/high/xhigh`。 |
| `ApiVlmEngineOptions.params.image_detail` | 本体で使用 | env default `auto`。UI/CLI では未露出。 |
| `ApiVlmEngineOptions.timeout` | 本体で使用 | UI/CLI/ベンチで指定。 |
| `ApiVlmEngineOptions.concurrency` | 本体で使用 | `1` 固定。 |
| `temperature` | 未使用 | GPT-5 互換のため Docling OpenAI request から除去。 |
| `stop_strings`, custom stopping criteria | 未使用 | 明示指定なし。 |
| `InlineVlmOptions`, `HuggingFaceVlmOptions`, `ApiVlmOptions` | 未使用 | 現実装は新しい engine/options 系の `VlmConvertOptions + ApiVlmEngineOptions` を使用。 |

## VLM response formats

Docling 標準の `ResponseFormat` と現状:

| ResponseFormat | ステータス | 現状 |
|---|---:|---|
| `MARKDOWN` | 本体で使用 | UI/CLI/ベンチで使用。 |
| `HTML` | 本体で使用 | UI/CLI/ベンチで使用。 |
| `DOCTAGS` | 未使用 | 出力先として採用していない。 |
| `DOCLANG` | 未使用 | 出力先として採用していない。 |
| `DEEPSEEKOCR_MARKDOWN` | 未使用 | DeepSeek OCR 系 format なし。 |
| `OTSL` | 未使用 | 表構造トークン列としての出力なし。 |
| `PLAINTEXT` | 未使用 | プレーンテキスト VLM 出力なし。 |

PoC 独自の VLM prompt variants:

| prompt variant | ステータス | 備考 |
|---|---:|---|
| `strict_preserve` | 本体で使用 | デフォルト。 |
| `table_first` | 本体で使用 | CLI/ベンチで指定可能。UI の Routed 呼び出しでは現状デフォルト。 |

## OutputFormat / エクスポート

Docling 標準の `OutputFormat` と現状:

| OutputFormat | ステータス | 現状 |
|---|---:|---|
| `MARKDOWN` | 本体で使用 | `document.export_to_markdown()`。 |
| `HTML` | 本体で使用 | テーブルごとに `table.export_to_html()`。 |
| `TEXT` | 未使用 | `export_to_text()` は使っていない。 |
| `JSON` | 間接利用 | Docling document JSON ではなく、PoC 独自 JSON と tables JSON を保存。 |
| `YAML`, `HTML_SPLIT_PAGE`, `DOCTAGS`, `VTT` | 未使用 | 出力形式として使っていない。 |

## ベンチでのみ網羅している標準 PDF 設定

`benchmarks/run_poc_matrix.py` の標準マトリクスでは以下を振っている。

| 設定 | 値 |
|---|---|
| `do_table_structure` | `True/False` |
| `table_structure_options.mode` | `fast/accurate` |
| `table_structure_options.do_cell_matching` | `True/False` |
| `do_ocr` | `True/False` |
| `ocr_options.force_full_page_ocr` | `True/False` |
| `force_backend_text` | `True/False` |
| `images_scale` | `1.0/2.0` |
| `generate_page_images` | `images_scale != 1.0` |
| batch profile | `ocr_batch_size`, `layout_batch_size`, `table_batch_size` を `1/4/8` |

## ベンチでのみ網羅している VLM 設定

`benchmarks/run_poc_matrix.py` の VLM マトリクスでは以下を振っている。

| 設定 | 値 |
|---|---|
| `model` | `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.2` |
| `response_format` | `markdown/html` |
| `scale` | `1.0/2.0/2.5` |
| `reasoning_effort` | `none/low/medium` |
| `prompt_variant` | `strict_preserve/table_first` |
| `max_completion_tokens` | 固定値 |
| `timeout_seconds` | 固定値 |

## 未使用領域の要約

大きく未使用なのは以下。

1. PDF 以外の入力形式と、その format/backend options。
2. PDF backend の明示切替、PDF password、remote/local fetch。
3. OCR engine の明示選択と言語指定。
4. layout model / accelerator / artifacts / document timeout の明示設定。
5. picture classification/description、chart extraction、code/formula enrichment。
6. OpenAI 以外の VLM engine、ローカル VLM、VLM の doctags/doclang/otsl/plaintext 出力。
7. `convert_all`, `convert_string`, `headers`, `max_num_pages`, `max_file_size`。

## 実装上の注意

- 本体の Standard mode は `DocumentConverter()` デフォルトで、設定を UI から選べない。
- Routed mode は標準 PDF オプションのうち、表/OCR/ページ画像/バッチサイズに絞って制御している。
- OpenAI VLM は Docling 標準の `VlmPipeline` を使っているが、OpenAI GPT-5 系互換のため request path を patch している。
- VLM の `image_detail` と `max_size` は helper には存在するが、UI/CLI にはまだ出していない。
- ベンチでは本体より広いパラメータを試しているが、OCR engine、layout、backend、enrichment、非 PDF format までは網羅していない。
