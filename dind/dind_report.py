"""Docker-in-Docker demo for a Lambda MicroVM.

Proves the sandbox-spawning model works inside a MicroVM: starts dockerd, pulls an
image, runs a nested container, and confirms the nested container can hold NET_RAW.

A MicroVM has no NET_ADMIN, so dockerd cannot create Docker's default bridge or its
iptables NAT -> it runs with --bridge=none --iptables=false, and nested containers use
--network host (egress is the MicroVM's own, enforced at the platform connector, not by
in-container iptables). dockerd is started lazily on the first request so it does not
depend on surviving the Firecracker snapshot/restore.

Serves the result as JSON on :8080.
"""
import http.server
import json
import subprocess
import time

IMG = "python:3.13-alpine"


def sh(cmd, timeout=240):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "out": (p.stdout + p.stderr).strip()[:4000]}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": f"TIMEOUT after {timeout}s"}


def docker_up():
    return subprocess.run("docker info", shell=True, capture_output=True).returncode == 0


def _start(flags):
    subprocess.Popen(f"dockerd {flags} >>/var/log/dockerd.log 2>&1", shell=True)
    for _ in range(30):
        if docker_up():
            return True
        time.sleep(1)
    return False


def ensure_dockerd():
    if docker_up():
        return "already running"
    base = "--bridge=none --iptables=false"
    if _start(base):
        return "started"
    # overlay2 may be unavailable in the microVM fs; retry with the vfs storage driver
    subprocess.run("pkill dockerd 2>/dev/null", shell=True)
    time.sleep(2)
    if _start(base + " --storage-driver=vfs"):
        return "started (vfs)"
    return "FAILED (see dockerd_log)"


def demo():
    out = {"dockerd": ensure_dockerd()}
    if not docker_up():
        out["dockerd_log"] = sh("tail -n 40 /var/log/dockerd.log")
        return out
    out["docker_version"] = sh("docker version --format 'server {{.Server.Version}}'")
    out["pull"] = sh(f"docker pull {IMG}")
    out["nested_run"] = sh(
        f"docker run --rm --network host {IMG} "
        "python3 -c \"import os; print('nested container OK, uid', os.getuid())\""
    )
    out["nested_NET_RAW"] = sh(
        f"docker run --rm --network host --cap-add=NET_RAW {IMG} "
        "python3 -c \"import socket; "
        "socket.socket(socket.AF_PACKET, socket.SOCK_RAW).close(); "
        "print('nested AF_PACKET raw socket OK')\""
    )
    return out


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(demo(), indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
