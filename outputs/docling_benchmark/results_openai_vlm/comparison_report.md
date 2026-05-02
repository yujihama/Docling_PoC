# Docling Standard vs OpenAI VLM Evaluation

## Outcome

OpenAI VLM was executed against C01, but the upstream OpenAI API returned `insufficient_quota`. Docling logged the API error and continued with empty page responses, so the benchmark stops after C01 to avoid repeated failed API calls.

## Comparison

| Case | Pages | Standard overall | Standard time | OpenAI VLM status | OpenAI VLM overall | OpenAI VLM time |
|---|---:|---:|---:|---|---:|---:|
| C01 | 2 | 0.912 | 5.336s | failed | 0.000 | 2.845s |
| C02 | 5 | 1.000 | 16.666s | not run | - | - |
| C03 | 9 | 0.651 | 15.844s | not run | - | - |
| C04 | 16 | 1.000 | 51.901s | not run | - | - |
| C05 | 4 | 1.000 | 52.950s | not run | - | - |

## Diagnosis

- The API key is present and the model setting is `gpt-5.2`.
- The VLM request reached OpenAI.
- OpenAI returned `insufficient_quota` for the page-image requests.
- Previous UI behavior looked like a quick success because Docling swallows that API error and returns empty generated text.
- The app now treats empty Markdown from OpenAI VLM mode as a conversion failure, so the user sees an error instead of an empty result.