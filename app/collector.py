"""A read-only adapter. UI work is isolated so cancellation can stop a stuck library."""
import importlib.util
import multiprocessing
import os
import queue
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from .models import Message, SHANGHAI
from .history_reader import HistoryReadError, PHASE_DETAILS


def parse_time(value, anchor, ui_relative=False):
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        # Only the live WeChat history UI defines an isolated clock as today.
        if not ui_relative:
            return None
        text = anchor.strftime("%Y-%m-%d") + " " + text
    weekday = re.fullmatch(r"星期([一二三四五六日天])\s+(\d{1,2}:\d{2})", text)
    if weekday and ui_relative:
        day = "一二三四五六日".find(weekday[1].replace("天", "日"))
        days = (anchor.weekday() - day) % 7 or 7
        text = (anchor - timedelta(days=days)).strftime("%Y-%m-%d") + " " + weekday[2]
    month_day = re.fullmatch(r"(\d{1,2})月(\d{1,2})日\s+(\d{1,2}:\d{2})", text)
    if month_day and ui_relative:
        try:
            date = datetime.strptime(f"{anchor.year}-{month_day[1]}-{month_day[2]} {month_day[3]}", "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
            if date.date() > anchor.date():
                date = date.replace(year=date.year - 1)
            return date
        except ValueError:
            return None
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    for word, days in [("今天", 0), ("昨天", 1), ("前天", 2)]:
        if text.startswith(word):
            text = (anchor - timedelta(days=days)).strftime("%Y-%m-%d") + text[len(word):]
    try:
        value = datetime.fromisoformat(text)
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        except ValueError:
            return None


def describe_read_error(exc, phase="read"):
    # Never display exception text: SDK errors may include private chat contents.
    name = type(exc).__name__
    if isinstance(exc, HistoryReadError):
        return {"error": str(exc), "error_code": exc.code}
    known = {
        "NotStartError": ("wechat_not_running", "微信未启动，请启动并登录后重试"),
        "NotLoginError": ("wechat_not_logged_in", "微信尚未登录，请登录后重试"),
        "NotFoundError": ("wechat_ui_unavailable", "微信聊天控件不可访问，当前读取方式不兼容此客户端或账号；未读取到群消息"),
        "NoSuchFriendError": ("group_not_found", "未找到该群，请核对微信中的完整群名"),
        "NotFriendError": ("chat_unavailable", "该聊天记录不可访问，请检查群是否仍在当前账号中"),
        "NetWorkError": ("wechat_offline", "微信当前离线，请恢复连接后重试"),
        "ModuleNotFoundError": ("dependency_missing", "微信读取依赖缺失，请重新安装读取组件"),
        "ImportError": ("dependency_incompatible", "微信读取组件加载失败，请检查依赖版本"),
        "ElementNotFoundError": ("history_control_missing", "未能定位微信历史记录控件，请检查客户端兼容性"),
    }
    code, detail = known.get(name, ("adapter_initialization_failed", "微信读取组件初始化失败，请检查微信版本与读取依赖") if phase == "initialize" else ("history_read_failed", "微信历史读取失败，请检查客户端兼容性后重试"))
    return {"error": detail, "error_code": code}


def read_child(group, count, output, options=None, stop=None):
    # The library can print personal chat contents. Never forward its stdout/logs.
    import contextlib
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        from .read_diagnostics import ReadDiagnostics
        from .history_reader import read_history
        options = options or {}
        streaming = options.get("stream", False)
        diag = ReadDiagnostics(Path(__file__).resolve().parent.parent / "data" / "diagnostics" / "history-read-latest.json",
                               notify=output.put if streaming else None)
        phase, com = "initialize", None
        try:
            diag.event("initialize")
            import pythoncom
            pythoncom.CoInitialize()
            com = pythoncom
            if count == 0:
                from pyweixin import Contacts
                phase = "read"
                result = Contacts.get_groups_info(is_maximize=True, close_weixin=False)
            else:
                phase = "read"
                result = read_history(group, count, diag,
                                      emit=output.put if streaming else None,
                                      stopped=stop.is_set if stop is not None else lambda: False,
                                      start=datetime.fromisoformat(options["start"]) if options.get("start") else None,
                                      end=datetime.fromisoformat(options["end"]) if options.get("end") else None,
                                      timeout=options.get("total_timeout", 600))
                diag.event("returned", rows=len(result["rows"]))
                output.put({"event": "done", **result, "rows": []} if streaming else result)
                return
            diag.event("returned", rows=len(result))
            output.put({"rows": result})
        except Exception as exc:
            output.put({**describe_read_error(exc, phase), "diagnostic": diag.failure(exc)})
        finally:
            if com is not None:
                com.CoUninitialize()


def preflight():
    installed = importlib.util.find_spec("pyweixin") is not None
    running = False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        running = bool(re.search(rb'"(?:Weixin|WeChat)\.exe"', result.stdout, re.I))
    return {"adapter_installed": installed, "wechat_running": running, "detail": "已检测到微信进程；请先用一个群测试兼容性" if installed and running else "请安装微信读取依赖" if not installed else "请启动并登录电脑版微信"}


class Collector:
    def __init__(self, reader=None, max_rows=20000, page_timeout=60, total_timeout=600):
        self.reader, self.max_rows, self.page_timeout = reader, max_rows, page_timeout
        self.total_timeout = total_timeout

    def discover(self, cancel):
        rows = self.page("", 0, cancel)
        if not isinstance(rows, list) or any(not isinstance(name, str) for name in rows):
            raise RuntimeError("未能识别群名，请检查微信版本或直接填写群名")
        return list(dict.fromkeys(name.strip() for name in rows if 0 < len(name.strip()) <= 120))

    def _events(self, group, count, cancel, options=None):
        if cancel.is_set():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        ctx = multiprocessing.get_context("spawn")
        options = {**(options or {}), "total_timeout": self.total_timeout}
        output = ctx.Queue(maxsize=8)
        stop = ctx.Event()
        process = ctx.Process(target=read_child, args=(group, count, output, options, stop), daemon=True)
        process.start()
        started = activity = time.monotonic()
        phases_seen, most_rows = set(), -1
        try:
            while not cancel.is_set() and time.monotonic() - activity < self.page_timeout and time.monotonic() - started < self.total_timeout:
                try:
                    result = output.get(timeout=0.2)
                    if "error" in result:
                        error = HistoryReadError(result["error_code"], result["error"])
                        error.diagnostic = result.get("diagnostic")
                        raise error
                    phase = result.get("phase")
                    rows = result.get("rows")
                    advanced = type(rows) is int and rows > most_rows
                    if advanced:
                        most_rows = rows
                    if result.get("event") != "progress" or phase not in phases_seen or advanced:
                        activity = time.monotonic()
                    phases_seen.add(phase)
                    yield result
                    if result.get("event") not in {"progress", "batch"}:
                        return
                except queue.Empty:
                    if not process.is_alive():
                        raise HistoryReadError("worker_exited", "微信读取进程提前结束，已读记录保留，可重试")
            raise HistoryReadError("read_cancelled" if cancel.is_set() else "history_read_timeout",
                                   "扫描已取消，已读记录保留" if cancel.is_set() else "本次读取超时，已读记录保留，可重试")
        finally:
            stop.set()
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
            process.join(timeout=3)
            output.close()

    def page(self, group, count, cancel):
        if self.reader:
            return self.reader(group, count)
        for event in self._events(group, count, cancel):
            if "rows" in event:
                return event["rows"]
        return []

    def collect(self, group, start, end, cancel, on_batch, on_progress=None):
        report = {"group": group, "state": "reading", "count": 0, "earliest": None, "latest": None, "unknown_times": 0, "unreadable_rows": 0, "detail": "正在读取历史消息"}
        seen, known, unknown = {}, [], 0

        def ingest(rows):
            nonlocal known, unknown
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise HistoryReadError("invalid_history_rows", "未获得可解析的历史记录，请检查群名称和客户端版本")
            fresh = []
            for row in rows:
                stamp = parse_time(row.get("消息发送时间"), end, ui_relative=True)
                kind = "text" if row.get("消息类型") in {"文本", "text"} else "unparsed"
                try:
                    message = Message(group=group, sender=row.get("消息发送人") or "未知发布者", text=str(row.get("消息内容", "")),
                                      sent_at=stamp, kind=kind, source_id=row.get("source_id"))
                except ValidationError:
                    # Incompatible/oversized rows cannot discard the other rows
                    # in this batch or support a complete-coverage claim.
                    report["unreadable_rows"] += 1
                    continue
                key = message.source_id or (message.sender, message.text, str(stamp), kind)
                if key not in seen and (stamp is None or start <= stamp <= end):
                    fresh.append(message)
                seen[key] = message
            known = [m.sent_at for m in seen.values() if m.sent_at]
            unknown = sum(m.sent_at is None for m in seen.values())
            batch = [m for m in seen.values() if m.sent_at is None or start <= m.sent_at <= end]
            if fresh:
                on_batch(fresh)
            report.update(count=len(batch), earliest=min(known).isoformat() if known else None,
                          latest=max(known).isoformat() if known else None, unknown_times=unknown)

        def failed(exc):
            safe = describe_read_error(exc)
            return {**report, "state": "cancelled" if cancel.is_set() else "incomplete",
                    "detail": safe["error"], "error_code": safe["error_code"],
                    **({"diagnostic": exc.diagnostic} if getattr(exc, "diagnostic", None) else {})}

        # The production reader uses one incremental worker/session, not repeated
        # reads of the first 200/800/... rows. The injected legacy reader remains
        # supported for offline integrations.
        if not self.reader:
            try:
                result = {}
                for event in self._events(group, self.max_rows, cancel,
                                          {"stream": True, "start": start.isoformat(), "end": end.isoformat()}):
                    if event.get("event") == "batch":
                        ingest(event["rows"])
                    elif event.get("event") == "progress":
                        phase = event.get("phase")
                        if phase in PHASE_DETAILS:
                            report.update(phase=phase, detail=PHASE_DETAILS[phase])
                    else:
                        result = event
                    if on_progress:
                        on_progress(dict(report))
                boundary = any(t < start for t in known)
                metadata = {"read_reason": result.get("reason", "unknown"), "batch_sizes": result.get("batch_sizes", []),
                            "continuity_issues": result.get("continuity_issues", {})}
                if result.get("coverage") and boundary and not unknown and not report["unreadable_rows"]:
                    return {**report, **metadata, "state": "complete", "detail": "已读取目标时间段消息；范围限本机可访问历史"}
                detail = "已读取部分消息，尚未证明覆盖完整48小时"
                if report["unreadable_rows"]:
                    detail = "部分记录格式不兼容，已保留其余可读取消息，覆盖范围不完整"
                elif unknown:
                    detail = "部分消息时间无法判断，已保留可读取记录"
                elif result.get("continuous") is False:
                    detail = "历史翻页连续性无法确认，已保留可读取记录"
                elif result.get("reason") == "limit":
                    detail = "达到单群读取上限，尚未证明覆盖48小时"
                return {**report, **metadata, "state": "incomplete", "detail": detail}
            except Exception as exc:
                return failed(exc)

        count = 200
        while not cancel.is_set():
            try:
                rows = self.page(group, min(count, self.max_rows), cancel)
                ingest(rows)
                boundary = any(t < start for t in known)
                if boundary and report["unreadable_rows"]:
                    return {**report, "state": "incomplete", "detail": "部分记录格式不兼容，已保留其余可读取消息，覆盖范围不完整"}
                if boundary and not unknown:
                    return {**report, "state": "complete", "detail": "已读到48小时之前的记录；覆盖范围限本机可访问历史"}
                if boundary and unknown:
                    return {**report, "state": "incomplete", "detail": "已触及时间边界，但部分消息时间无法判断"}
                if len(rows) < min(count, self.max_rows):
                    return {**report, "state": "incomplete", "detail": "本机可读取记录不足以证明覆盖完整48小时"}
                if count >= self.max_rows:
                    return {**report, "state": "incomplete", "detail": "达到单群读取上限，尚未证明覆盖48小时"}
                count = min(count * 4, self.max_rows)
            except Exception as exc:
                return failed(exc)
        return {**report, "state": "cancelled", "detail": "扫描已取消，已读记录保留"}
