# OpenAI VLM Benchmark Report

- Model: `gpt-5.4-mini`
- Settings: `{"max_completion_tokens": 12000, "reasoning_effort": "none", "timeout_seconds": 180.0, "scale": 2.0}`
- Cases attempted: 5
- Successful: 5
- Failed: 0
- Total time: 200.868 sec

| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Error |
|---|---|---:|---:|---:|---:|---:|---:|---|
| C01 | ok | 2 | 2/2 | 1.000 | 1.000 | 1.000 | 9.194 |  |
| C02 | ok | 5 | 5/14 | 1.000 | 0.868 | 0.927 | 32.582 |  |
| C03 | ok | 9 | 9/15 | 0.889 | 0.943 | 0.929 | 48.359 |  |
| C04 | ok | 16 | 16/24 | 1.000 | 0.969 | 0.983 | 96.521 |  |
| C05 | ok | 4 | 4/4 | 1.000 | 1.000 | 1.000 | 14.212 |  |