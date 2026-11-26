from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import threading
import time
import traceback
import atexit
from datetime import datetime
from pathlib import Path
from typing import TextIO


ENTRY_FILENAME = "bot_v76_00_three_stage_ctrlv_paper.py"
INITIAL_ROOT = Path(__file__).resolve().parent


def _entry_exists(source_root: Path) -> bool:
    try:
        return (source_root / "core" / ENTRY_FILENAME).is_file()
    except OSError:
        return False


def _source_candidate(folder: Path) -> Path | None:
    if _entry_exists(folder):
        return folder
    try:
        source = folder / "SOURCE"
    except TypeError:
        return None
    if _entry_exists(source):
        return source
    return None


def _search_v76_sources(folder: Path, depth: int) -> Path | None:
    if depth < 0:
        return None
    found = _source_candidate(folder)
    if found is not None:
        return found
    try:
        children = list(folder.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        name = child.name.upper()
        if not (name.startswith("V76_") or name == "SOURCE"):
            continue
        found = _search_v76_sources(child, depth - 1)
        if found is not None:
            return found
    return None


def _resolve_source_root(initial_root: Path) -> Path:
    """Recover from nested or partial extractions by finding the valid SOURCE."""

    found = _source_candidate(initial_root)
    if found is not None:
        return found
    package_root = initial_root.parent if initial_root.name.upper() == "SOURCE" else initial_root
    found = _search_v76_sources(package_root, 4)
    if found is not None:
        return found
    current = package_root
    for _ in range(5):
        parent = current.parent
        if parent == current:
            break
        found = _source_candidate(parent)
        if found is not None:
            return found
        try:
            siblings = list(parent.iterdir())
        except OSError:
            siblings = []
        for sibling in siblings:
            try:
                if not sibling.is_dir() or not sibling.name.upper().startswith("V76_"):
                    continue
            except OSError:
                continue
            found = _search_v76_sources(sibling, 3)
            if found is not None:
                return found
        current = parent
    return initial_root


ROOT = _resolve_source_root(INITIAL_ROOT)
CORE = ROOT / "core"
LOG_DIR = ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "v75_73_dashboard.log"
STARTUP_STATUS = LOG_DIR / "v76_02_startup_status.txt"
PARENT_STARTUP_STATUS = LOG_DIR / "v75_95_startup_status.txt"
PID_PATH = LOG_DIR / "v76_dashboard.pid"
TARGET = CORE / ENTRY_FILENAME
CREDENTIAL_POINTER = Path.home() / ".v75_last_credentials_path.txt"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))


class Tee:
    """Keep the visible diagnostic console and the dashboard log identical."""

    def __init__(self, *streams: TextIO) -> None:
        self.streams = streams

    def write(self, value: str) -> int:
        for stream in self.streams:
            try:
                stream.write(value)
            except Exception:
                pass
        return len(value)

    def flush(self) -> None:
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return any(bool(getattr(stream, "isatty", lambda: False)()) for stream in self.streams)


def write_status(stage: str, detail: str = "") -> None:
    text = f"{datetime.now().isoformat(timespec='seconds')}\nSTAGE={stage}\n{detail}\n"
    try:
        STARTUP_STATUS.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _read_status(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _status_text() -> str:
    return _read_status(STARTUP_STATUS) + "\n" + _read_status(PARENT_STARTUP_STATUS)


def _clear_stale_parent_startup_status() -> None:
    """Do not treat a previous dashboard close as this launch's GUI failure."""

    try:
        PARENT_STARTUP_STATUS.unlink(missing_ok=True)
    except OSError:
        pass


def _write_pid() -> None:
    try:
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _clear_pid() -> None:
    try:
        if PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_previous_v76_processes() -> None:
    """Stop stale V76 dashboard Python processes before starting a new one.

    This intentionally targets only Python/py processes whose command line is
    clearly this V76 dashboard family.  It avoids the current Python process
    and its py.exe parent, so other Python work is left alone.
    """

    if sys.platform != "win32" or os.getenv("V76_SKIP_STALE_CLEANUP"):
        return
    current_pid = os.getpid()
    script = rf"""
$current = {current_pid}
$currentProc = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
$parent = if ($currentProc) {{ [int]$currentProc.ParentProcessId }} else {{ 0 }}
$targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {{
    $_.ProcessId -ne $current -and $_.ProcessId -ne $parent -and
    $_.Name -match '^(python|pythonw|py)\.exe$' -and
    $_.CommandLine -and
    ($_.CommandLine -match 'bot_v76_00_three_stage_ctrlv_paper\.py|launcher\.py') -and
    ($_.CommandLine -match 'V76_|v76_|CLICK_ARROW_0600|ZIP_SAFE|SAVED_0600|THREE_STAGE_CTRLV')
}}
foreach ($p in $targets) {{
    try {{ Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }} catch {{ }}
}}
"""
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except Exception as exc:
        try:
            with LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(f"[{datetime.now().isoformat(timespec='seconds')}] V76_STALE_CLEANUP_SKIP {type(exc).__name__}: {exc}\n")
        except OSError:
            pass


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeError):
        return {}
    return values


