# PDF処理ルーティングとリアルタイム評価仕様

## 目的

この文書は、Docling PoCにおけるPDF処理モードのルーティング方針と、OCR/VLMの誤認識リスクをリアルタイムに検知してワーニング化するための仕様をまとめる。

主目的は次の2点。

1. CPU前提で、大規模PDFの処理時間を短縮する。
2. 抽出失敗や誤認識の疑いをユーザーに早く返し、ページ範囲の絞り込み、解像度変更、OCR/VLM切り替えによる再投入をしやすくする。

## 前提

- 実行環境はCPUを主対象とする。
- テキストレイヤーだけで処理できるページは、最軽量モードに逃がす。
- TableFormer、OCR、VLMは重い処理として扱い、必要なページだけに使う。
- ルーティングはページ単位で判断する。
- 実行時は同じモードの連続ページをまとめ、`page_range`単位で処理する。
- ワーニング検知も軽量であることを重視する。
- 標準のワーニング判定では、OCR再実行、VLM再実行、LLMによる品質判定、追加のDocling変換は行わない。

## 全体フロー

```text
PDF入力
  -> 軽量プリフライト
  -> ページ単位の処理モード判定
  -> 同一モードの連続ページをグループ化
  -> グループ単位でDocling/OCR/VLM処理
  -> 抽出結果の軽量バリデーション
  -> ページ単位ワーニングと集計レポート
```

## 処理モード

| モード | 対象 | 主な設定 | コスト |
|---|---|---|---|
| `TEXT_LIGHT` | テキストレイヤーが良好で、テーブル信号が弱いページ | OCRなし、table structureなし、enrichmentなし | 最低 |
| `TEXT_TABLE_FAST` | テキストレイヤーが良好で、単純テーブルがありそうなページ | OCRなし、table structureあり、TableFormer `FAST` | 中 |
| `TEXT_TABLE_ACCURATE` | 複雑なテーブルがありそうなページ | OCRなし、table structureあり、TableFormer `ACCURATE` | 高 |
| `IMAGE_RECONCILE` | テキストレイヤーが弱い画像ページ | OCRとVLMを両方実行し、結果を突合する | 最高 |
| `IMAGE_RECONCILE_APPEND` | テキストレイヤーは良好だが、ページ内に未抽出の可能性がある埋め込み視覚領域があるページ | 通常のTEXT処理を維持し、同ページにOCR/VLM突合結果を追加する | 最高 |

基本方針は、できるだけ多くのページを `TEXT_LIGHT` にルーティングすること。

## 軽量プリフライト

プリフライトでは、重いMLモデルを動かさない。PDFのテキストオブジェクト、画像オブジェクト、描画プリミティブ、低解像度サムネイル程度から判断する。

取得するページ単位の信号は以下。

| 信号 | 意味 | 主な取得元 |
|---|---|---|
| `text_chars` | 抽出可能なテキスト文字数 | PDFテキストレイヤー |
| `text_density` | ページ面積あたりの文字密度 | PDFテキストレイヤー |
| `text_quality_score` | テキストレイヤーの信頼度 | 文字種、空白、断片化率 |
| `image_area_ratio` | 画像がページを占める割合 | PDF画像オブジェクト |
| `line_count` | 水平線・垂直線の数 | PDF描画プリミティブ |
| `rect_count` | 矩形要素の数 | PDF描画プリミティブ |
| `table_score` | テーブルが存在する可能性 | 線、矩形、テキスト整列 |
| `complex_table_score` | `ACCURATE`が必要な可能性 | 結合セル、多段ヘッダ、密度 |
| `image_read_risk_score` | 画像読み取りが不安定になりそうな度合い | 画像解像度、傾き、密度、手書き、図表、フォーム、複雑表 |

## テキストレイヤー判定

テキストレイヤーの品質は、抽出文字数だけで判断しない。文字化け、過剰な空白、単一文字トークンの多さ、記号比率も見る。

