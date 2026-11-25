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
# 3.0.x builds report "2.4.1.01" like the game but, unlike 2.4-001213, save ra with sd
# and move with daddu. 3.0b38-b52 with ",p" (optimize for speed) pad branch targets to
# 8 bytes with a nop, as most game code does; 3.0.3 never does.
CC = "MWCIncludes=. wibo tools/mwcc/3.0b52-030722/mwccps2.exe -c $flags -nostdinc -Iinclude -Isrc"
# files the original built without ",p" (unaligned branch targets, no pad nops)
NO_SPEED = {"src/game_0017ECD0.c", "src/game_001B19A0.c", "src/game_001C9FB0.c",
            "src/game_0013ECA0.c", "src/game_0013E220.c"}

if "--split" in sys.argv or not (ROOT / LD_SCRIPT).exists():
    # splat never deletes asm for functions that became C; stale files skew progress and m2cgen
    for f in (ROOT / "asm").glob("*.s"):
        f.unlink()
    subprocess.run([sys.executable, "-m", "splat", "split", YAML], cwd=ROOT, check=True)

# The splat linker script is the source of truth for which objects exist.
objs = re.findall(r"(build/\S+?\.o)\(", (ROOT / LD_SCRIPT).read_text())
objs = list(dict.fromkeys(objs))

lines = [
    f"rule as\n  command = {AS} -o $out $in\n",
    f"rule cc\n  command = {CC} -o $out $in\n",
    f"rule ld\n  command = mips-linux-gnu-ld -EL -T {LD_SCRIPT} -T linker/undefined_syms_auto.txt"
    " -T linker/undefined_funcs_auto.txt -Map $map --no-check-sections --no-warn-rwx-segments --no-warn-mismatch -o $out\n",
    "rule bin\n  command = mips-linux-gnu-objcopy -O binary $in $out\n",
    "rule check\n  command = sha1sum -c config/main.sha1 && touch $out\n",
]
for o in objs:
    src = o[len("build/"):-2]  # build/asm/80.s.o -> asm/80.s
    lines.append(f"build {o}: {'cc' if src.endswith('.c') else 'as'} {src}")
    if src.endswith(".c"):
        lines.append(f"  flags = {'-O4' if src in NO_SPEED else '-O4,p'}")
lines += [
    f"build build/{BASENAME}.elf: ld | {' '.join(objs)} {LD_SCRIPT}\n  map = build/{BASENAME}.map",
    f"build build/main.bin: bin build/{BASENAME}.elf",
    "build build/main.ok: check build/main.bin | config/main.sha1",
    "default build/main.ok",
]
(ROOT / "build.ninja").write_text("\n".join(lines) + "\n")
print(f"build.ninja: {len(objs)} objects")