def credential_values(values: dict[str, str]) -> tuple[str, str, str, str]:
    """Read supported Kiwoom key names without exposing or persisting values."""

    token = values.get("KIWOOM_ACCESS_TOKEN", "").strip()
    app = (values.get("KIWOOM_APP_KEY", "") or values.get("APP_KEY", "")).strip()
    secret = (
        values.get("KIWOOM_SECRET_KEY", "")
        or values.get("KIWOOM_APP_SECRET", "")
        or values.get("APP_SECRET", "")
    ).strip()
    base_url = (values.get("KIWOOM_BASE_URL", "") or values.get("BASE_URL", "")).strip()
    return token, app, secret, base_url


def credential_priority(values: dict[str, str]) -> int:
    """Prefer Kiwoom-named credentials over generic REST test variables."""

    if values.get("KIWOOM_APP_KEY", "").strip() and (
        values.get("KIWOOM_SECRET_KEY", "").strip()
        or values.get("KIWOOM_APP_SECRET", "").strip()
    ):
        return 3
    if values.get("APP_KEY", "").strip() and values.get("APP_SECRET", "").strip():
        return 2
    if values.get("KIWOOM_ACCESS_TOKEN", "").strip():
        return 1
    return 0


def install_credentials(values: dict[str, str]) -> bool:
    """Install one complete credential set in process memory only."""

    token, app, secret, base_url = credential_values(values)
    if not token and not (app and secret):
        return False
    current_app = os.getenv("KIWOOM_APP_KEY", "").strip()
    current_secret = (os.getenv("KIWOOM_SECRET_KEY", "") or os.getenv("KIWOOM_APP_SECRET", "")).strip()
    current_base = os.getenv("KIWOOM_BASE_URL", "").strip().rstrip("/")
    # Never combine a partial credential pair or an explicit server with a
    # different source. An existing token is retained for account verification.
    if app and secret and ((current_app and current_app != app) or
                           (current_secret and current_secret != secret)):
        return False
    if current_base and base_url and current_base != base_url.rstrip("/"):
        return False
    if token and not os.getenv("KIWOOM_ACCESS_TOKEN", "").strip():
        os.environ["KIWOOM_ACCESS_TOKEN"] = token
    if app and secret:
        os.environ["KIWOOM_APP_KEY"] = app
        os.environ["KIWOOM_SECRET_KEY"] = secret
    if base_url and not os.getenv("KIWOOM_BASE_URL", "").strip():
        os.environ["KIWOOM_BASE_URL"] = base_url
    return True


