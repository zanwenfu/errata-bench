"""Does an AI reviewer catch what the developer caught? (the study, docs/study.md)

Two lists, made independently, then merged:

- thread A, the machine: at each point where the agent hands its work back to
  the developer (a report), a reviewer reads what the agent did and lists what
  it would push back on. It never sees anything after that point;
- thread B, the human: each reply the developer then wrote, classified as
  pushback or not, and if so, what it objects to;
- the merge: a third reading lines the two lists up. Each item is caught by
  both, by the developer only, or by the reviewer only.

`sessions` decides what each side sees; `review`, `human` and `merge` make the
three readings; `scripts/study.py` runs them.
"""
