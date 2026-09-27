# Every stored `record cut` citation, checked against what its grader was shown (#4)

`python scripts/audit_cut_citations.py runs/d44-{grok-4.6,DeepSeek-V4-Pro,Mistral-Large-3} runs/d45-{grok-4.6,DeepSeek-V4-Pro,Mistral-Large-3} runs/v1-subset-graded`, 09-27. The v1 subset's readings made no `record cut` claim. Examples are left out here: they quote grader evidence, which can name a developer's home folder.

```
run                              grader             shown elsewhere  invented      none
d44-DeepSeek-V4-Pro              gpt-6-astra          379         3         0         0
d44-DeepSeek-V4-Pro              gpt-6-sol            223         6         0         2
d44-Mistral-Large-3              gpt-6-astra          267         0         0         0
d44-Mistral-Large-3              gpt-6-sol            171         1         0         0
d44-grok-4.6                     gpt-6-astra          589         0         0         0
d44-grok-4.6                     gpt-6-sol            369        11         0         1
d45-DeepSeek-V4-Pro              gpt-6-astra          247         2         0         0
d45-DeepSeek-V4-Pro              gpt-6-sol            148         0         0         0
d45-Mistral-Large-3              gpt-6-astra          255         0         0         0
d45-Mistral-Large-3              gpt-6-sol            163         0         0         0
d45-grok-4.6                     gpt-6-astra          219         0         0         0
d45-grok-4.6                     gpt-6-sol            119         0         0         1
all                                                  3149        23         0         4
```
