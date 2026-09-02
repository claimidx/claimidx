from pathlib import Path

from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.fingerprint import error_features, family_fingerprint
from claimidx.query import ask, ingest
from claimidx.store import Store


def _ingest(db: Path, fix: str, *, alternative: bool = False) -> dict:
    return ingest(
        "ModuleNotFoundError: No module named 'future_pkg'",
        fix_k="pin",
        fix_b=fix,
        eval='python -c "import future_pkg"',
        eco="py",
        rt="py@3.13",
        dep=["future-pkg@2.0"],
        alternative=alternative,
        db=db,
    )


def test_v1_claim_projects_to_v2_graph(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    result = _ingest(db, "future-pkg>=2")
    graph = Store(db).graph(result["id"])
    assert graph is not None
    assert graph["failure"]["fp_v1"] == result["fp"]
    assert graph["remedy"]["legacy_claim_id"] == result["id"]
    assert graph["proof"]["legacy_cmd"].startswith("python -c")
    assert graph["observations"] == []


def test_alternative_remedies_coexist_and_are_related(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    first = _ingest(db, "future-pkg>=2")
    second = _ingest(db, "Use the compatibility module", alternative=True)
    assert first["id"] != second["id"]
    store = Store(db)
    graph = store.failure_graph(first["fp"])
    assert graph is not None
    assert len(graph["remedies"]) == 2
    second_graph = store.graph(second["id"])
    assert second_graph is not None
    assert second_graph["relations"][0]["kind"] == "alternative"


def test_confirmation_appends_immutable_observation(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    result = _ingest(db, "future-pkg>=2")
    store = Store(db)
    store.confirm(result["id"], "did:claimidx:test", replayed=True, detail={"returncode": 0, "env": {"rt": "py@3.13"}})
    graph = store.graph(result["id"])
    assert graph is not None
    observation = graph["observations"][0]
    assert observation["held"] is True
    assert observation["replayed"] is True
    assert observation["actual_exit"] == 0
    assert observation["evidence_hash"]


def test_family_features_do_not_change_v1_fingerprint_contract():
    raw = "HTTP 429: package future_pkg failed at C:/private/tree/file.py"
    features = error_features(raw)
    assert features["codes"] == ["429"]
    assert "path" in features["normalization_risk"]
    assert len(family_fingerprint(err=raw, eco="py")) == 64


def test_v2_graph_http_routes_are_additive(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    result = _ingest(db, "future-pkg>=2")
    client = TestClient(create_app(str(db)))
    by_claim = client.get(f"/api/v2/claims/{result['id']}")
    assert by_claim.status_code == 200
    assert by_claim.json()["failure"]["fp_v1"] == result["fp"]
    by_failure = client.get(f"/api/v2/failures/{result['fp']}")
    assert by_failure.status_code == 200
    assert len(by_failure.json()["remedies"]) == 1


def test_http_alternative_cannot_overwrite_an_existing_claim_id(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    first = _ingest(db, "future-pkg>=2")
    client = TestClient(create_app(str(db)))
    response = client.post(
        "/api/publish",
        json={
            "id": first["id"],
            "err": "ModuleNotFoundError: No module named 'future_pkg'",
            "fix_k": "constraint",
            "fix_b": "Use a different adapter.",
            "eval": 'python -c "import future_pkg"',
            "eco": "py",
            "rt": "py@3.13",
            "dep": ["future-pkg@2.0"],
            "own": "did:claimidx:agent-a",
            "alternative": True,
        },
    )
    assert response.status_code == 409
    graph = Store(db).failure_graph(first["fp"])
    assert graph is not None
    assert len(graph["remedies"]) == 1


def test_candidate_index_preserves_matching(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    _ingest(db, "future-pkg>=2")
    result = ask("ModuleNotFoundError: No module named 'future_pkg'", eco="py", rt="py@3.13", dep=["future-pkg@2.0"], db=db)
    assert result["hit"] is True
    assert result["claims"][0]["fix"]["b"] == "future-pkg>=2"
