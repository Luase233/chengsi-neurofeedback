"""Standard-library support for the native launchers; never manage by health alone."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request


def available_port(preferred, lan):
    address = "0.0.0.0" if lan else "127.0.0.1"
    for port in [*range(preferred, min(65535, preferred + 99) + 1), 0]:
        with socket.socket() as probe:
            try:
                probe.bind((address, port))
                return probe.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Could not find an available local port")


def process_record(root, pid):
    root = Path(root).resolve()
    command = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()
    if not command:
        return None
    executable = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"python(?:w|3(?:\.\d+)?)?", Path(executable).name, re.IGNORECASE):
        raise RuntimeError("The saved PID belongs to another program. No process was reused or stopped")
    script = str(root / "server.py")
    script_match = re.search(r'(?:^|\s)(?:"' + re.escape(script) + r'"|' + re.escape(script) + r')(?=\s|$)', command)
    # ps can omit quotes around paths containing spaces. Match the entire known
    # interpreter prefix, so a foreign script cannot pass server.py as an argument.
    interpreters = {str(root / ".venv" / "bin" / "python"), executable,
                    sys.executable, getattr(sys, "_base_executable", sys.executable)}
    interpreters.update(str(Path(value).resolve()) for value in tuple(interpreters))
    prefixes = {quoted + options for value in interpreters
                for quoted in (value, '"' + value + '"')
                for options in (" -X utf8 -u", " -u")}
    if not script_match or command[:script_match.start()].strip() not in prefixes:
        raise RuntimeError("The saved PID belongs to another project. No process was reused or stopped")
    arguments = command[script_match.end():]
    match = re.search(r"(?:^|\s)--port\s+(\d+)(?=\s|$)", arguments)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise RuntimeError("The managed process has no valid port argument")
    # The launcher always puts data-dir last; ps does not retain argv quoting.
    data = re.search(r'(?:^|\s)--data-dir\s+(.+)$', arguments)
    data_dir = str((root / data[1].strip('"')).resolve()) if data else str(root / "runtime" / "sessions")
    created = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True).stdout.strip()
    return {"pid": pid, "port": int(match[1]), "root": str(root), "server_script": script,
            "started_at": created, "lan_enabled": bool(re.search(r"(?:^|\s)--lan(?=\s|$)", arguments[:data.start()] if data else arguments)), "data_dir": data_dir}


def managed_record(root):
    root = Path(root).resolve()
    pid_file = root / "preview-server.pid"
    if not pid_file.exists():
        return None
    value = pid_file.read_text().strip()
    if not value.isdigit():
        raise RuntimeError("Invalid saved PID. No process was reused or stopped")
    record = process_record(root, int(value))
    if record is None:
        return None
    state_file = root / "runtime" / "preview-server.json"
    if state_file.exists():
        saved = json.loads(state_file.read_text(encoding="utf-8-sig"))
        if any(saved.get(key) != record[key] for key in ("pid", "port", "root", "server_script", "started_at")):
            raise RuntimeError("The managed state does not match the current process. No process was reused or stopped")
    return record


def owns_listener(record):
    # macOS ships lsof. Check ownership before requesting health or sending finish.
    result = subprocess.run(["lsof", "-nP", "-a", "-p", str(record["pid"]),
                             "-iTCP:" + str(record["port"]), "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return str(record["pid"]) in result.stdout.split()


def healthy(record):
    if not owns_listener(record):
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:" + str(record["port"]) + "/api/health", timeout=1) as response:
            value = json.load(response)
        return (value.get("service") == "chengsi-backend" and str(value.get("api_version")) == "1"
                and bool(value.get("lan_enabled")) == record["lan_enabled"])
    except (OSError, ValueError):
        return False


def save_record(root, record):
    root = Path(root).resolve()
    state_file = root / "runtime" / "preview-server.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record) + "\n", encoding="utf-8")
    temporary.replace(state_file)
    (root / "preview-server.pid").write_text(str(record["pid"]) + "\n")


def spawn_windows(root, port, lan, data_dir):
    root = Path(root).resolve()
    arguments = [sys.executable, "-X", "utf8", "-u", str(root / "server.py"), "--port", str(port)]
    if lan:
        arguments.append("--lan")
    if data_dir:
        arguments += ["--data-dir", str(Path(data_dir).resolve())]
    with (root / "backend-server.log").open("w") as output, (root / "backend-server-error.log").open("w") as errors:
        # close_fds also prevents background children retaining a launcher's pipes.
        process = subprocess.Popen(arguments, cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                   close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    print(process.pid)


def locked(root, script, arguments):
    import fcntl
    root = Path(root).resolve()
    target = (root / script).resolve()
    if script not in ("start.sh", "stop.sh") or target.parent != root:
        raise RuntimeError("Invalid launcher script")
    lock_file = root / "runtime" / "preview-server.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a") as handle:
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another launcher is busy. Wait for it to finish, then retry")
                time.sleep(.1)
        environment = {**os.environ, "CHENGSI_LAUNCH_LOCK_ROOT": str(root)}
        return subprocess.call(["/bin/bash", str(target), *arguments], env=environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "healthy", "owned", "record", "port", "locked", "spawn"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--lan", action="store_true")
    parser.add_argument("--data-dir")
    args, remaining = parser.parse_known_args()
    if args.command != "locked" and remaining:
        parser.error("unrecognized arguments: " + " ".join(remaining))
    if args.command == "port":
        print(available_port(args.port, args.lan))
        return 0
    if args.command == "spawn":
        if os.name != "nt":
            raise RuntimeError("This spawn entry is for the Windows launcher")
        spawn_windows(args.root, args.port, args.lan, args.data_dir)
        return 0
    if args.command == "locked":
        remaining = remaining[1:] if remaining[:1] == ["--"] else remaining
        return locked(args.root, remaining[0], remaining[1:])
    record = process_record(args.root, args.pid) if args.pid is not None else managed_record(args.root)
    if args.command == "inspect":
        print(json.dumps(record))
        return 0
    if record is None:
        return 1
    if args.command == "record":
        save_record(args.root, record)
        return 0
    return 0 if (healthy(record) if args.command == "healthy" else owns_listener(record)) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