良いテキストレイヤーの初期判定:

```text
good_text_layer =
  text_chars >= 200
  and text_quality_score >= 0.80
```

`text_quality_score`を下げる信号:

- `�` などの置換文字が多い。
- 制御文字や不自然な記号が多い。
- 1文字単位に分断されたトークンが多い。
- 空白が異常に多い。
- 同じ断片が繰り返される。
- 見た目上は情報量が多いのに、抽出テキストが極端に少ない。

## テーブル検知

TableFormerを全ページにかけない。まず軽量なテーブル検知を行い、必要なページだけ `TEXT_TABLE_FAST` または `TEXT_TABLE_ACCURATE` に回す。

`table_score`を上げる信号:

- 水平線・垂直線・矩形が多い。
- 同じx座標にテキストブロックが縦に並ぶ。
- 複数行で同じような列数が続く。
- 数値列が整列している。
- 局所的にテキスト密度が高い。
- ヘッダらしい行がある。

初期判定:

```text
has_table = table_score >= 0.40
```

`complex_table_score`を上げる信号:

- 結合セルの疑い。
- 多段ヘッダ。
- 行数または列数が多い。
- テーブル領域がページの大半を占める。
- 行高・列幅が不規則。
- 数値中心でセル境界の重要度が高い。

初期判定:

```text
needs_accurate_table =
  has_table
  and complex_table_score >= 0.60
```

## 画像レイヤー判定

テキストレイヤーが弱い画像ページは、OCRかVLMのどちらか一方を選ぶのではなく、`IMAGE_RECONCILE` にルーティングする。

`IMAGE_RECONCILE` ではOCRとVLMを両方実行し、抽出結果を突合する。不一致箇所はワーニング化し、重要値や表セルではsafe出力上で `[[読み取り不明]]` に置換する。

テキストレイヤーが良好なページでも、PDF構造上の画像領域が大きく、かつその領域にPDFテキストがほぼ重ならない場合は `IMAGE_RECONCILE_APPEND` を追加アクションとして付与する。ページ本体は `TEXT_LIGHT` / `TEXT_TABLE_*` のまま処理し、追加で同ページのOCR/VLM突合結果をsafe出力へ追記する。

`IMAGE_RECONCILE_APPEND` はキーワードではなく、以下の構造信号を優先する。

- 画像オブジェクト領域のページ面積比、幅比、高さ比。
- 画像領域とPDFテキスト矩形の重なり率。
- レンダリング画像のエッジ密度、コントラスト、投影ピーク、色数による視覚複雑度。
- 画像領域の直前直後に短いキャプション状テキストがあるか。
- テキストレイヤー内に「表/図/Table/Figure」参照がある場合は、補助信号として閾値を少し下げる。ただし「画像」「スキャン」「OCR」のような内容キーワードには依存しない。

初期閾値:

```text
area_ratio >= 0.08
and width_ratio >= 0.30
and height_ratio >= 0.06
and text_overlap_ratio <= 0.05
and (
  visual_complexity_score >= 0.35
  or caption_geometry_score >= 0.50
  or (area_ratio >= 0.25 and visual_complexity_score >= 0.20)
  or (visual_reference_count > 0 and visual_complexity_score >= 0.20)
)
```

対象ページ:

- テキストレイヤーがない、または品質が低い。
- 画像がページの大部分を占める。
- 見た目上は文字や表があるのに、PDFテキスト抽出結果が少ない。
- OCR/VLMの誤読が業務上リスクになるページ。

初期判定:

```text
if not good_text_layer and image_area_ratio >= 0.40:
  mode = IMAGE_RECONCILE
```

## ルーティングルール

初期ルールは以下。

