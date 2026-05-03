# Docling Benchmark Report

Synthetic PDFs were generated with deterministic ground truth strings.
Accuracy is a string-recall benchmark, not a semantic human-evaluation score.

## Summary

- PDFs: 5
- Total pages: 36
- Total measured time: 140.001 sec
- Mean overall score: 0.913

## Results

| Case | Pages | Tags | Tables expected/detected | Text recall | Table recall | Detection F1 | Confidence | Low confidence | Overall | Total sec | Sec/page |
|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| C01 | 2 | clean, vector, short | 2/2 | 0.750 | 1.000 | 1.000 | 0.934 | false | 0.912 | 5.007 | 2.504 |
| C02 | 5 | dense, merged-header, rotated | 5/5 | 1.000 | 1.000 | 1.000 | 1.000 | false | 1.000 | 16.165 | 3.233 |
| C03 | 9 | mixed-orientation, charts, sidebars | 9/7 | 0.556 | 0.690 | 0.875 | 0.707 | true | 0.651 | 15.073 | 1.675 |
| C04 | 16 | long, dense, cross-page | 16/16 | 1.000 | 1.000 | 1.000 | 1.000 | false | 1.000 | 51.038 | 3.190 |
| C05 | 4 | scanned, image-only, ocr | 4/4 | 1.000 | 1.000 | 1.000 | 1.000 | false | 1.000 | 52.718 | 13.180 |

## Notes

### C01 - 2-page clean vector PDF with ordinary tables.
- File: `outputs/docling_benchmark/pdfs/case01_clean_financial_2p.pdf`
- Missing text anchors sample: `C01-FOOTNOTE-P01, C01-FOOTNOTE-P02`
- Missing table cells sample: none

### C02 - 5-page dense vector PDF with merged table headers and rotated side labels.
- File: `outputs/docling_benchmark/pdfs/case02_dense_multisection_5p.pdf`
- Missing text anchors sample: none
- Missing table cells sample: none

### C03 - 9-page vector PDF with portrait/landscape pages, sidebars, charts, and tables.
- File: `outputs/docling_benchmark/pdfs/case03_mixed_orientation_9p.pdf`
- Missing text anchors sample: `C03-CHART-P01, C03-CHART-P02, C03-CHART-P03, C03-CHART-P04, C03-CHART-P05`
- Missing table cells sample: `AMT-1043.86, AMT-1898.40, AMT-2221.99, AMT-2331.52, AMT-2534.37`

### C04 - 16-page long vector register with repeated dense tables.
- File: `outputs/docling_benchmark/pdfs/case04_long_register_16p.pdf`
- Missing text anchors sample: none
- Missing table cells sample: none

### C05 - 4-page image-only PDF that simulates scanned forms.
- File: `outputs/docling_benchmark/pdfs/case05_scanned_image_4p.pdf`
- Missing text anchors sample: none
- Missing table cells sample: none
