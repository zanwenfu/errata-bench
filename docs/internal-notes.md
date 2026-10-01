# Internal notes

How the project is run, for its maintainers. It is not part of the benchmark's description; the
dated record of every decision, bug and experiment is the [research log](research-log.md).

## Running the experiments

- **Pinned code.** Each run executes from its own checkout of a tagged commit,
  so nothing changed afterwards can reach a run in progress.
- **Resumable, not append-only.** A stage appends rows and a rerun resumes;
  rows that errored, or went stale when a task was rebuilt, are pruned by
  rewriting the file atomically, so a file holds only current rows. Run
  directories are copied off the server and backed up off-machine.

## Spending

| step | what could go wrong | how it is checked | evidence |
|---|---|---|---|
| Spending | a run overspends, or bills the wrong account | each paid run since the D-40 run has a spend guard that stops it at a stop line agreed in advance; since 30 September the rows of every stage that calls a model record their token use, so the steps before Harbor (finding tasks, screening, an admission) can be guarded too, and an ambiguous provider setting is refused before any call | the D-40 run cost about $1,320 at list prices, under its $2,500 line |

## Open gaps no issue tracks

Checked against the code and the log on 30 September 2026, and again on 1
October. Each is in the research log's open gaps. G-62 is in #11, and G-53's
subject, relabelling pushback, is step 7 of #16; the others are in no issue
yet, and #11 is where they belong when one is opened.

- **G-52: the finding stages are asked once.** The screening gates are asked
  repeatedly and settled by majority (D-34), but triage, the reader, locate and
  the signature are asked once, and the whole re-screen has not been measured
  again since R-32.
- **G-53: how many moments SWE-chat's pushback label misses is unmeasured.**
  The label has been read (14 `non_pushback` messages in G-53's entry, 25
  labelled moments in PUSHBACK-FINDINGS.md), but its miss rate has not: a
  model reading a few hundred `non_pushback` messages would say. The Entire
  collector's rows carry no label at all (#16).
- **G-56: one single call remains in screening: the surveyor.** The re-check
  after a repair is asked as often as the leak gate since 30 September.
- **G-61: the hedge rule is applied twice.** The pipeline and v1 admission use
  the clean standard only, so it is contained; `rejudge` still carries both.
- **G-62: an untestable control excludes its task,** a rule never decided on
  the evidence (7 tasks across the run directories on 09-22).
- **G-24: the capture plugin.** The licence half is done; the plugin exists
  only as archived phase-0 work.
