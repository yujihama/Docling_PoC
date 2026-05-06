# PDF検証所見 2026-05-06

## 実施内容

- `docs/pdf_validation_command_guide.md` の標準実行をベースに、実務で起こりやすいPDFを10件作成・取得して実行した。
- 追加で、本文はテキストレイヤー、押印だけが画像レイヤーの混在PDF `V09` を作成し、標準実行相当で検証した。
- 追加で、複雑な多段ヘッダー表 `V10` と、Excelの長大・横長表をPDF化した想定の `V11` を作成し、標準実行相当で検証した。
- 作成PDFは `outputs/pdf_validation_suite/pdfs/` に保存した。
- 期待アンカーは `outputs/pdf_validation_suite/manifest.json` に保存した。
- 標準実行の集計は `outputs/pdf_validation_suite/validation_summary_20260506.csv` と `outputs/pdf_validation_suite/validation_summary_20260506.md` に保存した。
- 画像押印ケースの集計は `outputs/pdf_validation_suite/validation_image_stamps_summary_20260506.csv` と `outputs/pdf_validation_suite/validation_image_stamps_summary_20260506.md` に保存した。
- 複雑表・Excel長大表ケースの集計は `outputs/pdf_validation_suite/validation_extended_tables_summary_20260506.csv` と `outputs/pdf_validation_suite/validation_extended_tables_summary_20260506.md` に保存した。
- 欠落が出た5件は `--force-reconcile-pages` でも再実行し、`outputs/pdf_validation_suite/validation_force_summary_20260506.csv` と `outputs/pdf_validation_suite/validation_force_summary_20260506.md` に保存した。

## テストデータ

| case | 内容 | ページ | 種別 |
|---|---|---:|---|
| V01 | 請求書・発注書、明細表、バーコード、回転承認スタンプ | 2 | 生成 |
| V02 | 銀行明細、二段組注記、密な取引表、透かし、極小文字 | 2 | 生成 |
| V03 | 申込フォーム、チェックボックス、下線入力欄、承認表、黒塗り | 2 | 生成 |
| V04 | 日本語請求明細、CIDフォント、縦書き回転ラベル、表 | 2 | 生成 |
| V05 | 画像のみの低品質スキャン領収書、傾き、ノイズ、薄い行 | 2 | 生成 |
| V06 | 横向き技術図面、グリッド、コールアウト、回転文字、極小注記 | 1 | 生成 |
| V07 | ラボレポート、単位、不等号、基準範囲、黒塗り | 1 | 生成 |
| V08 | メールスレッドPDF、引用、コード風テキスト、例外表 | 2 | 生成 |
| V09 | 本文テキストレイヤー + 画像レイヤー押印。大きな承認印、受領印、表に重なる薄い印、小型印 | 2 | 生成 |
| V10 | 複雑な構造の表。結合セル風の多段ヘッダー、サブヘッダー、小計行、複数行セル、注記列 | 2 | 生成 |
| V11 | Excelの長大・横長表をPDF化した想定。6ページ、極小文字、繰り返しヘッダー、数式風セル | 6 | 生成 |
| W01 | IRS Form W-9、記入フォーム、チェックボックス、説明ページ | 6 | Web取得 |
| W02 | SEC Form 10-K、長文法定フォーム、表項目、長文 | 19 | Web取得 |

## 標準実行結果

| case | mode概要 | unknown | mask | needs_retry | アンカー回収 |
|---|---|---:|---:|---:|---:|
| V01 | TEXT_TABLE_ACCURATE | 0 | 0 | 0 | 36/38 |
| V02 | TEXT_TABLE_FAST | 0 | 0 | 0 | 64/64 |
| V03 | TEXT_TABLE_FAST | 0 | 0 | 0 | 26/28 |
| V04 | TEXT_TABLE_FAST | 0 | 0 | 0 | 32/34 |
| V05 | IMAGE_RECONCILE | 0 | 0 | 0 | 44/44 |
| V06 | TEXT_TABLE_FAST | 0 | 0 | 0 | 11/13 |
| V07 | TEXT_TABLE_FAST | 0 | 0 | 0 | 16/17 |
| V08 | TEXT_TABLE_FAST, TEXT_TABLE_VLM fallback | 0 | 0 | 0 | 16/16 |
| W01 | TEXT_TABLE系, TEXT_LIGHT, TEXT_TABLE_VLM fallback | 0 | 0 | 0 | 6/6 |
| W02 | TEXT_TABLE系, TEXT_LIGHT, IMAGE_RECONCILE | 0 | 0 | 0 | 6/6 |

## 画像レイヤー押印 追加検証

