# Can SWE-chat become a runnable benchmark?

Measured 2026-09-13. Every number here came from running something, not from
reading the dataset card. Where a measurement was wrong the first time, the
correction is noted — several were.

**Short answer: yes.** A real fail-to-pass case was built end to end from
SWE-chat data and verified in a container. The remaining work is scale and
screening, not feasibility.

---

## The proof

Repository `zchee/spanner-manager`, commit `241d66c5f155`, which changed
`codegen/generator.go` and `codegen/generator_test.go` together.

Take the **parent** commit's source, restore **only the test file** from the
child commit, and run:

```
codegen/generator_test.go:527:30: undefined: detectGoModuleConfig
FAIL  github.com/zchee/spanner-manager/codegen [build failed]
```

Same tree at the child commit:

```
ok  github.com/zchee/spanner-manager/codegen  1.654s
```

Fail-to-pass. Another agent can be handed the parent tree plus that test and
scored objectively on whether it makes the test pass. This is the SWE-bench
construction, built entirely from SWE-chat.

### The construction that does *not* work

Checking out the parent wholesale and running its own tests. Both states pass,
because reverting the source also reverts the test — the parent runs the old
test set and is green. The first attempt did exactly this and produced a false
negative. **The tests must come from the child, the source from the parent.**

---

## Does the environment reproduce?

Two languages, stock images, no hand-holding:

| | Image | Result | Warm run |
|---|---|---|---:|
| Go | `golang:1.26` | build clean, 6 packages pass | **18.3 s** |
| TypeScript | `oven/bun:1` | 69 pass, 1 skip, 0 fail | **0.7 s** |

TypeScript matters most — it is 59% of the candidate sessions. It failed twice
before working, both times because of the image, not the repo: the first lacked
`git` (pnpm needs it), the second lacked `bun` while the project pins
`packageManager: pnpm@10.33.0` and drives tests through `bun test`. **Toolchain
selection is the real environment problem**, and it is machine-recoverable: the
lockfile names the manager (37 npm, 19 bun, 16 pnpm repos) and `engines.node`
names the runtime.

At these rates a full sweep of 2,292 candidates is **1–2 hours at 8-way
parallelism**. Compute is not the constraint.

---

## Are the repositories still there?

Cloning at the exact recorded commit, across distinct repos:

| | |
|---|---:|
| Commit fetchable | **26 / 30 (87%)** |
| Commit **and** parent fetchable | 12 / 15 (80%) |

Failures are repositories gone private or deleted — an auth prompt, not a
missing commit.

What repos actually ship, measured by cloning 16 of them rather than inferring
from diffs:

| | |
|---|---:|
| Dependency manifest | 88% |
| Tests present | 88% |
| CI workflow | 69% |
| Lockfile | 56% |
| Dockerfile | 19% |

---

## How many candidates are there?

| Stage | Count |
|---|---:|
| Sessions resolving to a patched commit | 3,919 |
| Commits touching a test file | 2,847 (31%) |
| **Commits *modifying* an existing test** | **2,140** |
| Commits adding a new test file | 910 |
| Agent-authored files **and** touching tests | 907 |

The 2,140 modified-test commits are the strongest pool: a commit that only
*adds* tests will pass at the parent too, because the new tests do not exist
there. The 907 where the agent wrote both the code and the test are separately
interesting — that is where a weakened test would hide.

All 205 repositories are public GitHub, all licensed: 141 MIT, 22 Apache-2.0,
16 other permissive, 21 copyleft (AGPL/GPL) needing a decision.

---

## What is missing

1. **Test selection.** Running a whole suite is slow and fragile. The target is
   the specific test the commit touched.
2. **Screening for flakes and external services.** Some suites need databases or
   network. One repo's "external dependency" turned out to be the string
   `"localhost:9010"` inside a table-driven config test — screening has to
   execute, not grep.
3. **Yield rate is unknown.** One case works. What fraction of the 2,140 are
   genuinely scorable needs a sample of ~50 across Go, TypeScript and Python.
   That is the next experiment and it is a few hours of compute.
4. **The corpus excludes the worst failures.** The paper is explicit: *"If the
   user abandons the agent's output entirely, session logs are not committed."*
   We are mining for failures in data that structurally omits the total ones.

---

## Measurement errors made while producing this

Recorded because each one, if reported, would have said the benchmark was
infeasible — and every mistake pointed pessimistic.

| What broke | Wrong answer it gave | Truth |
|---|---|---|
| DuckDB `similar to` matches whole strings | "0 commits touch tests" (twice) | 2,847 |
| `git ls-remote <sha>` only lists refs | "commits are gone" | 87% fetchable |
| `timeout` does not exist on macOS | container run silently skipped, `exit=0` from `echo` | tests pass |
| zsh `set -- $spec` left variables empty | two clones "failed" | both fine |
| Wrong container images for TypeScript | "TS does not reproduce" | 69 tests pass |
| Paired a patch with the wrong commit's tree | "reverse-apply is broken" | pairing error |

The lesson worth keeping: when a measurement says a thing is impossible, suspect
the instrument first.
