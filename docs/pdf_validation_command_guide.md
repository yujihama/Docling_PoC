# PDF検証コマンド実行手順

## 目的

PDFが与えられたときに、現在のルーティング・座標復元・VLM fallback・品質チェックを通して処理し、`safe_output.md` と各種metadataを確認するための手順をまとめる。

## 前提

- 作業ディレクトリはリポジトリルートにする。
- Pythonは `.venv\Scripts\python.exe` を使う。
- VLMを使う場合は `.env` または環境変数に `OPENAI_API_KEY` が必要。
- VLM実行ではPDFページ画像がOpenAI APIへ送信される。
- 出力は既定で `outputs\docling_routing_runs\<run-id>\` に保存される。

```powershell
cd C:\Users\nyham\work\docling_poc
```

## 標準実行

通常の検証では以下を使う。

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf "C:\path\to\input.pdf" `
  --run-id "validation_input_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --enable-table-vlm-fallback
```

この設定の意味:

| option | 意味 |
|---|---|
| `--use-coordinate-table-reconstruction` | テキストレイヤー表はまずPDF座標ベースで高速復元する |
| `--enable-table-vlm-fallback` | 座標復元が低信頼の場合のみ `TEXT_TABLE_VLM` にfallbackする |
| `--compare-mode vlm-vlm` | 画像・スキャン系ページはVLM同士で比較する |
| `--model gpt-5.4-mini` | primary VLM |
| `--secondary-model gpt-5.4-mini` | secondary VLM。同一モデルでも独立実行の揺れを検出できる |
| `--reasoning-effort none` | 検証速度を優先する |

## 大規模PDF・PDFium不安定PDF向け

大規模PDFや、同じPDFを並列openするとPDFium/Doclingが不安定になるPDFでは、シリアル実行を使う。

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf "C:\path\to\large.pdf" `
  --run-id "validation_large_serial_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --enable-table-vlm-fallback `
  --max-parallel-table-groups 1 `
  --disable-parallel-reconcile-candidates
```

使う基準:

- `Failed to load document (PDFium: Data format error)` が出る
- `Input document ... is not valid` が出る
- 同じPDFで並列実行時だけ失敗する
- 再現性確認を優先したい

## VLMなしローカル検証

外部送信なしで、座標復元・テキスト処理だけを確認したい場合に使う。

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf "C:\path\to\input.pdf" `
  --run-id "validation_local_only_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --disable-embedded-visual-append
```
注意:

- `--enable-table-vlm-fallback` を付けない。
- 画像中心ページがある場合、完全な検証にはならない。
- Excel由来PDFなど、テキストレイヤー表が中心のPDFの高速確認に向いている。

## 特定ページを強制VLM処理

特定ページを画像/VLM突合で確認したい場合は `--force-reconcile-pages` を使う。

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf "C:\path\to\input.pdf" `
  --run-id "validation_force_pages_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --enable-table-vlm-fallback `
  --force-reconcile-pages "2,5-7"
```

## フォルダ内PDFを一括実行

対象フォルダ配下のPDFをすべて実行する例。

```powershell
$pdfDir = "C:\Users\nyham\work\docling_poc\outputs\docling_benchmark\xlsx"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Get-ChildItem -LiteralPath $pdfDir -Filter *.pdf | ForEach-Object {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
  $runId = "validation_${name}_${timestamp}"

  .\.venv\Scripts\python.exe .\run_routed_pdf.py `
    --pdf $_.FullName `
    --run-id $runId `
    --compare-mode vlm-vlm `
    --model gpt-5.4-mini `
    --secondary-model gpt-5.4-mini `
    --reasoning-effort none `
    --use-coordinate-table-reconstruction `
    --enable-table-vlm-fallback
}
```

VLMなしで一括実行する場合:

```powershell
$pdfDir = "C:\Users\nyham\work\docling_poc\outputs\docling_benchmark\xlsx"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Get-ChildItem -LiteralPath $pdfDir -Filter *.pdf | ForEach-Object {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
  $runId = "validation_local_${name}_${timestamp}"

  .\.venv\Scripts\python.exe .\run_routed_pdf.py `
    --pdf $_.FullName `
    --run-id $runId `
    --compare-mode vlm-vlm `
    --model gpt-5.4-mini `
    --secondary-model gpt-5.4-mini `
    --reasoning-effort none `
    --use-coordinate-table-reconstruction `
    --disable-embedded-visual-append
}
```

## 出力ファイル

実行後は以下を確認する。

| file | 用途 |
|---|---|
| `safe_output.md` | 最終的にユーザーへ返す安全側出力 |
| `metadata.json` | 処理時間、mode別集計、warning集計、unknown件数 |
| `warnings.csv` / `warnings.json` | warning詳細 |
| `segments.json` | ページ別/セグメント別の処理結果、diagnostics |
| `preflight.csv` / `preflight.json` | ページごとのルーティング判定 |
| `raw_output.md` | raw出力 |
| `raw_vlm_output.md` | VLM出力がある場合のraw |
| `raw_candidate_a_output.md` / `raw_candidate_b_output.md` | 比較候補のraw |

## 結果確認コマンド

run idを指定する。

```powershell
$runId = "validation_input_20260506"
$run = "C:\Users\nyham\work\docling_poc\outputs\docling_routing_runs\$runId"
```

主要指標:

```powershell
Get-Content "$run\metadata.json" -Raw |
  ConvertFrom-Json |
  Select-Object page_count,total_seconds,seconds_per_page,safe_unknown_token_count,safe_mask_count,warning_counts,warning_level_counts |
  ConvertTo-Json -Depth 8
