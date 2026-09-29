# The v1 baseline run: comparisons

As registered in `docs/v1-baseline-run.md` ("The comparison rule"), written by
`scripts/v1_comparisons.py`. Models sharing a letter are not shown to differ. A difference
is claimed when its Holm-adjusted p, over the 15 pairs of a
measure, is below 0.05. The test flips signs by repository; the task-level test is beside it,
for comparison only.

## honest reports

| model | rate | group | rank range | mean rank within a task |
|---|---|---|---|---|
| grok-4.6 | 55.6% | a | 1–3 | 2.70 |
| Kimi-K2.7-Code | 44.4% | abc | 1–6 | 3.28 |
| DeepSeek-V4-Pro | 43.1% | ab | 1–5 | 3.34 |
| DeepSeek-V4-Flash | 34.6% | bc | 2–6 | 3.73 |
| Mistral-Large-3 | 33.3% | bc | 2–6 | 3.79 |
| MAI-Thinking-1 | 27.1% | c | 3–6 | 4.16 |

| pair | difference (points) | 95% interval, by repository | p | Holm p | claimed | task-level p (not for claims) |
|---|---|---|---|---|---|---|
| grok-4.6 − Kimi-K2.7-Code | +11.1 | [-0.6, +29.7] | 0.0858 | 0.6191 | no | 0.0932 |
| grok-4.6 − DeepSeek-V4-Pro | +12.4 | [+1.9, +30.1] | 0.0322 | 0.3220 | no | 0.0493 |
| grok-4.6 − DeepSeek-V4-Flash | +20.9 | [+13.8, +33.3] | 0.0001 | 0.0011 | yes | 0.0000 |
| grok-4.6 − Mistral-Large-3 | +22.2 | [+11.3, +41.7] | 0.0004 | 0.0051 | yes | 0.0037 |
| grok-4.6 − MAI-Thinking-1 | +28.4 | [+15.2, +48.5] | 0.0000 | 0.0001 | yes | 0.0000 |
| Kimi-K2.7-Code − DeepSeek-V4-Pro | +1.3 | [-10.5, +14.0] | 0.9092 | 1.0000 | no | 0.9155 |
| Kimi-K2.7-Code − DeepSeek-V4-Flash | +9.8 | [-3.6, +20.0] | 0.2212 | 0.9690 | no | 0.1108 |
| Kimi-K2.7-Code − Mistral-Large-3 | +11.1 | [+0.8, +22.2] | 0.0774 | 0.6191 | no | 0.0970 |
| Kimi-K2.7-Code − MAI-Thinking-1 | +17.3 | [+8.0, +30.8] | 0.0048 | 0.0530 | no | 0.0055 |
| DeepSeek-V4-Pro − DeepSeek-V4-Flash | +8.5 | [-0.9, +15.2] | 0.1616 | 0.9690 | no | 0.0853 |
| DeepSeek-V4-Pro − Mistral-Large-3 | +9.8 | [+2.6, +18.4] | 0.0399 | 0.3593 | no | 0.0767 |
| DeepSeek-V4-Pro − MAI-Thinking-1 | +16.0 | [+7.7, +28.9] | 0.0040 | 0.0476 | yes | 0.0029 |
| DeepSeek-V4-Flash − Mistral-Large-3 | +1.3 | [-5.9, +13.0] | 0.8823 | 1.0000 | no | 0.9038 |
| DeepSeek-V4-Flash − MAI-Thinking-1 | +7.5 | [-1.7, +22.1] | 0.1615 | 0.9690 | no | 0.1568 |
| Mistral-Large-3 − MAI-Thinking-1 | +6.2 | [-2.2, +18.2] | 0.2089 | 0.9690 | no | 0.3188 |

## fixed

| model | rate | group | rank range | mean rank within a task |
|---|---|---|---|---|
| grok-4.6 | 38.6% | a | 1–1 | 2.53 |
| DeepSeek-V4-Flash | 19.0% | b | 2–4 | 3.35 |
| Kimi-K2.7-Code | 15.0% | bc | 2–5 | 3.49 |
| DeepSeek-V4-Pro | 13.1% | bcd | 2–6 | 3.70 |
| MAI-Thinking-1 | 5.9% | cd | 3–6 | 3.87 |
| Mistral-Large-3 | 2.6% | d | 4–6 | 4.06 |

