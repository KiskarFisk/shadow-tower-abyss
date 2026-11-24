"""m2c candidates for unmatched game functions with branches. Run in WSL from the repo root:
  python tools/m2cgen.py MAXSIZE OUT.c   then   python tools/batch.py check OUT.c
Needs the m2c decompiler cloned at ~/m2c (github.com/matt-kempster/m2c; the PyPI "m2c" is unrelated).

m2c runs a few passes. Each pass gets a context of function prototypes: the exact ones from src/ plus
every other function's signature from the previous pass. That gets callee argument counts right, so
arguments a function just passes through show up. Then each candidate is compiled, and assignments and
returns that MWCC rejects as implicit int/pointer conversions get an explicit cast.
"""
import os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

mx, out = int(sys.argv[1], 0), sys.argv[2]
M2C = [sys.executable, os.path.expanduser("~/m2c/m2c.py"), "--target", "mips-mwcc-c", "--valid-syntax", "--globals", "used"]
CC = [os.path.expanduser("~/.local/bin/wibo"), "tools/mwcc/3.0b52-030722/mwccps2.exe", "-c", "-O4,p",
      "-nostdinc", "-Iinclude"]
TYPES = "s8 u8 s16 u16 s32 u32 s64 u64 f32 f64 M2C_UNK M2C_UNK8 M2C_UNK16 M2C_UNK32 M2C_UNK64".split()
CTX_HEAD = "".join(f"typedef {t} {n};\n" for t, n in [
    ("signed char", "s8"), ("unsigned char", "u8"), ("short", "s16"), ("unsigned short", "u16"), ("int", "s32"),
    ("unsigned int", "u32"), ("long long", "s64"), ("unsigned long long", "u64"), ("float", "f32"),
    ("double", "f64"), ("int", "M2C_UNK"), ("signed char", "M2C_UNK8"), ("short", "M2C_UNK16"),
    ("int", "M2C_UNK32"), ("long long", "M2C_UNK64")])
OK_WORDS = set(TYPES) | set("void char short int long float double unsigned signed const".split())
DEF = re.compile(r"^([A-Za-z_][^;{}()\n]*\b(func_[0-9A-F]{8})\s*\([^;{}\n]*\))[ \t]*\{?[ \t]*$", re.M)

todo = []
for f in sorted(Path("asm").glob("game*.s")):
    for m in re.finditer(r"nonmatching (func_\w+), (0x[0-9A-F]+)\n(.*?)endlabel \1", f.read_text(), re.S):
        if int(m[2], 16) <= mx and re.search(r"^\s*\.L", m[3], re.M):
            todo.append((m[1], str(f)))


def protos(text):
    """{name: prototype} for function definitions whose types the context parser will know."""
    res = {}
    for m in DEF.finditer(text):
        words = set(re.findall(r"[A-Za-z_]\w*", re.sub(r"\barg\d+\b|\bfunc_\w+", "", m[1])))
        if words <= OK_WORDS | {w for w in words if re.fullmatch(r"a\d|arg\w*|p|v|n|i", w)}:
            res.setdefault(m[2], m[1] + ";")
    return res


exact = protos("\n".join(p.read_text() for p in Path("src").glob("*.c")))


def run(t, sigs, tmp):
    name, asm = t
    ctx = Path(tmp, name + ".ctx.c")
    ctx.write_text(CTX_HEAD + "\n".join(p for n, p in {**sigs, **exact}.items() if n != name) + "\n")
    r = subprocess.run(M2C + ["--context", str(ctx), "-f", name, asm], capture_output=True, text=True)
    return name, r.stdout.replace("void *", "s8 *") if r.returncode == 0 else ""


res = {}
with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(8) as ex:
    for _ in range(3):  # signatures settle after a couple of passes
        sigs = {n: p for c in res.values() for n, p in protos(c).items()}
        res = dict(ex.map(lambda t: run(t, sigs, tmp), todo))


def fix_casts(src):
    """Compile; cast the right-hand side of rejected implicit int/pointer conversions. Repeat."""
    for _ in range(6):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "a.c").write_text(src)
            r = subprocess.run(CC + ["-o", f"{t}/a.o", f"{t}/a.c"], capture_output=True, text=True,
                               env={**os.environ, "MWCIncludes": "."})
        errs = re.findall(r"#\s+(\d+): .*\n#\s+Error:.*\n#\s+illegal implicit conversion from '[^']*' to\s*(?:\n#\s*)?'([^']*)'",
                          r.stdout + r.stderr)
        if not errs:
            return src
        lines, changed = src.split("\n"), False
        for ln, ty in errs:
            i = int(ln) - 1
            m = re.match(r"^(\s*)(return |[^=]+?[^=!<>]= )(.+);$", lines[i])
            if m and not m[3].startswith(f"({ty})"):
                lines[i] = f"{m[1]}{m[2]}({ty})({m[3]});"
                changed = True
        if not changed:
            return src
        src = "\n".join(lines)
    return src


blocks, skipped = [], 0
good = [(n, c) for n, c in res.items() if c.strip() and not re.search(r"M2C_(?!FIELD|BITWISE|UNK)|\?|second half", c)]
with ThreadPoolExecutor(8) as ex:
    blocks = list(ex.map(lambda nc: fix_casts('#include "m2c.h"\n' + re.sub(r"^#include .*\n", "", nc[1], flags=re.M)), good))
Path(out).write_text("\n// ---\n".join(blocks))
print(f"{len(todo)} functions, {len(blocks)} candidates, {len(todo) - len(blocks)} skipped (unsupported m2c output)")
