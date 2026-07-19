import base64
import dataclasses
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import build_pdf


BARCODE = "A12345678"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("api-data")
    os.environ["APP_DATA_DIR"] = str(data_dir)
    os.environ["DLS_ENABLED"] = "false"
    os.environ["INGEST_ENABLED"] = "false"

    import app.main as main_module
    main = importlib.reload(main_module)
    with TestClient(main.app) as client:
        yield client, main


@pytest.fixture()
def client(api):
    client, main = api
    # Reset to open mode between tests.
    main.settings = dataclasses.replace(main.settings, api_key="")
    return client


@pytest.fixture()
def main(api):
    return api[1]


def import_json_payload(**overrides):
    payload = {
        "name": "front-label-001",
        "barcode_link_info": BARCODE,
        "command_sequence": "J1\x03M0,0\x03D10,10",
        "append_etx": True,
    }
    payload.update(overrides)
    return payload


def test_health_is_open(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_api_key_enforced_when_configured(client, main) -> None:
    main.settings = dataclasses.replace(main.settings, api_key="secret")
    assert client.get("/jobs").status_code == 401
    assert client.get("/health").status_code == 200

    ok = client.get("/jobs", headers={"X-API-Key": "secret"})
    assert ok.status_code == 200


def test_import_json_round_trip(client) -> None:
    response = client.post("/jobs/import-json", json=import_json_payload())
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    details = client.get(f"/jobs/{job_id}")
    assert details.status_code == 200
    assert details.json()["barcode_link_info"] == BARCODE


def test_import_json_rejects_etx_only_sequence(client) -> None:
    response = client.post(
        "/jobs/import-json",
        json=import_json_payload(command_sequence="\x03", append_etx=False),
    )
    assert response.status_code == 400


def test_import_json_rejects_long_or_control_names(client) -> None:
    response = client.post(
        "/jobs/import-json",
        json=import_json_payload(name="x" * 26),
    )
    assert response.status_code == 422

    response = client.post(
        "/jobs/import-json",
        json=import_json_payload(name="bad\x1ename"),
    )
    assert response.status_code == 422


def test_import_json_rejects_bad_barcode(client) -> None:
    response = client.post(
        "/jobs/import-json",
        json=import_json_payload(barcode_link_info="short"),
    )
    assert response.status_code == 422  # pydantic length gate

    response = client.post(
        "/jobs/import-json",
        json=import_json_payload(barcode_link_info="A12-4567!"),
    )
    assert response.status_code == 400


def test_import_pdf_conversion(client) -> None:
    pdf = build_pdf("10 250 m 110 150 l S", height=300)
    response = client.post(
        "/jobs/import-pdf",
        files={"file": ("label.pdf", pdf, "application/pdf")},
        data={"name": "pdf-job", "barcode_link_info": BARCODE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command_length"] > 0

    details = client.get(f"/jobs/{body['job_id']}")
    assert "J1" in details.json()["command_preview"]


def test_import_pdf_rejects_hpgl_command_type(client) -> None:
    pdf = build_pdf("10 10 m 20 20 l S", height=300)
    response = client.post(
        "/jobs/import-pdf",
        files={"file": ("label.pdf", pdf, "application/pdf")},
        data={
            "name": "pdf-job",
            "barcode_link_info": BARCODE,
            "command_type": "1",
        },
    )
    assert response.status_code == 400
    assert "GP-GL only" in response.json()["detail"]


def test_import_pdf_enforces_size_limit(client, main) -> None:
    main.settings = dataclasses.replace(main.settings, max_upload_bytes=100)
    pdf = build_pdf("10 10 m 20 20 l S", height=300)
    response = client.post(
        "/jobs/import-pdf",
        files={"file": ("label.pdf", pdf, "application/pdf")},
        data={"name": "pdf-job", "barcode_link_info": BARCODE},
    )
    assert response.status_code == 413


def test_ingest_endpoints_exist(client) -> None:
    status = client.get("/ingest/status")
    assert status.status_code == 200
    started = client.post("/ingest/start")
    assert started.status_code == 200
    stopped = client.post("/ingest/stop")
    assert stopped.status_code == 200
