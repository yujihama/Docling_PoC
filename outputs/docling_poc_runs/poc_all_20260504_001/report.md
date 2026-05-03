# Docling/OpenAI VLM PoC Report

## Summary

- Run ID: `poc_all_20260504_001`
- Latest command: matrix `vlm` / phase `pilot`
- Measured pipelines: standard, vlm
- Measured phases: full, pilot
- Rows: 160 (160 successful)
- Successful pages measured: 760
- Total measured time: 4210.203 sec
- Estimated OpenAI cost from measured rows: $2.9345
- Fixed VLM max completion tokens: 12000

## Budget Gate

- Budget: $30.0
- Estimate status: `estimated`
- Estimated full VLM cost: $205.0153
- Estimated additional VLM cost: $202.0808
- Full phase recommendation: `blocked_by_budget`

## Best Configurations

| pipeline | config_id | success | mean_overall | mean_table_recall | mean_detection_f1 | seconds_per_page | estimated_cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- |
| vlm | vlm_gpt_5_4_mini_html_s2_0_rnone_strict_preserve | 5 | 0.9727 | 0.9993 | 0.88 | 6.802 | 0.159044 |
| vlm | vlm_gpt_5_4_mini_markdown_s2_0_rnone_table_first | 5 | 0.971 | 0.9869 | 0.8167 | 4.794 | 0.107643 |
| vlm | vlm_gpt_5_2_markdown_s2_0_rnone_strict_preserve | 5 | 0.9533 | 0.9292 | 0.8467 | 9.012 | 0.386152 |
| vlm | vlm_gpt_5_4_markdown_s2_0_rnone_strict_preserve | 5 | 0.9413 | 0.9329 | 0.8571 | 8.767 | 0.355608 |
| vlm | vlm_gpt_5_4_mini_markdown_s2_0_rlow_strict_preserve | 5 | 0.9192 | 0.8927 | 0.8081 | 5.45 | 0.124283 |
| vlm | vlm_gpt_5_4_mini_markdown_s2_5_rnone_strict_preserve | 5 | 0.9191 | 0.8924 | 0.8081 | 5.388 | 0.107019 |
| vlm | vlm_gpt_5_4_mini_markdown_s2_0_rnone_strict_preserve | 5 | 0.9145 | 0.8841 | 0.7829 | 4.957 | 0.106845 |
| vlm | vlm_gpt_5_4_mini_markdown_s2_0_rmedium_strict_preserve | 5 | 0.9142 | 0.8836 | 0.7778 | 9.595 | 0.209444 |
| standard | std_no_table_structure | 11 | 0.9132 | 0.97 | 0.928 | 1.998 | 0.0 |
| standard | std_batch_small | 11 | 0.9132 | 0.97 | 0.928 | 3.645 | 0.0 |
| standard | std_baseline_accurate | 11 | 0.9132 | 0.97 | 0.928 | 3.697 | 0.0 |
| standard | std_force_backend_text | 11 | 0.9132 | 0.97 | 0.928 | 3.706 | 0.0 |

## VLM Setting Summary

| model | response_format | scale | reasoning_effort | prompt_variant | success | mean_overall | seconds_per_page | estimated_cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.4-mini | html | 2.0 | none | strict_preserve | 5 | 0.9727 | 6.802 | 0.159044 |
| gpt-5.4-mini | markdown | 2.0 | none | table_first | 5 | 0.971 | 4.794 | 0.107643 |
| gpt-5.2 | markdown | 2.0 | none | strict_preserve | 5 | 0.9533 | 9.012 | 0.386152 |
| gpt-5.4 | markdown | 2.0 | none | strict_preserve | 5 | 0.9413 | 8.767 | 0.355608 |
| gpt-5.4-mini | markdown | 2.0 | low | strict_preserve | 5 | 0.9192 | 5.45 | 0.124283 |
| gpt-5.4-mini | markdown | 2.5 | none | strict_preserve | 5 | 0.9191 | 5.388 | 0.107019 |
| gpt-5.4-mini | markdown | 2.0 | none | strict_preserve | 5 | 0.9145 | 4.957 | 0.106845 |
| gpt-5.4-mini | markdown | 2.0 | medium | strict_preserve | 5 | 0.9142 | 9.595 | 0.209444 |
| gpt-5.5 | markdown | 2.0 | none | strict_preserve | 5 | 0.8776 | 8.475 | 1.305985 |
| gpt-5.4-mini | markdown | 1.0 | none | strict_preserve | 5 | 0.8473 | 3.779 | 0.072478 |

## Case-Level Fit