| case | mode概要 | extra action | unknown | mask | needs_retry | アンカー回収 |
|---|---|---|---:|---:|---:|---:|
| V09 | TEXT_TABLE_FAST | IMAGE_RECONCILE_APPEND x2 | 1 | 2 | 4 | 35/38 |

- V09 page 1: 大きな画像承認印 `IMG-STAMP-V09-P01-APPROVED` は回収できた。
- V09 page 2: 受領印 `IMG-STAMP-V09-P02-RECEIVED` は埋め込み画像候補として検知されたが、VLM候補間で `IMG` / `MG` の差分が出て安全側でマスクされた。
- V09 page 2: 表に重なる薄い押印 `IMG-STAMP-V09-P02-OVERLAP` と小型押印 `IMG-STAMP-V09-P02-SMALL` は現在の埋め込み画像候補条件では回収対象外になった。

## 複雑表・Excel長大表 追加検証

| case | mode概要 | unknown | mask | needs_retry | アンカー回収 | warning |
|---|---|---:|---:|---:|---:|---|
| V10 | TEXT_TABLE_ACCURATE -> TEXT_TABLE_COORD | 0 | 0 | 0 | 38/38 | COORDINATE_TEXT_LAYER_SUPPLEMENTED x2, COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS x2 |
| V11 | TEXT_TABLE_ACCURATE -> TEXT_TABLE_COORD | 0 | 0 | 0 | 44/44 | COORDINATE_TEXT_LAYER_SUPPLEMENTED x6 |

- V10: 2ページとも複雑表シグナルで `TEXT_TABLE_ACCURATE` に入り、座標復元で処理された。多段ヘッダーや小計行のアンカーは回収できた。
- V11: 6ページのExcel風長大表も `TEXT_TABLE_ACCURATE` で処理され、期待アンカーは全件回収できた。
- 両ケースともVLM fallbackは発生せず、テキストレイヤー補完のinfo warningのみ発生した。

## 見つかった抜け漏れ

- V01: 回転承認スタンプ `V01-APPROVAL-STAMP-P01/P02` が `safe_output.md` に出なかった。標準実行では warning なし相当で、unknown/mask/retry も0。
- V03: ヘッダにあるフォームID `FORM-V03-APPL-01/02` が出なかった。フォーム入力値や表の主要値は出ているが、タイトル行末のIDが落ちた。
- V04: 回転した縦書きラベル `V04-縦書き-P01/P02` が標準実行では落ちた。
- V06: 技術図面の `CALL-V06-03-GRID` と回転文字 `ROTATE-V06-NORTH-ELEVATION` が落ちた。図面の小さいコールアウト・回転文字に弱い。
- V07: ラボ注記の先頭トークン `NOTE-V07-DELTA-CHECK` が落ち、本文の `compare against 2026-04 specimen.` だけ残った。
- V09: 画像レイヤー押印のうち、重なり印・小型印は検知閾値の外にあり、受領印はVLM不一致後の安全側マスクで `safe_output.md` に残らなかった。

## 強制VLM再実行の結果

- V04の縦書きラベルは `--force-reconcile-pages 1-2` で回収できた。ただし `VLM_COORD_TABLE_STRUCTURE_MISMATCH` が出た。
- V01、V03、V06、V07は欠落が残り、さらに `MASKED_AS_UNKNOWN`、`safe_unknown_token_count`、`needs_retry` が増えた。
- このため、強制VLMは回転日本語の確認には効く場合があるが、表や図面を含むページへ一律適用すると安全側マスクや構造不一致が増える。

## 判断

標準実行は、スキャン画像、通常表、Web由来フォームでは概ね安定した。一方で、`safe_unknown_token_count=0`、`safe_mask_count=0`、`needs_retry=0` でも、回転文字、縦書き、フォームタイトル末尾、技術図面の小さいラベル、表外注記が静かに落ちるケースがあった。

## 推奨追加確認

- rotated text、vertical text、tiny callout、form header/footer、table外注記を含むアンカー回収テストを回帰テストに入れる。
- 画像レイヤー押印について、大きい独立画像・本文に重なる画像・小型印を分けた回帰テストを入れる。
- 複雑表について、多段ヘッダー、小計行、Excel由来の長大・横長表を回帰テストに入れる。
- `TEXT_TABLE_COORD`/`TEXT_TABLE_FAST` の出力で、表に巻き込まれているページ上部・下部テキストが消えていないかをチェックする。
- `VLM_COORD_WEAK_EVIDENCE` と `COORDINATE_TABLE_FALLBACK_TO_VLM` が多いWebフォームは、アンカー回収は成功してもコスト・再現性リスクありとしてレビュー対象にする。
- `--force-reconcile-pages` は全体適用ではなく、回転・縦書き・画像化ページに絞って使う。