def recover_existing_credentials() -> str:
    """Find a complete key set from an earlier sibling package.

    Secret values are placed only in this process environment.  They are
    never copied into the ZIP and never written to the startup log.
    """

    # A token string is not proof of validity and cannot renew itself. Keep it,
    # but also recover a complete key pair for the client's bounded refresh.
    if os.getenv("KIWOOM_APP_KEY", "").strip() and (
        os.getenv("KIWOOM_SECRET_KEY", "").strip()
        or os.getenv("KIWOOM_APP_SECRET", "").strip()
    ):
        install_credentials(dict(os.environ))
        return "environment"

    # Fast path after the first successful discovery.  The file stores only
    # the path, never a key value, so subsequent starts avoid a Desktop scan.
    try:
        cached_path = Path(CREDENTIAL_POINTER.read_text(encoding="utf-8").strip())
        cached = parse_env_file(cached_path) if cached_path.is_file() else {}
    except (OSError, UnicodeError):
        cached_path, cached = Path(), {}
    # A cached generic APP_KEY/APP_SECRET file may be an unrelated REST test
    # file.  Keep it only as a fallback while searching for explicit Kiwoom
    # credentials first.
    if credential_priority(cached) == 3 and install_credentials(cached):
        return "cached-pointer"

    candidates = [
        cached_path,
        CORE / "v73_keys.env",
        ROOT / "v73_keys.env",
        CORE / ".env",
        ROOT / ".env",
        Path.home() / "Downloads" / "v73_keys.env",
        Path.home() / "다운로드" / "v73_keys.env",
        Path.home() / "Desktop" / "코딩관련파일들" / "키움REST" / ".env",
        Path.home() / "Desktop" / "기타" / ".env",
    ]
    search_roots = (ROOT.parent.parent, ROOT.parent.parent.parent)
    patterns = (
        "*/SOURCE/core/v73_keys.env",
        "*/SOURCE/v73_keys.env",
        "*/*/SOURCE/core/v73_keys.env",
        "*/*/SOURCE/v73_keys.env",
        "*/*/*/SOURCE/core/v73_keys.env",
        "*/*/*/SOURCE/v73_keys.env",
    )
    for search_root in search_roots:
        try:
            for pattern in patterns:
                candidates.extend(search_root.glob(pattern))
        except OSError:
            continue

    unique: dict[str, Path] = {}
    for path in candidates:
        try:
            if path.is_file():
                unique[str(path.resolve()).casefold()] = path
        except OSError:
            continue
    existing = list(unique.values())
    parsed = {path: parse_env_file(path) for path in existing}
    existing.sort(
        key=lambda path: (credential_priority(parsed[path]), path.stat().st_mtime),
        reverse=True,
    )

    for path in existing:
        values = parsed[path]
        if not install_credentials(values):
            continue
        try:
            CREDENTIAL_POINTER.write_text(str(path.resolve()), encoding="utf-8")
        except OSError:
            pass
        return "recovered-sibling"
    return "environment" if os.getenv("KIWOOM_ACCESS_TOKEN", "").strip() else "not-found"


