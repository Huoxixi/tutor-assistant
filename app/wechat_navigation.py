"""Version-independent, process-bound chat navigation. Never sends messages."""
import time

from .history_reader import HistoryReadError

SEARCH_NAMES = {'搜索', '搜尋', 'Search'}
CHAT_TITLE_ID = 'content_view.top_content_view.title_h_view.left_v_view.left_content_v_view.left_ui_.big_title_line_h_view.current_chat_name_label'


def search_editor(main, stopped=lambda: False):
    """Support both resident and collapsed search inputs, with no coordinates."""
    def editors():
        return [item for item in main.descendants(control_type='Edit', class_name='mmui::XValidatorTextEdit')
                if item.is_visible() and item.is_enabled()]
    found = editors()
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise HistoryReadError('wechat_search_ambiguous', '微信搜索输入框不唯一，已停止以免打开错误聊天')
    buttons = [item for item in main.descendants(control_type='Button')
               if item.window_text() in SEARCH_NAMES and item.is_visible() and item.is_enabled()]
    if len(buttons) != 1:
        raise HistoryReadError('wechat_search_unavailable', '未识别到微信搜索入口，请提供本机兼容性诊断')
    if stopped():
        raise HistoryReadError('read_cancelled', '扫描已取消，已读记录保留')
    buttons[0].click_input()
    deadline = time.monotonic()+3
    while time.monotonic() < deadline and not stopped():
        found = editors()
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            break
        time.sleep(.1)
    raise HistoryReadError('wechat_search_unavailable', '微信搜索输入框未展开，请重试或提供本机兼容性诊断')


def exact_result(items, group):
    matches = [item for item in items if item.class_name() == 'mmui::SearchContentCellView'
               and item.window_text() == group]
    if len(matches) != 1:
        raise HistoryReadError('group_not_found' if not matches else 'group_ambiguous',
            '未找到完整群名匹配的搜索结果' if not matches else '发现多个同名聊天，已停止读取，请在微信中区分名称后重试')
    return matches[0]


class ReadNavigator:
    def __init__(self, stopped=lambda: False):
        self.stopped = stopped

    def open_dialog_window(self, friend, is_maximize=True, search_pages=0):
        import psutil
        import win32gui
        import win32process
        from pywinauto import Desktop
        desktop = Desktop(backend='uia')
        main_windows = []
        def visit(hwnd, _):
            native = win32gui.GetClassName(hwnd)
            if not (native.startswith('Qt') and native.endswith('QWindowIcon')):
                return
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            try:
                if psutil.Process(pid).name().lower() != 'weixin.exe':
                    return
                window = desktop.window(handle=hwnd)
                if window.class_name() == 'mmui::MainWindow':
                    main_windows.append(window)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return
        win32gui.EnumWindows(visit, None)
        if len(main_windows) != 1:
            raise HistoryReadError('wechat_main_window_unavailable', '未找到唯一的可访问微信主窗口，请保持一个已登录微信主窗口后重试')
        main = main_windows[0]
        def check_cancelled():
            if self.stopped():
                raise HistoryReadError('read_cancelled', '扫描已取消，已读记录保留')
        check_cancelled()
        main.restore()
        if is_maximize:
            main.maximize()
        main.set_focus()
        title = main.child_window(auto_id=CHAT_TITLE_ID, control_type='Text', title=friend)
        if title.exists(timeout=.2):
            return main
        editor = search_editor(main, self.stopped)
        check_cancelled()
        editor.click_input()
        editor.set_text(friend)
        results = main.child_window(auto_id='search_list', control_type='List')
        deadline = time.monotonic()+5
        items = []
        while time.monotonic() < deadline:
            check_cancelled()
            if results.exists(timeout=.1):
                items = results.children(control_type='ListItem')
                if any(item.class_name() == 'mmui::SearchContentCellView' and item.window_text() == friend for item in items):
                    break
            time.sleep(.1)
        result = exact_result(items, friend)
        check_cancelled()
        result.click_input()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            check_cancelled()
            if title.exists(timeout=.1):
                return main
            time.sleep(.1)
        raise HistoryReadError('wechat_chat_unconfirmed', '未能确认已打开目标群，已停止读取')
