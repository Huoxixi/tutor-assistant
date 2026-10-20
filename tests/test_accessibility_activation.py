import struct

import pytest

from app.accessibility import EXPECTED_RVA, activate_checked, locate_gate


def fake_pe(target=EXPECTED_RVA, writable=True):
    data = bytearray(2048)
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 2)
    struct.pack_into("<H", data, 0x94, 0xF0)
    table = 0x84 + 20 + 0xF0
    for index, values in enumerate([(0x100, 0x1000, 0x100, 0x400, 0x20000000), (0x100, EXPECTED_RVA - 8, 0x100, 0x600, 0x80000000 if writable else 0)]):
        size, rva, raw_size, raw, flags = values
        struct.pack_into("<IIII", data, table + index * 40 + 8, size, rva, raw_size, raw)
        struct.pack_into("<I", data, table + index * 40 + 36, flags)
    code = b"\x48\x85\xc9\x0f\x84\0\0\0\0\x80\x3d" + struct.pack("<i", target - 0x1010) + b"\0\x0f\x84"
    data[0x400:0x400 + len(code)] = code
    return bytes(data)


def test_gate_requires_exact_target_and_writable_section():
    assert locate_gate(fake_pe())[0] == 0x1000
    with pytest.raises(ValueError):
        locate_gate(fake_pe(target=EXPECTED_RVA + 1))
    with pytest.raises(ValueError):
        locate_gate(fake_pe(writable=False))


@pytest.mark.parametrize("accessible,expected", [(True, "activated"), (False, "activation_failed")])
def test_activation_keeps_only_verified_success(monkeypatch, accessible, expected):
    monkeypatch.setattr("app.accessibility.time.sleep", lambda _: None)
    value = [b"\0"]
    writes, reports = [], []
    def write(data):
        writes.append(data)
        value[0] = data
    def probe():
        return {"accessible": bool(writes) and accessible}
    result = activate_checked(lambda: value[0], write, probe, lambda report: reports.append(dict(report)))
    assert result["state"] == expected
    assert reports[0]["original_byte"] == 0
    assert value[0] == (b"\1" if accessible else b"\0")
    assert result["restored"] is (not accessible)


def test_probe_error_restores_original(monkeypatch):
    monkeypatch.setattr("app.accessibility.time.sleep", lambda _: None)
    value = [b"\0"]
    def probe():
        if value[0] == b"\1":
            raise RuntimeError("UIA probe failed")
        return {"accessible": False}
    result = activate_checked(lambda: value[0], lambda data: value.__setitem__(0, data), probe, lambda _: None)
    assert result["state"] == "activation_error" and result["restored"]
    assert value[0] == b"\0"


def test_non_boolean_refused_before_any_write():
    with pytest.raises(ValueError):
        activate_checked(lambda: b"\x7f", lambda _: pytest.fail("must not write"), lambda: {}, lambda _: None)


def test_preexisting_one_never_overwritten():
    result = activate_checked(lambda: b"\1", lambda _: pytest.fail("must not write"), lambda: {"accessible": False}, lambda _: None)
    assert result["state"] == "flag_already_enabled_but_tree_unavailable"


def test_failed_restore_reported_as_failure(monkeypatch):
    monkeypatch.setattr("app.accessibility.time.sleep", lambda _: None)
    value = [b"\0"]
    def write(data):
        if data == b"\0":
            raise OSError("process unavailable")
        value[0] = data
    result = activate_checked(lambda: value[0], write, lambda: {"accessible": False}, lambda _: None)
    assert result["state"] == "restore_failed" and not result["restored"]