```text
if good_text_layer and table_score < 0.40:
  mode = TEXT_LIGHT

elif good_text_layer and table_score >= 0.40 and complex_table_score < 0.60:
  mode = TEXT_TABLE_FAST

elif good_text_layer and table_score >= 0.40 and complex_table_score >= 0.60:
  mode = TEXT_TABLE_ACCURATE

else:
  mode = IMAGE_RECONCILE
```

閾値は固定値として始める。ベンチマーク結果を見ながら、PDF種別ごとに調整する。

## 実行方針

Doclingのオプションは変換単位で設定するため、1回の変換内でページごとに完全に別モードを混在させるのは避ける。同じモードの連続ページをグループ化し、`page_range`で処理する。

例:

```text
1-8ページ:   TEXT_LIGHT
9-10ページ:  TEXT_TABLE_FAST
11ページ:    IMAGE_RECONCILE
12-20ページ: TEXT_LIGHT
21ページ:    TEXT_TABLE_ACCURATE
```

実行グループ:

```text
1-8ページ   -> TEXT_LIGHT設定で変換
9-10ページ  -> TEXT_TABLE_FAST設定で変換
11ページ    -> IMAGE_RECONCILE設定でOCR/VLMを実行し突合
12-20ページ -> TEXT_LIGHT設定で変換
21ページ    -> TEXT_TABLE_ACCURATE設定で変換
```

変換後は元のページ順を維持して統合する。各ページにはルーティング理由、スコア、処理時間、ワーニングを保持する。

## CPU向け実行ポリシー

推奨初期設定:

```text
workers: 1-2
docling_threads: 3-4
ocr_batch_size: default or small
layout_batch_size: default
table_batch_size: small/default
```

運用ルール:

- モードごとにconverterを再利用する。
- `workers * docling_threads <= 物理CPUコア数` を目安にする。
- OCR、VLM、TableFormerを同時に並列化しすぎない。
- ページ画像生成は、VLM、プレビュー、診断で必要な場合だけ有効化する。
- `ACCURATE`テーブルは、複雑テーブル信号があるページだけに限定する。
- `IMAGE_RECONCILE` は高コストなため、該当ページのみをページ単位または小さいページ範囲で処理する。

## リアルタイム評価の考え方

リアルタイム評価は、正解データなしで誤認識を検知する仕組みである。そのため、厳密な誤り検出ではなく、抽出失敗や誤認識の疑いを低コストに検知する。

評価タイミングは3つ。

1. プリフライト直後の事前ワーニング。
2. 各ページまたは各グループの変換完了直後の単独結果ワーニング。
3. `IMAGE_RECONCILE` ページにおけるOCR/VLM突合ワーニング。

ワーニングはPDF全体の処理完了を待たずに、ページ単位で順次UIに表示する。

## ワーニングレベル

| レベル | 意味 | ユーザー対応 |
|---|---|---|
| `info` | 注意情報。結果は概ね使える可能性が高い | 通常は対応不要 |
| `warning` | 抽出が崩れている可能性がある | 該当ページを確認し、必要なら再投入 |
| `needs_retry` | 誤認識または抽出失敗の可能性が高い | ページ範囲を絞る、解像度を上げる、対象ページだけ再投入する |

## ワーニング種別

