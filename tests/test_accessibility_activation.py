import struct

import pytest

from app.accessibility import EXPECTED_RVA, activate_checked, locate_gate
from app.accessibility import scan_gate, select_gate, CompatibilityError


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


def adaptive_pe(target=0x4050, anchor=True, duplicate=False):
    """Synthetic x64 image with separate code, const and writable sections."""
    data = bytearray(0x2000)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, 0x8664, 3)
    struct.pack_into('<H', data, 0x94, 0xF0)
    struct.pack_into('<H', data, 0x98, 0x20B)
    for i, (rva, raw, flags) in enumerate([(0x1000, 0x400, 0x60000000),
                                           (0x3000, 0xC00, 0x40000000),
                                           (0x4000, 0x1400, 0xC0000000)]):
        pos = 0x188+i*40
        struct.pack_into('<IIII', data, pos+8, 0x800, rva, 0x800, raw)
        struct.pack_into('<I', data, pos+36, flags)
    def gate(offset, address):
        rva = 0x1000+offset-0x400
        code = b'\x48\x85\xc9\x0f\x84\0\0\0\0\x80\x3d'+struct.pack('<i', address-rva-16)+b'\0\x0f\x84'
        data[offset:offset+len(code)] = code
    gate(0x440, target)
    if duplicate:
        gate(0x480, target+1)
    if anchor:
        category = b'qt.accessibility.core\0'
        data[0xC00:0xC00+len(category)] = category
        data[0x500:0x507] = b'\x48\x8d\x0d'+struct.pack('<i', 0x3000-0x1107)
    return bytes(data)


@pytest.mark.parametrize('version,target', [('4.1.11.22',0x4010), ('4.1.15.13',0x4020),
                                            ('4.1.15.56',0x4050), ('4.2.0.1',0x4070)])
def test_feature_selection_uses_binary_address_instead_of_version_address(version,target):
    plan = select_gate(version, adaptive_pe(target))
    assert plan['gate_rva'] == target
    assert plan['method'] == 'binary_features'
    assert len(plan['checks']) == 3


@pytest.mark.parametrize('image', [adaptive_pe(anchor=False), adaptive_pe(duplicate=True),
                                   adaptive_pe(target=0x3010), b'not a DLL'],
                         ids=['no-anchor','ambiguous','readonly-target','malformed'])
def test_unanchored_ambiguous_readonly_or_malformed_binary_refused(image):
    with pytest.raises(ValueError):
        scan_gate(image)


def test_rejects_writable_code_and_overlapping_or_truncated_sections():
    for mutation in ('code', 'overlap', 'truncate'):
        data = bytearray(adaptive_pe())
        if mutation == 'code':
            struct.pack_into('<I', data, 0x188+36, 0xE0000000)
        elif mutation == 'overlap':
            struct.pack_into('<I', data, 0x188+40+20, 0x400)
        else:
            del data[0x1BFF:]
        with pytest.raises(ValueError):
            scan_gate(bytes(data))


def test_rejects_old_major_versions_even_with_matching_features():
    with pytest.raises(CompatibilityError):
        select_gate('3.9.12.17', adaptive_pe())


def test_feature_scan_cancellation_has_no_mutation():
    with pytest.raises(InterruptedError):
        scan_gate(adaptive_pe(), stopped=lambda: True)


def test_cancellation_during_successful_probe_rolls_back(monkeypatch):
    monkeypatch.setattr('app.accessibility.time.sleep', lambda _: None)
    value, cancelled = [b'\0'], [False]
    def probe():
        if value[0] == b'\1':
            cancelled[0] = True
        return {'accessible': value[0] == b'\1'}
    result = activate_checked(lambda: value[0], lambda x: value.__setitem__(0,x), probe,
                               lambda _: None, stopped=lambda: cancelled[0])
    assert result['restored'] and value[0] == b'\0'
