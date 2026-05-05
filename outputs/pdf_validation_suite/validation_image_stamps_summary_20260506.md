# PDF validation suite result

- Run prefix: `validation_image_stamps`
- Cases: 1
- Runs found: 1

## Summary

| case | status | pages | modes | unknown | masks | needs_retry | anchor recall |
|---|---:|---:|---|---:|---:|---:|---:|
| V09 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 1 | 2 | 4 | 35/38 (92.1%) |

## Missing Anchors

### V09 v09_text_pdf_image_layer_stamps_2p.pdf

- `IMG-STAMP-V09-P02-OVERLAP`
- `IMG-STAMP-V09-P02-RECEIVED`
- `IMG-STAMP-V09-P02-SMALL`

## Warnings

- `V09` warning_counts={"COORDINATE_TABLE_FALLBACK_TO_VLM": 1, "COORDINATE_TEXT_LAYER_SUPPLEMENTED": 1, "EMBEDDED_VISUAL_REGION_CANDIDATE": 2, "MASKED_AS_UNKNOWN": 2, "RECONCILE_TABLE_FALLBACK_APPLIED": 1, "VLM_COORD_MASKED_UNSUPPORTED_VALUE": 1, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 1, "VLM_COORD_WEAK_EVIDENCE": 2, "VLM_MISSING_COORD_VALUE": 1, "VLM_UNSUPPORTED_COORD_VALUE": 1, "VLM_VLM_DISAGREEMENT": 1} levels={"info": 7, "needs_retry": 4, "warning": 3}
