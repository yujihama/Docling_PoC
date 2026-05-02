# OpenAI VLM Benchmark Report

- Model: `gpt-5.4-mini`
- Settings: `{"max_completion_tokens": 12000, "reasoning_effort": "none", "timeout_seconds": 240.0, "scale": 2.0, "response_format": "html"}`
- Cases attempted: 5
- Successful: 5
- Failed: 0
- Total time: 311.071 sec

| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Error |
|---|---|---:|---:|---:|---:|---:|---:|---|
| C01 | ok | 2 | 2/2 | 1.000 | 1.000 | 1.000 | 12.172 |  |
| C02 | ok | 5 | 5/5 | 0.800 | 0.969 | 0.913 | 54.016 |  |
| C03 | ok | 9 | 9/9 | 0.815 | 0.996 | 0.933 | 70.161 |  |
| C04 | ok | 16 | 16/16 | 1.000 | 1.000 | 1.000 | 154.609 |  |
| C05 | ok | 4 | 4/4 | 1.000 | 1.000 | 1.000 | 20.113 |  |