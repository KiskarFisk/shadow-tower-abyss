#!/usr/bin/env python3
"""Generate build.ninja. Run inside WSL/Linux: python3 configure.py [--split] && ninja"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
YAML = "config/SLPS_252.17.yaml"
LD_SCRIPT = "linker/SLPS_252.17.ld"
BASENAME = "SLPS_252.17"

# o64: o32 register names (what spimdisasm emits) with 64-bit regs, so ld/sd stay native.
AS = "mips-linux-gnu-as -EL -march=r5900 -mabi=o64 -G0 -no-pad-sections -Iinclude"
# ponytail: optimisation flags are a guess until the first function matches.
CC = "wibo tools/mwcc/2.4-001213/mwccps2.exe -c -O2 -nostdinc -Iinclude -Isrc"

if "--split" in sys.argv or not (ROOT / LD_SCRIPT).exists():
    subprocess.run([sys.executable, "-m", "splat", "split", YAML], cwd=ROOT, check=True)

# The splat linker script is the source of truth for which objects exist.
objs = re.findall(r"(build/\S+?\.o)\(", (ROOT / LD_SCRIPT).read_text())
objs = list(dict.fromkeys(objs))

lines = [
    f"rule as\n  command = {AS} -o $out $in\n",
    f"rule cc\n  command = {CC} -o $out $in\n",
    f"rule ld\n  command = mips-linux-gnu-ld -EL -T {LD_SCRIPT} -T linker/undefined_syms_auto.txt"
    " -T linker/undefined_funcs_auto.txt -Map $map --no-check-sections --no-warn-rwx-segments -o $out\n",
    "rule bin\n  command = mips-linux-gnu-objcopy -O binary $in $out\n",
    "rule check\n  command = sha1sum -c config/main.sha1 && touch $out\n",
]
for o in objs:
    src = o[len("build/"):-2]  # build/asm/80.s.o -> asm/80.s
    lines.append(f"build {o}: {'cc' if src.endswith('.c') else 'as'} {src}")
lines += [
    f"build build/{BASENAME}.elf: ld | {' '.join(objs)} {LD_SCRIPT}\n  map = build/{BASENAME}.map",
    f"build build/main.bin: bin build/{BASENAME}.elf",
    "build build/main.ok: check build/main.bin | config/main.sha1",
    "default build/main.ok",
]
(ROOT / "build.ninja").write_text("\n".join(lines) + "\n")
print(f"build.ninja: {len(objs)} objects")
