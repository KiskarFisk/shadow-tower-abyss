#!/usr/bin/env python3
"""Batch-match game functions. Run in WSL from the repo root, after a successful `ninja`.

  batch.py list [MAXSIZE]        unmatched game functions (name addr size) up to MAXSIZE bytes
  batch.py asm NAME...           print the asm of functions
  batch.py check FILE.c          FILE holds blocks separated by `// ---`, each standalone C defining
                                 exactly one func_XXXXXXXX; compiles each, reports MATCH/FAIL
  batch.py apply FILE.c          check, then move every MATCH into src/ (contiguous runs share a file)
                                 and add c subsegments to the yaml
"""
import os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
YAML = Path("config/SLPS_252.17.yaml")
CC = [os.path.expanduser("~/.local/bin/wibo"), "tools/mwcc/3.0b52-030722/mwccps2.exe",
      "-c", os.environ.get("FLAGS", "-O4,p"), "-nostdinc", "-Iinclude", "-Isrc"]  # FLAGS=-O4 for non-speed files
EXE = Path("SLPS_252.17").read_bytes()
off = lambda addr: addr - 0x100000 + 0x80


def funcs():
    """asm-order list of (name, addr, size, asmfile, text) for unmatched game functions."""
    out = []
    for f in sorted(Path("asm").glob("game*.s")):
        for m in re.finditer(r"nonmatching (func_([0-9A-F]{8})), (0x[0-9A-F]+)\n(.*?)endlabel \1",
                             f.read_text(), re.S):
            out.append((m[1], int(m[2], 16), int(m[3], 16), f, m[4]))
    return out


def elf_syms():
    nm = subprocess.run(["mips-linux-gnu-nm", "build/SLPS_252.17.elf"], capture_output=True, text=True).stdout
    return {n: v for v, _, n in (l.split() for l in nm.splitlines() if len(l.split()) == 3)}


def check_block(src, syms, info):
    name = re.search(r"^\S.*\b(func_[0-9A-F]{8})\s*\([^;]*$", src, re.M)
    if not name:
        return None, "no function"
    name = name[1]
    if name not in info:
        return name, "not an unmatched game function"
    addr, size = info[name]
    with tempfile.TemporaryDirectory() as t:
        Path(t, "a.c").write_text(src)
        r = subprocess.run(CC + ["-o", f"{t}/a.o", f"{t}/a.c"], capture_output=True, text=True,
                           env={**os.environ, "MWCIncludes": "."})
        if not Path(t, "a.o").exists():
            return name, "compile error: " + "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                                                      if "MWCIncludes" not in l and "Usage Warning" not in l)
        nm = subprocess.run(["mips-linux-gnu-nm", f"{t}/a.o"], capture_output=True, text=True).stdout
        defs, fo = set(), None
        for l in nm.splitlines():
            p = l.split()
            if len(p) == 3:
                defs.add(p[2])
                if p[2] == name:
                    fo = int(p[0], 16)
        Path(t, "s.ld").write_text("".join(f"{n} = 0x{v};\n" for n, v in syms.items()
                                           if n not in defs and n[:2] in ("fu", "D_", "en")))
        Path(t, "a.ld").write_text(f"_gp = 0x209B70;\nSECTIONS {{ .text 0x{addr - fo:x} : {{ *(.text) }}"
                                   " /DISCARD/ : { *(*) } }\n")
        r = subprocess.run(["mips-linux-gnu-ld", "-EL", "--no-warn-mismatch", "--no-warn-rwx-segments", "-e", "0",
                            "-T", f"{t}/a.ld", "-T", f"{t}/s.ld", "-o", f"{t}/a.elf", f"{t}/a.o"],
                           capture_output=True, text=True)
        if r.returncode:
            return name, "link error: " + r.stderr.strip()
        subprocess.run(["mips-linux-gnu-objcopy", "-O", "binary", "-j", ".text", f"{t}/a.elf", f"{t}/a.bin"])
        new = Path(t, "a.bin").read_bytes()[fo:fo + size]
    orig = EXE[off(addr):off(addr) + size]
    if new == orig:
        return name, "MATCH"
    if os.environ.get("DIFF"):  # DIFF=1: side-by-side disassembly of failures
        def dis(b):
            with tempfile.NamedTemporaryFile(suffix=".bin") as f:
                f.write(b); f.flush()
                out = subprocess.run(["mips-linux-gnu-objdump", "-D", "-z", "-b", "binary", "-m", "mips:5900", "-EL",
                                      "-M", "gpr-names=32", f"--adjust-vma=0x{addr:x}", f.name],
                                     capture_output=True, text=True).stdout
            return [l.split("\t", 2)[-1].replace("\t", " ") for l in out.splitlines() if re.match(r"\s+[0-9a-f]+:", l)]
        a, b = dis(orig), dis(new)
        print(f"--- {name}\n" + "\n".join(f"{'  ' if x == y else '! '}{x:<34} {y}"
                                           for x, y in zip(a + [""] * len(b), b + [""] * len(a)) if x or y))
    bad = [i for i in range(0, size, 4) if new[i:i + 4] != orig[i:i + 4]]
    return name, f"FAIL ({len(bad)}/{size // 4} words differ, first at +0x{bad[0]:x}; got {len(new)} bytes)" \
        if bad else f"FAIL (size {len(new)} vs {size})"


