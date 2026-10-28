"""Start/reuse a local session and show it in an independent browser window."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = ROOT / '.local-window' / 'sessions' / 'first-try'


def connect(session):
    info = json.loads((Path(session) / 'connection.json').read_text(encoding='utf-8'))
    if Path(info['session']).resolve() != Path(session).resolve():
        raise ValueError('当前连接不属于所选工程。')
    request = Request(f"http://127.0.0.1:{int(info['port'])}/api/state",
                      headers={'X-Timing-Token': info['token']})
    with urlopen(request, timeout=2) as response:
        state = json.load(response)
    return info, state


def start(session, project=None):
    session = Path(session).resolve()
    if project is not None and (session / 'head.json').exists():
        raise ValueError('--project 只用于初始化新会话。请用新的 --session，或通过协作接口修改当前工程。')
    try:
        info, _ = connect(session)
        return info
    except (OSError, ValueError, KeyError):
        pass
    session.mkdir(parents=True, exist_ok=True)
    temp = session / 'temp'
    temp.mkdir(exist_ok=True)
    environment = dict(os.environ, TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', str(ROOT / 'local_window' / 'server.py'), '--session', str(session)]
    if project:
        command.extend(['--project', str(Path(project).resolve())])
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (session / 'server.log').open('ab', buffering=0) as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, close_fds=True, **kwargs)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # Another launch may have acquired the same session lock first.
            try:
                info, _ = connect(session)
                return info
            except (OSError, ValueError, KeyError):
                raise RuntimeError(f'后台启动失败，请查看 {session / "server.log"}')
        try:
            info, _ = connect(session)
            return info
        except (OSError, ValueError, KeyError):
            time.sleep(0.15)
    raise RuntimeError(f'后台启动超时，请查看 {session / "server.log"}')


def find_browser(browser=None):
    """Resolve an explicit browser, Windows installations, or PATH, without running it."""
    if browser is not None:
        candidate = Path(browser).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(str(browser))
        if found:
            return Path(found).resolve()
        raise ValueError(f'找不到指定的浏览器：{browser}')

    roots = [os.environ.get(name) for name in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')]
    if os.name == 'nt':
        roots.extend(('C:/Program Files (x86)', 'C:/Program Files'))
    for root in filter(None, roots):
        for relative in ('Microsoft/Edge/Application/msedge.exe',
                         'Google/Chrome/Application/chrome.exe',
                         'Chromium/Application/chrome.exe'):
            candidate = Path(root) / relative
            if candidate.is_file():
                return candidate.resolve()
    for name in ('msedge', 'chrome', 'chromium', 'chromium-browser', 'google-chrome'):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def open_window(info, browser=None, browser_data=None):
    browser = find_browser(browser)
    if browser is None:
        raise RuntimeError('未找到 Edge 或 Chrome。后台已就绪，请安装或指定支持应用窗口的浏览器。')
    # Legacy launchers retain their shared profile. Portable callers provide
    # a location inside the selected session instead of writing into the app.
    base = Path(browser_data).resolve() if browser_data is not None else None
    profile = base / 'profile' if base is not None else ROOT / '.local-window' / 'browser-profile'
    profile.mkdir(parents=True, exist_ok=True)
    temporary = base / 'temp' if base is not None else ROOT / '.local-window' / 'browser-temp'
    temporary.mkdir(exist_ok=True)
    url = f"http://127.0.0.1:{info['port']}/#token={info['token']}"
    args = [str(browser), '--app=' + url, '--user-data-dir=' + str(profile),
            '--no-first-run', '--no-default-browser-check', '--disable-background-mode',
            '--disable-features=msEdgeSidebarV2', '--window-size=1440,960']
    # A visible browser window is the explicitly requested user interface.
    window_options = {}
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 1  # Win32 SW_SHOWNORMAL is not exported by subprocess.
        window_options['startupinfo'] = startup
    subprocess.Popen(args, cwd=ROOT, env=dict(os.environ, TEMP=str(temporary), TMP=str(temporary)),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **window_options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, default=DEFAULT_SESSION)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--browser-data', type=Path)
    args = parser.parse_args()
    info = start(args.session, args.project)
    if not args.no_open:
        open_window(info, browser=args.browser, browser_data=args.browser_data)
    print(json.dumps({'session': info['session'], 'port': info['port'], 'pid': info['pid'],
                      'window_requested': not args.no_open}, ensure_ascii=False))


if __name__ == '__main__':
    main()
