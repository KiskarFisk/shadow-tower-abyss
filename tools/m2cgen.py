"""m2c candidates for unmatched game functions with branches. Run in WSL from the repo root:
  python tools/m2cgen.py MAXSIZE OUT.c   then   python tools/batch.py check OUT.c
Needs the m2c decompiler cloned at ~/m2c (github.com/matt-kempster/m2c; the PyPI "m2c" is unrelated).
"""
import re, subprocess, sys, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
mx, out = int(sys.argv[1], 0), sys.argv[2]
TD = ("typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16;\n"
      "typedef int s32; typedef unsigned int u32; typedef long long s64; typedef unsigned long long u64;\n"
      "typedef float f32; typedef double f64;\n")
todo = []
for f in sorted(Path("asm").glob("game*.s")):
    for m in re.finditer(r"nonmatching (func_\w+), (0x[0-9A-F]+)\n(.*?)endlabel \1", f.read_text(), re.S):
        if int(m[2], 16) <= mx and re.search(r"^\s*\.L", m[3], re.M):
            todo.append((m[1], str(f)))
def run(t):
    r = subprocess.run([sys.executable, os.path.expanduser("~/m2c/m2c.py"), "--target", "mips-mwcc-c", "--valid-syntax", "--globals", "used", "-f", t[0], t[1]],
                       capture_output=True, text=True)
    return t[0], r.stdout if r.returncode == 0 else ""
with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(run, todo))
blocks, why = [], {}
blocks, skipped = [], 0
for n, c in res:
    if not c.strip() or re.search(r"M2C_(?!FIELD|BITWISE|UNK)|\?|second half", c):
        skipped += 1
        continue
    c = re.sub(r"^#include .*\n", "", c, flags=re.M).replace("void *", "s8 *")
    blocks.append('#include "m2c.h"\n' + c)
Path(out).write_text("\n// ---\n".join(blocks))
print(f"{len(todo)} functions, {len(blocks)} candidates, {skipped} skipped (unsupported m2c output)")
