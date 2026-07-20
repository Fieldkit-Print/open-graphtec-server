# The Data Link protocol

How the server talks to cutters, and what the states on the status page
mean.

## The naming inversion

Despite the name, a "Data Link **Server**" is the TCP **client**. The
cutter listens on port 9100, never initiates anything, and never pushes
events — it only answers when polled. "Server" describes the data flow:
this software serves cut data when the cutter asks for it.

## The cycle

The server polls each cutter's Data Link status about once a second.
A barcode-driven cut walks through these states (shown on the status
page as the cutter tile's state):

| Code | Label | Server's action |
|---|---|---|
| 0 | Stopped | idle; wait |
| 1 | Scanning barcode | reset per-job state, arm for the cycle |
| 2 | Requesting job list | ask for the scanned barcode, look up jobs, send up to 8 names (an empty list is still sent — required) |
| 3 | Selecting job | wait (skipped entirely when exactly one job was offered) |
| 4 | Job determined | read the selection, send the registration vector and command type, then stream the whole job |
| 5 | Cutting | wait |
| 6 | User operating | operator is at the panel (e.g. retrying a scan) |
| 7 | Error | wait for the operator to acknowledge |

Two robustness rules from the protocol are worth knowing because you'll
see their effects:

- **Standby**: on any error, illegal state jump (a sign another Data
  Link server is driving the same machine), or rejected command, the
  server stops participating — polling only — until the cutter starts a
  fresh scan cycle. It never exits; the status page shows
  "Standby — waiting for a scan cycle".
- **One connection at a time**: the cutter accepts a single TCP
  connection. The server opens a fresh connection per command and
  retries sends on a fixed budget, which is also how multiple tools can
  time-share a machine.

## Startup handshake

When a Data Link worker starts it reads the cutter's model/firmware and
GP-GL step size, shows them on the status page, and raises a persistent
error if the step size disagrees with `GPGL_STEPS_PER_MM` — a mismatch
would scale every cut (a 0.025 mm cutter cuts 0.1 mm-scaled data at
quarter size).

## Multiple cutters

Configure named cutters with `CUTTERS` (see
[configuration.md](configuration.md)). Each gets its own polling worker
and its own lock file; all share one job library, so **any** cutter that
scans a barcode is offered the matching jobs — print a roll, walk it to
whichever machine is free. API endpoints take `?cutter=name` to address
one machine.

Only one server process may talk to a given cutter: per-cutter lock
files prevent double-starting locally, and you should never point two
server installations at the same machine.

## Cutter panel prerequisites

- LINK / Data Link destination: **SERVER (LAN)**
- ARMS: **MARK TYPE 2, 4-point**
- COMMAND: **GP-GL** or **AUTO**

## Events

The cutter can't push notifications, so the server synthesizes an
activity log from its polling loop — status transitions, barcode
requests with the offered job names, jobs delivered with byte counts,
and standby causes. It's on the status page, at `GET /dls/events`, and
(for job deliveries and unmatched scans) can be forwarded as
[webhooks](webhooks.md).
