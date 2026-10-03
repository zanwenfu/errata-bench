"""Break the study's rules one at a time and see a check in study_holds.py fail each time (docs/study.md).

    .venv/bin/python checks/study_mutants.py

Each mutant replaces one line or block of the study's code with a wrong one, in a copy of src/, scripts/,
checks/ and docs/ in a temporary folder, and runs checks/study_holds.py there with a fresh bytecode cache. A
mutant is caught when the checks fail. It prints one line per mutant and exits 1 if any survives or no longer
applies (the code it names has changed). The model client stays unreachable, as in the checks. It takes a
minute or two; not run in CI.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SES = "src/errata_bench/study/sessions.py"
RUN = "scripts/study.py"
REV = "src/errata_bench/study/review.py"
MER = "src/errata_bench/study/merge.py"

M = []


def mutant(name, rel, old, new):
    M.append((name, rel, old, new))


mutant("the transcript is ignored", SES,
       "            if transcript is not None:\n                if key in transcript.other and key not in typed_keys:",
       "            if False:\n                if key in transcript.other and key not in typed_keys:")
mutant("copies pass (matching ignores order)", SES,
       "    for j in range(after if same_message else after + 1, len(typed)):", "    for j in range(0, len(typed)):")
mutant("a row found nowhere keeps the table's word", SES,
       "                if j is None:\n                    continue\n                at = j",
       "                if j is not None:\n                    at = j")
mutant("a teammate's message is the developer's", SES,
       'NOT_THE_DEVELOPER = (*NOT_A_PROMPT, "<teammate-message")', 'NOT_THE_DEVELOPER = (*NOT_A_PROMPT,)')
mutant("teamName alone marks a teammate", SES,
       'teammate = key.startswith("<teammate-message") or (e.get("teamName") and not e.get("permissionMode"))',
       'teammate = key.startswith("<teammate-message") or e.get("teamName")')
mutant("a refused call is not a message", SES,
       '        if kind == "tool_result" and raw.startswith(REJECTED):', '        if False:')
mutant("Claude Code's note stays in the refusal", SES,
       "            said = said.split(NOTE, 1)[0].strip()", "            said = said.strip()")
mutant("Ultraplan's own refusal counts", SES,
       "            if said.startswith(NOTICES):\n                continue", "            if False:\n                continue")
mutant("a delivered queued prompt counts twice", SES,
       "            if key in waiting:   # the delivery of a message queued",
       "            if False:   # the delivery of a message queued")
mutant("a queued message's label is lost", SES,
       "                out[q] = (out[q][0], out[q][1], out[q][2] or label)\n                if transcript is not None:",
       "                pass\n                if transcript is not None:")
mutant("a queued JSON entry is not read", SES, '            if text.startswith("{"):', '            if False:')
mutant("a queued notice or UI command counts", SES,
       'if (not text or text.startswith(("<", *NOTICES)) or key in fired or re.fullmatch(r"/[\\w:.-]+", text)',
       'if (not text or text.startswith("<") or key in fired')
mutant("a /loop re-run counts", SES,
       '            if not said or (said.startswith("/loop") and key in fired) or _automatic(key, automatic):',
       '            if not said:')
mutant("the agent's scheduled prompts are not collected", SES,
       '                            and b.get("name") in ("ScheduleWakeup", "CronCreate")',
       '                            and b.get("name") in ()')
mutant("a short automatic text drops longer ones", SES,
       "        if len(a) >= 30 and len(key) >= 30 and (a.startswith(key[:60]) or key.startswith(a[:60])):",
       "        if (a.startswith(key[:60]) or key.startswith(a[:60])):")
mutant("anonymised copies do not match", SES,
       '    return PLACEHOLDER.sub("<X>", _norm(text))', '    return _norm(text)')
mutant("no near match for the next entry", SES,
       "        if SequenceMatcher(None, key[:3000], typed[j][:3000], autojunk=False).ratio() >= 0.9:",
       "        if False:")
mutant("an image-only message is dropped", SES, '        if key == "":', '        if False:')
mutant("a synthetic message is the agent's report", SES,
       '    return t.get("turn_type") == "assistant_response" and t.get("model") == SYNTHETIC', '    return False')
mutant("a missing report is not flagged", SES, "    if not report.ends_with_report:", "    if False:")
mutant("the window reads past the reply", SES,
       '    work = [t for t in turns if report.request_end < (t.get("turn_number") or 0) < report.handoff_turn\n'
       '            and t.get("turn_type") != "user_prompt" and not _synthetic(t)]\n    assert',
       '    work = [t for t in turns if report.request_end < (t.get("turn_number") or 0) < report.handoff_turn + 1000\n'
       '            and t.get("turn_type") != "user_prompt" and not _synthetic(t)]\n    assert True or')
mutant("rows in a row are not merged", SES, "            if out and not since_work:", "            if False:")
mutant("a notice ends a report", SES,
       "        if s.startswith((*NOT_THE_DEVELOPER, *NOTICES)):\n            continue", "        if False:\n            continue")
mutant("a quote in the context counts as the work's", REV,
       '"quote_in_work": found and bool(work) and quote_appears(p.quote, work)', '"quote_in_work": found')
mutant("the merge looks back one report too far", MER, "LOOKBACK = 2 ", "LOOKBACK = 3 ")
mutant("the spend line is not checked", RUN,
       '            if state["stopped"] or max(state["spent"], _spent(run)) >= max_usd:', '            if False:')
mutant("the spend line is read once, not per call", RUN,
       '            if state["stopped"] or max(state["spent"], _spent(run)) >= max_usd:',
       '            if state["stopped"] or state["spent"] >= max_usd:')
mutant("a 'same' without its words counts", RUN,
       '            and bool(v.get("developer_words_found", True)) and bool(v.get("reviewer_words_found", True)))',
       '            and True)')
mutant("an incomplete merge is a miss", RUN,
       '        elif any(v.get("missing") or not v.get("match_known", True) for v in vs):', '        elif False:')
mutant("a merge with no id right is kept", RUN,
       '                if verdicts and all(v.get("missing") for v in verdicts):', '                if False:')
mutant("an approved plan is classified", RUN, '        if r.get("reply_kind") == "plan":', '        if False:')

mutant("the merge ignores --limit", RUN,
       '    todo = todo[: args.limit] if getattr(args, "limit", 0) else todo\n    with only_one(run, f"running merge',
       '    with only_one(run, f"running merge')

mutant("the merge is not told the rules version", RUN,
       "                result = await M.merge(rr, rep[\"reply\"], cands, model=_model(), rules=rules)",
       "                result = await M.merge(rr, rep[\"reply\"], cands, model=_model())")
mutant("a merge row does not record its rules", RUN,
       '            base = {"session_id": sid, "index": k, "rules": rules}',
       '            base = {"session_id": sid, "index": k}')
mutant("a folder mixes rules versions", RUN,
       "    if held - {rules}:", "    if False:")
mutant("the second rules lose the guide's examples", MER,
       'INSTRUCTIONS = {1: INSTRUCTIONS_1, 2: INSTRUCTIONS_1 + "\\n" + EXAMPLES}',
       'INSTRUCTIONS = {1: INSTRUCTIONS_1, 2: INSTRUCTIONS_1}')
mutant("the merge ignores the rules it is given", MER,
       "instructions=with_field_guide(INSTRUCTIONS[rules], Merge)",
       "instructions=with_field_guide(INSTRUCTIONS[RULES], Merge)")
mutant("the merge's call does not pass its rules on", MER,
       "    agent = agent_for(model, rules)", "    agent = agent_for(model)")
mutant("a call on the next line is missed", RUN,
       "        if line:\n            return line.strip",
       "        if True:\n            return line.strip")
mutant("can't tell counts in the share", RUN,
       'for s, c in calls.values()\n                                                        if c != "can\'t tell"])}',
       'for s, c in calls.values()])}')
SRC_CALL = '            return line.strip(".").lower().replace("' + chr(0x2019) + '", "' + "'" + '")'
mutant("a call keeps its full stop", RUN, SRC_CALL, SRC_CALL.replace('.strip(".")', "", 1))
mutant("a curly apostrophe is not read", RUN, SRC_CALL, SRC_CALL.split(".replace(")[0])
mutant("a call whose mark is deleted is no call", RUN,
       "            elif v or v is None:\n                unread.append(h.group(1))",
       "            elif v:\n                unread.append(h.group(1))")
mutant("alone.md is written over", RUN, '    if (run / f"alone{tag}.md").exists():', "    if False:")
mutant("a caught sheet is written over", RUN,
       'out_path, key_path = run / "caught.md", run / "caught-key.json"\n    if out_path.exists():',
       'out_path, key_path = run / "caught.md", run / "caught-key.json"\n    if False:')
mutant("a replies sheet is written over", RUN,
       'out_path, key_path = run / "replies.csv", run / "replies-key.csv"\n    if out_path.exists():',
       'out_path, key_path = run / "replies.csv", run / "replies-key.csv"\n    if False:')
mutant("the replies sheet's halves are wrong", RUN,
       'sizes = {"real error": args.n // 2, "other pushback": args.n // 4}',
       'sizes = {"real error": args.n // 4, "other pushback": args.n // 4}')
mutant("an approved plan enters the replies sheet", RUN,
       'if not r.get("approved_plan") and key(r) in reports', 'if key(r) in reports')
mutant("the replies sheet gives the reading away", RUN,
       'rep["reply"][:6000], "", "", ""])', 'rep["reply"][:6000], name, "", ""])')
mutant("unclear counts as a real error", RUN,
       's["real_error"] += p == "yes" and e == "yes"', 's["real_error"] += p == "yes" and e in ("yes", "unclear")')
mutant("the replies' real-error share counts pushback", RUN,
       's["real_error_share"] = T.share(s["real_error"], s["labelled"])',
       's["real_error_share"] = T.share(s["pushback"], s["labelled"])')
mutant("a spreadsheet's byte-order mark breaks the header", RUN,
       'text = path_.read_text(encoding="utf-8-sig")', 'text = path_.read_text(encoding="utf-8")')
SRC_LETTERS = next(l for l in (REPO / RUN).read_text().splitlines() if l.startswith("    text = re.split("))
mutant("semicolons are not read", RUN,
       '    delimiter = max((",", ";", "\\t"), key=header.count)', '    delimiter = ","')
mutant("the caught sheet takes only part of the disputed", RUN,
       'take += [(name, k) for k in (ks if name == "disputed" else ks[: args.per_stratum])]',
       'take += [(name, k) for k in ks[: args.per_stratum]]')
mutant("the caught sheet shows the stratum", RUN,
       'reply {k[1]}\\n\\n**The developer replied:**\\n\\n"',
       'reply {k[1]}\\n\\n({name}) **The developer replied:**\\n\\n"')
mutant("the caught sheet shows the merge's reason", RUN,
       '            lines.append(f"**{letter}** (handback {j}): {p[\'what_is_wrong\']}\\n\\n{_fenced(p[\'quote\'])}")',
       '            lines.append(f"**{letter}** (handback {j}): {p[\'what_is_wrong\']} {m1[k][\'verdicts\'][0].get(\'reason\', \'\')}\\n\\n{_fenced(p[\'quote\'])}")')
mutant("the caught sheet allows one version in both folders", RUN,
       "    if len(rules[0]) != 1 or len(rules[1]) != 1 or rules[0] == rules[1]:",
       "    if len(rules[0]) != 1 or len(rules[1]) != 1:")
mutant("a pushback merged over other problems is taken", RUN,
       '    if strata.get("not merged alike under both"):', "    if False:")
mutant("letters past Z are dropped silently", RUN, "    if widest > len(LETTERS):", "    if False:")
mutant("a long reply is cut without a mark", RUN,
       '''        reply = rep["reply"] if len(rep["reply"]) <= 4000 else (''',
       '''        reply = rep["reply"][:4000] if True else (''')
mutant("an annotated heading hides its item", RUN,
       r'''CAUGHT_HEAD = re.compile(r"^## (\d+)\. session (\w+), reply (\d+)\b.*$", re.M)''',
       r'''CAUGHT_HEAD = re.compile(r"^## (\d+)\. session (\w+), reply (\d+)$", re.M)''')
mutant("an annotated alone heading hides its item", RUN,
       r'''ALONE_HEAD = re.compile(r"^## (\d+)\. session (\w+), handback (\d+) \((r\d+p\d+)\).*$", re.M)''',
       r'''ALONE_HEAD = re.compile(r"^## (\d+)\. session (\w+), handback (\d+) \((r\d+p\d+)\)$", re.M)''')
mutant("the next item is read as a call", RUN,
       '        if line.startswith(("<details>", "## ")) or line == "---":',
       '        if line.startswith("<details>"):')
mutant("backticks make a call unreadable", RUN, SRC_LETTERS, SRC_LETTERS.replace('.replace("`", "")', ""))
mutant("a comment after the letters makes a call unreadable", RUN, SRC_LETTERS,
       '    text = text.replace("`", "")')
mutant("a call may name a letter the item does not have", RUN,
       "    if parts and all(len(x) == 1 and x.upper() in allowed for x in parts):",
       "    if parts and all(len(x) == 1 for x in parts):")
mutant("a stratum is not weighed back to its size", RUN,
       "                cells[(bool(person[i]), bool(items[i][field]))] += pop[s] / count[s]",
       "                cells[(bool(person[i]), bool(items[i][field]))] += 1")
mutant("the pushbacks with no problem are left out", RUN,
       '            cells[(False, False)] += pop.get("no problems", 0)   # nothing to call: neither caught',
       "            pass")
mutant("figures are given before every stratum has its calls", RUN,
       "    enough = all(called.get(s, 0) >= min(MIN_CALLS, size[s]) for s in sampled)",
       "    enough = bool(person)")
mutant("a version is consistent before every item is called", RUN,
       '"consistent_with_the_person": every and _interval(bias)["low"] <= 0 <= _interval(bias)["high"],',
       '"consistent_with_the_person": _interval(bias)["low"] <= 0 <= _interval(bias)["high"],')
mutant("the uncalled pushbacks are not drawn", RUN,
       "                total += k[s] + (rng.binomialvariate(rest, p) if rest else 0)",
       "                total += k[s] * pop[s] / called[s]")
mutant("a stratum called in full is drawn as if part were uncalled", RUN,
       "                    left = pop[s] - called[s]", "                    left = max(1, pop[s] - called[s])")
mutant("kappa's interval ignores the uncalled", RUN,
       "                        drawn[c] += counts[s].get(c, 0) + (left * gammas[n_c] / sum(gammas) if left else 0)",
       "                        drawn[c] += counts[s].get(c, 0) * pop[s] / called[s]")
mutant("the bias is taken from the rounded share", RUN,
       '"merge_minus_person": {"share": round(merge_share[version] - point, 4), **_interval(bias)},',
       '"merge_minus_person": {"share": round(merge_share[version] - round(point, 4), 4), **_interval(bias)},')
mutant("which problem is not weighed", RUN,
       "                \"same_problem_when_both_caught\": round(sum(weight[items[i][\"stratum\"]] for i in both\n"
       "                                                           if person[i] & set(items[i][field])) / w_both, 4)",
       "                \"same_problem_when_both_caught\": round(sum(1 for i in both\n"
       "                                                           if person[i] & set(items[i][field])) / len(both), 4)")
mutant("the disputed sign test is one-sided", RUN,
       "        p = min(1.0, 2 * sum(math.comb(n, x) for x in range(top, n + 1)) / 2 ** n)",
       "        p = min(1.0, sum(math.comb(n, x) for x in range(top, n + 1)) / 2 ** n)")
mutant("the merge drops SWE-chat's label", RUN,
       'if rr.get("approved_plan") or not (rr.get("is_pushback") or rr.get("reply_label") in S.PUSHBACK_KINDS):',
       'if rr.get("approved_plan") or not rr.get("is_pushback"):')
mutant("the merge takes an approved plan with a pushback label", RUN,
       'if rr.get("approved_plan") or not (rr.get("is_pushback") or rr.get("reply_label") in S.PUSHBACK_KINDS):',
       'if not (rr.get("is_pushback") or rr.get("reply_label") in S.PUSHBACK_KINDS):')
mutant("SWE-chat's labelled pushbacks keep approved plans", RUN,
       'if r.get("reply_label") in S.PUSHBACK_KINDS\n                and not r.get("approved_plan")}',
       'if r.get("reply_label") in S.PUSHBACK_KINDS}')
mutant("the share by SWE-chat's label counts ours", RUN,
       '"by_swe_chat_label": caught(labelled),', '"by_swe_chat_label": caught(pushbacks),')
mutant("both-say counts every pushback", RUN,
       '"both_say_pushback": caught([k for k in pushbacks if k in labelled]),',
       '"both_say_pushback": caught(list(pushbacks)),')
mutant("the lenient share counts same only", RUN,
       'return T.bootstrap([(k[0], merged[k] in ("same", "related")) for k in keys if k in merged])',
       'return T.bootstrap([(k[0], merged[k] == "same") for k in keys if k in merged])')
mutant("a related beside a same without words is not related", RUN,
       '        elif any(v.get("match") == "related" and not v.get("missing") for v in vs):\n'
       '            outcome[k] = "related"\n'
       '        elif any(v.get("match") == "same" for v in vs):\n'
       '            outcome[k] = "same, words not found"',
       '        elif any(v.get("match") == "same" for v in vs):\n'
       '            outcome[k] = "same, words not found"\n'
       '        elif any(v.get("match") == "related" and not v.get("missing") for v in vs):\n'
       '            outcome[k] = "related"')
mutant("combine drops the merges", RUN,
       '    for name in ("sessions", "reports", "review", "human", "merge"):',
       '    for name in ("sessions", "reports", "review", "human"):')
mutant("combine mixes versions of the rules", RUN,
       "    if len(set().union(*rules.values())) > 1:", "    if False:")
mutant("the estimate counts the rules versions, not their text", RUN,
       'len(M.INSTRUCTIONS[M.RULES])}', 'len(M.INSTRUCTIONS)}')
mutant("the estimate counts SWE-chat's labels only", RUN,
       '    labelled = round(1.2 * sum(r.get("reply_label") in S.PUSHBACK_KINDS for r in reports))',
       '    labelled = sum(r.get("reply_label") in S.PUSHBACK_KINDS for r in reports)')


failed = 0
for name, rel, old, new in M:
    with tempfile.TemporaryDirectory() as tmp:
        for d in ("src", "scripts", "checks", "docs"):
            shutil.copytree(REPO / d, Path(tmp) / d, ignore=shutil.ignore_patterns("__pycache__"))
        f = Path(tmp) / rel
        text = f.read_text()
        if old not in text:
            print(f"NOT APPLIED  {name}")
            failed += 1
            continue
        f.write_text(text.replace(old, new, 1))
        env = {k: v for k, v in os.environ.items() if k not in ("ERRATA_PROVIDER", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY")}
        env["PYTHONPYCACHEPREFIX"] = str(Path(tmp) / "pyc")   # a stale .pyc once made a survivor read caught
        env["ERRATA_DOTENV"] = "0"
        r = subprocess.run([sys.executable, "checks/study_holds.py"], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=600)
        caught = r.returncode != 0
        crash = "Traceback" in (r.stdout + r.stderr)
        failed += not caught
        print(f"{'caught  ' if caught else 'SURVIVED'}  {name}" + ("  (by a crash)" if caught and crash else ""))

print(f"{len(M) - failed} of {len(M)} mutants caught")
sys.exit(1 if failed else 0)
