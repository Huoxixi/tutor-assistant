"""Per-read UIA initialization for the verified WeChat build; no chat contents.

Reference: fanyuantaier/wechatauto-replica, accessibility.py (Apache-2.0).
This implementation accepts only the locally verified WeChat build and gate.
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
            if report["after"]["accessible"]:
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
        if win32process.GetWindowThreadProcessId(hwnd)[1] == pid and win32gui.GetClassName(hwnd) == "Qt51514QWindowIcon":
            windows.append(hwnd)
    win32gui.EnumWindows(visit, None)
    result = {"accessible": False, "windows": []}
    for hwnd in windows:
        window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        cls = window.class_name()
        children = window.children()
        mmui = sum(child.class_name().startswith("mmui::") for child in children)
        result["windows"].append({"class": cls, "children": len(children), "mmui_children": mmui})
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
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() != "weixin.exe":
            continue
        handle = win32api.OpenProcess(0x0410, False, process.pid)
        try:
            for base in win32process.EnumProcessModules(handle):
                path = win32process.GetModuleFileNameEx(handle, base)
                if Path(path).name.lower() == "weixin.dll":
                    matches.append((process.pid, int(base), path))
        finally:
            handle.Close()
    if not matches:
        from .history_reader import HistoryReadError
        raise HistoryReadError("wechat_not_running", "未找到已运行的微信4客户端，请启动并登录后重试")
    if len(matches) != 1:
        raise RuntimeError("Exactly one running WeChat DLL is required")
    pid, base, path = matches[0]
    info = win32api.GetFileVersionInfo(path, "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    version = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
    baseline = probe_accessibility(pid)
    if baseline["accessible"]:
        report = {"pid": pid, "version": version, "state": "already_accessible",
                  "write_attempted": False, "before": baseline}
        save_report(output, report)
        return report
    if version != EXPECTED_VERSION:
        raise ValueError("This WeChat build was not verified; no write permitted")
    data = Path(path).read_bytes()
    code_rva, code = verify_build(version, data)
    identity = {"pid": pid, "version": version, "dll_sha256": hashlib.sha256(data).hexdigest(), "gate_rva": hex(EXPECTED_RVA), "started_at": datetime.now(timezone.utc).isoformat()}
    del data
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = ctypes.c_int
    kernel.WriteProcessMemory.argtypes = kernel.ReadProcessMemory.argtypes
    kernel.WriteProcessMemory.restype = ctypes.c_int
    rights = 0x0438 if apply else 0x0410
    handle = win32api.OpenProcess(rights, False, pid)
    try:
        def read_at(address, size):
            buf, count = ctypes.create_string_buffer(size), ctypes.c_size_t()
            if not kernel.ReadProcessMemory(int(handle), address, buf, size, ctypes.byref(count)) or count.value != size:
                raise OSError("Bounded memory read failed")
            return buf.raw

        if read_at(base + code_rva, len(code)) != code:
            raise RuntimeError("The loaded code differs from the verified file; no write permitted")

        def read():
            return read_at(base + EXPECTED_RVA, 1)

        def write(value):
            if not apply or value not in (b"\0", b"\1"):
                raise RuntimeError("Only an explicitly authorized boolean write is permitted")
            count = ctypes.c_size_t()
            buf = ctypes.create_string_buffer(value, 1)
            if not kernel.WriteProcessMemory(int(handle), base + EXPECTED_RVA, buf, 1, ctypes.byref(count)) or count.value != 1:
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
    except Exception as exc:
        save_report(output, {"state": "initialization_refused", "error_type": type(exc).__name__})
        code = "wechat_compatibility_unsupported" if isinstance(exc, ValueError) else "wechat_compatibility_failed"
        raise HistoryReadError(code, "微信界面兼容性初始化未通过，请确认微信已登录、与本程序以相同权限运行，并使用已支持的微信版本4.1.15.13") from None
    if stopped():
        raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
    if report["state"] not in {"activated", "already_accessible"}:
        raise HistoryReadError("wechat_accessibility_failed", "微信界面访问初始化失败，已停止读取；请查看本机兼容性诊断后重试")
    return report
