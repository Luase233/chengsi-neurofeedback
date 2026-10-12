"""Launcher checks use isolated projects and ephemeral ports, never the live backend."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("launcher_state", ROOT / "launcher_state.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_port_selection_preserves_free_preference_and_skips_occupied_owner():
    preferred = free_port()
    assert launcher.available_port(preferred, False) == preferred
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", preferred))
        owner.listen()
        selected = launcher.available_port(preferred, False)
        assert selected != preferred
        assert owner.getsockname()[1] == preferred


def test_managed_record_requires_exact_project_and_creation_time(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    root.mkdir()
    (root / "preview-server.pid").write_text("123\n")
    script = root / "server.py"
    details = {"command=": f'{root / ".venv" / "bin" / "python"} -u "{script}" --port 8877 --lan --data-dir "{root / "data with spaces"}"',
               "comm=": "/Library/Frameworks/Python.framework/Python", "lstart=": "Mon Oct 12 13:00:00 2026"}
    monkeypatch.setattr(launcher.subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(stdout=details[arguments[-1]]))
    record = launcher.managed_record(root)
    assert record["port"] == 8877 and record["lan_enabled"]
    assert record["data_dir"] == str(root / "data with spaces")
    launcher.save_record(root, record)
    assert launcher.managed_record(root) == record
    details["lstart="] = "Mon Oct 12 13:01:00 2026"
    with pytest.raises(RuntimeError, match="does not match"):
        launcher.managed_record(root)
    details["command="] = details["command="].replace(str(script), str(script) + ".other")
    with pytest.raises(RuntimeError, match="another project"):
        launcher.managed_record(root)


def usable_bash():
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"]
    bash = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()
                 and "system32" not in candidate.lower()), None)
    if not bash:
        pytest.skip("No usable bash for syntax validation")
    return bash


def test_mac_process_requires_server_to_be_first_script(tmp_path, monkeypatch):
    root = tmp_path / "项目 with spaces"
    root.mkdir()
    interpreter = str(root / ".venv" / "bin" / "python")
    script = str(root / "server.py")
    details = {"comm=": "/Library/Frameworks/Python.framework/Python", "lstart=": "Mon Oct 12 13:00:00 2026"}
    monkeypatch.setattr(launcher.subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(stdout=details[arguments[-1]]))
    for prefix in (interpreter + " -X utf8 -u", '"' + interpreter + '" -u', details["comm="] + " -u"):
        details["command="] = prefix + ' "' + script + '" --port 8877 --lan'
        assert launcher.process_record(root, 123)["port"] == 8877
    details["command="] = interpreter + ' -u /tmp/foreign-worker.py -u "' + script + '" --port 8877 --lan'
    with pytest.raises(RuntimeError, match="another project"):
        launcher.process_record(root, 123)


def test_mac_stop_rechecks_pid_after_acquiring_start_lock(tmp_path):
    bash = usable_bash()
    root = tmp_path / "stop project with spaces"
    (root / ".venv" / "bin").mkdir(parents=True)
    shutil.copyfile(ROOT / "stop.sh", root / "stop.sh")
    # Simulate a start holder publishing its PID while stop waits for the lock.
    # A stop checking for PID before locking exits without observing this state.
    stub = root / ".venv" / "bin" / "python"
    stub.write_text('''#!/bin/bash
set -euo pipefail
case "$2" in
  locked)
    task_root="$4"
    sleep 0.1
    echo 123 > "$task_root/preview-server.pid"
    touch "$task_root/lock-acquired"
    export CHENGSI_LAUNCH_LOCK_ROOT="$task_root"
    exec /bin/bash "$task_root/stop.sh" ;;
  inspect) printf 'null\\n' ;;
  *) exit 2 ;;
esac
''', encoding="utf-8", newline="\n")
    result = subprocess.run([bash, "-c", 'chmod +x "$1"; exec /bin/bash "$2"', "launcher-test", str(stub), str(root / "stop.sh")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "lock-acquired").exists()
    assert "already stopped" in result.stdout and not (root / "preview-server.pid").exists()


def test_shell_scripts_parse_when_bash_is_available():
    bash = usable_bash()
    for name in ["start.sh", "stop.sh", "启动系统.command", "关闭系统.command"]:
        result = subprocess.run([bash, "-n", str(ROOT / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


FAKE_SERVER = r'''
import argparse, json, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--lan', action='store_true')
parser.add_argument('--data-dir')
args = parser.parse_args()
root = pathlib.Path(__file__).parent
if (root / 'race-once').exists():
    (root / 'race-once').unlink()
    sys.exit('[Errno 10048] address already in use')
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        base = 'http://127.0.0.1:' + str(args.port)
        value = ({'service':'chengsi-backend','api_version':'1','lan_enabled':args.lan} if self.path == '/api/health'
                 else {'lan_enabled':args.lan,'participant_urls':[base+'/participant.html?pair=test'],
                       'local_participant_url':base+'/participant.html'} if self.path == '/api/connection'
                 else {'phase':'idle','session_id':None})
        content = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(content)
    def do_POST(self):
        (root / 'control-requested').write_text(self.path)
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0' if args.lan else '127.0.0.1', args.port), Handler).serve_forever()
'''


def health(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
        return json.load(response)


@pytest.mark.skipif(os.name != "nt", reason="Windows process and launcher integration")
def test_windows_launch_fallback_reuse_restart_and_safe_stop(tmp_path):
    root = tmp_path / "项目 launcher with spaces"
    foreign_root = tmp_path / "older project"
    root.mkdir(); foreign_root.mkdir()
    for name in ["start.ps1", "stop.ps1", "launch.ps1", "launcher-process.ps1", "launcher_state.py"]:
        shutil.copyfile(ROOT / name, root / name)
    for directory in [root, foreign_root]:
        (directory / "server.py").write_text(FAKE_SERVER, encoding="utf-8")
    foreign_worker = foreign_root / "foreign-worker.py"
    foreign_worker.write_text(FAKE_SERVER.replace("args = parser.parse_args()", "args, ignored = parser.parse_known_args()"), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")], check=True)
    powershell = shutil.which("powershell.exe")
    preferred = free_port()
    foreign = subprocess.Popen([sys._base_executable, "-u", str(foreign_worker), "-u", str(root / "server.py"), "--port", str(preferred), "--lan"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    state_file = root / "runtime" / "preview-server.json"

    def command(script, *arguments, check=True):
        result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / script),
                                 *map(str, arguments)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0
        return result

    try:
        for _ in range(40):
            try:
                health(preferred); break
            except OSError:
                time.sleep(.05)
        data_dir = root / "historical data"
        first = command("launch.ps1", "-NoBrowser", "-Port", preferred, "-DataDir", data_dir)
        state = json.loads(state_file.read_text(encoding="utf-8-sig"))
        actual = state["port"]
        assert actual != preferred
        assert f"http://127.0.0.1:{actual}/operator.html" in first.stdout
        assert f"http://127.0.0.1:{actual}/participant.html" in first.stdout
        assert state["data_dir"] == str(data_dir)
        assert health(actual)["lan_enabled"]
        first_pid = state["pid"]
        reused = command("start.ps1", "-Port", preferred, "-PassThru")
        assert "Already running" in reused.stdout
        assert json.loads(state_file.read_text(encoding="utf-8-sig"))["pid"] == first_pid
        command("stop.ps1", "-Port", preferred, check=False)
        assert health(actual) and health(preferred)
        command("start.ps1", "-LocalOnly", check=False)
        # Legacy numeric PID files recover the actual port without probing 8768.
        state_file.unlink()
        command("start.ps1", "-Port", preferred)
        assert json.loads(state_file.read_text(encoding="utf-8-sig"))["port"] == actual
        # A foreign worker passing this project's script as data must be rejected
        # even with only a legacy PID file and matching Chengsi health response.
        pid_file = root / "preview-server.pid"
        saved_pid = pid_file.read_bytes()
        saved_state = state_file.read_bytes()
        state_file.unlink()
        pid_file.write_text(str(foreign.pid))
        command("stop.ps1", check=False)
        assert foreign.poll() is None and health(preferred)
        pid_file.write_bytes(saved_pid)
        state_file.write_bytes(saved_state)
        command("launch.ps1", "-NoBrowser", "-Restart", "-LocalOnly", "-Port", preferred, "-DataDir", data_dir)
        restarted = json.loads(state_file.read_text(encoding="utf-8-sig"))
        assert restarted["pid"] != first_pid and not health(restarted["port"])["lan_enabled"]
        command("launch.ps1", "-Action", "Stop")
        assert not pid_file.exists() and not state_file.exists()
        assert foreign.poll() is None and not (foreign_root / "control-requested").exists()
        # Simulate another owner winning after the bind probe, then retry safely.
        requested = free_port()
        (root / "race-once").touch()
        command("start.ps1", "-LocalOnly", "-Port", requested)
        assert json.loads(state_file.read_text(encoding="utf-8-sig"))["port"] != requested
        command("stop.ps1")
    finally:
        try:
            if (root / "preview-server.pid").exists():
                if (root / "preview-server.pid").read_text().strip() == str(foreign.pid):
                    (root / "preview-server.pid").write_bytes(saved_pid)
                command("stop.ps1")
        finally:
            foreign.terminate()
            foreign.wait(timeout=5)
