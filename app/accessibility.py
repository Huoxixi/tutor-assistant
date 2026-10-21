"""Per-read UIA initialization with binary feature checks; no chat contents.

Reference: fanyuantaier/wechatauto-replica, accessibility.py (Apache-2.0).
Known binary identity takes the fast path. Other 4.1+ x64 builds require a
unique gate linked to Qt's accessibility logging category before any write.
"""
import ctypes
import hashlib
import json
import re
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_VERSION = "4.1.15.13"
EXPECTED_SHA256 = "10f8e995453e2da46d4f2b5080cd6da1f13cc5147746adc119ceae38cb039de5"
EXPECTED_RVA = 0x0B135C38
PATTERN = re.compile(rb"\x48\x85\xc9\x0f\x84....\x80\x3d(.{4})\x00\x0f\x84", re.DOTALL)
CORE = b"qt.accessibility.core\0"
LEA = re.compile(rb"[\x40-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....", re.DOTALL)


class CompatibilityError(ValueError):
    def __init__(self, reason, version):
        self.reason, self.version = reason, version
        super().__init__(reason)


def pe_sections(data):
    """Reject truncated/overlapping or non-x64 images before decoding RVAs."""
    try:
        if len(data) < 64 or data[:2] != b'MZ':
            raise ValueError('Invalid DOS header')
        pe = struct.unpack_from('<I', data, 60)[0]
        if data[pe:pe+4] != b'PE\0\0':
            raise ValueError('Invalid PE signature')
        coff = pe+4
        machine, count = struct.unpack_from('<HH', data, coff)
        opt = struct.unpack_from('<H', data, coff+16)[0]
        if machine != 0x8664 or not 1 <= count <= 96 or opt < 112:
            raise ValueError('Unsupported PE architecture')
        if struct.unpack_from('<H', data, coff+20)[0] != 0x20B:
            raise ValueError('Expected PE32+')
        sections = []
        for index in range(count):
            pos = coff+20+opt+40*index
            size, rva, raw_size, raw = struct.unpack_from('<IIII', data, pos+8)
            flags = struct.unpack_from('<I', data, pos+36)[0]
            if raw_size and (raw < coff+20+opt+40*count or raw+raw_size > len(data)):
                raise ValueError('Invalid section bounds')
            section = (rva, max(size, raw_size), raw, raw_size, flags)
            for other in sections:
                if max(rva, other[0]) < min(rva+section[1], other[0]+other[1]):
                    raise ValueError('Overlapping virtual sections')
                if raw_size and other[3] and max(raw, other[2]) < min(raw+raw_size, other[2]+other[3]):
                    raise ValueError('Overlapping file sections')
            sections.append(section)
        return sections
    except struct.error:
        raise ValueError('Truncated PE image') from None


def scan_gate(data, stopped=lambda: False):
    """Locate a unique semantically anchored gate, never try guessed addresses.

    All checks refer to the immutable DLL file. Returned instruction/category
    bytes must also match the loaded image before the single-byte mutation.
    """
    sections = pe_sections(data)

    def section_at(offset, size=1):
        return next((s for s in sections if s[2] <= offset and offset+size <= s[2]+s[3]), None)

    categories = []
    for match in re.finditer(re.escape(CORE), data):
        sec = section_at(match.start(), len(CORE))
        if sec and sec[4] & 0x40000000 and not sec[4] & (0x20000000 | 0x80000000):
            categories.append((sec[0]+match.start()-sec[2], CORE))
    if not categories:
        raise ValueError('Accessibility category absent')
    candidates = {}
    for match in PATTERN.finditer(data):
        if stopped():
            raise InterruptedError('Cancelled')
        sec = section_at(match.start(), len(match[0]))
        if not sec or not sec[4] & 0x20000000 or sec[4] & 0x80000000:
            continue
        code_rva = sec[0]+match.start()-sec[2]
        target = code_rva+16+struct.unpack('<i', match[1])[0]
        target_sec = next((s for s in sections if s[0] <= target < s[0]+s[1]), None)
        if not target_sec or not target_sec[4] & 0x80000000 or target_sec[4] & 0x20000000:
            continue
        # The logging-category reference must lie in the same executable
        # section and within a small code neighbourhood of the gate check.
        low, high = max(sec[2], match.start()-0x4000), min(sec[2]+sec[3], match.start()+0x4000)
        links = []
        for lea in LEA.finditer(data, low, high):
            lea_rva = sec[0]+lea.start()-sec[2]
            referenced = lea_rva+7+struct.unpack_from('<i', lea[0], 3)[0]
            if any(rva == referenced for rva, _ in categories):
                links.append((lea_rva, lea[0], referenced))
        if links:
            link = min(links, key=lambda value: abs(value[0]-code_rva))
            checks = [(code_rva, match[0]), (link[0], link[1]), (link[2], CORE)]
            candidates.setdefault(target, (code_rva, match[0], checks))
    if len(candidates) != 1:
        raise ValueError('No unique accessibility gate')
    target, (code_rva, code, checks) = next(iter(candidates.items()))
    return {'gate_rva': target, 'code_rva': code_rva, 'code': code,
            'checks': checks, 'method': 'binary_features'}