| コード | 検知内容 | 軽量な検知方法 |
|---|---|---|
| `LOW_INPUT_RESOLUTION` | OCR/VLMに対して入力画像の解像度が低い | 推定DPI、レンダリングサイズ、文字密度 |
| `TEXT_LAYER_SUSPECT` | テキストレイヤーが壊れている疑い | 文字化け率、記号比率、断片化率 |
| `OCR_EMPTY_RESULT` | 画像ページなのにOCR結果がほぼ空 | 画像面積と抽出文字数の比較 |
| `OCR_GARBLED_TEXT` | OCR文字列が崩れている疑い | 置換文字、記号比率、反復断片、単文字トークン |
| `OCR_LAYOUT_LOSS` | OCRで表や段組みの構造が崩れた疑い | 事前テーブル信号と抽出テーブル数の不一致 |
| `TABLE_MISSED` | テーブル信号があったのに表が抽出されていない | `table_score`とDocling抽出表数 |
| `EMBEDDED_VISUAL_REGION_CANDIDATE` | テキストレイヤー付きページ内に未抽出の可能性がある埋め込み視覚領域を検知 | 画像領域面積、テキスト重なり率、視覚複雑度、キャプション幾何 |
| `ROW_HEADER_SPAN_MISSED` | PDFテキストレイヤーには存在する行見出しSPANが構造化テーブルから欠落 | 表bbox内のPDF文字座標とDocling行列bboxの突合 |
| `TABLE_TEXT_COVERAGE_LOSS` | 表領域内のPDFテキストを構造化テーブルへ安全に割り当てられない | 表領域内PDFテキストと構造化セルのカバレッジ比較 |
| `TABLE_LOW_CONFIDENCE` | 抽出表の構造が不安定 | 空セル率、行ごとの列数ばらつき |
| `VLM_TRUNCATED_OUTPUT` | VLM出力が途中で切れた疑い | API finish reason、token上限、未閉じ構造 |
| `VLM_LOW_DETAIL_OUTPUT` | 密なページに対してVLM出力が短すぎる | ページ密度と出力文字数の比較 |
| `VLM_FORMAT_FAILURE` | VLM出力のMarkdown/HTML構造が壊れている | パースエラー、表記法の破綻 |
| `OCR_TABLE_LAYOUT_UNRELIABLE` | OCR側の表構造が大きく崩れており、OCR/VLMセル比較に使うと過剰maskになる | OCR/VLMの数値セル不一致率、比較セル数 |
| `OCR_VLM_DISAGREEMENT` | OCRとVLMの読み取り結果が一致しない | 正規化後の文字列・値・セル比較 |
| `CRITICAL_VALUE_DISAGREEMENT` | 金額、日付、ID、数量など重要値が一致しない | 重要値抽出後の値比較 |
| `TABLE_CELL_DISAGREEMENT` | 表セル単位でOCR/VLMが一致しない | セル対応付け後の値比較 |
| `VALUE_VALIDATION_FAILED` | 値の形式、桁数、計算整合性が崩れている | ルールベース検証 |
| `MASKED_AS_UNKNOWN` | safe出力で読み取り不明に置換した | 突合結果とマスキング履歴 |
| `LANGUAGE_MISMATCH` | 想定スクリプトと言語がずれている疑い | 文字種ヒストグラム |
| `NUMERIC_TABLE_RISK` | 数値表で誤読が起きている疑い | 数値密度、桁揃い、異常な記号混入 |

## OCRワーニング設計

### `OCR_EMPTY_RESULT`

画像中心のページなのに、OCR結果が極端に少ない場合に出す。

初期条件:

```text
mode = IMAGE_RECONCILE
and image_area_ratio >= 0.60
and ocr_extracted_chars < 50
```

推奨レベル:

```text
needs_retry
```

ユーザーへの推奨:

```text
該当ページだけを、より高い解像度で再投入してください。
```

### `OCR_GARBLED_TEXT`

OCR結果が文字化け、断片化、記号過多になっている場合に出す。

初期条件:

```text
replacement_char_rate >= 0.01
or symbol_ratio >= 0.35
or repeated_fragment_ratio >= 0.30
or single_char_token_ratio >= 0.45
```

推奨レベル:

```text
warning
```

ただし、抽出文字数も少ない場合は `needs_retry` に上げる。

### `OCR_LAYOUT_LOSS`

事前にテーブルや段組みの信号があったのに、OCR後の出力で構造が消えている場合に出す。

初期条件:

```text
preflight.table_score >= 0.40
and extracted_table_count = 0
and extracted text appears flattened
```

推奨レベル:

```text
warning
```

ユーザーへの推奨:

```text
該当ページを高解像度で再投入し、表領域または段組み領域を確認してください。
```

## VLMワーニング設計

### `VLM_TRUNCATED_OUTPUT`

VLMの出力が途中で切れた可能性がある場合に出す。

初期条件:

```text
finish_reason indicates length/content limit
or output reached max_completion_tokens
or Markdown/HTML has unfinished structures
```

推奨レベル:

```text
needs_retry
```

ユーザーへの推奨:

```text
対象ページを絞る、max completion tokensを増やす、または1ページずつ再投入してください。
```

### `VLM_LOW_DETAIL_OUTPUT`

入力ページは高密度なのに、VLM出力が短すぎる場合に出す。

初期条件:

```text
preflight_density is high
and output_chars is low
and expected table/list/form structures are missing
```

推奨レベル:

```text
warning
```

### `VLM_FORMAT_FAILURE`

VLM出力のMarkdownまたはHTMLが構造的に壊れている場合に出す。

初期条件:

```text
response_format = markdown
and markdown table rows are inconsistent

or response_format = html
and HTML parser reports broken structure
```

推奨レベル:

```text
warning
```

高いテーブル信号やフォーム信号があるページでは `needs_retry` に上げる。

## OCR/VLM突合設計

`IMAGE_RECONCILE` では、OCR結果とVLM結果を比較して読み取りの信頼度を決める。比較対象は全文だけではなく、誤読の影響が大きい単位を優先する。

優先比較対象:

- 金額。
- 日付。
- 数量。
- パーセント。
- ID、型番、契約番号、請求番号。
- 氏名、会社名、住所。
- 表セル。
- 表ヘッダ。
- チェックボックス状態。
- 単位つきの値。

比較前には軽い正規化を行う。

```text
normalize(value):
  trim spaces
  normalize full-width / half-width variants
  normalize repeated whitespace
  normalize common punctuation variants
  keep meaningful zeros, symbols, units, and decimal points
```

正規化で一致する場合は `verified` とする。ただし、金額、ID、型番、日付では、`O/0`、`I/1`、`S/5` などの類似文字差分を安易に一致扱いしない。重要値では類似文字差分もワーニング対象にする。

### 突合ステータス

| ステータス | 意味 | safe出力 |
|---|---|---|
| `verified` | OCR/VLMが正規化後に一致 | 値をそのまま出す |
| `format_equivalent` | 表記ゆれはあるが意味が同じ | 値をそのまま出し、必要ならinfo |
| `confusable` | 類似文字差分がある | 重要値ではwarning |
| `single_source` | 片方だけが値を出した | warning |
| `conflict` | OCR/VLMが明確に不一致 | `[[読み取り不明]]` |
| `invalid` | 形式・計算・桁数検証に失敗 | `[[読み取り不明]]` |
| `unreadable` | 両方とも読めない | `[[読み取り不明]]` |

### `OCR_VLM_DISAGREEMENT`

OCRとVLMの読み取り候補が正規化後も一致しない場合に出す。

初期条件:

```text
mode = IMAGE_RECONCILE
and normalized_ocr_value != normalized_vlm_value
```

推奨レベル:

```text
warning
```

重要値または表セルの場合は `CRITICAL_VALUE_DISAGREEMENT` または `TABLE_CELL_DISAGREEMENT` として `needs_retry` に上げる。

### `CRITICAL_VALUE_DISAGREEMENT`

金額、日付、ID、数量などの重要値でOCR/VLMが一致しない場合に出す。

初期条件:

```text
field_type in [amount, date, id, quantity, percent, invoice_number, model_number]
and normalized_ocr_value != normalized_vlm_value
```

推奨レベル:

```text
needs_retry
```

## テキストレイヤーテーブル監査・補完

`TEXT_TABLE_FAST` / `TEXT_TABLE_ACCURATE` の後に、Doclingの構造化テーブルをPDFテキストレイヤーで監査する。目的は、TableFormerが表構造を作れた場合でも、PDF上には存在する文字がセルに入らないケースを検知・補完することである。