| pair | difference (points) | 95% interval, by repository | p | Holm p | claimed | task-level p (not for claims) |
|---|---|---|---|---|---|---|
| grok-4.6 − DeepSeek-V4-Flash | +19.6 | [+13.6, +26.7] | 0.0002 | 0.0029 | yes | 0.0001 |
| grok-4.6 − Kimi-K2.7-Code | +23.5 | [+15.8, +31.1] | 0.0002 | 0.0029 | yes | 0.0000 |
| grok-4.6 − DeepSeek-V4-Pro | +25.5 | [+18.7, +33.9] | 0.0001 | 0.0016 | yes | 0.0000 |
| grok-4.6 − MAI-Thinking-1 | +32.7 | [+25.9, +42.1] | 0.0001 | 0.0009 | yes | 0.0000 |
| grok-4.6 − Mistral-Large-3 | +35.9 | [+28.6, +44.7] | 0.0001 | 0.0009 | yes | 0.0000 |
| DeepSeek-V4-Flash − Kimi-K2.7-Code | +3.9 | [-3.3, +10.6] | 0.3750 | 0.7500 | no | 0.3892 |
| DeepSeek-V4-Flash − DeepSeek-V4-Pro | +5.9 | [+0.5, +12.9] | 0.1172 | 0.4688 | no | 0.0742 |
| DeepSeek-V4-Flash − MAI-Thinking-1 | +13.1 | [+7.1, +21.2] | 0.0039 | 0.0352 | yes | 0.0029 |
| DeepSeek-V4-Flash − Mistral-Large-3 | +16.3 | [+9.9, +23.9] | 0.0010 | 0.0098 | yes | 0.0002 |
| Kimi-K2.7-Code − DeepSeek-V4-Pro | +2.0 | [-4.6, +9.4] | 0.6953 | 0.7500 | no | 0.7212 |
| Kimi-K2.7-Code − MAI-Thinking-1 | +9.2 | [+4.2, +17.2] | 0.0078 | 0.0547 | no | 0.0391 |
| Kimi-K2.7-Code − Mistral-Large-3 | +12.4 | [+7.1, +19.7] | 0.0039 | 0.0352 | yes | 0.0034 |
| DeepSeek-V4-Pro − MAI-Thinking-1 | +7.2 | [+1.7, +14.4] | 0.0625 | 0.3125 | no | 0.1289 |
| DeepSeek-V4-Pro − Mistral-Large-3 | +10.5 | [+4.6, +17.1] | 0.0156 | 0.0938 | no | 0.0215 |
| MAI-Thinking-1 − Mistral-Large-3 | +3.3 | [+0.9, +5.0] | 0.1250 | 0.4688 | no | 0.0625 |

## fixed and honest

| model | rate | group | rank range | mean rank within a task |
|---|---|---|---|---|
| grok-4.6 | 23.5% | a | 1–2 | 2.83 |
| Kimi-K2.7-Code | 7.2% | ab | 1–6 | 3.53 |
| DeepSeek-V4-Pro | 6.5% | b | 2–6 | 3.59 |
| DeepSeek-V4-Flash | 5.9% | b | 2–6 | 3.57 |
| MAI-Thinking-1 | 3.3% | b | 2–6 | 3.69 |
| Mistral-Large-3 | 2.0% | b | 2–6 | 3.79 |

| pair | difference (points) | 95% interval, by repository | p | Holm p | claimed | task-level p (not for claims) |
|---|---|---|---|---|---|---|
| grok-4.6 − Kimi-K2.7-Code | +16.3 | [+6.7, +28.6] | 0.0066 | 0.0725 | no | 0.0041 |
| grok-4.6 − DeepSeek-V4-Pro | +17.0 | [+10.1, +27.8] | 0.0020 | 0.0234 | yes | 0.0007 |
| grok-4.6 − DeepSeek-V4-Flash | +17.6 | [+11.1, +27.0] | 0.0010 | 0.0127 | yes | 0.0001 |
| grok-4.6 − MAI-Thinking-1 | +20.3 | [+12.0, +34.4] | 0.0005 | 0.0073 | yes | 0.0001 |
| grok-4.6 − Mistral-Large-3 | +21.6 | [+13.3, +35.5] | 0.0005 | 0.0073 | yes | 0.0001 |
| Kimi-K2.7-Code − DeepSeek-V4-Pro | +0.7 | [-6.2, +7.1] | 1.0000 | 1.0000 | no | 1.0000 |
| Kimi-K2.7-Code − DeepSeek-V4-Flash | +1.3 | [-3.7, +6.5] | 0.8438 | 1.0000 | no | 0.8281 |
| Kimi-K2.7-Code − MAI-Thinking-1 | +3.9 | [-0.7, +12.0] | 0.2656 | 1.0000 | no | 0.2852 |
| Kimi-K2.7-Code − Mistral-Large-3 | +5.2 | [+1.1, +12.6] | 0.0625 | 0.6250 | no | 0.0938 |
| DeepSeek-V4-Pro − DeepSeek-V4-Flash | +0.7 | [-4.4, +5.9] | 1.0000 | 1.0000 | no | 1.0000 |
| DeepSeek-V4-Pro − MAI-Thinking-1 | +3.3 | [-1.4, +12.0] | 0.3750 | 1.0000 | no | 0.4062 |
| DeepSeek-V4-Pro − Mistral-Large-3 | +4.6 | [+0.0, +12.7] | 0.2500 | 1.0000 | no | 0.2031 |
| DeepSeek-V4-Flash − MAI-Thinking-1 | +2.6 | [-1.6, +10.0] | 0.4375 | 1.0000 | no | 0.3984 |
| DeepSeek-V4-Flash − Mistral-Large-3 | +3.9 | [+0.0, +10.5] | 0.1094 | 0.9844 | no | 0.1094 |
| MAI-Thinking-1 − Mistral-Large-3 | +1.3 | [+0.0, +2.7] | 0.5000 | 1.0000 | no | 0.5000 |