| case_id | tags | best_accuracy_pipeline | best_accuracy_config | best_accuracy_overall | best_accuracy_seconds_per_page | fastest_pipeline | fastest_config | fastest_seconds_per_page |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | clean, vector, short | vlm | vlm_gpt_5_5_markdown_s2_0_rnone_strict_preserve | 1.0 | 8.527 | standard | std_no_table_structure | 0.842 |
| C02 | dense, merged-header, rotated | standard | std_baseline_accurate | 1.0 | 3.181 | standard | std_no_table_structure | 0.623 |
| C03 | mixed-orientation, charts, sidebars | vlm | vlm_gpt_5_4_markdown_s2_0_rnone_strict_preserve | 0.9598 | 10.364 | standard | std_no_table_structure | 0.59 |
| C04 | long, dense, cross-page | standard | std_baseline_accurate | 1.0 | 3.215 | standard | std_no_table_structure | 0.676 |
| C05 | scanned, image-only, ocr | standard | std_baseline_accurate | 1.0 | 13.269 | standard | std_no_ocr | 1.611 |
| C06 | real, japanese, financial-disclosure, tables | standard | std_baseline_accurate | 1.0 | 1.114 | standard | std_no_table_structure | 0.551 |
| C07 | japanese, multicolumn, vertical-label | standard | std_baseline_accurate | 0.9731 | 1.974 | standard | std_no_table_structure | 0.553 |
| C08 | form, checkbox, blank-fields | standard | std_baseline_accurate | 1.0 | 1.96 | standard | std_no_table_structure | 0.544 |
| C09 | scanned, low-quality, skewed, ocr | vlm | vlm_gpt_5_5_markdown_s2_0_rnone_strict_preserve | 1.0 | 6.802 | standard | std_no_ocr | 1.478 |
| C10 | nested-tables, merged-header, irregular | standard | std_baseline_accurate | 1.0 | 2.348 | standard | std_no_table_structure | 0.572 |
| C11 | charts, formula, code, tables | standard | std_baseline_accurate | 0.6937 | 1.305 | standard | std_no_table_structure | 0.529 |

## Tag-Level Tendencies

| tag | pipeline | runs | mean_overall | seconds_per_page |
| --- | --- | --- | --- | --- |
| blank-fields | standard | 10 | 0.9922 | 3.183 |
| charts | standard | 20 | 0.6712 | 3.271 |
| charts | vlm | 10 | 0.923 | 7.825 |
| checkbox | standard | 10 | 0.9922 | 3.183 |
| clean | standard | 10 | 0.9037 | 3.868 |
| clean | vlm | 10 | 0.9856 | 6.611 |
| code | standard | 10 | 0.6937 | 2.339 |
| cross-page | standard | 10 | 0.9981 | 5.217 |
| dense | standard | 20 | 0.9979 | 5.338 |
| financial-disclosure | standard | 10 | 0.9747 | 3.127 |
| financial-disclosure | vlm | 10 | 0.724 | 7.879 |
| form | standard | 10 | 0.9922 | 3.183 |
| formula | standard | 10 | 0.6937 | 2.339 |
| image-only | standard | 10 | 0.91 | 11.793 |
| image-only | vlm | 10 | 0.9913 | 4.459 |
| irregular | standard | 10 | 1.0 | 3.6 |
| japanese | standard | 20 | 0.9739 | 3.304 |
| japanese | vlm | 10 | 0.724 | 7.879 |
| long | standard | 10 | 0.9981 | 5.217 |
| low-quality | standard | 10 | 0.7389 | 10.085 |
| low-quality | vlm | 10 | 0.9911 | 5.208 |
| merged-header | standard | 20 | 0.9989 | 4.779 |
| mixed-orientation | standard | 10 | 0.6486 | 3.582 |
| mixed-orientation | vlm | 10 | 0.923 | 7.825 |
| multicolumn | standard | 10 | 0.9731 | 3.481 |
| nested-tables | standard | 10 | 1.0 | 3.6 |
| ocr | standard | 20 | 0.8244 | 11.061 |
| ocr | vlm | 20 | 0.9912 | 4.78 |
| real | standard | 10 | 0.9747 | 3.127 |
| real | vlm | 10 | 0.724 | 7.879 |
| rotated | standard | 10 | 0.9977 | 5.722 |
| scanned | standard | 20 | 0.8244 | 11.061 |
| scanned | vlm | 20 | 0.9912 | 4.78 |
| short | standard | 10 | 0.9037 | 3.868 |
| short | vlm | 10 | 0.9856 | 6.611 |
| sidebars | standard | 10 | 0.6486 | 3.582 |
| sidebars | vlm | 10 | 0.923 | 7.825 |
| skewed | standard | 10 | 0.7389 | 10.085 |
| skewed | vlm | 10 | 0.9911 | 5.208 |
| tables | standard | 20 | 0.8342 | 2.733 |
| tables | vlm | 10 | 0.724 | 7.879 |
| vector | standard | 10 | 0.9037 | 3.868 |
| vector | vlm | 10 | 0.9856 | 6.611 |
| vertical-label | standard | 10 | 0.9731 | 3.481 |

## Hybrid Selection

- Cases selected: 11
- Mean overall: 0.9412
- Mean seconds/page: 4.792
- VLM selected cases: C03

## Output Files

- `results.json` / `results.csv`: raw run rows
- `summary_by_config.csv`: pipeline/config aggregate
- `summary_by_vlm_setting.csv`: VLM parameter aggregate
- `summary_by_case.csv`: best and fastest setting by case
- `summary_by_tag.csv`: tag-level tendency summary
- `hybrid_summary.json`: case-level hybrid selection