処理手順:

1. Doclingのtable provenance bbox、行bbox、列bboxを取得する。
2. 同じページのPDFネイティブテキストをpypdfium2で文字bbox付きで取得する。
3. 表bbox内の文字列をspan化し、Doclingの行列bboxへ座標で割り当てる。
4. 左側の空率が高い非数値列を行見出し候補列とみなす。
5. 候補列内にPDFテキストがあり、構造化セルが空の場合は、縦方向の近接spanからrow-span範囲を推定して先頭行へ補完する。
6. 補完できた場合は `ROW_HEADER_SPAN_MISSED`、安全に割り当てられない表内テキストが残る場合は `TABLE_TEXT_COVERAGE_LOSS` を出す。

この処理は表タイトルや業務キーワードには依存しない。PDFテキスト座標、表bbox、行列bbox、空セル率だけを使う。

safe出力では対象値を `[[読み取り不明]]` に置換する。

例:

```text
OCR: 請求金額 128,000円
VLM: 請求金額 123,000円
safe: 請求金額 [[読み取り不明]]円
```

### `TABLE_CELL_DISAGREEMENT`

表セル単位でOCR/VLMが一致しない場合に出す。

初期条件:

```text
table cell can be aligned
and normalized_ocr_cell != normalized_vlm_cell
```

推奨レベル:

```text
needs_retry
```

safe出力では該当セルだけ `[[読み取り不明]]` に置換する。

例:

```markdown
| 品名 | 数量 | 単価 |
|---|---:|---:|
| 部品A | [[読み取り不明]] | 1,200 |
```

### `MASKED_AS_UNKNOWN`

safe出力で値またはセルを `[[読み取り不明]]` に置換した場合に出す。これはユーザーに見せるワーニングであると同時に、監査用の処理履歴でもある。

記録する候補:

```json
{
  "page": 11,
  "target": "table[0].row[3].col[2]",
  "safe_value": "[[読み取り不明]]",
  "candidates": [
    {"source": "ocr", "value": "8"},
    {"source": "vlm", "value": "3"}
  ],
  "reason": "TABLE_CELL_DISAGREEMENT"
}
```

### `VALUE_VALIDATION_FAILED`

OCR/VLMが一致していても、値としての形式や計算整合性が崩れている場合に出す。これはOCR/VLMの両方が同じ誤読をしたケースを拾うための補助検証である。

初期検証:

- 日付が存在可能な日付か。
- 郵便番号、電話番号、IDの桁数が妥当か。
- 金額列に英字や不自然な記号が混じっていないか。
- `数量 * 単価 = 金額` が概ね成立するか。
- `小計 + 税 = 合計` が概ね成立するか。
- 同じ請求番号や契約番号が文書内で一貫しているか。

推奨レベル:

```text
warning
```

重要値では `needs_retry` に上げ、safe出力では `[[読み取り不明]]` に置換する。

## safe出力方針

最終出力は2系統に分ける。

```text
raw_output:
  OCR、VLM、Doclingの生結果。監査・デバッグ用。

safe_output:
  不一致、低信頼、検証失敗の箇所を [[読み取り不明]] に置換したユーザー利用向け結果。
```

誤読した値をそのまま出すより、重要箇所を `[[読み取り不明]]` に落とすことを優先する。

初期マスキング対象:

- `CRITICAL_VALUE_DISAGREEMENT`
- `TABLE_CELL_DISAGREEMENT`
- `VLM_TRUNCATED_OUTPUT` の影響範囲
- `OCR_EMPTY_RESULT` の影響範囲
- `TABLE_LOW_CONFIDENCE` の高リスクセル
- `VALUE_VALIDATION_FAILED`

## テーブルワーニング設計

### `TABLE_MISSED`

