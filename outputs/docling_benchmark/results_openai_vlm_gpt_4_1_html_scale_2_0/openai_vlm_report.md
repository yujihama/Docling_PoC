# OpenAI VLM Benchmark Report

- Model: `gpt-4.1`
- Settings: `{"max_completion_tokens": 12000, "reasoning_effort": "none", "timeout_seconds": 240.0, "scale": 2.0, "response_format": "html"}`
- Cases attempted: 5
- Successful: 5
- Failed: 0
- Total time: 460.046 sec

| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Error |
|---|---|---:|---:|---:|---:|---:|---:|---|
| C01 | ok | 2 | 2/2 | 1.000 | 0.921 | 0.956 | 15.347 |  |
| C02 | ok | 5 | 5/5 | 0.400 | 0.658 | 0.602 | 78.634 |  |
| C03 | ok | 9 | 9/9 | 0.889 | 0.890 | 0.901 | 121.801 |  |
| C04 | ok | 16 | 16/16 | 1.000 | 0.602 | 0.781 | 222.528 |  |
| C05 | ok | 4 | 4/4 | 1.000 | 0.862 | 0.924 | 21.736 |  |