"""Expected Write plan over one large source image (streamed, never loaded whole).

Every change is registered with its expected source bytes, checked for range, overlap and
protected regions, verified against the immutable source, applied to a fresh copy, and the
final file is audited so that every differing byte belongs to a registered write.
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1 << 22


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Write:
    writer: str
    offset: int
    expected: bytes
    final: bytes


class WritePlan:
    def __init__(self, source: Path, protected: list[tuple[int, int]] | None = None):
        self.source = Path(source)
        self.size = self.source.stat().st_size
        self.protected = protected or []
        self.writes: list[Write] = []

    def add(self, writer: str, offset: int, expected: bytes, final: bytes) -> None:
        if len(expected) != len(final):
            raise PlanError(f"{writer}: expected/final length mismatch")
        if offset < 0 or offset + len(final) > self.size:
            raise PlanError(f"{writer}: range {offset:#x}+{len(final)} outside source")
        self.writes.append(Write(writer, offset, bytes(expected), bytes(final)))

    def verify(self) -> None:
        spans = sorted(self.writes, key=lambda w: w.offset)
        for a, b in zip(spans, spans[1:]):
            if b.offset < a.offset + len(a.final):
                raise PlanError(f"{b.writer}: overlap with {a.writer} at {b.offset:#x}")
        with open(self.source, "rb") as f:
            for w in self.writes:
                for lo, hi in self.protected:
                    if w.offset < hi and lo < w.offset + len(w.final):
                        raise PlanError(f"{w.writer}: protected range {lo:#x}-{hi:#x}")
                f.seek(w.offset)
                if f.read(len(w.expected)) != w.expected:
                    raise PlanError(f"{w.writer}: expected source mismatch at {w.offset:#x}")

    def apply(self, out: Path) -> None:
        out = Path(out)
        self.verify()
        tmp = out.with_name(out.name + ".partial")
        try:
            shutil.copyfile(self.source, tmp)
            with open(tmp, "r+b") as f:
                for w in self.writes:
                    f.seek(w.offset)
                    f.write(w.final)
                f.flush()
                os.fsync(f.fileno())
            self.audit(tmp)
            os.replace(tmp, out)
        finally:
            if tmp.exists():
                tmp.unlink()

    def audit(self, out: Path) -> None:
        out = Path(out)
        if out.stat().st_size != self.size:
            raise PlanError("unexplained size change")
        spans = sorted((w.offset, w.offset + len(w.final)) for w in self.writes)
        with open(out, "rb") as b:
            for w in self.writes:
                b.seek(w.offset)
                if b.read(len(w.final)) != w.final:
                    raise PlanError(f"{w.writer}: final bytes did not land at {w.offset:#x}")
        with open(self.source, "rb") as a, open(out, "rb") as b:
            pos = 0
            while True:
                x, y = a.read(CHUNK), b.read(CHUNK)
                if not x:
                    break
                if x != y:
                    for i in range(len(x)):
                        if x[i] != y[i] and not any(lo <= pos + i < hi for lo, hi in spans):
                            raise PlanError(f"unexplained change at {pos + i:#x}")
                pos += len(x)
