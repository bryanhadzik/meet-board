"""Self-update from GitHub Releases (Windows exe build only).

The GitHub Actions workflow publishes each build as a release with
meet-board.exe attached. The Update button:
  1. downloads the new exe next to the running one (meet-board.exe.new)
  2. writes a small .cmd that waits for this process to exit, swaps the files
     and starts the new exe with the same arguments
  3. exits this process

settings.json sits beside the exe and is untouched, so settings survive.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from _version import __version__
from app_paths import FROZEN

REPO = os.environ.get('MEET_BOARD_REPO', 'bryanhadzik/meet-board')
ASSET_NAME = 'meet-board.exe'
API_LATEST = 'https://api.github.com/repos/%s/releases/latest'

_status = {'state': 'idle', 'message': '', 'progress': 0}


def _version_tuple(v):
    nums = re.findall(r'\d+', v or '')
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def is_newer(latest, current=__version__):
    return _version_tuple(latest) > _version_tuple(current)


def find_token(token=None):
    """GitHub token for a private repo: explicit setting, GITHUB_TOKEN, or the
    GitHub CLI's login (`gh auth token`) if gh is installed. None if public
    access is all we have."""
    if token:
        return token
    if os.environ.get('GITHUB_TOKEN'):
        return os.environ['GITHUB_TOKEN']
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        out = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True,
                             timeout=10, creationflags=flags)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _request(url, token=None, accept='application/vnd.github+json'):
    headers = {'Accept': accept, 'User-Agent': 'meet-board/%s' % __version__}
    if not token:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20)
    # With a token, follow redirects by hand: GitHub sends asset downloads to a
    # signed storage URL that rejects requests still carrying Authorization.
    headers['Authorization'] = 'Bearer %s' % token
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(urllib.request.Request(url, headers=headers), timeout=20)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get('Location'):
            return urllib.request.urlopen(urllib.request.Request(
                e.headers['Location'], headers={'User-Agent': headers['User-Agent']}), timeout=60)
        raise


def check(token=None):
    """Return {current, latest, newer, asset_url, notes, published, can_update, error}."""
    info = {'current': __version__, 'latest': '', 'newer': False, 'asset_url': '',
            'notes': '', 'published': '', 'can_update': FROZEN and sys.platform == 'win32',
            'repo': REPO, 'error': ''}
    token = find_token(token)
    try:
        with _request(API_LATEST % REPO, token) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            info['error'] = ('No release found for %s. Either nothing has been published yet, or the repo '
                             'is private and no GitHub token is available (sign in with "gh auth login" '
                             'or set github_token in settings.json).' % REPO)
        else:
            info['error'] = 'GitHub returned %s' % e
        return info
    except Exception as e:  # network down at the pool is normal
        info['error'] = 'Could not reach GitHub: %s' % e
        return info
    info['latest'] = rel.get('tag_name', '').lstrip('v')
    info['notes'] = rel.get('body', '') or ''
    info['published'] = rel.get('published_at', '') or ''
    for a in rel.get('assets', []):
        if a.get('name', '').lower() == ASSET_NAME:
            # API url works for private repos with a token; browser url for public
            info['asset_url'] = a.get('url') if token else a.get('browser_download_url')
    info['newer'] = is_newer(info['latest'])
    if not info['asset_url']:
        info['error'] = 'Latest release has no %s attached' % ASSET_NAME
    return info


def status():
    return dict(_status)


def _swap_script(exe, new_exe, pid, args):
    arg_str = subprocess.list2cmdline(args)
    # Plain goto loops (no parenthesised blocks) so %vars% re-expand each pass.
    return '\r\n'.join([
        '@echo off',
        'title meet-board updater',
        'echo Waiting for meet-board to close...',
        ':wait',
        'tasklist /FI "PID eq %d" 2>nul | find "%d" >nul' % (pid, pid),
        'if errorlevel 1 goto closed',
        'timeout /t 1 /nobreak >nul',
        'goto wait',
        ':closed',
        'set /a tries=0',
        ':swap',
        'move /y "%s" "%s" >nul 2>&1' % (new_exe, exe),
        'if not exist "%s" goto launch' % new_exe,
        'set /a tries+=1',
        'if %tries% geq 30 goto launch',
        'timeout /t 1 /nobreak >nul',
        'goto swap',
        ':launch',
        'start "" "%s" %s' % (exe, arg_str),
        '(goto) 2>nul & del "%~f0"',
        '',
    ])


def _run_update(asset_url, token, on_exit):
    try:
        exe = os.path.abspath(sys.executable)
        new_exe = exe + '.new'
        _status.update(state='downloading', message='Downloading update...', progress=0)
        accept = 'application/octet-stream'
        with _request(asset_url, token, accept=accept) as r, open(new_exe, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    _status['progress'] = int(done * 100 / total)
        if os.path.getsize(new_exe) < 1024 * 1024:
            raise RuntimeError('Downloaded file is too small - not an exe?')
        with open(new_exe, 'rb') as f:
            if f.read(2) != b'MZ':
                raise RuntimeError('Downloaded file is not a Windows exe')

        script = os.path.join(tempfile.gettempdir(), 'meet-board-update-%d.cmd' % os.getpid())
        with open(script, 'w') as f:
            f.write(_swap_script(exe, new_exe, os.getpid(), sys.argv[1:]))
        _status.update(state='restarting', message='Restarting with the new version...', progress=100)
        flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
        subprocess.Popen(['cmd.exe', '/c', script], creationflags=flags, close_fds=True)
        time.sleep(1.0)  # let the browser get the status response
        on_exit()
    except Exception as e:
        _status.update(state='error', message='Update failed: %s' % e)
        try:
            os.remove(exe + '.new')
        except Exception:
            pass


def start_update(token=None, on_exit=None):
    """Kick off download+swap in the background. Returns (ok, message)."""
    if not (FROZEN and sys.platform == 'win32'):
        return False, 'Running from source - update with "git pull" instead.'
    if _status['state'] in ('downloading', 'restarting'):
        return False, 'Update already in progress.'
    token = find_token(token)
    info = check(token)
    if info['error']:
        return False, info['error']
    if not info['newer']:
        return False, 'Already on the latest version (%s).' % info['current']
    on_exit = on_exit or (lambda: os._exit(0))
    threading.Thread(target=_run_update, args=(info['asset_url'], token, on_exit), daemon=True).start()
    return True, 'Updating %s -> %s' % (info['current'], info['latest'])