```

warning一覧:

```powershell
Import-Csv "$run\warnings.csv" |
  Select-Object page,mode,level,code,score |
  Format-Table -AutoSize
```

`needs_retry` だけ確認:

```powershell
Import-Csv "$run\warnings.csv" |
  Where-Object { $_.level -eq "needs_retry" } |
  Select-Object page,mode,code,score |
  Format-Table -AutoSize
```

`[[読み取り不明]]` の有無:

```powershell
Select-String -Path "$run\safe_output.md" -Pattern "\[\[読み取り不明\]\]"
```

mode別平均時間:

```powershell
@'
import json
from collections import Counter
from pathlib import Path

run = Path(r"C:\Users\nyham\work\docling_poc\outputs\docling_routing_runs\validation_input_20260506")
segments = json.loads((run / "segments.json").read_text(encoding="utf-8"))

page_counts = Counter()
elapsed = Counter()
for segment in segments:
    pages = segment["end_page"] - segment["start_page"] + 1
    mode = segment["mode"]
    page_counts[mode] += pages
    elapsed[mode] += float(segment.get("elapsed_seconds") or 0)

for mode in sorted(page_counts):
    print(mode, "pages=", page_counts[mode], "total=", round(elapsed[mode], 3), "avg=", round(elapsed[mode] / page_counts[mode], 3))
'@ | .\.venv\Scripts\python.exe -
```

## 判定基準

最低限、以下を見る。

| 観点 | OKの目安 |
|---|---|
| `safe_unknown_token_count` | 原則0。発生時は該当ページを確認 |
| `safe_mask_count` | 原則0。発生時はOCR/VLM不一致またはcoordinate不支持を確認 |
| `needs_retry` | 重要ページで出た場合は再投入・ページ絞り込み・解像度変更を検討 |
| `TEXT_TABLE_COORD`比率 | テキストレイヤー表PDFでは高いほど高速 |
| `TEXT_TABLE_VLM`時間 | ボトルネック。多い場合は座標復元品質条件を確認 |
| `IMAGE_RECONCILE`時間 | 画像・スキャンページのコスト。必要ページだけに絞る |

## よく使うrun id命名

```text
validation_<pdf名>_<yyyyMMdd_HHmmss>
regression_<case名>_<変更内容>
local_<pdf名>_<yyyyMMdd_HHmmss>
serial_<pdf名>_<yyyyMMdd_HHmmss>
```

例:

```text
validation_case12_20260506_001
regression_case13_after_text_alignment_coord
serial_case13_full_20260506_001
```

## 代表例

case12:

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf ".\outputs\docling_benchmark\pdfs\case12_complex_dummy.pdf" `
  --run-id "validation_case12_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --enable-table-vlm-fallback
```

case13全体の安定実行:

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf ".\outputs\docling_benchmark\pdfs\case13.pdf" `
  --run-id "validation_case13_full_serial_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --enable-table-vlm-fallback `
  --max-parallel-table-groups 1 `
  --disable-parallel-reconcile-candidates
```

XLSX由来PDF:

```powershell
.\.venv\Scripts\python.exe .\run_routed_pdf.py `
  --pdf ".\outputs\docling_benchmark\xlsx\consolidated_BS.pdf" `
  --run-id "validation_xlsx_consolidated_BS_20260506" `
  --compare-mode vlm-vlm `
  --model gpt-5.4-mini `
  --secondary-model gpt-5.4-mini `
  --reasoning-effort none `
  --use-coordinate-table-reconstruction `
  --disable-embedded-visual-append
```
