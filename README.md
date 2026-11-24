# Shadow Tower Abyss decompilation

Work-in-progress decompilation of Shadow Tower Abyss (PS2, `SLPS_252.17`, Japan).
Built with Metrowerks MW MIPS C 2.4.1.01.

This repository contains no game data. You need your own copy of the game.

## Setup

1. Copy `SLPS_252.17` from your disc into the repo root.
   SHA-1: `283c43ed15120b1578869c536a1fa1ca05ca9732`
2. Linux/WSL: `sudo apt install binutils-mips-linux-gnu ninja-build python3-venv`,
   plus [wibo](https://github.com/decompals/wibo) on `PATH`.
3. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Put the decomp.me compiler `mwcps2-3.0.3-020716` in `tools/mwcc/3.0.3-020716/`.
   (It reports "2.4.1.01" like the game; the older `2.4-001213` build does not match.)
5. `.venv/bin/python configure.py --split && ninja`

`ninja` builds `build/main.bin` (the loaded segment) and checks its SHA-1 against `config/main.sha1`.

## Decompiling a function

Matched functions live in `src/`, each file listed as a `c` subsegment in the yaml between
`asm` subsegments. Check a candidate with `tools/diff_func.sh src/file.c START END`
(hex addresses), which diffs the compiled code against the original.
