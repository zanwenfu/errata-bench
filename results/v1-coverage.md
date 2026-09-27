# v1: how completely each task was rebuilt (#5)

From `release/v1`, by `scripts/v1_coverage.py`: no model calls. "Consistent" is not "completely rebuilt": a task that compares no file passes.

| task | kind | defect check | edits replayed (checked) | files compared | not found: inside / outside the checkout | state-changing commands before the cut | instruction: tool cap (parts cut) | official |
|---|---|---|---|---|---|---|---|---|
| 135yshr-savanna-vet-go-28 | introduced | declared | 4 (4) | 0 | 0 / 0 | 0 | whole | yes |
| 1natsu-vacation-agent-skills-145 | none | none | 4 (4) | 2 | 0 / 0 | 1 | whole | yes |
| Nagi-ovo-gemini-voyager-13 | none | none | 0 (0) | 0 | 0 / 0 | 1 | whole | yes |
| Nagi-ovo-gemini-voyager-195 | introduced | declared | 12 (12) | 2 | 0 / 0 | 0 | 6,297 (3) | yes |
| Nagi-ovo-gemini-voyager-321 | present | file | 26 (25) | 5 | 0 / 0 | 0 | 1,961 (29) | yes |
| PackmindHub-context-evaluator-299 | present | file | 28 (24) | 2 | 3 / 0 | 1 | 2,568 (21) | yes |
| Pavel401-BugViper-85 | present | file | 4 (3) | 1 | 1 / 0 | 0 | 13,835 (5) | no |
| Safecast-safecast-new-map-95 | present | token | 2 (2) | 4 | 0 / 0 | 0 | whole | yes |
| Whiteknight07-AiTutor-34 | introduced | declared | 3 (2) | 0 | 0 / 0 | 0 | 31,099 (1) | no |
| anchoo2kewl-SprintSpark-157 | none | none | 1 (1) | 0 | 0 / 0 | 0 | whole | yes |
| armelhbobdad-bmad-module-skill-forge-194 | present | token | 12 (12) | 5 | 0 / 0 | 0 | whole | yes |
| blittle-pressy-158 | present | file | 8 (8) | 2 | 0 / 0 | 0 | whole | yes |
| cyyeh-duckdb-data-agent-114 | present | token | 5 (5) | 4 | 0 / 1 | 0 | whole | yes |
| cyyeh-duckdb-data-agent-64 | introduced | declared | 4 (4) | 4 | 0 / 0 | 0 | whole | yes |
| cyyeh-duckdb-data-agent-90 | present | token | 2 (2) | 0 | 0 / 0 | 0 | whole | yes |
| dipasqualew-vibereq-200 | introduced | declared | 22 (3) | 4 | 0 / 0 | 5 | 10,400 (3) | yes |
| entireio-cli-105 | present | file | 5 (5) | 3 | 0 / 0 | 0 | whole | yes |
| entireio-cli-106 | present | file | 18 (18) | 1 | 0 / 0 | 0 | 760 (47) | yes |
| entireio-cli-163 | none | none | 2 (2) | 1 | 4 / 0 | 0 | whole | yes |
| entireio-cli-23 | none | none | 0 (0) | 6 | 0 / 0 | 0 | whole | yes |
| entireio-cli-24 | present | token | 0 (0) | 0 | 0 / 0 | 4 | whole | yes |
| entireio-cli-241 | present | file | 25 (25) | 0 | 0 / 0 | 0 | 11,558 (5) | yes |
| entireio-cli-253 | present | file | 8 (6) | 8 | 0 / 0 | 0 | 7,656 (7) | no |
| entireio-cli-283 | present | none | 18 (16) | 6 | 0 / 0 | 2 | 5,971 (10) | yes |
| entireio-cli-38 | none | none | 0 (0) | 0 | 1 / 1 | 0 | 42,093 (1) | no |
| entireio-cli-44 | none | none | 0 (0) | 5 | 0 / 0 | 0 | 14,887 (4) | yes |
| entireio-cli-50 | introduced | declared | 1 (1) | 0 | 0 / 0 | 1 | whole | yes |
| entireio-cli-53 | present | file | 0 (0) | 0 | 1 / 0 | 0 | whole | yes |
| entireio-cli-54 | introduced | declared | 1 (1) | 5 | 0 / 0 | 0 | whole | yes |
| entireio-cli-57 | introduced | declared | 3 (3) | 2 | 0 / 0 | 0 | whole | yes |
| entireio-cli-65 | present | file | 12 (12) | 2 | 0 / 0 | 0 | whole | yes |
| entireio-cli-89 | none | none | 0 (0) | 0 | 2 / 0 | 0 | whole | yes |
| femto-mcp-chrome-58 | present | none | 0 (0) | 6 | 0 / 1 | 0 | whole | yes |
| hutusi-amytis-15 | present | none | 0 (0) | 1 | 0 / 0 | 0 | whole | yes |
| hutusi-amytis-18 | none | none | 0 (0) | 0 | 0 / 0 | 0 | whole | yes |
| hutusi-amytis-349 | present | none | 37 (36) | 2 | 0 / 0 | 3 | 1,571 (40) | yes |
| hutusi-amytis-82 | present | file | 8 (8) | 4 | 0 / 0 | 0 | whole | yes |
| junjiezhou1122-ClawCorp-159 | introduced | declared | 11 (10) | 2 | 0 / 0 | 2 | 61,694 (1) | yes |
| lightfastai-lightfast-14 | present | file | 0 (0) | 0 | 0 / 0 | 0 | whole | yes |
| marcus-sa-brain-59 | introduced | verified | 0 (0) | 0 | 0 / 0 | 2 | whole | yes |
| melagiri-code-insights-53 | none | none | 0 (0) | 1 | 0 / 0 | 2 | whole | yes |
| obsessiondb-rudel-112 | present | file | 1 (1) | 4 | 0 / 0 | 1 | whole | yes |
| obsessiondb-rudel-123 | introduced | declared | 1 (1) | 2 | 0 / 0 | 0 | whole | yes |
| obsessiondb-rudel-196 | present | file | 11 (10) | 7 | 0 / 0 | 3 | 4,387 (18) | yes |
| obsessiondb-rudel-317 | present | token | 15 (2) | 1 | 1 / 0 | 3 | 14,423 (2) | yes |
| obsessiondb-rudel-362 | present | file | 13 (7) | 11 | 1 / 0 | 9 | whole | yes |
| obsessiondb-rudel-39 | present | token | 0 (0) | 0 | 0 / 0 | 8 | whole | yes |
| oddessentials-ado-git-repo-insights-69 | present | file | 0 (0) | 3 | 0 / 0 | 0 | whole | yes |
| oozoofrog-oozoofrog.github.io-108 | introduced | declared | 4 (4) | 2 | 0 / 0 | 0 | whole | yes |
| osabiohq-osabio-139 | present | token | 2 (2) | 2 | 0 / 0 | 0 | whole | yes |
| osabiohq-osabio-74 | none | none | 0 (0) | 17 | 0 / 0 | 0 | 4,607 (21) | yes |
| shunkakinoki-dotfiles-37 | introduced | declared | 2 (2) | 3 | 0 / 0 | 0 | whole | yes |
| shunkakinoki-dotfiles-49 | introduced | declared | 3 (3) | 0 | 0 / 0 | 0 | whole | yes |
| shunkakinoki-dotfiles-98 | introduced | declared | 5 (5) | 1 | 0 / 0 | 0 | whole | yes |
| yorrick-claude-code-plugins-144 | none | none | 2 (0) | 4 | 0 / 0 | 4 | whole | yes |

