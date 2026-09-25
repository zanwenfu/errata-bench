#!/usr/bin/env python3
"""Which paired differences hold under both judges (Holm < 0.05, same sign), per D-40 task set."""
import re, sys
NAN = float("nan")

def parse(path):
    out, section = {}, None
    for line in open(path):
        # The three endpoints' headers, by name: "judge: ..." is also the file's own
        # first line, so a prefix test put the unverified-claim pairs under the
        # primary endpoint's key and overwrote them.
        for name in ("PRIMARY", "judge: makes an unverified claim", "clean pass"):
            if line.startswith(name):
                section = {"PRIMARY": "misreported", "judge: makes an unverified claim": "unverified claim",
                           "clean pass": "clean pass"}[name]
        m = re.match(r"\s+d40-(\S+) - d40-(\S+): \d+ tasks, mean difference ([+-][\d.]+) .*Holm ([\d.]+)", line)
        if m:
            out[(section, m.group(1), m.group(2))] = (float(m.group(3)), float(m.group(4)))
    return out

suffix = sys.argv[1] if len(sys.argv) > 1 else ""
for tset in ("headline", "all", "new"):
    a = parse(f"tests-{tset}-gpt-6-astra{suffix}.txt")
    s = parse(f"tests-{tset}-gpt-6-sol{suffix}.txt")
    print(f"=== {tset}{suffix}: significant (Holm < 0.05) under BOTH judges, same sign")
    both = 0
    for k in a:
        if k in s:
            (da, pa), (ds, ps) = a[k], s[k]
            if pa < 0.05 and ps < 0.05 and (da > 0) == (ds > 0):
                both += 1
                print(f"   {k[0]:16} {k[1]:18} vs {k[2]:18} astra {da:+.3f} (Holm {pa:.4f}) | sol {ds:+.3f} (Holm {ps:.4f})")
    only = [k for k in a if a[k][1] < 0.05 and not (k in s and s[k][1] < 0.05)]
    sol_only = [k for k in s if s[k][1] < 0.05 and not (k in a and a[k][1] < 0.05)]
    print(f"   -> {both} hold under both; {len(only)} under gpt-6-astra only; {len(sol_only)} under gpt-6-sol only")
    for k in only:
        y = s.get(k, (NAN, NAN))
        print(f"      astra only: {k[0]:16} {k[1]:18} vs {k[2]:18} astra {a[k][0]:+.3f} (Holm {a[k][1]:.4f}) | sol {y[0]:+.3f} (Holm {y[1]:.4f})")
    for k in sol_only:
        y = a.get(k, (NAN, NAN))
        print(f"      sol only:   {k[0]:16} {k[1]:18} vs {k[2]:18} sol {s[k][0]:+.3f} (Holm {s[k][1]:.4f}) | astra {y[0]:+.3f} (Holm {y[1]:.4f})")
