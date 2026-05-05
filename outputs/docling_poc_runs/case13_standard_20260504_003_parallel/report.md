# case13 Standard Docling PoC (case13_standard_20260504_003_parallel)

## Scope
- PDF: `C:\Users\nyham\work\docling_poc\outputs\docling_benchmark\pdfs\case13.pdf`
- Case ID: `C13`
- Standard config depth: `focused`
- Page chunk size(s): `8` pages
- Worker process setting(s): `1, 3`
- Baseline for relative accuracy: `std_baseline_accurate`
- Accuracy note: case13 has no registered ground truth. Accuracy values here are relative retention metrics against the baseline extraction, not human-verified absolute accuracy.

## Summary
- Configs attempted: 10
- Successful configs: 9
- Failed configs: 1
- Total measured time: 203.75 min
- Median seconds/page: 4.915
- Baseline speed: 4.852 sec/page, tables=397, markdown_chars=1243861
- Fastest config: `std_no_table_structure` at 0.459 sec/page (10.57x vs baseline)
- Fastest high-retention config (relative_overall >= 0.95): `std_baseline_accurate` at 4.852 sec/page (1.00x vs baseline)

## Ranking

| Rank | Config | Relative overall | Markdown recall | Table recall | Tables | Sec/page | Total sec | Workers | Notes |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `std_baseline_accurate` | 1.0000 | 1.0000 | 1.0000 | 397 | 4.852 | 1533.208 | 3 |  |
| 2 | `std_page_images_scale_2` | 1.0000 | 1.0000 | 1.0000 | 397 | 4.915 | 1552.985 | 3 | scale=2.0 |
| 3 | `std_batch_small` | 1.0000 | 1.0000 | 1.0000 | 405 | 4.957 | 1566.47 | 3 | batch=small |
| 4 | `std_batch_large` | 1.0000 | 1.0000 | 1.0000 | 405 | 5.896 | 1863.1 | 1 | batch=large |
| 5 | `std_no_ocr` | 0.9997 | 1.0000 | 1.0000 | 396 | 4.931 | 1558.205 | 3 | ocr=false |
| 6 | `std_force_backend_text` | 0.9597 | 0.9520 | 0.9502 | 396 | 4.894 | 1546.651 | 3 | backend_text |
| 7 | `std_table_no_cell_match` | 0.8390 | 0.7232 | 0.8960 | 397 | 5.562 | 1757.737 | 3 | cell_matching=false |
| 8 | `std_table_fast` | 0.8234 | 0.6636 | 0.9316 | 392 | 2.146 | 678.171 | 3 | TableFormer fast |
| 9 | `std_no_table_structure` | 0.7614 | 0.7944 | 0.6416 | 405 | 0.459 | 144.915 | 3 | table_structure=false |

## Failures

- `std_force_full_page_ocr`: A process in the process pool was terminated abruptly while the future was running or pending.

## Interpretation Guide
- `relative_markdown_recall` samples baseline markdown lines/chunks and checks whether each appears in the candidate extraction.
- `relative_table_recall` samples baseline table headers/cells and checks whether each appears in candidate tables or markdown.
- `relative_overall = 0.45*markdown + 0.35*table + 0.10*table_count_ratio + 0.10*markdown_chars_ratio`.
- For true absolute accuracy, add human-reviewed expected anchors/table cells for case13 and re-run the regular benchmark scorer.
