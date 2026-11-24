#!/bin/bash
# Compile a C file and diff its code against the original exe.
# usage: tools/diff_func.sh src/file.c START_HEX END_HEX   (e.g. 12C0A0 12C110)
# Needs a prior `ninja` build (symbol addresses come from build/SLPS_252.17.elf).
# Env: MW=<compiler dir under tools/mwcc> (default 3.0.3-020716), FLAGS (default -O4).
set -e
cd "$(dirname "$0")/.."
export PATH=$HOME/.local/bin:$PATH MWCIncludes=.
T=$(mktemp -d); trap 'rm -rf $T' EXIT
s=$((0x$2)); e=$((0x$3))

wibo tools/mwcc/${MW:-3.0.3-020716}/mwccps2.exe -c ${FLAGS:--O4} -nostdinc -Iinclude -Isrc -o $T/a.o "$1" 2>&1 \
  | grep -vE "MWCIncludes|Usage Warning" || true

# Resolve every symbol to its address in the matching build, except those this file defines.
mips-linux-gnu-nm -g --defined-only $T/a.o | awk '{print $3}' > $T/defs
mips-linux-gnu-nm build/SLPS_252.17.elf | awk '$3 ~ /^(func_|D_|entry)/ {print $3" = 0x"$1";"}' \
  | grep -vwFf $T/defs > $T/syms.ld || true
printf '_gp = 0x209B70;\nSECTIONS { .text 0x%x : { *(.text) } /DISCARD/ : { *(*) } }\n' $s > $T/a.ld
mips-linux-gnu-ld -EL --no-warn-mismatch --no-warn-rwx-segments -e 0 -T $T/a.ld -T $T/syms.ld -o $T/a.elf $T/a.o
mips-linux-gnu-objcopy -O binary -j .text $T/a.elf $T/new.bin
# the real link pads to the next 16-byte-aligned object; mimic that
[ $(stat -c%s $T/new.bin) -lt $((e - s)) ] && truncate -s $(( ($(stat -c%s $T/new.bin) + 15) / 16 * 16 )) $T/new.bin
tail -c +$((s - 0x100000 + 0x80 + 1)) SLPS_252.17 | head -c $((e - s)) > $T/orig.bin

dis() { mips-linux-gnu-objdump -D -z -b binary -m mips:5900 -EL -M gpr-names=32 --adjust-vma=$s "$1" | grep -E "^\s+[0-9a-f]+:" | cut -f3-; }
if diff <(dis $T/orig.bin) <(dis $T/new.bin) && cmp -s $T/orig.bin $T/new.bin; then
  echo "MATCH ($((e - s)) bytes)"
else
  exit 1
fi
