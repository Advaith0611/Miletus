import os

os.environ["MOCK_PROVIDERS"] = "true"

from fastapi.testclient import TestClient

from backend.app import create_app


def test_health_upload_and_multi_file_job(tmp_path):
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/api/generate", files=[
        ("files", ("one.md", b"# One\nFirst document.", "text/markdown")),
        ("files", ("two.txt", b"Second document.", "text/plain")),
        ("files", ("one.md", b"A second file with the same name.", "text/markdown")),
    ])
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    with client as active_client:
        for _ in range(30):
            status = active_client.get(f"/api/jobs/{job_id}").json()
            if status["status"] in {"complete", "failed"}:
                break
        assert status["status"] == "complete"
        assert active_client.get(f"/api/jobs/{job_id}/audio").status_code == 200
        assert "Teacher:" in active_client.get(f"/api/jobs/{job_id}/transcript").text


def test_rejects_unsupported_extension():
    client = TestClient(create_app())
    response = client.post("/api/generate", files={"files": ("bad.exe", b"no", "application/octet-stream")})
    assert response.status_code == 415
