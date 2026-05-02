# OpenAI VLM Benchmark Report

- Model: `gpt-5.4-mini`
- Settings: `{"max_completion_tokens": 12000, "reasoning_effort": "none", "timeout_seconds": 240.0, "scale": 2.5, "response_format": "markdown"}`
- Cases attempted: 5
- Successful: 5
- Failed: 0
- Total time: 228.379 sec

| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Error |
|---|---|---:|---:|---:|---:|---:|---:|---|
| C01 | ok | 2 | 2/2 | 1.000 | 1.000 | 1.000 | 10.407 |  |
| C02 | ok | 5 | 5/14 | 1.000 | 0.871 | 0.929 | 37.064 |  |
| C03 | ok | 9 | 9/15 | 0.889 | 0.946 | 0.931 | 55.734 |  |
| C04 | ok | 16 | 16/32 | 1.000 | 0.937 | 0.965 | 109.231 |  |
| C05 | ok | 4 | 4/4 | 1.000 | 1.000 | 1.000 | 15.943 |  |