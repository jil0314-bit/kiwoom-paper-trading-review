from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "SOURCE"
CORE_ROOT = SOURCE_ROOT / "core"
REAL_RUNTIME_ROOTS = tuple(
    sorted(
        path.resolve()
        for path in SOURCE_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("runtime")
    )
)
REAL_RUNTIME = SOURCE_ROOT / "runtime_v7592"
ENTRY_FILE = "bot_v76_00_three_stage_ctrlv_paper.py"

SUITE_MODULES = (
    "tests.test_state_manager",
    "tests.test_state_invariants",
    "tests.test_order_idempotency",
    "tests.test_evidence_integrity",
    "tests.test_replay_003",
    "tests.test_replay_case_001",
)

EXPECTED_COUNTS = {
    "tests.test_state_manager": 13,
    "tests.test_state_invariants": 7,
    "tests.test_order_idempotency": 7,
    "tests.test_evidence_integrity": 7,
    "tests.test_replay_003": 7,
    "tests.test_replay_case_001": 8,
}

SITE_CUSTOMIZE = r'''
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import builtins


REAL_RUNTIMES = tuple(
    Path(value).resolve()
    for value in __import__("json").loads(
        os.environ["V7609_HARNESS_REAL_RUNTIMES"]
    )
)
SANDBOX_ROOT = Path(os.environ["V7609_HARNESS_SANDBOX_ROOT"]).resolve()
TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
builtins.V7609_HARNESS_BLOCKED_EVENTS = []


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _path(value):
    if isinstance(value, int):
        return None
    try:
        return Path(value).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return None


def _write_open(mode, flags) -> bool:
    text = str(mode or "")
    if any(marker in text for marker in ("w", "a", "x", "+")):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return bool(flags & write_flags)
    return False


def _deny_real_runtime(path: Path) -> None:
    if any(_inside(path, root) for root in REAL_RUNTIMES):
        builtins.V7609_HARNESS_BLOCKED_EVENTS.append(f"runtime:{path}")
        raise PermissionError(f"HARNESS_REAL_RUNTIME_BLOCKED:{path}")


def _audit(event, args):
    if event == "open" and args:
        path = _path(args[0])
        if path is None:
            return
        _deny_real_runtime(path)
        mode = args[1] if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 else 0
        if _write_open(mode, flags) and not (
            _inside(path, SANDBOX_ROOT) or _inside(path, TEMP_ROOT)
        ):
            raise PermissionError(f"HARNESS_OUTSIDE_WRITE_BLOCKED:{path}")
    elif event in {
        "socket.connect", "socket.connect_ex", "socket.bind",
        "socket.getaddrinfo", "urllib.Request",
    }:
        builtins.V7609_HARNESS_BLOCKED_EVENTS.append(f"network:{event}")
        raise RuntimeError(f"HARNESS_NETWORK_BLOCKED:{event}")
    elif event in {"subprocess.Popen", "os.system"}:
        builtins.V7609_HARNESS_BLOCKED_EVENTS.append(f"process:{event}")
        raise RuntimeError(f"HARNESS_PROCESS_BLOCKED:{event}")
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename"}:
        for value in args[:2]:
            path = _path(value)
            if path is not None:
                _deny_real_runtime(path)


sys.addaudithook(_audit)


def _network_blocked(*_args, **_kwargs):
    builtins.V7609_HARNESS_BLOCKED_EVENTS.append("network:socket")
    raise RuntimeError("HARNESS_NETWORK_BLOCKED")


socket.socket.connect = _network_blocked
socket.socket.connect_ex = _network_blocked
socket.create_connection = _network_blocked


def _process_exit_blocked(code=0):
    raise RuntimeError(f"HARNESS_OS_EXIT_BLOCKED:{code}")


os._exit = _process_exit_blocked


def _thread_start_blocked(self, *args, **kwargs):
    del self, args, kwargs
    builtins.V7609_HARNESS_BLOCKED_EVENTS.append("thread:start")
    raise RuntimeError("HARNESS_THREAD_START_BLOCKED")


threading.Thread.start = _thread_start_blocked

try:
    import tkinter

    def _tk_blocked(self, *args, **kwargs):
        del self, args, kwargs
        builtins.V7609_HARNESS_BLOCKED_EVENTS.append("gui:tk")
        raise RuntimeError("HARNESS_TK_BLOCKED")

    tkinter.Tk.__init__ = _tk_blocked
except Exception:
    pass
'''