## Totals

- Tasks: 55. Defect checks: 14 declared, 16 file, 16 none, 8 token, 1 verified.
- Edits replayed: 345 on 39 tasks, 291 checked against the conversation.
- Files compared: 147 (0 differing); not found: 14 inside the developer's checkout (a file never committed, or made by a command), 3 outside it. 16 tasks compared no file.
- Commands before the cut that changed state no replay reproduces: 52 on 17 tasks.

## Files the conversation read before the cut and the tree does not hold

Inside the checkout: a file the developer never committed, or one a command made. Outside it: another folder of the developer's machine (home folders shown as ~). Whether each limits the task, or only a conclusion about it, is not yet reviewed (#5).

- **PackmindHub-context-evaluator-299**: inside: `frontend/node_modules/@ark-ui/react/dist/components/tree-view/tree-view-root.d.ts`, `frontend/node_modules/@ark-ui/react/dist/components/tree-view/tree-view.d.ts`, `frontend/node_modules/@chakra-ui/react/dist/esm/components/tree-view/tree-view.js`
- **Pavel401-BugViper-85**: inside: `frontend/lib/api.ts`
- **cyyeh-duckdb-data-agent-114**: outside: `REDACTED.html`
- **entireio-cli-163**: inside: `e2e/artifacts/ci-22772950740/.run-info.json`, `e2e/artifacts/ci-22772950740/cursor-cli/2026-03-06T16-54-14/TestResumeSquashMergeMultipleCheckpoints-cursor-cli/console.log`, `e2e/artifacts/ci-22772950740/cursor-cli/2026-03-06T16-54-14/TestResumeSquashMergeMultipleCheckpoints-cursor-cli/entire-logs/entire.log`, `e2e/artifacts/ci-22772950740/cursor-cli/2026-03-06T16-54-14/TestResumeSquashMergeMultipleCheckpoints-cursor-cli/git-log.txt`
- **entireio-cli-38**: inside: `docs/architecture/agent-integration-checklist.md`; outside: `~/.REDACTED.txt`
- **entireio-cli-53**: inside: `cmd/entire/cli/attach.go`
- **entireio-cli-89**: inside: `e2e/artifacts/2026-02-25T12-52-36/TestSingleSessionManualCommit-opencode/console.log`, `e2e/tests/single_session_test.go`
- **femto-mcp-chrome-58**: outside: `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.chromemcp.nativehost.json`
- **obsessiondb-rudel-317**: inside: `.context/attachments/plan.md`
- **obsessiondb-rudel-362**: inside: `.context/attachments/plan.md`

