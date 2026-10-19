"""Project-owned history adapter. Imports the Windows SDK only on an explicit read.

Navigation is reused from pyweixin; its title-regex history opener, multi-select
traversal and positional two-pass parser are deliberately not used.
"""
import hashlib
import calendar
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from statistics import median


PHASE_DETAILS = {
    "initialize": "正在初始化读取组件",
    "open_chat": "正在打开目标群",
    "open_history": "正在打开聊天记录",
    "locate_history": "正在定位聊天记录窗口",
    "locate_list": "正在定位消息列表",
    "position_history": "正在按日期定位读取起点",
    "identify_sender": "正在核对消息发布者",
    "read_history": "正在读取历史消息",
    "advance_history": "正在读取后续消息",
    "returned": "历史读取已结束",
}


class HistoryReadError(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class HistoryItem:
    text: str
    class_name: str
    runtime_id: tuple = ()
    sender: str = ""
    position: int | None = None
    step_proven: bool = False

    @property
    def identity(self):
        # A runtime ID is only an in-session overlap hint, never a database ID.
        return self.runtime_id, self.class_name, self.text, self.sender


SYSTEM_CLASSES = {"mmui::ChatItemView", "mmui::ChatSystemInfoItemView"}
HISTORY_CLASS = "mmui::SearchMsgUniqueChatWindow"
TIME_SUFFIX = re.compile(
    r"(?:\s+)(?P<time>(?:\d{4}年)?\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"|(?:今天|昨天|前天|星期[一二三四五六日天])\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}:\d{2}(?::\d{2})?)\s*\Z"
)


def parse_item(item):
    """Unknown types remain unparsed. Remove only the trailing timestamp."""
    if item.class_name in SYSTEM_CLASSES:
        return None
    text = item.text.strip()
    if not text:
        return None
    match = TIME_SUFFIX.search(text)
    return {
        "消息内容": text[:match.start()].rstrip() if match else text,
        "消息发送时间": match["time"] if match else None,
        "消息发送人": item.sender.strip() or "未知发布者",
        "消息类型": "文本" if item.class_name == "mmui::ChatTextItemView" else "unparsed",
    }


def pair_sender_label(selected, normal_items):
    """Match the same message's two UI labels, never two history array indexes.

    The selected label exposes `publisher + whitespace + original label`.
    Names containing spaces are preserved. An invalid prefix remains unknown;
    the independently observed normal body is still safe to retain.
    """
    candidates = {item.text for item in normal_items
                  if item.class_name == selected.class_name and item.text
                  and selected.text.endswith(item.text)}
    if not candidates:
        return None
    normal = max(candidates, key=len)
    prefix = selected.text[:-len(normal)]
    sender = prefix.strip()
    if not prefix or not prefix[-1].isspace() or not sender or len(sender) > 120 or any(c in sender for c in "\r\n\t"):
        sender = ""
    return normal, sender


def select_history_window(windows, group, previous_handles):
    """Bind to a triggered window within the verified chat's process, never [0]."""
    candidates = [w for w in windows if w.class_name() == HISTORY_CLASS]
    exact = [w for w in candidates if any(mark in w.window_text() for mark in (
        f"“{group}”", f"「{group}」", f'"{group}"',
    )) or w.window_text() == group]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise HistoryReadError("history_window_ambiguous", "找到多个目标群历史窗口，请关闭多余窗口后重试")
    candidates = [w for w in candidates if not (
        (quoted := re.findall(r'“([^”]+)”|「([^」]+)」|"([^"]+)"', w.window_text()))
        and group not in [next(part for part in match if part) for match in quoted])]
    created = [w for w in candidates if w.handle not in previous_handles]
    if len(created) == 1:
        return created[0]
    # A newly created window may have a generic title. An existing generic
    # window cannot establish the chat's identity and is not silently reused.
    if len(candidates) > 1:
        raise HistoryReadError("history_window_ambiguous", "无法唯一确定目标群历史窗口，请关闭多余窗口后重试")
    return None


def select_history_list(lists):
    named, structural = [], []
    for control in lists:
        if control.window_text() in {"聊天记录", "聊天記錄", "Chat History"}:
            named.append(control)
        elif any(child.class_name().startswith("mmui::Chat") for child in control.children()):
            structural.append(control)
    candidates = named or structural
    if len(candidates) != 1:
        raise HistoryReadError("history_list_ambiguous" if candidates else "history_list_missing",
                               "无法唯一定位历史消息列表，请检查客户端兼容性")
    return candidates[0]


