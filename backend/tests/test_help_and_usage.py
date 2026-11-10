from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.catalog import SIMULATIONS


def add_simulation(client: TestClient, *, age_days: float, status: str, shape: str, took_s: float):
    created = datetime.now(UTC) - timedelta(days=age_days)
    doc = {
        "_id": f"job-{age_days}-{shape}-{status}",
        "user_id": client.get("/api/auth/me").json()["id"],
        "thread_id": None,
        "case": {"shape": shape},
        "status": status,
        "created_at": created,
        "updated_at": created + timedelta(seconds=took_s),
    }
    client.portal.call(client.app.state.db[SIMULATIONS].insert_one, doc)


def test_usage_counts_runs_by_period_status_and_shape(client: TestClient):
    add_simulation(client, age_days=1, status="completed", shape="cylinder", took_s=600)
    add_simulation(client, age_days=2, status="failed", shape="airfoil", took_s=30)
    add_simulation(client, age_days=3, status="running", shape="cylinder", took_s=0)
    add_simulation(client, age_days=20, status="completed", shape="airfoil", took_s=3600)
    add_simulation(client, age_days=100, status="completed", shape="wedge", took_s=60)

    usage = client.get("/api/simulations/usage").json()

    assert usage["week"]["runs"] == 3
    assert (usage["week"]["completed"], usage["week"]["failed"], usage["week"]["running"]) == (
        1,
        1,
        1,
    )
    assert usage["week"]["compute_s"] == 630  # the running one doesn't count yet
    assert usage["week"]["by_shape"] == {"cylinder": 2, "airfoil": 1}
    assert usage["month"]["runs"] == 4 and usage["month"]["compute_s"] == 4230
    assert usage["all_time"]["runs"] == 5 and usage["all_time"]["by_shape"]["wedge"] == 1


def test_usage_is_empty_without_simulations(client: TestClient):
    usage = client.get("/api/simulations/usage").json()

    assert usage["all_time"] == {
        "runs": 0,
        "completed": 0,
        "failed": 0,
        "running": 0,
        "compute_s": 0.0,
        "by_shape": {},
    }


def test_status_reports_each_dependency(client: TestClient):
    checks = {check["id"]: check for check in client.get("/api/status").json()}

    assert set(checks) == {"database", "models", "xfoil", "work_station"}
    assert checks["database"]["ok"] is True
    assert checks["models"]["ok"] is True
    assert checks["work_station"]["ok"] is False  # none configured in tests
    assert "Settings" in checks["work_station"]["detail"]