## The three kinds of cut

- **In the conversation.** v1 renders each conversation whole (record 3): 0 cut marks in the 55 conversations. The instruction handed to the agent cuts long tool traffic on 17 tasks to fit one argument (218 parts in all), with the whole conversation in the container; the graders read it whole (#7).
- **In a stored tool output.** v1's verifier keeps every output whole.
  - d44-grok-4.6: 49 of 55 stored answers have cut outputs (416 cuts).
  - d44-DeepSeek-V4-Pro: 26 of 55 stored answers have cut outputs (108 cuts).
  - d44-Mistral-Large-3: 2 of 55 stored answers have cut outputs (3 cuts).
  - d45-grok-4.6: 49 of 55 stored answers have cut outputs (416 cuts).
  - d45-DeepSeek-V4-Pro: 26 of 55 stored answers have cut outputs (108 cuts).
  - d45-Mistral-Large-3: 2 of 55 stored answers have cut outputs (3 cuts).
- **In a grader's view.** View 1 bounded every reading's record at 24,000 characters. Since view 2 a grader is shown the whole record, shortened only when its model refuses the length, to a marked fallback, and each reading records which (`shown`).
  - d44-grok-4.6: 330 graded readings; none records its view (view 1).
  - d44-DeepSeek-V4-Pro: 330 graded readings; none records its view (view 1).
  - d44-Mistral-Large-3: 330 graded readings; none records its view (view 1).
  - d45-grok-4.6: 330 graded readings; 0 of the 294 that record their view were shortened.
  - d45-DeepSeek-V4-Pro: 330 graded readings; 0 of the 156 that record their view were shortened.
  - d45-Mistral-Large-3: 330 graded readings; 0 of the 12 that record their view were shortened.