def select_gate(version, data, stopped=lambda: False):
    if not re.fullmatch(r'4\.(?:[1-9]\d*)\.\d+\.\d+', version):
        raise CompatibilityError('unsupported_version', version)
    if version == EXPECTED_VERSION and hashlib.sha256(data).hexdigest() == EXPECTED_SHA256:
        rva, code = locate_gate(data)
        return {'gate_rva': EXPECTED_RVA, 'code_rva': rva, 'code': code,
                'checks': [(rva, code)], 'method': 'verified_binary'}
    try:
        return scan_gate(data, stopped)
    except ValueError:
        raise CompatibilityError('binary_features_unrecognized', version) from None


def locate_gate(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Not a PE file")
    coff = pe + 4
    if struct.unpack_from("<H", data, coff)[0] != 0x8664:
        raise ValueError("Only the verified x64 build is supported")
    count = struct.unpack_from("<H", data, coff + 2)[0]
    opt = struct.unpack_from("<H", data, coff + 16)[0]
    table = coff + 20 + opt
    sections = []
    for index in range(count):
        pos = table + 40 * index
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, pos + 8)
        flags = struct.unpack_from("<I", data, pos + 36)[0]
        sections.append((rva, virtual_size, raw, raw_size, flags))
    for match in PATTERN.finditer(data):
        off = match.start()
        section = next((s for s in sections if s[2] <= off < s[2] + s[3] and s[4] & 0x20000000), None)
        if not section:
            continue
        code_rva = section[0] + off - section[2]
        target = code_rva + 16 + struct.unpack("<i", match[1])[0]
        if target == EXPECTED_RVA and any(s[0] <= target < s[0] + max(s[1], s[3]) and s[4] & 0x80000000 for s in sections):
            return code_rva, match[0]
    raise ValueError("The verified gate pattern is absent; no write permitted")


def activate_checked(read, write, probe, persist, stopped=lambda: False):
    original = read()
    if original not in (b"\0", b"\1"):
        raise ValueError("The target is not a boolean flag; no write permitted")
    report = {"original_byte": original[0], "write_attempted": False, "restored": False}
    baseline = probe()
    report["before"] = baseline
    if baseline["accessible"]:
        report["state"] = "already_accessible"
        persist(report)
        return report
    if original == b"\1":
        report["state"] = "flag_already_enabled_but_tree_unavailable"
        persist(report)
        return report
    keep = False
    try:
        if stopped():
            report["state"] = "cancelled"
            persist(report)
            return report
        report["write_attempted"] = True
        report["state"] = "attempting_activation"
        persist(report)  # Record the original before the only permitted mutation.
        write(b"\1")
        if read() != b"\1":
            raise RuntimeError("Flag readback failed")
        for _ in range(8):
            if stopped():
                break
            time.sleep(0.25)
            report["after"] = probe()
            if report["after"]["accessible"] and not stopped():
                keep = True
                report["state"] = "activated"
                break
        if not keep and report["write_attempted"]:
            report["state"] = "activation_failed"
    except BaseException as exc:
        report.update(state="activation_error", error_type=type(exc).__name__)
    finally:
        if not keep and report["write_attempted"]:
            try:
                write(original)
                report["restored"] = read() == original
                if not report["restored"]:
                    report["state"] = "restore_failed"
            except BaseException as exc:
                report.update(state="restore_failed", restore_error_type=type(exc).__name__)
        persist(report)
    return report


