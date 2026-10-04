#!/bin/sh
# Build assets/subtitle/SUB.BIN (code only; the build appends the voice index) from crt.S + sub.c.
# Needs a big-endian SH cross gcc; Debian/Ubuntu: apt install gcc-sh4-linux-gnu (no -m2 there,
# so we build for SH-4 without FPU and reject every instruction the SH-2 lacks).
# Windows: run inside WSL. EXTRA=-DLOG builds the diagnostic logger (no subtitles drawn).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/../../assets/subtitle/SUB.BIN}"
TMP="${TMPDIR:-/tmp}/khsub"
mkdir -p "$TMP"
CC=${CC:-sh4-linux-gnu-gcc}
PFX=${CC%gcc}
FLAGS="-m4-nofpu -mb -Os -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-zero-initialized-in-bss \
       -fno-tree-loop-distribute-patterns -Wall -Wextra -Werror $EXTRA"
$CC $FLAGS -c "$HERE/crt.S" -o "$TMP/crt.o"
$CC $FLAGS -c "$HERE/sub.c" -o "$TMP/sub.o"
${PFX}ld -EB --no-warn-rwx-segments -z noexecstack -T "$HERE/sub.ld" "$TMP/crt.o" "$TMP/sub.o" -o "$TMP/sub.elf"
${PFX}objdump -d "$TMP/sub.elf" > "$TMP/sub.lst"
python3 "$HERE/check_sh2.py" "$TMP/sub.lst"
${PFX}objcopy -O binary "$TMP/sub.elf" "$OUT"
${PFX}nm -n "$TMP/sub.elf" | grep -E " (T|t) "
ls -l "$OUT"
