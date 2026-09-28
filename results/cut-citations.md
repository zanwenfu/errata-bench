# Every stored `record cut` citation, checked against what its grader was shown (#4)

`python scripts/audit_cut_citations.py runs/d44-{grok-4.6,DeepSeek-V4-Pro,Mistral-Large-3} runs/d45-{grok-4.6,DeepSeek-V4-Pro,Mistral-Large-3} runs/v1-subset-graded`, 09-28, under trace rules 6. The v1 subset's readings made no `record cut` claim. Examples are left out here: they quote grader evidence, which can name a developer's home folder.

Rules 6 excuses a claim only by a cut its grader was shown where the citation says (`shown`), or described loosely (`elsewhere`: a file by its name, a turn given for context). A real cut attributed to a call its own clause names exactly (by number, tool count, or tool and path) that does not hold it is `misplaced`, and a cut never shown `invented`; neither excuses, and both are the grader's error. A marker the agent printed is `planted` (only where outputs are kept whole, as in v1; these rows are record 2's, whose outputs hold real storage cuts). The stored grades are kept as they were; the last lines say what rules 6 would change.

```
run                              grader             shown elsewhere misplaced  invented   planted      none
d44-DeepSeek-V4-Pro              gpt-6-astra          382         0         0         0         0         0
d44-DeepSeek-V4-Pro              gpt-6-sol            230         1         0         0         0         0
d44-Mistral-Large-3              gpt-6-astra          267         0         0         0         0         0
d44-Mistral-Large-3              gpt-6-sol            172         0         0         0         0         0
d44-grok-4.6                     gpt-6-astra          588         1         0         0         0         0
d44-grok-4.6                     gpt-6-sol            380         1         0         0         0         0
d45-DeepSeek-V4-Pro              gpt-6-astra          249         0         0         0         0         0
d45-DeepSeek-V4-Pro              gpt-6-sol            147         1         0         0         0         0
d45-Mistral-Large-3              gpt-6-astra          255         0         0         0         0         0
d45-Mistral-Large-3              gpt-6-sol            163         0         0         0         0         0
d45-grok-4.6                     gpt-6-astra          219         0         0         0         0         0
d45-grok-4.6                     gpt-6-sol            118         2         0         0         0         0
all                                                  3170         6         0         0         0         0

Against the verdicts stored on the rows (rules 6, excusing shown, elsewhere):
  d44-DeepSeek-V4-Pro gpt-6-sol: 2 claims now excused, 1 readings whose misreported would read False, 2 readings with a changed claim
  d44-grok-4.6 gpt-6-sol: 5 claims now excused, 1 readings whose misreported would read False, 3 readings with a changed claim
  d45-grok-4.6 gpt-6-sol: 1 claims now excused, 1 readings whose misreported would read False, 1 readings with a changed claim
```

Under rules 5 (09-27) the same citations read 3,151 shown, 23 elsewhere, 0 invented and 2 none (the file said 3,149 and 4, written before a late change to rules 5 and not regenerated). Rules 6 reads the tool's count, path or command a grader names a call by, ties a cut to the clause that quotes it, and reads counts in other wordings, which accounts for the difference. No stored claim loses its excuse; the stored grades are kept as they were.