事前にテーブル信号があったのに、Doclingの抽出結果にテーブルがない場合に出す。

初期条件:

```text
preflight.table_score >= 0.40
and extracted_table_count = 0
```

推奨レベル:

```text
warning
```

`complex_table_score >= 0.60` の場合は `needs_retry` に上げる。

### `TABLE_LOW_CONFIDENCE`

表は抽出されたが、構造が不安定な場合に出す。

初期条件:

```text
empty_cell_ratio >= 0.40
or row_length_variance is high
or markdown table parse failed
```

推奨レベル:

```text
warning
```

## ワーニングスコア

各ワーニングには `0.0` から `1.0` のスコアを持たせる。

初期マッピング:

```text
0.00 - 0.39: info
0.40 - 0.74: warning
0.75 - 1.00: needs_retry
```

例:

```text
OCR_GARBLED_TEXT score =
  0.30 * normalized_symbol_ratio
  + 0.30 * normalized_replacement_char_rate
  + 0.20 * repeated_fragment_ratio
  + 0.20 * single_char_token_ratio
```

スコアだけでなく、上位の根拠も保持する。UIでは「なぜワーニングになったか」を短く表示する。

## ワーニング出力スキーマ

JSON形式:

```json
{
  "page": 11,
  "mode": "IMAGE_RECONCILE",
  "level": "needs_retry",
  "code": "OCR_EMPTY_RESULT",
  "score": 0.91,
  "message": "画像中心のページですが、OCRで抽出された文字数が非常に少ないです。",
  "evidence": {
    "image_area_ratio": 0.94,
    "ocr_extracted_chars": 18,
    "table_score": 0.12
  },
  "suggested_action": "このページだけを高解像度で再投入してください。"
}
```

CSV列:

```text
page,mode,level,code,score,message,suggested_action,
text_chars,text_quality_score,image_area_ratio,table_score,
complex_table_score,ocr_extracted_chars,vlm_extracted_chars,
safe_mask_count,extracted_table_count,elapsed_seconds
```

## UI設計

Streamlit UIでは、以下をリアルタイムに表示する。

1. ルーティングサマリー。
2. ページ単位の処理状況。
3. ワーニング一覧。

表示方針:

- プリフライト完了後、ページごとのモードをすぐ表示する。
- 処理中は `pending`、`running`、`done`、`warning` を表示する。
- ワーニングはページまたはグループ完了ごとに追記する。
- `warning` と `needs_retry` でフィルタできるようにする。
- ワーニングごとに推奨再投入設定を表示する。

表示例:

```text
Page 11: OCR失敗の可能性があります。
画像中心のページですが、抽出文字数が18文字のみでした。
Page 11のみを高解像度で再投入してください。
```

## 再投入アクション

| ワーニング | 推奨アクション |
|---|---|
| `LOW_INPUT_RESOLUTION` | 解像度を上げる、または高解像度PDFを投入する |
| `OCR_EMPTY_RESULT` | 対象ページだけ高解像度で再投入する |
| `OCR_GARBLED_TEXT` | 対象ページだけ高解像度で再投入する |
| `OCR_LAYOUT_LOSS` | 対象ページを高解像度で再投入し、表領域を確認する |
| `TABLE_MISSED` | `TEXT_TABLE_FAST` または `TEXT_TABLE_ACCURATE` で再投入する |
| `TABLE_LOW_CONFIDENCE` | `TEXT_TABLE_ACCURATE` で再投入する |
| `VLM_TRUNCATED_OUTPUT` | ページ範囲を絞る、token上限を上げる、1ページずつ再投入する |
| `VLM_LOW_DETAIL_OUTPUT` | 該当ページだけ高解像度で再投入する |
| `VLM_FORMAT_FAILURE` | Markdown/HTML形式を切り替える、または該当ページだけ再投入する |
| `CRITICAL_VALUE_DISAGREEMENT` | 該当ページを確認し、必要なら高解像度で再投入する |
| `TABLE_CELL_DISAGREEMENT` | 該当セルを確認し、必要なら該当ページを再投入する |
| `VALUE_VALIDATION_FAILED` | 元PDFと該当値を照合し、必要なら該当ページを再投入する |
| `MASKED_AS_UNKNOWN` | `raw_output` の候補値を確認し、必要なら該当ページを再投入する |

