# OpenAI VLM Benchmark Report

- Model: `gpt-4.1`
- Settings: `{"max_completion_tokens": 12000, "reasoning_effort": "none", "timeout_seconds": 240.0, "scale": 2.0, "response_format": "markdown"}`
- Cases attempted: 5
- Successful: 5
- Failed: 0
- Total time: 272.548 sec

| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Error |
|---|---|---:|---:|---:|---:|---:|---:|---|
| C01 | ok | 2 | 2/2 | 1.000 | 0.921 | 0.956 | 10.673 |  |
| C02 | ok | 5 | 5/8 | 0.800 | 0.667 | 0.747 | 50.068 |  |
| C03 | ok | 9 | 9/11 | 0.889 | 0.869 | 0.889 | 66.110 |  |
| C04 | ok | 16 | 16/16 | 1.000 | 0.611 | 0.786 | 131.315 |  |
| C05 | ok | 4 | 4/4 | 1.000 | 0.862 | 0.924 | 14.382 |  |