"""Local desktop lifecycle. No WeChat or map request is made on launch."""
import argparse
import json
import multiprocessing
import os
import socket
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

from .runtime import VERSION, database_path


def bind_local_socket(port=8765):
    for candidate in range(port, port + 20):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(('127.0.0.1', candidate))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
    raise RuntimeError('本地端口被占用，请关闭其他家教拾单窗口后重试')


def local_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=2) as response:
        return json.load(response)


class InstanceLock:
    """One process per data directory; OS releases the byte lock on crashes."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handle = None

    def acquire(self):
        import msvcrt
        self.handle = (self.directory / 'desktop.lock').open('a+b')
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b'0'); self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.handle.close(); self.handle = None
            return False

    def release(self):
        if self.handle:
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close(); self.handle = None


def start_server(db, port=8765):
    import uvicorn
    from .main import create_app
    app = create_app(db)
    instance = uuid.uuid4().hex

    @app.get('/desktop-health', include_in_schema=False)
    def health():
        return {'app': 'TutorAssistant', 'version': VERSION, 'instance': instance}

    sock = bind_local_socket(port)
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=sock.getsockname()[1],
        log_config=None, access_log=False, loop='asyncio', http='h11', ws='none', lifespan='off'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    worker.start()
    for _ in range(300):
        if server.started:
            return app, server, worker, sock, url, instance
        if not worker.is_alive():
            break
        time.sleep(.1)
    server.should_exit = True; sock.close()
    raise RuntimeError('本地页面启动失败，请重新打开程序')


def stop_server(app, server, worker, sock):
    app.state.service.cancel.set()
    app.state.service.set_wechat_authorization(False)
    app.state.service.travel.authorize(False)
    server.should_exit = True
    worker.join(timeout=5)
    sock.close()


def dependency_probe(output):
    """Spawn/import and DPAPI check only; never navigate or read WeChat."""
    import contextlib
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                from pyweixin import Navigator, Contacts
                from pyweixin.Uielements import Buttons
                import win32crypt
                encrypted = win32crypt.CryptProtectData(b'synthetic-package-check', None, None, None, None, 1)
                assert win32crypt.CryptUnprotectData(encrypted, None, None, None, 1)[1] == b'synthetic-package-check'
                output.put({'ok': True, 'wechat_import': bool(Navigator and Contacts and Buttons), 'dpapi': True})
            finally:
                pythoncom.CoUninitialize()
        except BaseException as exc:
            output.put({'ok': False, 'error_type': type(exc).__name__, 'detail': str(exc)[:500]})


def self_test(directory):
    """Packaging diagnostics run only in a new explicitly supplied directory."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ['TUTOR_DB'] = str(directory / 'tutor.sqlite3')
    result = {'version': VERSION, 'synthetic': True}
    instance = None
    try:
        import tkinter
        root = tkinter.Tk(); root.withdraw()
        result['tk_version'] = root.tk.call('info', 'patchlevel'); root.destroy()
        ctx = multiprocessing.get_context('spawn')
        output = ctx.Queue()
        child = ctx.Process(target=dependency_probe, args=(output,))
        child.start()
        try:
            result['dependencies'] = output.get(timeout=60)
        finally:
            child.join(timeout=5)
            if child.is_alive(): child.terminate(); child.join(timeout=5)
            output.close(); output.join_thread()
        assert result['dependencies']['ok'], result['dependencies']
        instance = start_server(database_path(), 18765)
        app, server, worker, sock, url, token = instance
        result['health'] = local_json(url + '/desktop-health')['instance'] == token
        state = local_json(url + '/api/state')
        result['no_ai'] = not state['model']['enabled'] and not state['model']['ai_enabled']
        result['no_wechat_authorization'] = not state['wechat_authorization']['authorized']
        result['no_map_authorization'] = not local_json(url + '/api/travel')['authorized']
        result['zero_map_requests'] = local_json(url + '/api/travel')['requests_current_run'] == 0
        result['empty_orders'] = local_json(url + '/api/orders')['orders'] == []
        result['regions'] = len(local_json(url + '/api/regions')['provinces']) == 34
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for route in ('/', '/static/app.css', '/static/app.js', '/static/travel.js', '/guide'):
            with opener.open(url + route) as response:
                assert response.status == 200 and len(response.read()) > 100
        result['resources'] = True
        result['ok'] = all(result[k] for k in ('health','no_ai','no_wechat_authorization',
            'no_map_authorization','zero_map_requests','empty_orders','regions','resources'))
    except BaseException as exc:
        result.update(ok=False, error_type=type(exc).__name__, detail=str(exc)[:500])
    finally:
        if instance: stop_server(*instance[:4])
        (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.get('ok') else 1


def main():
    parser = argparse.ArgumentParser(description='家教拾单 Windows 启动器')
    parser.add_argument('--self-test', metavar='NEW_DIRECTORY')
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器（诊断用）')
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(self_test(args.self_test))
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw()
    lock, instance, runtime_file = None, None, None
    try:
        db = database_path()
        os.environ['TUTOR_DB'] = str(db)
        lock = InstanceLock(db.parent)
        runtime_file = db.parent / 'desktop-session.json'
        if not lock.acquire():
            for _ in range(50):
                try:
                    saved = json.loads(runtime_file.read_text(encoding='utf-8'))
                    port = int(saved['port'])
                    assert 1024 <= port <= 65535
                    url = 'http://127.0.0.1:' + str(port)
                    health = local_json(url + '/desktop-health')
                    if health['app'] == 'TutorAssistant' and health['instance'] == saved['instance']:
                        if not args.no_browser: webbrowser.open(url)
                        return
                except (OSError, ValueError, KeyError, AssertionError):
                    time.sleep(.1)
            messagebox.showinfo('家教拾单', '程序已经在运行，请从已有启动窗口打开页面。', parent=root)
            return
        instance = start_server(db)
        app, server, worker, sock, url, token = instance
        runtime_file.write_text(json.dumps({'port': sock.getsockname()[1], 'instance': token}), encoding='utf-8')
        root.title('家教拾单 ' + VERSION); root.geometry('440x240'); root.resizable(False, False)
        root.configure(bg='#f6f8f7')
        tk.Label(root, text='家教拾单已启动', bg='#f6f8f7', fg='#104b3e', font=('Microsoft YaHei UI', 18, 'bold')).pack(pady=(24,8))
        tk.Label(root, text=url, bg='#f6f8f7', fg='#182d28', font=('Segoe UI', 11)).pack()
        tk.Label(root, text='关闭此窗口将退出；数据会保留。', bg='#f6f8f7', fg='#586b64', font=('Microsoft YaHei UI', 10)).pack(pady=10)
        actions = tk.Frame(root, bg='#f6f8f7'); actions.pack(pady=8)
        tk.Button(actions, text='打开页面', command=lambda: webbrowser.open(url), padx=18, pady=5).pack(side='left', padx=6)
        tk.Button(actions, text='打开数据目录', command=lambda: os.startfile(db.parent), padx=12, pady=5).pack(side='left', padx=6)
        tk.Button(actions, text='退出', command=root.quit, padx=12, pady=5).pack(side='left', padx=6)
        root.protocol('WM_DELETE_WINDOW', root.quit)
        root.deiconify()
        if not args.no_browser: webbrowser.open(url)
        root.mainloop()
    except Exception:
        messagebox.showerror('无法启动家教拾单', '本地页面未能启动。请确认文件已完整解压，且用户数据目录可写，然后重新打开。', parent=root)
    finally:
        if instance:
            stop_server(*instance[:4])
            if runtime_file: runtime_file.unlink(missing_ok=True)
        if lock: lock.release()
        root.destroy()
