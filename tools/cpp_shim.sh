#!/bin/bash
# Minimal `cpp -P` stand-in for decomp-permuter, using MWCC's preprocessor (-EP).
# Put a symlink named `cpp` to this script first on PATH when running the permuter.
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
args=() file=
for a in "$@"; do
    case $a in
        -P|-nostdinc) ;;
        -D*|-I*) args+=("$a") ;;
        -*) ;;
        *) file=$(readlink -f "$a") ;;
    esac
done
cd "$repo"
MWCIncludes=. ~/.local/bin/wibo tools/mwcc/3.0b52-030722/mwccps2.exe -EP -nostdinc -Iinclude "${args[@]}" "$file" |
    grep -v '^/\* #line' | grep -v '^\s*$'
