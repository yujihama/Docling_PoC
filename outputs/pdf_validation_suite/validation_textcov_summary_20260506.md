# PDF validation suite result

- Run prefix: `validation_suite_textcov`
- Cases: 10
- Runs found: 5

## Summary

| case | status | pages | modes | unknown | masks | needs_retry | anchor recall |
|---|---:|---:|---|---:|---:|---:|---:|
| V01 | ok | 2/2 | `{"TEXT_TABLE_ACCURATE": 2}` | 0 | 0 | 0 | 38/38 (100.0%) |
| V02 | missing_run | /2 | `` |  |  |  | 0/64 (0.0%) |
| V03 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 28/28 (100.0%) |
| V04 | ok | 2/2 | `{"TEXT_TABLE_FAST": 2}` | 0 | 0 | 0 | 34/34 (100.0%) |
| V05 | missing_run | /2 | `` |  |  |  | 0/44 (0.0%) |
| V06 | ok | 1/1 | `{"TEXT_TABLE_FAST": 1}` | 0 | 0 | 0 | 13/13 (100.0%) |
| V07 | ok | 1/1 | `{"TEXT_TABLE_FAST": 1}` | 0 | 0 | 0 | 17/17 (100.0%) |
| V08 | missing_run | /2 | `` |  |  |  | 0/16 (0.0%) |
| W01 | missing_run | /6 | `` |  |  |  | 0/6 (0.0%) |
| W02 | missing_run | /19 | `` |  |  |  | 0/6 (0.0%) |

## Missing Anchors

## Warnings

- `V01` warning_counts={"COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS": 2, "COORDINATE_TEXT_LAYER_SUPPLEMENTED": 2} levels={"info": 4}
- `V03` warning_counts={"COORDINATE_TEXT_LAYER_SUPPLEMENTED": 2} levels={"info": 2}
- `V04` warning_counts={"COORDINATE_TEXT_LAYER_SUPPLEMENTED": 2} levels={"info": 2}
- `V06` warning_counts={"COORDINATE_TEXT_LAYER_SUPPLEMENTED": 1} levels={"info": 1}
- `V07` warning_counts={"COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS": 1, "COORDINATE_TEXT_LAYER_SUPPLEMENTED": 1} levels={"info": 2}