def selection_points(item_rect, list_rect):
    """Visible message-body points, expressed relative to the stable list.

    Qt can destroy a message wrapper between showing its rectangle and the
    click. Clicking through the list avoids consulting that stale wrapper.
    """
    top, bottom = max(item_rect.top, list_rect.top), min(item_rect.bottom, list_rect.bottom)
    if bottom - top < 16:
        return []
    y = min(max(item_rect.top + 60, top + 15), bottom - 7)
    points = []
    for offset in (140, 90, 110):
        x = min(max(item_rect.left + offset, list_rect.left + 7), list_rect.right - 15)
        click_y = min(top + 80, bottom - 7) if offset == 140 else y
        point = (x - list_rect.left, click_y - list_rect.top)
        if point not in points:
            points.append(point)
    return points


def calendar_day_point(image, day):
    """Locate the day in Qt's childless calendar using local pixel rows.

    The inspected widget has a Monday-first header and evenly spaced cells.
    Infer spacing from rendered glyph rows rather than fixed screen pixels.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 70 or height < 70:
        raise HistoryReadError("history_calendar_layout", "日期网格尺寸无法识别")
    background = median(sum(rgb.getpixel(p)) / 3 for p in ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)))
    active = []
    for y in range(height):
        count = 0
        for x in range(width):
            pixel = rgb.getpixel((x, y))
            if max(pixel) - min(pixel) < 45 and sum(pixel) / 3 < background - 40:
                count += 1
        if count >= 3:
            active.append(y)
    bands = []
    for y in active:
        if not bands or y - bands[-1][-1] > max(3, width // 140):
            bands.append([y])
        else:
            bands[-1].append(y)
    centers = [(b[0] + b[-1]) / 2 for b in bands]
    weeks = len(calendar.monthcalendar(day.year, day.month))
    if len(centers) != weeks + 1:
        raise HistoryReadError("history_calendar_layout", "日期网格行数无法可靠识别")
    gaps = [b-a for a, b in zip(centers, centers[1:])]
    pitch = median(gaps)
    if pitch <= 0 or any(abs(g-pitch) > pitch * .15 for g in gaps) or not 6 < width / pitch < 8:
        raise HistoryReadError("history_calendar_layout", "日期网格间距无法可靠识别")
    row = (day.replace(day=1).weekday() + day.day - 1) // 7
    x = width / 2 + (day.weekday() - 3) * pitch
    y = centers[row + 1]
    if not 0 <= x < width or not 0 <= y < height:
        raise HistoryReadError("history_calendar_layout", "目标日期不在可访问网格内")
    return round(x), round(y)


class UIAHistoryDriver:
    """All UI effects are deferred until open(), called only by a scan worker."""
    def __init__(self, desktop=None, navigator=None, buttons=None, cursor_mode=True):
        self.desktop, self.navigator, self.buttons = desktop, navigator, buttons
        self.window = self.message_list = None
        self.owns_window = False
        self.cursor_mode = cursor_mode
        self.selection_active = False
        self.cursor_ready = False
        self.position = 0
        self.step_proven = True
        self.normal_cache = []
        self.publisher_cache = {}
        self.cursor = None
        self.diag = None
        self.stopped = lambda: False
        self.order = "descending"
        self.batch_size = 100

    def _calendar_popup(self):
        candidates = [w for w in self.desktop.windows(process=self.window.element_info.process_id, visible_only=True)
                      if w.class_name() == "mmui::XPopover"
                      and any(c.class_name() == "mmui::DateNumPanel" for c in w.descendants())]
        if len(candidates) > 1:
            raise HistoryReadError("history_calendar_ambiguous", "找到多个日期选择器，已停止读取")
        return candidates[0] if candidates else None

    def _set_calendar_part(self, popup, target, suffix):
        for _ in range(13):
            if self.stopped():
                raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
            matches = [(combo, int(text[:-1])) for combo in popup.descendants(control_type="ComboBox")
                       for child in combo.children() if (text := child.window_text())
                       and re.fullmatch(r"\d+" + suffix, text)]
            if len(matches) != 1:
                break
            combo, current = matches[0]
            if current == target:
                return
            arrows = sorted(combo.descendants(control_type="Button"), key=lambda c: c.rectangle().top)
            if len(arrows) != 2:
                break
            arrows[0 if current < target else 1].click_input()
            time.sleep(.05)
        raise HistoryReadError("history_calendar_month", "无法可靠切换到目标年月")

    def position_at(self, start, end):
        """Jump to the start date; Qt then exposes chronological messages."""
        if self.stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        self.diag.event("position_history")
        tabs = [c for c in self.window.descendants(control_type="TabItem")
                if c.window_text() in {"日期", "Date"}]
        if len(tabs) != 1:
            raise HistoryReadError("history_date_missing", "无法定位历史记录日期入口")
        tabs[0].click_input()
        deadline = time.monotonic() + 2
        popup = None
        while not self.stopped() and time.monotonic() < deadline:
            popup = self._calendar_popup()
            if popup is not None:
                break
            time.sleep(.05)
        if popup is None:
            raise HistoryReadError("history_calendar_missing", "无法打开历史日期选择器")
        self._set_calendar_part(popup, start.year, "年")
        self._set_calendar_part(popup, start.month, "月")
        panels = [c for c in popup.descendants() if c.class_name() == "mmui::DateNumPanel"]
        if len(panels) != 1:
            raise HistoryReadError("history_calendar_layout", "无法唯一定位日期网格")
        panel = panels[0]
        rect = panel.rectangle()
        x, y = calendar_day_point(panel.capture_as_image(), start.date())
        panel.click_input(coords=(rect.left+x, rect.top+y), absolute=True, use_log=False)
        deadline = time.monotonic() + 2
        while not self.stopped() and time.monotonic() < deadline:
            if self._calendar_popup() is None:
                self.message_list = select_history_list(self.window.descendants(control_type="List"))
                self.order = "ascending"
                return
            time.sleep(.05)
        raise HistoryReadError("history_date_unavailable", "目标日期没有可定位记录或日期选择未生效")

    def open(self, group, diag, stopped):
        self.diag, self.stopped = diag, stopped
        if stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        if self.desktop is None:
            from pywinauto import Desktop
            from pyweixin import Navigator
            from pyweixin.Uielements import Buttons
            self.desktop, self.navigator, self.buttons = Desktop(backend="uia"), Navigator, Buttons
        diag.event("open_chat")
        main = self.navigator.open_dialog_window(friend=group, is_maximize=True, search_pages=0)
        pid = main.element_info.process_id
        previous = {w.handle for w in self.desktop.windows(process=pid, visible_only=True)
                    if w.class_name() == HISTORY_CLASS}
        if stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        diag.event("open_history")
        button = main.child_window(**self.buttons.ChatHistoryButton)
        if not button.exists(timeout=1):
            raise HistoryReadError("history_button_missing", "当前聊天无法打开历史记录，请核对群名称")
        button.click_input()
        diag.event("locate_history")
        deadline = time.monotonic() + 6
        while not stopped() and time.monotonic() < deadline:
            self.window = select_history_window(
                self.desktop.windows(process=pid, visible_only=True), group, previous)
            if self.window is not None:
                break
            time.sleep(0.1)
        if self.window is None:
            if stopped():
                raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
            raise HistoryReadError("history_window_missing", "聊天记录已打开，但无法定位记录窗口，请检查客户端兼容性")
        self.owns_window = self.window.handle not in previous
        if stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        diag.event("locate_list")
        deadline = time.monotonic() + 4
        while True:
            if stopped():
                raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
            lists = self.window.descendants(control_type="List")
            diag.event("locate_list", lists=len(lists))
            try:
                self.message_list = select_history_list(lists)
                break
            except HistoryReadError as exc:
                if exc.code != "history_list_missing" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        diag.event("position_history")
        self._key("{HOME}")

    def _key(self, keys):
        # Never send global keystrokes. Stop if focus cannot be established in
        # this specific list; there is no fall-through to the message editor.
        self.message_list.set_focus()
        def focused():
            return self.message_list.has_keyboard_focus() or any(
                child.has_keyboard_focus() for child in self.message_list.children())
        if not focused():
            # Some Qt builds focus the item rather than the list. Attempt one
            # explicit text-item focus, avoiding image/link/menu activation.
            texts = [child for child in self.message_list.children()
                     if child.class_name() == "mmui::ChatTextItemView"]
            if texts:
                texts[0].set_focus()
        if not focused():
            raise HistoryReadError("history_focus_unavailable", "历史消息列表未获得焦点，已停止读取，可重试")
        self.message_list.type_keys(keys, set_foreground=True)

    def _plain_snapshot(self):
        result = []
        for control in self.message_list.children():
            class_name = control.class_name()
            if not class_name.startswith("mmui::Chat"):
                continue
            # Do not infer sender from the first line of an order. A client
            # exposing a dedicated sender field can supply it without two passes.
            sender = ""
            for child in control.children():
                if child.element_info.automation_id in {"sender_name", "senderName"}:
                    sender = child.window_text()
                    break
            result.append(HistoryItem(control.window_text(), class_name,
                                      tuple(control.element_info.runtime_id or ()), sender))
        return result

    def _normal_controls(self):
        return [c for c in self.message_list.children()
                if c.class_name().startswith("mmui::Chat")
                and c.class_name() not in SYSTEM_CLASSES
                and c.element_info.control_type != "CheckBox"]

    def _remember_normal(self):
        controls = self._normal_controls()
        self.normal_cache = [HistoryItem(c.window_text(), c.class_name(), tuple(c.element_info.runtime_id or ()))
                             for c in controls]
        return controls

    def _enter_selection(self, target):
        if self.stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        original = target.window_text()
        kind = target.class_name()
        r, p = target.rectangle(), self.message_list.rectangle()
        points = selection_points(r, p)
        if not points:
            raise HistoryReadError("history_cursor_offscreen", "当前消息不在历史列表可访问区域，已停止读取")
        menus = []
        menu_items = []
        for point in points:
            if self.stopped():
                raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
            # Avoid SDK client_to_screen: Qt's UIA rectangle may disappear
            # briefly even for the list. Coordinates were bounded above.
            self.message_list.click_input(button="right", coords=(p.left + point[0], p.top + point[1]),
                                          absolute=True, use_log=False)
            deadline = time.monotonic() + 0.5
            while not self.stopped() and time.monotonic() < deadline:
                menu_items = [m for window in self.desktop.windows(process=self.window.element_info.process_id, visible_only=True)
                              for m in window.descendants(control_type="MenuItem")]
                menus = [m for m in menu_items if m.window_text() in {"多选", "多選", "Select..."}]
                if menus:
                    break
                time.sleep(0.05)
            if len(menus) == 1:
                break
            # Escape without an open menu closes Qt's history window.
            if menu_items:
                self.message_list.type_keys("{ESC}", set_foreground=True)
            if menus:  # Ambiguous menus cannot be resolved by another click.
                break
        if len(menus) != 1:
            raise HistoryReadError("history_selection_unavailable", "无法进入消息发布者核对状态，已停止读取")
        menus[0].click_input()
        self.selection_active = True
        deadline = time.monotonic() + 1.5
        while not self.stopped() and time.monotonic() < deadline:
            matches = [c for c in self.message_list.children()
                       if c.element_info.control_type == "CheckBox" and c.class_name() == kind
                       and c.window_text().endswith(original)]
            if len(matches) == 1:
                matches[0].set_focus()
                if matches[0].has_keyboard_focus():
                    self.cursor = matches[0]
                    return
            time.sleep(0.05)
        raise HistoryReadError("history_cursor_unavailable", "未能可靠定位当前消息，已停止读取")

    def _focused(self):
        nodes = [c for c in self.message_list.children()
                 if c.class_name().startswith("mmui::Chat") and c.has_keyboard_focus()]
        if len(nodes) != 1:
            raise HistoryReadError("history_focus_unavailable", "历史消息焦点不唯一，已停止读取")
        return nodes[0]

    @staticmethod
    def _sender_from_control(selected,current):
        """Use dedicated sender metadata only; never open a member filter."""
        try:
            names={c.window_text().strip() for c in current.children()
                   if c.element_info.automation_id in {'sender_name','senderName'}}
        except Exception:
            return None
        names={name for name in names if name and len(name)<=120 and not any(c in name for c in '\r\n\t')}
        if len(names)!=1:return None
        name=next(iter(names))
        if not selected.text.startswith(name):return None
        suffix=selected.text[len(name):]
        if not suffix or not suffix[0].isspace():return None
        body=suffix.lstrip()
        return (body,name) if body else None

    def snapshot(self):
        if self.stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        if not self.cursor_mode:
            return self._plain_snapshot()
        if not self.cursor_ready:
            controls = self._remember_normal()
            if not controls:
                return []
            self._enter_selection(controls[0])
            self.cursor_ready = True
        current = self._focused()
        selected = HistoryItem(current.window_text(), current.class_name(), tuple(current.element_info.runtime_id or ()))
        if selected.class_name in SYSTEM_CLASSES:
            return [HistoryItem(selected.text, selected.class_name, selected.runtime_id,
                                position=self.position, step_proven=self.step_proven)]
        cache_key = (selected.class_name, selected.text)
        pair = self.publisher_cache.get(cache_key)
        if pair is None:
            self.diag.event("identify_sender")
            pair = pair_sender_label(selected, self.normal_cache)
            if pair is None:
                pair = self._sender_from_control(selected,current)
            if pair is None:
                # Keep multi-select active for the whole read, including at
                # 100-message flushes. An unverified publisher stays unknown;
                # preserve the entire label instead of guessing its boundary.
                pair = (selected.text,"")
            self.publisher_cache[cache_key] = pair
        return [HistoryItem(pair[0], selected.class_name, selected.runtime_id, pair[1],
                            self.position, self.step_proven)]

    def advance(self):
        if not self.cursor_mode:
            self._key("{PGDN}")
            time.sleep(0.12)
            return
        before = self._focused()
        previous = (before.class_name(), before.window_text(), tuple(before.element_info.runtime_id or ()))
        # Target the focused message, not the list. Focusing the list before
        # every arrow loses Qt's item cursor and turns Down into pixel scrolling.
        before.type_keys("{DOWN}", set_foreground=True)
        deadline = time.monotonic() + 0.4
        while not self.stopped():
            current = self._focused()
            identity = (current.class_name(), current.window_text(), tuple(current.element_info.runtime_id or ()))
            if identity != previous:
                self.position += 1
                self.step_proven = True
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.02)

    def close(self):
        if self.selection_active and not self.owns_window:
            self.message_list.type_keys("{ESC}", set_foreground=True)
            self.selection_active = False
        if self.owns_window and self.window is not None:
            self.window.close()


def read_history(group, limit, diag, emit=None, stopped=lambda: False,
                 start=None, end=None, driver_factory=UIAHistoryDriver, timeout=600):
    """Walk one open history session and publish each page before advancing.

    A missing overlap, unknown timestamp, or ascending timestamp blocks a
    coverage claim. This conservatively retains data without hiding gaps.
    """
    from .collector import parse_time

    if limit <= 0:
        raise HistoryReadError("invalid_read_limit", "读取条数必须大于零")
    if timeout <= 0:
        raise HistoryReadError("history_read_timeout", "历史读取超时，已停止读取")
    driver = driver_factory()
    seen, previous, all_rows = set(), [], []
    occurrences = Counter()
    continuous, unknown, boundary = True, 0, False
    continuity_issues = Counter()
    last_stamp = None
    previous_position = None
    deadline = time.monotonic() + timeout
    stagnant = 0
    reason = "limit"
    pending = []
    batch_sizes = []

    def publish_pending():
        if pending and emit:
            batch = list(pending)
            pending.clear()
            batch_sizes.append(len(batch))
            emit({"event": "batch", "rows": batch})

    try:
        if stopped():
            raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
        driver.open(group, diag, stopped)
        if start is not None and hasattr(driver, "position_at"):
            driver.position_at(start, end)
        ascending = getattr(driver, "order", "descending") == "ascending"
        empty_deadline = min(deadline, time.monotonic() + 3)
        while len(all_rows) < limit:
            if stopped():
                raise HistoryReadError("read_cancelled", "扫描已取消，已读记录保留")
            if time.monotonic() >= deadline:
                raise HistoryReadError("history_read_timeout", "历史读取超时，已读记录保留，可重试")
            diag.event("read_history", rows=len(all_rows))
            items = driver.snapshot()
            if not items and not all_rows and time.monotonic() < empty_deadline:
                # A visible list may expose its rows a moment later. Wait in
                # place rather than sending navigation into an empty control.
                time.sleep(0.1)
                continue
            sequential = len(items) == 1 and items[0].position is not None
            if sequential:
                position = items[0].position
                if previous_position is None:
                    continuous = continuous and position == 0 and items[0].step_proven
                    if position != 0 or not items[0].step_proven:
                        continuity_issues["initial_cursor"] += 1
                elif position != previous_position:
                    continuous = continuous and position == previous_position + 1 and items[0].step_proven
                    if position != previous_position + 1 or not items[0].step_proven:
                        continuity_issues["cursor_step"] += 1
                previous_position = position
            # Missing UIA IDs cannot prove overlap, but must not collapse two
            # identical records in the same snapshot.
            local_occurrences = Counter()
            multiplicity = Counter(item.identity for item in items)
            entries = []
            for item in items:
                local_occurrences[item.identity] += 1
                identity = (item.position, item.class_name, item.text, item.sender) if sequential else (
                    item.identity if item.runtime_id and multiplicity[item.identity] == 1 else (item.identity, local_occurrences[item.identity]))
                entries.append((identity, item))
            ordered = [identity for identity, item in entries if item.class_name not in SYSTEM_CLASSES and item.text.strip()]
            if not sequential and any(not item.runtime_id or multiplicity[item.identity] > 1 for item in items):
                continuous = False
                continuity_issues["item_identity"] += 1
            if not sequential and previous and ordered:
                # A shared old timestamp/system row somewhere in the viewport
                # is not proof of a gap-free transition. Require an ordered
                # suffix/prefix overlap between actual messages.
                if not any(previous[-size:] == ordered[:size]
                           for size in range(min(len(previous), len(ordered)), 0, -1)):
                    continuous = False
                    continuity_issues["viewport_overlap"] += 1
            page = []
            new_items = 0
            for identity, item in entries:
                if identity in seen:
                    continue
                seen.add(identity)
                new_items += 1
                row = parse_item(item)
                if row is None:
                    continue
                stamp = parse_time(row["消息发送时间"], end, ui_relative=True) if end else None
                if end:
                    if ascending and stamp and start and stamp.date() < start.date():
                        raise HistoryReadError("history_date_before_start", "日期定位返回了起始日之前的记录，已停止读取")
                    if stamp is None:
                        unknown += 1
                    else:
                        if last_stamp and (stamp < last_stamp if ascending else stamp > last_stamp):
                            continuous = False
                            continuity_issues["timestamp_order"] += 1
                        last_stamp = stamp
                        boundary = boundary or bool(start and stamp < start)
                        row["消息发送时间"] = stamp.isoformat()
                fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
                occurrences[fingerprint] += 1
                # Preserve separate identical messages within the same minute.
                # Runtime IDs are intentionally excluded from persistent identity.
                source = f"{fingerprint}\0{occurrences[fingerprint]}"
                row["source_id"] = "uia1:" + hashlib.sha256(source.encode()).hexdigest()
                page.append(row)
                all_rows.append(row)
                if len(all_rows) >= limit:
                    break
            if page and emit:
                pending.extend(page)
                if len(pending) >= getattr(driver, "batch_size", 1):
                    publish_pending()
            diag.event("read_history", rows=len(all_rows), unknown=unknown)
            if boundary and not ascending:
                reason = "boundary"
                break
            if not items:
                reason = "empty"
                break
            stagnant = 0 if new_items else stagnant + 1
            if stagnant >= 3:
                reason = "no_progress"
                break
            if ordered:
                previous = ordered
            if len(all_rows) >= limit:
                break
            diag.event("advance_history", rows=len(all_rows))
            driver.advance()
        reached_end = boundary and (not ascending or reason in {"no_progress", "empty"})
        publish_pending()
        return {"rows": all_rows, "coverage": bool(reached_end and continuous and not unknown),
                "continuous": continuous, "reason": reason, "continuity_issues": dict(continuity_issues),
                "batch_sizes": batch_sizes}
    finally:
        # Flush a short final batch, including a partial batch on cancellation
        # or a later UI failure. The same session/cursor continues across 100s.
        try:
            publish_pending()
        finally:
            # Closing a temporary history window must not erase successful batches.
            try:
                driver.close()
            except Exception:
                pass