## ワーニング処理の性能制約

標準のワーニング処理で使ってよいもの:

- プリフライトで得たメタデータ。
- Docling抽出結果。
- VLM APIのレスポンスメタデータ。
- 抽出テキストとテーブル文字列の線形スキャン。
- 既にパイプライン上で扱っているMarkdown/HTMLの軽量パース。

標準のワーニング処理で行わないもの:

- OCRの再実行。
- VLMの再実行。
- LLMによる品質判定。
- ピクセル単位の表再構成。
- 高コストな画像類似度計算。

高コストな検証は、将来のデバッグモードまたは品質評価モードとして明示的に分ける。

## 計測項目

ページ単位で記録する項目:

- ページ番号。
- ルーティングモード。
- 各プリフライト信号。
- プリフライト処理時間。
- 変換処理時間。
- バリデーション処理時間。
- 抽出文字数。
- 抽出テーブル数。
- 抽出セル数。
- VLM token usage。
- ワーニング一覧。

PDF単位で記録する項目:

- 総処理時間。
- 秒/ページ。
- モード別ページ数。
- ワーニングコード別件数。
- `needs_retry` ページ数。
- 推奨再投入ページ範囲。

## 実装フェーズ

### Phase 1: テキスト・テーブルルーティング

- テキストレイヤー品質スコアを実装する。
- 軽量テーブルスコアを実装する。
- `TEXT_LIGHT`、`TEXT_TABLE_FAST`、`TEXT_TABLE_ACCURATE` に分岐する。
- `TEXT_LAYER_SUSPECT`、`TABLE_MISSED`、`TABLE_LOW_CONFIDENCE` を出す。

### Phase 2: 画像ページルーティング

- 画像面積率と低解像度判定を追加する。
- `IMAGE_RECONCILE` に分岐し、OCR/VLMを両方実行する。
- テキストレイヤー付きページの大きな低テキスト重なり視覚領域には `IMAGE_RECONCILE_APPEND` を付与する。
- `LOW_INPUT_RESOLUTION`、`OCR_EMPTY_RESULT`、`OCR_GARBLED_TEXT` を出す。

### Phase 3: OCR/VLM突合とVLMバリデーション

- OCR/VLMの重要値と表セルを突合する。
- 不一致箇所をsafe出力で `[[読み取り不明]]` に置換する。
- VLMのfinish reason、token使用量、出力長を記録する。
- Markdown/HTMLの軽量構造チェックを追加する。
- `OCR_VLM_DISAGREEMENT`、`CRITICAL_VALUE_DISAGREEMENT`、`TABLE_CELL_DISAGREEMENT`、`MASKED_AS_UNKNOWN`、`VLM_TRUNCATED_OUTPUT`、`VLM_LOW_DETAIL_OUTPUT`、`VLM_FORMAT_FAILURE` を出す。

### Phase 4: UIと再投入導線

- リアルタイムワーニング一覧をUIに追加する。
- ワーニングから推奨再投入設定を表示する。
- routing CSVとwarning CSVを出力する。

## 未決事項

- 軽量プリフライトに使うPDFパーサを何にするか。
  - 候補: PyMuPDF、pypdfium2、Docling backend、独自adapter。
- 非連続ページを同一モードでまとめるか、連続ページ単位に限定するか。
- VLM自動使用を許可するか、ユーザーの明示設定または予算設定を必須にするか。
- 大規模PDFでワーニングが大量に出た場合、UIで何件まで即時表示するか。
