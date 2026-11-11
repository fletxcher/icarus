from fastapi.testclient import TestClient


def test_status_reports_each_dependency(client: TestClient):
    checks = {check["id"]: check for check in client.get("/api/status").json()}

    assert set(checks) == {"database", "models", "xfoil", "work_station"}
    assert checks["database"]["ok"] is True
    assert checks["models"]["ok"] is True
    assert checks["work_station"]["ok"] is False  # none configured in tests
    assert "WS_HOST" in checks["work_station"]["detail"]
