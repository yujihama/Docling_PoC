# PDF validation suite result

- Run prefix: `validation_suite`
- Cases: 10
- Runs found: 10

## Summary

| case | status | pages | modes | unknown | masks | needs_retry | anchor recall |
|---|---:|---:|---|---:|---:|---:|---:|
| V01 | ok | 2/2 | `{"TEXT_TABLE_ACCURATE": 2}` | 0 | 0 | 0 | 36/38 (94.7%) |
| V02 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 64/64 (100.0%) |
| V03 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 26/28 (92.9%) |
| V04 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 32/34 (94.1%) |
| V05 | ok | 2/2 | `{"IMAGE_RECONCILE": 2}` | 0 | 0 | 0 | 44/44 (100.0%) |
| V06 | ok | 1/1 | `{"TEXT_TABLE_FAST": 1}` | 0 | 0 | 0 | 11/13 (84.6%) |
| V07 | ok | 1/1 | `{"TEXT_TABLE_FAST": 1}` | 0 | 0 | 0 | 16/17 (94.1%) |
| V08 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 16/16 (100.0%) |
| W01 | ok | 6/6 | `{"TEXT_LIGHT": 1, "TEXT_TABLE_ACCURATE": 4, "TEXT_TABLE_FAST": 1}` | 0 | 0 | 0 | 6/6 (100.0%) |
| W02 | ok | 19/19 | `{"IMAGE_RECONCILE": 2, "TEXT_LIGHT": 12, "TEXT_TABLE_FAST": 5}` | 0 | 0 | 0 | 6/6 (100.0%) |

## Missing Anchors

### V01 v01_invoice_purchase_order_2p.pdf

- `V01-APPROVAL-STAMP-P01`
- `V01-APPROVAL-STAMP-P02`

### V03 v03_application_form_checkboxes_2p.pdf

- `FORM-V03-APPL-01`
- `FORM-V03-APPL-02`

### V04 v04_japanese_vertical_mixed_2p.pdf

- `V04-縦書き-P01`
- `V04-縦書き-P02`

### V06 v06_landscape_technical_drawing_1p.pdf

- `CALL-V06-03-GRID`
- `ROTATE-V06-NORTH-ELEVATION`

### V07 v07_lab_report_units_redactions_1p.pdf

- `NOTE-V07-DELTA-CHECK: compare against 2026-04 specimen.`

## Warnings

- `V01` warning_counts={"COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS": 2} levels={"info": 2}
- `V02` warning_counts={"COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS": 2} levels={"info": 2}
- `V07` warning_counts={"COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS": 1} levels={"info": 1}
- `V08` warning_counts={"COORDINATE_TABLE_FALLBACK_TO_VLM": 2, "VLM_COORD_WEAK_EVIDENCE": 2} levels={"info": 4}
- `W01` warning_counts={"COORDINATE_TABLE_FALLBACK_TO_VLM": 5, "VLM_COORD_WEAK_EVIDENCE": 4} levels={"info": 9}
- `W02` warning_counts={"COORDINATE_TABLE_FALLBACK_TO_VLM": 5, "VLM_COORD_WEAK_EVIDENCE": 5} levels={"info": 10}
