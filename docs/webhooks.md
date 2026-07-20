# Webhooks

The cutter never pushes events, but the server knows the moment it hands
a job over — and can tell your systems. Configure:

```bash
WEBHOOK_URL=https://your-app.example.com/hooks/graphtec
WEBHOOK_SECRET=some-long-random-string   # optional but recommended
```

## Events

### `job.sent`
A cutter scanned a barcode, the operator confirmed (or the single match
auto-confirmed), and the server delivered the job. This is the "the file
has been read into the machine" moment.

```json
{
  "event": "job.sent",
  "timestamp": "2026-07-19T22:41:03.512345+00:00",
  "cutter": "fc9000",
  "job_id": 42,
  "name": "front-labels",
  "barcode_link_info": "F00000017",
  "command_bytes": 4812
}
```

Note: delivery to the cutter is not completion of the cut — the machine
still scans marks and cuts after this. Watch the cutter's status reach
`Cutting` then `Stopped` on `/dls/status` if you need cut-finished
timing.

### `barcode.no_match`
A cutter scanned a barcode and the server had **no** matching jobs — the
operator is standing at the machine with nothing to cut, so this is the
event worth alerting on.

```json
{
  "event": "barcode.no_match",
  "timestamp": "2026-07-19T22:45:11.004211+00:00",
  "cutter": "fc9000",
  "barcode_link_info": "A0100XXXX"
}
```

## Delivery semantics

- POST, `Content-Type: application/json`, fire-and-forget from a
  background thread — a slow receiver never stalls cutter communication.
- Three attempts with backoff; failures are logged and counted (see the
  `webhook` block in `/health`). There is no durable retry queue — treat
  webhooks as notifications, not as the system of record (that's the
  jobs API).
- Answer with any 2xx quickly; do your processing async.

## Verifying signatures

With `WEBHOOK_SECRET` set, each request carries
`X-OGS-Signature: sha256=<hex>` — the HMAC-SHA256 of the raw body.

```python
import hashlib, hmac

def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header or "")
```
