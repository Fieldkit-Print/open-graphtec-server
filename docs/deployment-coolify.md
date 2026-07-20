# Deploying with Coolify

Steps for running Open Graphtec Server on a Coolify-managed host (e.g. a
NUC on the shop network).

## Prerequisites

- The host must be able to reach the cutter(s) on TCP 9100 — same LAN or
  routed VLAN. From the host: `nc -vz <cutter-ip> 9100`.
- The host should be always-on; the server is the shop's cut-job
  library.

## Create the resource

1. In Coolify: **New Resource → Docker Compose**, source = this GitHub
   repository, branch `main`. Coolify uses the repo's
   `docker-compose.yml` (build context `./server`).
2. Set environment variables on the resource:

   | Variable | Value |
   |---|---|
   | `CUTTERS` | `fc9000=192.168.1.104:9100` (add more, comma-separated) |
   | `DLS_ENABLED` | `true` |
   | `API_KEY` | a long random string |
   | `WEBHOOK_URL` / `WEBHOOK_SECRET` | if you use [webhooks](webhooks.md) |
   | `GPGL_STEPS_PER_MM` | `10` unless the cutter says otherwise |

   Everything else has sensible defaults — see
   [configuration.md](configuration.md).
3. Deploy. The container healthcheck hits `/health`; Coolify shows it
   healthy once the app is up. Redeploys on push to `main` if you enable
   auto-deploy.

## Reaching the UI and API

The compose file publishes port **8080** on the host, so
`http://<nuc-ip>:8080/` works on the LAN immediately. If you prefer a
domain through Coolify's proxy (e.g. `cutserver.internal.example`),
attach one to the service in Coolify and optionally remove the `ports:`
mapping; the app listens on 8080 in-container.

The UI is safe to leave open on a trusted LAN (`/health` is read-only);
job management prompts for the `API_KEY` in the browser.

## Hot folders on a headless host

Job data lives in the `dls_data` named volume — durable across
redeploys, but not somewhere prepress staff can drop files. Decide how
files arrive:

- **API-first (simplest):** skip shares; submit via `POST
  /print/prepare` from scripts/RIP integrations. Nothing to mount.
- **Network share:** bind-mount host paths over the named volume's
  folders and export them via Samba on the host, e.g. add to the
  compose (or a Coolify volume mapping):

  ```yaml
  volumes:
    - dls_data:/data
    - /srv/graphtec/inbox/print:/data/inbox/print
    - /srv/graphtec/outbox/print:/data/outbox/print
    - /srv/graphtec/inbox/cut:/data/inbox/cut
    - /srv/graphtec/errors:/data/errors
  ```

  then share `/srv/graphtec` on the shop network. The named volume keeps
  the database; the bind mounts make the folders visible.

## Backups

Back up the `dls_data` volume (or at minimum `jobs.db` inside it) with
whatever routine covers the host. The database is the entire job
library; everything else is regenerable.

## Upgrades

Push to `main` (or click redeploy). The database schema is created
in-place; jobs and counters persist in the volume. Check `/version`
after deploy.
