#!/bin/sh
# Import the barcode cut test job into the Open Graphtec Server.
# Usage: ./import_test_job.sh [server-url]
SERVER="${1:-http://localhost:8080}"
curl -sS -X POST "$SERVER/jobs/import-json" \
  -H 'Content-Type: application/json' \
  -d '{"name": "barcode-cut-test", "barcode_link_info": "A0100ABCD", "command_type": 0, "regmark_fx": 100, "regmark_fy": 180, "regmark_rx": 0, "regmark_ry": 0, "command_sequence": "VEI5OQNUQjUxLDIwMANUQjUyLDIDVEI1NCwwLDADVEI1NSwxA1RCMjQsMjAwMCwxNTAwA1RCOTkDSjEDTTQwMCwzMDADRDgwMCwzMDAsODAwLDcwMCw0MDAsNzAwLDQwMCwzMDADTTQwMCw5MDADRDQwMCwxMzAwLDgwMCw5MDAsNDAwLDkwMANNMTQ1MCw3NTADRDE0NDgsNzc2LDE0NDEsODAxLDE0MzAsODI1LDE0MTUsODQ2LDEzOTYsODY1LDEzNzUsODgwLDEzNTEsODkxLDEzMjYsODk4LDEzMDAsOTAwLDEyNzQsODk4LDEyNDksODkxLDEyMjUsODgwLDEyMDQsODY1LDExODUsODQ2LDExNzAsODI1LDExNTksODAxLDExNTIsNzc2LDExNTAsNzUwLDExNTIsNzI0LDExNTksNjk5LDExNzAsNjc1LDExODUsNjU0LDEyMDQsNjM1LDEyMjUsNjIwLDEyNDksNjA5LDEyNzQsNjAyLDEzMDAsNjAwLDEzMjYsNjAyLDEzNTEsNjA5LDEzNzUsNjIwLDEzOTYsNjM1LDE0MTUsNjU0LDE0MzAsNjc1LDE0NDEsNjk5LDE0NDgsNzI0LDE0NTAsNzUwA00wLDADVEIwAw==", "command_sequence_encoding": "base64", "append_etx": false}'
echo
