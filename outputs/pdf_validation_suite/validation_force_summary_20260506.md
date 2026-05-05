# PDF validation suite result

- Run prefix: `validation_suite_force`
- Cases: 10
- Runs found: 5

## Summary

| case | status | pages | modes | unknown | masks | needs_retry | anchor recall |
|---|---:|---:|---|---:|---:|---:|---:|
| V01 | ok | 2/2 | `{"IMAGE_RECONCILE": 2}` | 2 | 4 | 7 | 36/38 (94.7%) |
| V02 | missing_run | /2 | `` |  |  |  | 0/64 (0.0%) |
| V03 | ok | 2/2 | `{"IMAGE_RECONCILE": 2}` | 2 | 2 | 4 | 26/28 (92.9%) |
| V04 | ok | 2/2 | `{"IMAGE_RECONCILE": 2}` | 0 | 0 | 2 | 34/34 (100.0%) |
| V05 | missing_run | /2 | `` |  |  |  | 0/44 (0.0%) |
| V06 | ok | 1/1 | `{"IMAGE_RECONCILE": 1}` | 2 | 2 | 3 | 11/13 (84.6%) |
| V07 | ok | 1/1 | `{"IMAGE_RECONCILE": 1}` | 1 | 1 | 2 | 16/17 (94.1%) |
| V08 | missing_run | /2 | `` |  |  |  | 0/16 (0.0%) |
| W01 | missing_run | /6 | `` |  |  |  | 0/6 (0.0%) |
| W02 | missing_run | /19 | `` |  |  |  | 0/6 (0.0%) |

## Missing Anchors

### V01 v01_invoice_purchase_order_2p.pdf

- `V01-APPROVAL-STAMP-P01`
- `V01-APPROVAL-STAMP-P02`

### V03 v03_application_form_checkboxes_2p.pdf

- `FORM-V03-APPL-01`
- `FORM-V03-APPL-02`

### V06 v06_landscape_technical_drawing_1p.pdf

- `CALL-V06-03-GRID`
- `ROTATE-V06-NORTH-ELEVATION`

### V07 v07_lab_report_units_redactions_1p.pdf

- `NOTE-V07-DELTA-CHECK: compare against 2026-04 specimen.`

## Warnings

- `V01` warning_counts={"MASKED_AS_UNKNOWN": 4, "RECONCILE_TABLE_FALLBACK_APPLIED": 1, "RECONCILE_TABLE_FALLBACK_SKIPPED": 1, "VALUE_VALIDATION_FAILED": 2, "VLM_COORD_FALLBACK_APPLIED": 1, "VLM_COORD_MASKED_UNSUPPORTED_VALUE": 2, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 2, "VLM_MISSING_COORD_VALUE": 1, "VLM_UNSUPPORTED_COORD_VALUE": 1, "VLM_VLM_DISAGREEMENT": 1} levels={"info": 3, "needs_retry": 7, "warning": 6}
- `V03` warning_counts={"MASKED_AS_UNKNOWN": 2, "VLM_COORD_FALLBACK_APPLIED": 1, "VLM_COORD_FALLBACK_SKIPPED": 1, "VLM_COORD_MASKED_UNSUPPORTED_VALUE": 2, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 2, "VLM_UNSUPPORTED_COORD_VALUE": 2} levels={"info": 2, "needs_retry": 4, "warning": 4}
- `V04` warning_counts={"VLM_COORD_FALLBACK_SKIPPED": 2, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 2} levels={"info": 2, "needs_retry": 2}
- `V06` warning_counts={"MASKED_AS_UNKNOWN": 2, "RECONCILE_TABLE_FALLBACK_APPLIED": 1, "VLM_COORD_MASKED_UNSUPPORTED_VALUE": 1, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 1, "VLM_UNSUPPORTED_COORD_VALUE": 1, "VLM_VLM_DISAGREEMENT": 1} levels={"info": 1, "needs_retry": 3, "warning": 3}
- `V07` warning_counts={"MASKED_AS_UNKNOWN": 1, "VLM_COORD_FALLBACK_SKIPPED": 1, "VLM_COORD_MASKED_UNSUPPORTED_VALUE": 1, "VLM_COORD_TABLE_STRUCTURE_MISMATCH": 1, "VLM_UNSUPPORTED_COORD_VALUE": 1} levels={"info": 1, "needs_retry": 2, "warning": 2}