def show_error(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except Exception:
        pass


def read_launch_segment(offset: int) -> str:
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as source:
            source.seek(offset)
            return source.read()
    except OSError:
        return ""


def dashboard_watchdog(offset: int, finished: threading.Event) -> None:
    """Never leave a failed hidden GUI process looking like 'nothing happened'."""

    deadline = time.monotonic() + 120.0
    prompt_seen = False
    while not finished.wait(0.5):
        segment = read_launch_segment(offset)
        status_text = _status_text()
        if "[3/3] 대시보드 표시 완료" in segment or "STAGE=DASHBOARD_VISIBLE" in status_text:
            write_status("DASHBOARD_VISIBLE", "대시보드 mainloop 진입 확인")
            return
        if "[설정] API 키 입력창 표시 완료" in segment or "STAGE=CREDENTIAL_PROMPT_VISIBLE" in status_text:
            prompt_seen = True
            deadline = max(deadline, time.monotonic() + 600.0)
            write_status("CREDENTIAL_PROMPT_VISIBLE", "키 입력창에서 저장하고 실행을 누르십시오")
        failure = ""
        for marker in ("GUI 시작 실패:", "Exception in thread", "설정 오류:", "인증 오류:"):
            if marker in segment:
                failure = marker
                break
        if "STAGE=GUI_THREAD_FAILED" in status_text:
            failure = "GUI_THREAD_FAILED"
        if failure and not prompt_seen:
            write_status("GUI_FAILED", f"로그에서 {failure} 발견\n{LOG_PATH}")
            show_error(
                "V76 dashboard start failed",
                f"Dashboard creation failed ({failure}).\n\nLog: {LOG_PATH}\nStatus: {STARTUP_STATUS}",
            )
            os._exit(86)
        if time.monotonic() >= deadline:
            stage = "CREDENTIAL_WAIT_TIMEOUT" if prompt_seen else "GUI_START_TIMEOUT"
            write_status(stage, f"로그를 확인하십시오\n{LOG_PATH}")
            show_error(
                "V76 dashboard start timeout",
                f"The dashboard did not become visible.\n\nLog: {LOG_PATH}\nStatus: {STARTUP_STATUS}",
            )
            os._exit(87)


def dashboard_marker_seen(offset: int) -> bool:
    if "[3/3] 대시보드 표시 완료" in read_launch_segment(offset):
        return True
    return "STAGE=DASHBOARD_VISIBLE" in _status_text()


def main() -> int:
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with LOG_PATH.open("a", encoding="utf-8", buffering=1) as log:
        try:
            offset = LOG_PATH.stat().st_size
        except OSError:
            offset = 0
        sys.stdout = Tee(original_stdout, log)
        sys.stderr = Tee(original_stderr, log)
        print("\n" + "=" * 72, flush=True)
        print(datetime.now().isoformat(timespec="seconds"), "V76.00 three-stage Ctrl+V PAPER start", flush=True)
        _clear_stale_parent_startup_status()
        write_status("LAUNCHER_ENTERED", str(TARGET))
        if ROOT != INITIAL_ROOT:
            print(f"[0/3] source root auto-repaired: {INITIAL_ROOT} -> {ROOT}", flush=True)
            write_status("SOURCE_ROOT_AUTO_REPAIRED", f"{INITIAL_ROOT}\n{ROOT}")
        cleanup_previous_v76_processes()
        _write_pid()
        atexit.register(_clear_pid)
        finished = threading.Event()
        monitor = threading.Thread(target=dashboard_watchdog, args=(offset, finished), daemon=True)
        monitor.start()
        try:
            credential_source = recover_existing_credentials()
            credential_label = {
                "environment": "current environment",
                "cached-pointer": "cached previous key path (fast)",
                "recovered-sibling": "previous V75 folder",
                "not-found": "base key prompt/standard search",
            }[credential_source]
            print(f"[0/3] credentials: {credential_label}", flush=True)
            write_status("CREDENTIAL_RECOVERY_DONE", credential_source)

            if not TARGET.exists():
                raise FileNotFoundError(f"entry file missing: {TARGET}")
            print("[1/3] entry confirmed", flush=True)
            print("[2/3] loading engine and dashboard", flush=True)
            write_status("ENGINE_IMPORT_STARTED", str(TARGET))
            sys.argv = [str(TARGET), *sys.argv[1:]]
            runpy.run_path(str(TARGET), run_name="__main__")

            if not dashboard_marker_seen(offset):
                write_status("ENGINE_RETURNED_WITHOUT_DASHBOARD", str(LOG_PATH))
                show_error(
                    "V76 dashboard did not open",
                    f"The engine ended before the dashboard became visible.\n\nLog: {LOG_PATH}\nStatus: {STARTUP_STATUS}",
                )
                return 88
            return 0
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
            if code and not dashboard_marker_seen(offset):
                write_status("SYSTEM_EXIT_BEFORE_DASHBOARD", f"exit={code}\n{LOG_PATH}")
                show_error(
                    "V76 dashboard stopped",
                    f"The program stopped before the dashboard opened. Exit={code}\n\nLog: {LOG_PATH}",
                )
            return code
        except Exception as exc:
            traceback.print_exc()
            write_status("UNHANDLED_STARTUP_ERROR", f"{type(exc).__name__}: {exc}\n{LOG_PATH}")
            show_error(
                "V76 startup error",
                f"{type(exc).__name__}: {exc}\n\nLog: {LOG_PATH}\nStatus: {STARTUP_STATUS}",
            )
            return 89
        finally:
            finished.set()
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    raise SystemExit(main())
