"""Set up a decomp-permuter input directory for one function. Run in WSL from the repo root:
  python tools/perm_setup.py FUNC CANDIDATES.c OUTDIR [FLAGS]
CANDIDATES.c is a batch.py-style file; the first `// ---` block defining FUNC is used. FLAGS defaults to -O4,p.
Then: python ~/decomp-permuter/permuter.py OUTDIR -j8 --stop-on-zero
(decomp-permuter: github.com/simonlindholm/decomp-permuter, cloned at ~/decomp-permuter)
"""
import os, re, subprocess, sys
from pathlib import Path

func, cands, out = sys.argv[1:4]
flags = sys.argv[4] if len(sys.argv) > 4 else "-O4,p"
repo = Path.cwd()
out = Path(out).resolve()
out.mkdir(parents=True, exist_ok=True)

block = next(b for b in Path(cands).read_text().split("// ---")
             if re.search(rf"^\S.*\b{func}\s*\([^;]*$", b, re.M))
# the permuter parses C with pycparser, so it needs preprocessed input (MWCC's own -EP does it)
WIBO = os.path.expanduser("~/.local/bin/wibo")
(out / "raw.c").write_text(block)
pre = subprocess.run([WIBO, "tools/mwcc/3.0b52-030722/mwccps2.exe", "-EP", "-nostdinc", "-Iinclude", str(out / "raw.c")],
                     capture_output=True, text=True, env={**os.environ, "MWCIncludes": "."}, check=True).stdout
(out / "base.c").write_text("".join(l + "\n" for l in pre.splitlines() if l.strip() and not l.startswith("/* #line")))

asm = next(m[0] for f in Path("asm").glob("game*.s")
           for m in [re.search(rf"nonmatching {func}, .*?endlabel {func}\n", f.read_text(), re.S)] if m)
(out / "target.s").write_text('.include "macro.inc"\n.set noat\n.set noreorder\n.section .text, "ax"\n\n' + asm)
subprocess.run(["mips-linux-gnu-as", "-EL", "-march=r5900", "-mabi=o64", "-G0", "-no-pad-sections", "-Iinclude",
                "-o", str(out / "target.o"), str(out / "target.s")], check=True)

(out / "compile.sh").write_text(f"""#!/bin/bash
# usage: compile.sh IN.c -o OUT.o
in=$(realpath "$1"); out=$(realpath -m "$3")
cd {repo}
MWCIncludes=. {os.path.expanduser('~/.local/bin/wibo')} tools/mwcc/3.0b52-030722/mwccps2.exe -c {flags} -nostdinc -o "$out" "$in"
""")
(out / "compile.sh").chmod(0o755)
(out / "settings.toml").write_text(f'func_name = "{func}"\ncompiler_type = "mwcc"\n')
print(out)
