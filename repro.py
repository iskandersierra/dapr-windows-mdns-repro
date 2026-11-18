"""Exercise self-hosted Dapr HTTP invocation over mDNS, without Aspire."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import socket
import subprocess
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class App(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/ping" else 404)
        self.end_headers()
        self.wfile.write(self.server.app_id.encode("ascii"))

    def log_message(self, *_args):
        pass


def get(port, path):
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def start_app(stack, app_id):
    server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), App))
    server.app_id = app_id
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack.callback(server.shutdown)
    return server.server_port


def free_ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in sockets]


def start_sidecar(stack, binary, app_id, app_port, ports, log_path):
    http, grpc, internal = ports
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [str(binary), "--app-id", app_id, "--app-port", str(app_port),
             "--dapr-http-port", str(http), "--dapr-grpc-port", str(grpc),
             "--dapr-internal-grpc-port", str(internal),
             "--dapr-listen-addresses", "127.0.0.1", "--log-level", "debug",
             "--enable-metrics=false", "--disable-init-endpoints", "config,subscribe",
             "--scheduler-host-address="],
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    stack.callback(stop, process)
    return process


def stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)


def wait_ready(process, port, log_path):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"daprd exited with {process.returncode}. See {log_path}")
        try:
            if get(port, "/v1.0/healthz")[0] == 204:
                return
        except (URLError, TimeoutError):
            pass
        sleep(0.1)
    raise RuntimeError(f"daprd did not become healthy. See {log_path}")


def trial(binary, app_ids, app_ports, directory, requests):
    directory.mkdir()
    ports = free_ports(3 * len(app_ids))
    with ExitStack() as stack:
        processes = [
            start_sidecar(stack, binary, app_id, app_ports[i], ports[i*3:i*3+3],
                          directory / f"{app_id}.log")
            for i, app_id in enumerate(app_ids)
        ]
        for i, process in enumerate(processes):
            wait_ready(process, ports[i*3], directory / f"{app_ids[i]}.log")
        for i, app_id in enumerate(app_ids):
            assert get(app_ports[i], "/ping") == (200, app_id)
        with (directory / "requests.log").open("w", encoding="utf-8") as log:
            for _ in range(requests):
                for i, caller in enumerate(app_ids):
                    for target in app_ids:
                        before = monotonic()
                        status, body = get(ports[i*3], f"/v1.0/invoke/{target}/method/ping")
                        if status == 200 and body == target:
                            outcome = "PASS"
                        elif status == 500 and "ERR_DIRECT_INVOKE" in body and f"couldn't find service: {target}" in body:
                            outcome = "MDNS_500"
                        else:
                            outcome = "OTHER"
                        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                        print(f"{timestamp} {outcome} {caller}->{target} {status} {monotonic()-before:.3f}s {body}", file=log, flush=True)
                        yield outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binaries", type=Path, nargs="+")
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--requests", type=int, default=1)
    parser.add_argument("--app-ids", nargs="+", default=["repro-a", "repro-b"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.app_ids) > 2 or len(set(args.app_ids)) != len(args.app_ids):
        parser.error("use one or two distinct app IDs")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]*", app_id) for app_id in args.app_ids):
        parser.error("app IDs may contain lowercase ASCII letters, digits and hyphens")
    if args.restarts < 1 or args.requests < 1:
        parser.error("--restarts and --requests must be positive")
    args.output.mkdir()
    with ExitStack() as stack:
        app_ports = [start_app(stack, app_id) for app_id in args.app_ids]
        for run in range(args.restarts):
            for phase, binary in enumerate(args.binaries, 1):
                name = f"{run+1:02}-{phase:02}-{binary.stem}"
                outcomes = list(trial(binary.resolve(), args.app_ids, app_ports,
                                      args.output / name, args.requests))
                print(f"{name} PASS={outcomes.count('PASS')} MDNS_500={outcomes.count('MDNS_500')} OTHER={outcomes.count('OTHER')}", flush=True)


if __name__ == "__main__":
    main()