def check(path):
    info = {n: (a, s) for n, a, s, _, _ in funcs()}
    syms = elf_syms()
    blocks = [b.strip() + "\n" for b in Path(path).read_text().split("// ---") if b.strip()]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda b: check_block(b, syms, info), blocks))
    # several blocks may try the same function; the first MATCH wins
    best = {}
    for (n, r), b in zip(res, blocks):
        if n not in best or (r == "MATCH" and best[n][0] != "MATCH"):
            best[n] = (r, b)
    for n, (r, _) in best.items():
        print(f"{n}: {r}")
    print(f"{sum(r == 'MATCH' for r, _ in best.values())}/{len(best)} match")
    return {n: b for n, (r, b) in best.items() if r == "MATCH"}


def apply(path):
    ok = check(path)
    if not ok:
        return
    fl = funcs()
    runs, cur, curf = [], [], None
    for n, a, s, f, _ in fl:
        if n in ok and (not cur or f == curf):
            cur.append((n, a)); curf = f
            continue
        if cur:
            runs.append(cur)
        cur, curf = ([(n, a)], f) if n in ok else ([], None)
    if cur:
        runs.append(cur)
    # split runs where two functions declare the same identifier differently
    ident = lambda l: re.search(r"(\w+)\s*[(\[;]", l)[1]
    parts = []
    for run in runs:
        decl, cur = {}, []
        for n, a in run:
            lines = ok[n].splitlines()
            k = next(j for j, l in enumerate(lines) if re.match(rf"^\S.*\b{n}\s*\([^;]*$", l))
            mine = {ident(l): l for l in lines[:k] if l.strip() and not l.startswith("typedef")}
            mine[n] = re.sub(r"\s*\{$", ";", lines[k])
            if any(decl.get(i, l) != l for i, l in mine.items()):
                parts.append(cur); decl, cur = {}, []
            decl.update(mine)
            cur.append((n, a))
        parts.append(cur)
    runs = parts
    starts = [a for _, a, *_ in fl]
    yaml = YAML.read_text()
    for run in reversed(runs):  # later runs first, so each run's end sees the next run's subsegment
        fname = f"game_{run[0][1]:08X}"
        header, bodies = [], []
        for n, _ in run:
            lines = ok[n].splitlines()
            k = next(j for j, l in enumerate(lines) if re.match(rf"^\S.*\b{n}\s*\([^;]*$", l))
            header += [l for l in lines[:k] if l.strip() and l not in header]
            bodies.append("\n".join(lines[k:]).strip())
        Path(f"src/{fname}.c").write_text("".join(h + "\n" for h in header) + ("\n" if header else "") +"\n\n".join(bodies) + "\n")
        segs = [(int(m[1], 16), m) for m in re.finditer(r"      - \[(0x[0-9A-F]+), (\w+).*\n", yaml)]
        st = off(run[0][1])
        # run ends at the next unmatched function or the next existing subsegment, whichever is first
        end = min([off(a) for a in starts if a > run[-1][1]] + [s for s, _ in segs if s > st])
        new = f"      - [0x{st:X}, c, {fname}]\n"
        if end not in [s for s, _ in segs]:
            new += f"      - [0x{end:X}, asm, game_{end - 0x80 + 0x100000:08X}]\n"
        prev = max((x for x in segs if x[0] <= st), key=lambda x: x[0])[1]
        if int(prev[1], 16) == st:
            yaml = yaml[:prev.start()] + new + yaml[prev.end():]
        else:
            yaml = yaml[:prev.end()] + new + yaml[prev.end():]
    YAML.write_text(yaml)
    print(f"applied {len(ok)} functions in {len(runs)} files")


cmd = sys.argv[1]
if cmd == "list":
    mx = int(sys.argv[2], 0) if len(sys.argv) > 2 else 1 << 30
    for n, a, s, f, _ in funcs():
        if s <= mx:
            print(n, hex(s))
elif cmd == "asm":
    want = set(sys.argv[2:])
    for n, a, s, f, t in funcs():
        if n in want:
            print(f"== {n} 0x{s:x}")
            print(re.sub(r"/\* \w+ \w+ \w+ \*/  ", "", t).rstrip())
elif cmd == "check":
    check(sys.argv[2])
elif cmd == "apply":
    apply(sys.argv[2])
