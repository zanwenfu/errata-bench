# errata-bench

Turning real developer↔coding-agent sessions into a runnable benchmark.

Empty by design. The previous system — live capture, a review UI, and twelve
detectors — is archived and not carried forward; see **Prior work** below.

## The goal

Take a real moment where a coding agent got something wrong, and turn it into a
task another agent can attempt and be scored on:

```
parent commit's source  +  the commit's test  →  test FAILS
     agent's fix applied  +  the commit's test  →  test PASSES
```

That construction is proven on real SWE-chat data. See
[docs/SWE-CHAT-FINDINGS.md](docs/SWE-CHAT-FINDINGS.md) for the working example,
the container timings, and the candidate counts.

## The data

`data/swe-chat` is a **symlink** to a 12 GB corpus that lives outside this repo:

```
data/swe-chat -> ../errata/data/corpora/swe-chat
```

It holds 5,850 Claude Code transcripts (9.7 GB) and six parquet tables:
`sessions`, `conversations`, `commits`, `checkpoints`, `repositories`,
`session_logs`.

To fetch it fresh:

```bash
huggingface-cli download SALT-NLP/SWE-chat --repo-type dataset \
  --local-dir data/swe-chat
```

**The exact revision is not recorded.** An earlier README claimed `f66cca9`, but
nothing on disk confirms it — there is no HuggingFace cache ref and the corpus
README is only the dataset card. The dataset is described upstream as "living"
and row counts drift, so **pin a revision on the next download** and record it
here.

Source: [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
licence `odc-by`. Collected via opt-in Entire.io CLI from public repositories.

## What came with us

| | |
|---|---|
| `gold/labels.jsonl` | 270 labelled cases (LLM-produced, cost $17.87) |
| `gold/GUIDELINE.md` | the labelling rubric they were produced under |
| `docs/SWE-CHAT-FINDINGS.md` | what was measured about benchmark feasibility |

**No human has validated those 270 labels.** They were written by
`gpt-6-astra`. Treat them as one model's opinion, not ground truth. About twenty
human labels would establish whether that opinion holds — the cheapest useful
thing available.

## Prior work

The capture system is preserved in full and reachable:

```
github.com/zanwenfu/errata
  tag    v0.1-capture-foundation
  branch archive/capture-foundation
```

28k lines of source, 15k of tests, 532 passing. Worth borrowing from rather than
rewriting blind: the transcript adapter (proven on 4,918 sessions), the twelve
detectors, the evaluation harness, and the git/environment reader.

Also there: `docs/DESIGN.md` (how the capture system worked),
`docs/RESULTS.md` (detector measurements), `docs/ISSUES.md` (fifteen bugs found
and what each cost).

## Next

1. Run the fail-to-pass oracle over ~50 modified-test commits spanning Go,
   TypeScript and Python. That converts "one case works" into a yield rate,
   which is the number that decides whether this is worth building.
2. Automate toolchain selection from lockfile and `engines`.
3. Get twenty human labels.
