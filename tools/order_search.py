#!/usr/bin/env python3
"""Brute-force the order of local declarations or of a run of statements; MWCC's register
allocation and scheduling follow source order. Run in WSL from the repo root, after a `ninja` build.

  tools/order_search.py FILE.c START END              permute the locals declared right after `) {`
  tools/order_search.py FILE.c START END --lines A B  permute lines A..B of FILE (1-based, one statement each)

START/END are hex addresses as for diff_func.sh. Prints the 5 best orders by diff-line count; on an
exact match writes FILE.match.c and stops. n lines = n! builds (5 = 120, about 3 minutes).
"""
import itertools, os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

path, start, end = sys.argv[1:4]
src = open(path).read()
if "--lines" in sys.argv:
    a, b = (int(x) for x in sys.argv[sys.argv.index("--lines") + 1:][:2])
    lines = src.splitlines(keepends=True)
    head, block, tail = "".join(lines[:a - 1]), lines[a - 1:b], "".join(lines[b:])
else:
    m = re.search(r"\) \{\n((?:    [^\n=(]+;\n)+)", src)
    head, block, tail = src[:m.start(1)], m[1].splitlines(keepends=True), src[m.end(1):]


def score(order):
    with tempfile.NamedTemporaryFile("w", suffix=".c", dir=os.path.dirname(path) or ".", delete=False) as f:
        f.write(head + "".join(order) + tail)
    try:
        r = subprocess.run(["tools/diff_func.sh", f.name, start, end], capture_output=True, text=True)
    finally:
        os.unlink(f.name)
    return (0 if r.returncode == 0 else sum(l[:1] in "<>" for l in r.stdout.splitlines()) or 9999), order


best = []
with ThreadPoolExecutor(os.cpu_count()) as ex:
    for s, order in ex.map(score, itertools.permutations(block)):
        if s == 0:
            out = re.sub(r"\.c$", ".match.c", path)
            open(out, "w").write(head + "".join(order) + tail)
            print("MATCH ->", out)
            ex.shutdown(cancel_futures=True)
            sys.exit()
        best = sorted(best + [(s, " | ".join(l.strip() for l in order))])[:5]
for s, o in best:
    print(s, o)
