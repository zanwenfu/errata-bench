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
| Spending | a run overspends, or bills the wrong account | each paid run since the D-40 run has a spend guard that stops it at a stop line agreed in advance | the D-40 run cost about $1,320 at list prices, under its $2,500 line |