def _copy_ignore(path: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name == "__pycache__" or name.endswith((".pyc", ".pyo"))
    }
    if Path(path).resolve() == CORE_ROOT.resolve():
        ignored.update({"v75_13_runtime", "v75_8_runtime"})
    return ignored


def _copy_sandbox(sandbox: Path) -> None:
    (sandbox / "SOURCE").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CORE_ROOT,
        sandbox / "SOURCE" / "core",
        ignore=_copy_ignore,
    )
    shutil.copytree(
        PROJECT_ROOT / "tests",
        sandbox / "tests",
        ignore=_copy_ignore,
    )
    for source_file in SOURCE_ROOT.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, sandbox / "SOURCE" / source_file.name)
    shutil.copy2(PROJECT_ROOT / "3_START_V76_GUI.vbs", sandbox)
    (sandbox / "sitecustomize.py").write_text(SITE_CUSTOMIZE, encoding="utf-8")


def _fingerprint_runtime() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for runtime_root in REAL_RUNTIME_ROOTS:
        if not runtime_root.exists():
            continue
        for path in sorted(runtime_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".csv"}:
                continue
            raw = path.read_bytes()
            result[str(path.relative_to(SOURCE_ROOT))] = {
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
    return result


def _child_environment(sandbox: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(sandbox),
            "V7609_HARNESS_MODE": "1",
            "V7609_HARNESS_REAL_RUNTIME": str(REAL_RUNTIME.resolve()),
            "V7609_HARNESS_REAL_RUNTIMES": json.dumps(
                [str(path) for path in REAL_RUNTIME_ROOTS]
            ),
            "V7609_HARNESS_SANDBOX_ROOT": str(sandbox.resolve()),
            "V7609_HARNESS_ENTRY": str(
                sandbox / "SOURCE" / "core" / ENTRY_FILE
            ),
        }
    )
    return environment


def _run_child(
    label: str,
    command: list[str],
    *,
    sandbox: Path,
    timeout: int,
) -> tuple[int, str, float]:
    print(f"[HARNESS] {label} START", flush=True)
    started = time.monotonic()
    environment = _child_environment(sandbox)
    with tempfile.TemporaryFile(mode="w+b") as output_file:
        process = subprocess.Popen(
            command,
            cwd=str(sandbox),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            shell=False,
        )
        last_heartbeat = started
        while process.poll() is None:
            now = time.monotonic()
            if now - started > timeout:
                process.kill()
                process.wait(timeout=10)
                raise TimeoutError(f"{label} TIMEOUT after {timeout}s")
            if now - last_heartbeat >= 5.0:
                print(f"[HARNESS] {label} RUNNING {now - started:.0f}s", flush=True)
                last_heartbeat = now
            time.sleep(0.25)
        elapsed = time.monotonic() - started
        output_file.seek(0)
        output = output_file.read().decode("utf-8", errors="replace")
    if output.strip():
        print(output.rstrip(), flush=True)
    print(
        f"[HARNESS] {label} END exit={process.returncode} elapsed={elapsed:.2f}s",
        flush=True,
    )
    return int(process.returncode), output, elapsed


def _guard_probe(sandbox: Path) -> tuple[bool, str]:
    probe = r'''
import os
from pathlib import Path
import socket
import tkinter

target = Path(os.environ["V7609_HARNESS_REAL_RUNTIME"]) / "paper_ledger" / "paper_state.json"
blocked = []
for label, action in (
    ("runtime", lambda: target.read_bytes()),
    ("network", lambda: socket.socket().connect(("127.0.0.1", 9))),
    ("tk", lambda: tkinter.Tk()),
):
    try:
        action()
    except (PermissionError, RuntimeError) as exc:
        blocked.append(label)
        print(f"GUARD_{label.upper()}_BLOCKED:{type(exc).__name__}")
    else:
        raise AssertionError(f"guard failed: {label}")
assert getattr(os._exit, "__name__", "") == "_process_exit_blocked"
print("GUARD_OS_EXIT_PATCHED")
assert blocked == ["runtime", "network", "tk"]
'''
    code, output, _elapsed = _run_child(
        "GUARD-PROBE",
        [sys.executable, "-B", "-c", probe],
        sandbox=sandbox,
        timeout=30,
    )
    required = {
        "GUARD_RUNTIME_BLOCKED",
        "GUARD_NETWORK_BLOCKED",
        "GUARD_TK_BLOCKED",
        "GUARD_OS_EXIT_PATCHED",
    }
    missing = sorted(marker for marker in required if marker not in output)
    return code == 0 and not missing, ",".join(missing)


