import pytest

from suchie2.writeplan import PlanError, WritePlan


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "src.bin"
    p.write_bytes(bytes(range(256)) * 16)
    return p


def test_apply_writes_and_audits(src, tmp_path):
    plan = WritePlan(src)
    plan.add("a", 0x10, bytes([0x10, 0x11]), b"\xaa\xbb")
    plan.add("b", 0x100, bytes([0x00]), b"\xcc")
    out = tmp_path / "out.bin"
    plan.apply(out)
    d = out.read_bytes()
    assert d[0x10:0x12] == b"\xaa\xbb" and d[0x100] == 0xCC
    assert len(d) == src.stat().st_size


def test_expected_mismatch_rejected_and_no_output(src, tmp_path):
    plan = WritePlan(src)
    plan.add("a", 0x10, b"\x00\x00", b"\x01\x02")
    out = tmp_path / "out.bin"
    with pytest.raises(PlanError, match="expected source"):
        plan.apply(out)
    assert not out.exists()


def test_overlap_rejected(src, tmp_path):
    plan = WritePlan(src)
    plan.add("a", 0x10, bytes([0x10, 0x11]), b"\x01\x02")
    plan.add("b", 0x11, bytes([0x11]), b"\x03")
    with pytest.raises(PlanError, match="overlap"):
        plan.apply(tmp_path / "out.bin")


def test_protected_range_rejected(src, tmp_path):
    plan = WritePlan(src, protected=[(0x0, 0x10)])
    plan.add("a", 0x0F, bytes([0x0F]), b"\x00")
    with pytest.raises(PlanError, match="protected"):
        plan.apply(tmp_path / "out.bin")


def test_out_of_range_and_length_mismatch_rejected(src):
    plan = WritePlan(src)
    with pytest.raises(PlanError):
        plan.add("a", 4095, b"\x00\x00", b"\x00\x00")
    with pytest.raises(PlanError):
        plan.add("a", 0, b"\x00", b"\x00\x00")


def test_audit_catches_unregistered_change(src, tmp_path):
    plan = WritePlan(src)
    plan.add("a", 0x10, bytes([0x10]), b"\x99")
    out = tmp_path / "out.bin"
    plan.apply(out)
    d = bytearray(out.read_bytes())
    d[0x200] ^= 0xFF
    out.write_bytes(bytes(d))
    with pytest.raises(PlanError, match="unexplained"):
        plan.audit(out)


def test_apply_runs_audit_and_leaves_nothing_on_failure(src, tmp_path, monkeypatch):
    plan = WritePlan(src)
    plan.add("a", 0x10, bytes([0x10]), b"\x99")
    real = plan.audit

    def tampered(path):
        d = bytearray(path.read_bytes())
        d[0x300] ^= 0xFF
        path.write_bytes(bytes(d))
        real(path)
    monkeypatch.setattr(plan, "audit", tampered)
    out = tmp_path / "out.bin"
    with pytest.raises(PlanError, match="unexplained"):
        plan.apply(out)
    assert not out.exists() and not list(tmp_path.glob("*.partial"))


def test_audit_checks_that_final_bytes_landed(src, tmp_path):
    plan = WritePlan(src)
    plan.add("a", 0x10, bytes([0x10, 0x11]), b"\x10\x99")   # first byte unchanged on purpose
    out = tmp_path / "out.bin"
    plan.apply(out)
    d = bytearray(out.read_bytes())
    d[0x11] = 0x11                                           # write "lost": output equals source
    out.write_bytes(bytes(d))
    with pytest.raises(PlanError, match="did not land"):
        plan.audit(out)
