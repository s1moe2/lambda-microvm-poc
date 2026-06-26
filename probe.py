"""Lambda MicroVM capability probe.

Serves a JSON report on port 8080 describing, at RUNTIME inside the microVM,
which network capabilities are available to a containerized workload:
NET_RAW (raw sockets, e.g. scapy), NET_ADMIN (iptables, tun config), and /dev/net/tun.

The decisive field is capabilities.CapBnd: the *bounding set* is the ceiling of what
any process here (including a nested container spawned with --cap-add) can hold.
"""
import http.server
import json
import os
import socket

CAP = {"NET_RAW": 13, "NET_ADMIN": 12, "SYS_ADMIN": 21}


def caps():
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("CapBnd:", "CapEff:", "CapPrm:")):
                k, v = line.split()
                m = int(v, 16)
                out[k.rstrip(":")] = {n: bool(m & (1 << b)) for n, b in CAP.items()}
    return out


def try_sock(family, typ, proto=0):
    try:
        socket.socket(family, typ, proto).close()
        return "OK"
    except PermissionError as e:
        return f"DENIED: {e}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def tun():
    if not os.path.exists("/dev/net/tun"):
        return "MISSING"
    try:
        os.close(os.open("/dev/net/tun", os.O_RDWR))
        return "OK"
    except Exception as e:  # noqa: BLE001
        return f"PRESENT-but-{e}"


def report():
    return {
        "uid": os.getuid(),
        "capabilities": caps(),  # CapBnd is the key line
        "raw_AF_INET_ICMP": try_sock(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP),
        "raw_AF_PACKET": try_sock(socket.AF_PACKET, socket.SOCK_RAW),  # L2 / scapy sendp
        "dev_net_tun": tun(),
    }


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(report(), indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