def _import_probe(sandbox: Path) -> tuple[bool, str]:
    probe = r'''
import builtins
import importlib.util
import os
from pathlib import Path
import sys
import threading

entry = Path(os.environ["V7609_HARNESS_ENTRY"]).resolve()
sys.path.insert(0, str(entry.parent))
before_threads = {(item.name, item.ident) for item in threading.enumerate()}
spec = importlib.util.spec_from_file_location("v7609_harness_import_probe", entry)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
except SystemExit as exc:
    raise AssertionError(f"IMPORT_SYSTEM_EXIT:{exc.code}") from exc
after_threads = {(item.name, item.ident) for item in threading.enumerate()}
assert module.VERSION == "V76.09-FOREGROUND-APP-ACTIVATE-0600-LINK-PAPER"
assert before_threads == after_threads, (before_threads, after_threads)
blocked = list(getattr(builtins, "V7609_HARNESS_BLOCKED_EVENTS", []))
assert blocked == [], blocked
print("IMPORT_OK")
print("IMPORT_NO_NETWORK_ORDER_GUI_PROCESS_THREAD")
print("IMPORT_REAL_RUNTIME_ACCESS=[]")
'''
    code, output, _elapsed = _run_child(
        "PRODUCTION-IMPORT-PROBE",
        [sys.executable, "-B", "-c", probe],
        sandbox=sandbox,
        timeout=300,
    )
    required = {
        "IMPORT_OK",
        "IMPORT_NO_NETWORK_ORDER_GUI_PROCESS_THREAD",
        "IMPORT_REAL_RUNTIME_ACCESS=[]",
    }
    missing = sorted(marker for marker in required if marker not in output)
    return code == 0 and not missing, ",".join(missing)


def _str02_probe(sandbox: Path) -> tuple[bool, str]:
    probe = r'''
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(os.environ["V7609_HARNESS_SANDBOX_ROOT"])
core = root / "SOURCE" / "core"
sys.path.insert(0, str(core))
from v7600_three_stage_logic import (
    active_interest_pullback_decision,
    prominent_bear_interest_decision,
    three_bear_rising_volume_decision,
)

case = root / "tests" / "replay_data" / "STR-02"
bars_path = case / "completed_1m_chronological.json"
manifest = json.loads((case / "manifest.json").read_text(encoding="utf-8"))
assert manifest["case_id"] == "STR-02"
assert manifest["symbol"] == "141080"
assert manifest["trade_date"] == "20260810"
assert hashlib.sha256(bars_path.read_bytes()).hexdigest().upper() == manifest["integrity"]["completed_1m_sha256"]
payload = json.loads(bars_path.read_text(encoding="utf-8"))
rows = payload if isinstance(payload, list) else payload["completed_1m"]
target_text = "20260810091600"
kst = timezone(timedelta(hours=9))
bars = [
    SimpleNamespace(
        timestamp=datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S").replace(tzinfo=kst),
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=float(row["close"]),
        volume=float(row["volume"]),
        acc_volume=int(row.get("acc_volume", 0) or 0),
    )
    for row in rows if str(row["timestamp"]) <= target_text
]
now = datetime(2026, 8, 10, 9, 16, tzinfo=kst)
active = active_interest_pullback_decision(bars, now=now)
prominent = prominent_bear_interest_decision(bars, now=now)
three_bear = three_bear_rising_volume_decision(bars, now=now)
assert not active.allowed
assert active.reason == "ACTIVE_PULLBACK_TRIGGER_NOT_FALLING_BEARISH"
assert not prominent.allowed
assert not three_bear.allowed
print("STR02_INTEREST_0916=BLOCKED")
print(f"STR02_REASON={active.reason}")
'''
    code, output, _elapsed = _run_child(
        "STR-02-ISOLATED-REPLAY",
        [sys.executable, "-B", "-c", probe],
        sandbox=sandbox,
        timeout=60,
    )
    required = {
        "STR02_INTEREST_0916=BLOCKED",
        "STR02_REASON=ACTIVE_PULLBACK_TRIGGER_NOT_FALLING_BEARISH",
    }
    missing = sorted(marker for marker in required if marker not in output)
    return code == 0 and not missing, ",".join(missing)