def probe_accessibility(pid):
    import win32gui
    import win32process
    from pywinauto import Desktop
    windows = []
    def visit(hwnd, _):
        native_class = win32gui.GetClassName(hwnd)
        if win32process.GetWindowThreadProcessId(hwnd)[1] == pid and native_class.startswith('Qt') and native_class.endswith('QWindowIcon'):
            windows.append(hwnd)
    win32gui.EnumWindows(visit, None)
    result = {"accessible": False, "logged_out": False, "windows": []}
    for hwnd in windows:
        window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        cls = window.class_name()
        children = window.children()
        mmui = sum(child.class_name().startswith("mmui::") for child in children)
        result["windows"].append({"class": cls, "children": len(children), "mmui_children": mmui})
        if cls == 'mmui::LoginWindow':
            result['logged_out'] = True
        if cls == "mmui::MainWindow" and children:
            result["accessible"] = True
    return result


def save_report(output, report):
    if output is None:
        return
    # No message contents, titles, paths, account names or SDK error text.
    try:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temp.replace(output)
    except OSError:
        pass


def verify_build(version, data):
    if version != EXPECTED_VERSION or hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise ValueError("This WeChat build was not verified; no write permitted")
    return locate_gate(data)


def run_activation(*, apply=False, output=None, stopped=lambda: False):
    import psutil
    import win32api
    import win32process

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("A 64-bit interpreter is required")
    matches = []
    denied = False
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() != "weixin.exe":
            continue
        try:
            handle = win32api.OpenProcess(0x0410, False, process.pid)
        except win32api.error as exc:
            if exc.winerror == 5:
                denied = True
            continue  # Exited/isolated helper processes are not main clients.
        try:
            for base in win32process.EnumProcessModules(handle):
                path = win32process.GetModuleFileNameEx(handle, base)
                if Path(path).name.lower() == "weixin.dll":
                    matches.append((process.pid, int(base), path))
        finally:
            handle.Close()
    if not matches:
        from .history_reader import HistoryReadError
        if denied:
            raise HistoryReadError('wechat_permission_denied', '无法访问微信进程，请让微信与本程序以相同权限运行')
        raise HistoryReadError("wechat_not_running", "未找到已运行的微信4客户端，请启动并登录后重试")
    if len(matches) != 1:
        from .history_reader import HistoryReadError
        raise HistoryReadError('wechat_multiple_clients', '检测到多个微信主进程，请只保留要读取的一个微信客户端后重试')
    pid, base, path = matches[0]
    info = win32api.GetFileVersionInfo(path, "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    version = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
    baseline = probe_accessibility(pid)
    identity = {'pid': pid, 'version': version,
                'started_at': datetime.now(timezone.utc).isoformat()}
    save_report(output, {**identity, 'state': 'checking', 'write_attempted': False, 'before': baseline})
    if baseline.get('logged_out') and not baseline['accessible']:
        from .history_reader import HistoryReadError
        raise HistoryReadError('wechat_not_logged_in', '微信尚未登录，请登录后重试')
    if baseline["accessible"]:
        report = {"pid": pid, "version": version, "state": "already_accessible",
                  "write_attempted": False, "before": baseline}
        save_report(output, report)
        return report
    data = Path(path).read_bytes()
    try:
        plan = select_gate(version, data, stopped)
    except CompatibilityError as exc:
        save_report(output, {**identity, 'state': exc.reason, 'write_attempted': False,
                             'dll_sha256': hashlib.sha256(data).hexdigest(), 'before': baseline})
        raise
    identity.update(dll_sha256=hashlib.sha256(data).hexdigest(),
                    gate_rva=hex(plan['gate_rva']), method=plan['method'])
    del data
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = ctypes.c_int
    kernel.WriteProcessMemory.argtypes = kernel.ReadProcessMemory.argtypes
    kernel.WriteProcessMemory.restype = ctypes.c_int
    rights = 0x0438 if apply else 0x0410
    try:
        handle = win32api.OpenProcess(rights, False, pid)
    except win32api.error as exc:
        if exc.winerror == 5:
            from .history_reader import HistoryReadError
            raise HistoryReadError('wechat_permission_denied', '无法初始化微信界面访问，请让微信与本程序以相同权限运行') from None
        raise
    try:
        def read_at(address, size):
            buf, count = ctypes.create_string_buffer(size), ctypes.c_size_t()
            if not kernel.ReadProcessMemory(int(handle), address, buf, size, ctypes.byref(count)) or count.value != size:
                raise OSError("Bounded memory read failed")
            return buf.raw

        for rva, expected in plan['checks']:
            if read_at(base + rva, len(expected)) != expected:
                save_report(output, {**identity, 'state': 'loaded_image_mismatch', 'write_attempted': False})
                raise CompatibilityError('loaded_image_mismatch', version)

        def read():
            return read_at(base + plan['gate_rva'], 1)

        def write(value):
            if not apply or value not in (b"\0", b"\1"):
                raise RuntimeError("Only an explicitly authorized boolean write is permitted")
            count = ctypes.c_size_t()
            buf = ctypes.create_string_buffer(value, 1)
            if not kernel.WriteProcessMemory(int(handle), base + plan['gate_rva'], buf, 1, ctypes.byref(count)) or count.value != 1:
                raise OSError("Single-byte flag write failed")

        def persist(report):
            save_report(output, {**identity, **report})

        if apply:
            report = activate_checked(read, write, lambda: probe_accessibility(pid), persist, stopped)
        else:
            report = {"state": "audit_only", "original_byte": read()[0], "before": probe_accessibility(pid), "write_attempted": False}
            persist(report)
        return {**identity, **report}
    finally:
        handle.Close()



def ensure_accessibility(stopped=lambda: False):
    """Called inside the authorized read worker, never at desktop startup.

    Recheck on every operation: the target flag is process-local and may reset.
    Unsupported clients with an already accessible tree need no modification.
    """
    from .history_reader import HistoryReadError
    from .runtime import data_directory
    if stopped():
        raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
    output = data_directory() / "diagnostics" / "accessibility-activation.json"
    try:
        report = run_activation(apply=True, output=output, stopped=stopped)
    except HistoryReadError:
        raise
    except (ImportError, ModuleNotFoundError):
        raise
    except CompatibilityError as exc:
        # Version is generated from four numeric file-version components.
        detail = f'微信 {exc.version} 的界面特征尚未识别，已停止读取；请提供本机兼容性诊断以便适配'
        if exc.reason == 'loaded_image_mismatch':
            detail = f'微信 {exc.version} 的运行组件与安装文件不一致，请完整退出并重新打开微信后重试'
        raise HistoryReadError('wechat_compatibility_unsupported', detail) from None
    except InterruptedError:
        raise HistoryReadError('read_cancelled', '扫描已取消，已读记录保留') from None
    except Exception as exc:
        save_report(output, {"state": "initialization_refused", "error_type": type(exc).__name__})
        code = "wechat_compatibility_unsupported" if isinstance(exc, ValueError) else "wechat_compatibility_failed"
        raise HistoryReadError(code, "微信界面兼容性初始化未通过，请确认微信已登录、与本程序以相同权限运行，并查看本机兼容性诊断") from None
    if stopped():
        raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
    if report["state"] not in {"activated", "already_accessible"}:
        raise HistoryReadError("wechat_accessibility_failed", "微信界面访问初始化失败，已停止读取；请查看本机兼容性诊断后重试")
    return report
