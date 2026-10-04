"""Reject SH-3/SH-4-only instructions in an objdump listing (the SH-2 has none of these).

Literal-pool words are disassembled by objdump too; every address loaded by a PC-relative
mov.w/mov.l (or taken by mova) is data, so it is skipped."""
import re
import sys

BAD = {"shad", "shld", "pref", "movca.l", "ocbi", "ocbp", "ocbwb", "ldtlb", "clrs", "sets", "fschg", "frchg"}
LINE = re.compile(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} ){2})\s*(\S+)\s*(.*)")
PCREL = re.compile(r"^(mov\.[wl]|mova)\s+([0-9a-f]+) <")

rows = []
for line in open(sys.argv[1]):
    m = LINE.match(line)
    if m:
        rows.append((int(m.group(1), 16), m.group(3), m.group(4).split("!")[0].strip(), line.strip()))
data = set()
for addr, op, args, _ in rows:
    m = PCREL.match(f"{op} {args}")
    if m:
        t = int(m.group(2), 16)
        data.update({t, t + 2} if op == "mov.l" else {t})
bad = []
for addr, op, args, line in rows:
    if addr in data:
        continue
    if op in BAD or op.startswith("f") or re.search(r"\b(r\d_bank|ssr|spc|sgr|dbr|fpul|fpscr)\b", args):
        bad.append(line)
print("\n".join(bad) if bad else "SH-2 check: ok")
sys.exit(1 if bad else 0)