def _suite_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for module in SUITE_MODULES:
        pattern = rf"^test_\S+ \({re.escape(module)}\."
        counts[module] = len(re.findall(pattern, output, flags=re.MULTILINE))
    return counts


def _self_check_counts(output: str) -> tuple[int, int]:
    passed = len(re.findall(r"^\[PASS\]", output, flags=re.MULTILINE))
    failed = len(re.findall(r"^\[FAIL\]", output, flags=re.MULTILINE))
    return passed, failed


def main(*, harness_002_only: bool = False) -> int:
    runtime_before = _fingerprint_runtime()
    holder = tempfile.TemporaryDirectory(prefix="v7609_harness_001_")
    sandbox = Path(holder.name) / "project"
    sandbox.mkdir(parents=True, exist_ok=True)
    print(f"[HARNESS] SANDBOX={sandbox}", flush=True)
    print("[HARNESS] copying immutable test inputs", flush=True)
    _copy_sandbox(sandbox)

    failures: list[str] = []
    guard_ok, guard_detail = _guard_probe(sandbox)
    if not guard_ok:
        failures.append(f"guard:{guard_detail or 'failed'}")

    import_ok, import_detail = _import_probe(sandbox)
    if not import_ok:
        failures.append(f"import:{import_detail or 'failed'}")

    str02_ok, str02_detail = _str02_probe(sandbox)
    if not str02_ok:
        failures.append(f"str02:{str02_detail or 'failed'}")

    suite_command = [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        *SUITE_MODULES,
        "-v",
    ]
    if not harness_002_only:
        suite_code, suite_output, _suite_elapsed = _run_child(
            "STATE+INV+IDEM+EVID+REPLAY",
            suite_command,
            sandbox=sandbox,
            timeout=300,
        )
        counts = _suite_counts(suite_output)
        print("[HARNESS] COUNTS " + json.dumps(counts, sort_keys=True), flush=True)
        if suite_code != 0:
            failures.append(f"suite_exit:{suite_code}")
        for module, expected in EXPECTED_COUNTS.items():
            if counts.get(module) != expected:
                failures.append(f"{module}:{counts.get(module, 0)}/{expected}")

    self_command = [
        sys.executable,
        "-B",
        str(sandbox / "SOURCE" / "core" / ENTRY_FILE),
        "--self-check",
    ]
    if not harness_002_only:
        self_code, self_output, _self_elapsed = _run_child(
            "FULL-SELF-CHECK",
            self_command,
            sandbox=sandbox,
            timeout=600,
        )
        self_pass, self_fail = _self_check_counts(self_output)
        print(
            f"[HARNESS] SELF_CHECK_ROWS pass={self_pass} fail={self_fail}",
            flush=True,
        )
        if self_code != 0 or self_fail or not self_pass:
            failures.append(
                f"self_check:exit={self_code},pass={self_pass},fail={self_fail}"
            )

    runtime_after = _fingerprint_runtime()
    changed = sorted(
        path
        for path in set(runtime_before) | set(runtime_after)
        if runtime_before.get(path) != runtime_after.get(path)
    )
    print(
        "[HARNESS] REAL_RUNTIME_HASH_CHANGED="
        + (json.dumps(changed, ensure_ascii=False) if changed else "[]"),
        flush=True,
    )
    print("[HARNESS] CHILD_REAL_RUNTIME_ACCESS=BLOCKED", flush=True)
    if changed:
        print(
            "[HARNESS] NOTE runtime changed externally while guarded children "
            "were denied access",
            flush=True,
        )

    holder.cleanup()

    if failures:
        print("[HARNESS] FAIL " + " | ".join(failures), flush=True)
        return 1
    print("[HARNESS] PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(harness_002_only="--harness-002" in sys.argv))
