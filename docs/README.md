# Open Graphtec Server — Documentation

Open Graphtec Server is a headless Data Link Server for Graphtec cutting
plotters: it prepares print files with barcodes and registration marks,
converts and stores cut jobs, and serves the right job the moment a cutter
scans its barcode.

## Where to start

| Doc | Read it when you want to… |
|---|---|
| [Getting started](getting-started.md) | install the server and cut your first barcode job |
| [Deploying with Coolify](deployment-coolify.md) | run it permanently on a Coolify-managed host |
| [Configuration](configuration.md) | look up any environment variable |
| [HTTP API](api.md) | integrate from a RIP, MIS, or script |
| [Print preparation](print-preparation.md) | add barcodes + marks to print files |
| [Hot folders](hot-folders.md) | drive the server from watched folders |
| [Barcodes & marks](barcode-and-marks.md) | understand the printed geometry and job matching |
| [Data Link protocol](data-link-protocol.md) | understand cutter communication and panel setup |
| [Webhooks](webhooks.md) | get notified when a cutter pulls a job |
| [Architecture](architecture.md) | understand the codebase and data flow |
| [Troubleshooting](troubleshooting.md) | fix scan errors and rejected files |
| [Development](development.md) | run the tests and extend the server |

## The workflow in one paragraph

Your prepress process produces a **print PDF** with cut paths marked
(ISO 19593-1 processing steps, or a `CutContour` spot color). The server
adds a **barcode and registration marks** to that file and simultaneously
registers the matching **cut job** — same geometry, computed once. You
print the marked file and load it into the cutter. The operator starts a
barcode scan; the cutter reads the code, asks the server, receives the
job, scans the four marks for registration, and cuts. Nobody touches a
computer at cut time.
