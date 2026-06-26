# Lambda MicroVM — NET_RAW / capability probe (PoC)

Checks, **at runtime inside a Firecracker Lambda MicroVM**, whether these network
capabilities are available to a containerized workload:

- `NET_RAW` — raw sockets (scapy, packet crafting)
- `NET_ADMIN` — the in-container egress firewall, tun configuration
- `/dev/net/tun` — custom tunnels / VPN-style protocols

## Prerequisites
- AWS CLI **new enough to include the `aws lambda-microvms` commands** (the service shipped
  2026-06-22; update the CLI if these subcommands are missing).
- Credentials with permission to create an S3 bucket, an IAM role, and MicroVM resources.
- `zip`, `python3`, `curl` locally. (Docker is **not** needed locally — the image is built in Lambda.)
- A MicroVM-supported region: `us-east-1`, `us-east-2`, `us-west-2`, `ap-northeast-1`, `eu-west-1`.

## Run
```bash
./setup.sh                 # uses us-east-1 by default
REGION=eu-west-1 ./setup.sh   # or pick another supported region
```
It prints a JSON report, e.g.:
```json
{
  "uid": 0,
  "capabilities": { "CapBnd": { "NET_RAW": true, "NET_ADMIN": true, "SYS_ADMIN": false }, ... },
  "raw_AF_INET_ICMP": "OK",
  "raw_AF_PACKET": "OK",
  "dev_net_tun": "OK"
}
```

## Interpreting it
- **`capabilities.CapBnd.NET_RAW`** is the decisive signal — the *bounding set* is the ceiling of
  what any process here can hold, including a nested container spawned with
  `--cap-add=NET_RAW`. `true` ⇒ scapy is viable in a microVM; `false` ⇒ no sub-container can get it.
- **`raw_AF_PACKET: OK`** is the strongest scapy proof (L2 frame crafting / `sendp`).
  `raw_AF_INET_ICMP: OK` is the weaker "basic raw sockets" signal.
- **`dev_net_tun: OK`** ⇒ tun/VPN-style custom tunnels are possible.

This probes the microVM's **own** process. Because `CapBnd` bounds nested containers too, it answers
the sub-container question; for belt-and-suspenders, extend the image to install Docker and rerun the
checks inside a `docker run --cap-add=NET_RAW --cap-add=NET_ADMIN` container.

## Clean up
```bash
./teardown.sh
```
Removes the MicroVM, image, IAM role, and S3 bucket. **This creates real, billable AWS resources —
tear down when done.** All commands are confirmed against the
[CLI reference](https://docs.aws.amazon.com/cli/latest/reference/lambda-microvms/); teardown is idempotent.
