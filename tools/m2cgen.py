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
# Shared struct headers: their types go into m2c's context (minus preprocessor lines) and every candidate includes them.
HEADERS = ["common.h", "actor.h"]
CTX_HEAD += "".join(re.sub(r"^#.*$", "", Path("include", h).read_text(), flags=re.M) for h in HEADERS)
STRUCTS = set(re.findall(r"\}\s*([A-Z]\w*);", CTX_HEAD))
OK_WORDS = set(TYPES) | STRUCTS | set("void char short int long float double unsigned signed const".split())
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
    for i in range(3):  # signatures settle after a couple of passes
        sigs = {n: p for c in res.values() for n, p in protos(c).items()}
        res = dict(ex.map(lambda t: run(t, sigs, tmp), todo))
        if i == 0:
            # Pass 0 guesses callee arg counts from registers. Later passes trust inferred signatures, which
            # drop parameters a callee never reads, so a passed-through a0 vanishes from the call. Keep both.
            first = res


def floats(src):
    """The game does single-precision math; m2c's doubles make MWCC call fptodp/dptofp."""
    src = re.sub(r"\bf64\b", "f32", src)
    return re.sub(r"(?<![\w.])(\d+\.\d*(?:e[-+]?\d+)?)(?![\w.])", r"\1f", src)


SEXT = re.compile(r"\((s|u)64\) \(([^()]+?) << 0x(30|38|20)\) >> 0x\3")


def narrow_variants(src):
    """MWCC sign-extends short/char values with 64-bit shifts, which m2c prints literally on s64 values.
    Variants: the shifts as casts with s64 -> s32, and additionally such parameters typed s16/s8."""
    if not SEXT.search(src):
        return [src]
    width = {"30": "16", "38": "8", "20": "32"}
    casts = SEXT.sub(lambda m: f"({m[1]}{width[m[3]]}) {m[2]}", src)
    a = re.sub(r"\b([su])64\b", r"\g<1>32", casts)
    narrowed = {m[2].strip(): f"{m[1]}{width[m[3]]}" for m in SEXT.finditer(src)}
    head, _, body = a.partition("{")
    for name, ty in narrowed.items():
        head = re.sub(rf"\b[su]32 {name}\b", f"{ty} {name}", head)
    return [src, a, head + "{" + body]


def far_globals(src):
    """MWCC reaches a plain `extern T D_X;` through $gp; globals out of gp range must be arrays to get lui/%lo."""
    for m in re.finditer(r"^extern ([^;\[\]()]*?)\b(D_([0-9A-F]{8}));$", src, re.M):
        if abs(int(m[3], 16) - 0x209B70) < 0x8000:
            continue
        name = m[2]
        body = src.replace(m[0], f"extern {m[1]}{name}[];")
        body = re.sub(rf"&{name}\b", f"@@{name}", body)
        body = re.sub(rf"(?<![@\w]){name}\b(?!\[\];)", f"{name}[0]", body)
        src = body.replace(f"@@{name}", name)
    return src


SIZES = {"s8": 1, "u8": 1, "char": 1, "M2C_UNK8": 1, "s16": 2, "u16": 2, "M2C_UNK16": 2,
         "s64": 8, "u64": 8, "M2C_UNK64": 8, "s128": 16, "u128": 16,
         "s32": 4, "u32": 4, "f32": 4, "int": 4, "M2C_UNK": 4, "M2C_UNK32": 4}


def pointee_sizes(src):
    """{name: (pointee type, size)} for `extern T D[];` and `T *name` declarations/parameters."""
    res = {}
    for m in re.finditer(r"\bextern ([\w ]+?)\s*(\*?)\s*(\w+)\[\];", src):
        t = m[1].replace("signed ", "s").replace("unsigned ", "u")
        res[m[3]] = (m[1] + m[2], 4 if m[2] else SIZES.get(t))
    for m in re.finditer(r"(?<![\w*])([A-Za-z_]\w*) (\*+)\s*(\w+)\s*[;,)]", src):
        res[m[3]] = (m[1] + m[2][1:], 4 if len(m[2]) > 1 else SIZES.get(m[1]))
    return {k: v for k, v in res.items() if v[1]}


def close(s, i):
    """Index of the parenthesis closing the one opened just before s[i]."""
    depth = 1
    while depth:
        depth += {"(": 1, ")": -1}.get(s[i], 0)
        i += 1
    return i - 1


def byte_offsets(src):
    """m2c does byte arithmetic on pointers it has no element type for (`*(D + (i * 4))`, `p += 4`), which C
    scales by the element size. Rewrite as indexing when the offset is a multiple of it, else via s8 *."""
    sz = pointee_sizes(src)
    out, i = [], 0
    for m in re.finditer(r"\*\((\w+) \+ ", src):
        if m.start() < i or m[1] not in sz:
            continue
        end = close(src, m.end())
        e = src[m.end():end]
        t, n = sz[m[1]]
        k = re.fullmatch(r"\((.+) \* (0x[0-9A-Fa-f]+|\d+)\)", e)
        if k and close(e, 1) == len(e) - 1 and int(k[2], 0) % n == 0:
            q = int(k[2], 0) // n
            new = f"{m[1]}[{k[1]}]" if q == 1 else f"{m[1]}[{k[1]} * {q}]"
        elif n > 1:
            new = f"*({t} *)((s8 *){m[1]} + {e})"
        else:
            continue
        out += [src[i:m.start()], new]
        i = end + 1
    src = "".join(out) + src[i:]

    def step(m):
        k, n = int(m[4], 0), sz.get(m[2], (0, 0))[1]
        return f"{m[1]}{m[2]} {m[3]}= {k // n};" if n > 1 and k % n == 0 else m[0]
    return re.sub(r"^(\s*)(\w+) ([-+])= (0x[0-9A-Fa-f]+|\d+);", step, src, flags=re.M)


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


ok = lambda c: c.strip() and not re.search(r"M2C_(?!FIELD|BITWISE|UNK)|\?|second half", c)
good = {(n, c) for r in (res, first) for n, c in r.items() if ok(c)}
with ThreadPoolExecutor(8) as ex:
    pre = []
    for n, c in sorted(good):
        c = far_globals("".join(f'#include "{h}"\n' for h in ["m2c.h"] + HEADERS) + re.sub(r"^#include .*\n", "", c, flags=re.M))
        pre += dict.fromkeys(v for s in dict.fromkeys([c, byte_offsets(c)]) for v in narrow_variants(floats(s)))
    blocks = list(ex.map(fix_casts, pre))
Path(out).write_text("\n// ---\n".join(blocks))
print(f"{len(todo)} functions, {len({n for n, _ in good})} with candidates, {len(blocks)} candidates")
