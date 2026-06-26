"""Docker-in-Docker diagnostic for a Lambda MicroVM.

A first run showed dockerd failing to start. This version surfaces WHY: it reports the
capability set, the cgroup / overlay environment dockerd depends on, and the real
dockerd/containerd error (not the truncated plugin-load noise). It tries a few start
strategies, including the official docker:dind entrypoint (which does the cgroup/mount
prep). If dockerd does come up, it pulls + runs a nested container and checks NET_RAW.

Hypothesis under test: a MicroVM lacks CAP_SYS_ADMIN (the probe showed SYS_ADMIN=false),
which dockerd/containerd need to mount cgroups/overlay -> DinD may be unsupported.

Serves the result as JSON on :8080.
"""
import http.server
import json
import subprocess
import time

IMG = "python:3.13-alpine"
LOG = "/var/log/dockerd.log"


def sh(cmd, timeout=240, limit=6000):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "out": (p.stdout + p.stderr).strip()[:limit]}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": f"TIMEOUT after {timeout}s"}


def docker_up():
    return subprocess.run("docker info", shell=True, capture_output=True).returncode == 0


def env_diag():
    return {
        "id": sh("id"),
        "caps": sh("grep Cap /proc/self/status"),
        "cgroup_fstype": sh("stat -fc %T /sys/fs/cgroup 2>&1"),  # cgroup2fs => v2
        "cgroup_mounts": sh("mount | grep -i cgroup || echo 'no cgroup mounts'"),
        "cgroup_controllers": sh("cat /sys/fs/cgroup/cgroup.controllers 2>&1"),
        "cgroup_writable": sh(
            "mkdir /sys/fs/cgroup/_probe 2>&1 && echo WRITABLE && rmdir /sys/fs/cgroup/_probe "
            "|| echo 'NOT writable'"
        ),
        "proc_fs_cgroup": sh("grep cgroup /proc/filesystems || echo none"),
        "proc_fs_overlay": sh("grep overlay /proc/filesystems || echo none"),
        "can_mount": sh("mount -t tmpfs none /mnt 2>&1 && echo 'mount OK' && umount /mnt || echo 'mount denied'"),
    }


def _try_start(cmd, secs=20):
    subprocess.run("pkill -9 dockerd containerd 2>/dev/null; sleep 1", shell=True)
    with open(LOG, "a") as f:
        f.write(f"\n===== starting: {cmd} =====\n")
    subprocess.Popen(f"{cmd} >>{LOG} 2>&1", shell=True)
    for _ in range(secs):
        if docker_up():
            return True
        time.sleep(1)
    return False


def ensure_dockerd():
    if docker_up():
        return "already running"
    strategies = [
        ("bare", "dockerd --bridge=none --iptables=false"),
        ("vfs", "dockerd --bridge=none --iptables=false --storage-driver=vfs"),
        ("dind-entrypoint", "dockerd-entrypoint.sh dockerd --bridge=none --iptables=false"),
    ]
    tried = []
    for label, cmd in strategies:
        if _try_start(cmd):
            return f"started via {label}"
        tried.append(label)
    return f"FAILED (tried: {', '.join(tried)})"


def demo():
    out = {"env": env_diag(), "dockerd": ensure_dockerd()}
    if not docker_up():
        out["dockerd_errors"] = sh(
            f"grep -iE 'level=(error|fatal)|failed|denied|permission|cannot|no such|sys_admin' {LOG} | tail -n 30"
        )
        out["dockerd_tail"] = sh(f"tail -n 20 {LOG}")
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
