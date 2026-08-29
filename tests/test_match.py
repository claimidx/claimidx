from claimidx.fingerprint import classify, fingerprint, normalize_error
from claimidx.match import rank, similarity
from claimidx.models import Claim, EvalSpec, Fix


def _claim(err: str, *, eco: str = "py", cls: str | None = None, dep: list[str] | None = None) -> Claim:
    cls = cls or classify(err)
    dep = dep or []
    return Claim(
        fp=fingerprint(err=err, cls=cls, eco=eco, rt="py@3.12", dep=dep),
        cls=cls,
        err=normalize_error(err),
        eco=eco,
        rt="py@3.12",
        dep=dep,
        fix=Fix(k="constraint", b="x"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:test",
        nc=4,
        st="confirmed",
    )


def test_class_and_eco_without_error_overlap_is_not_a_hit():
    perm = _claim("PermissionError: [Errno 13] Permission denied: 'claimidx.exe'", cls="perm")
    q = {"err": "eval head not allowlisted: gradlew.bat", "eco": "py"}
    assert similarity(q, perm) == 0.0
    assert rank(q, [perm]) == []


def test_different_missing_modules_are_not_the_same_hit():
    cgi = _claim("ModuleNotFoundError: No module named 'cgi'", eco="py")
    q = {"err": "ModuleNotFoundError: No module named 'autogen'", "eco": "py"}
    assert similarity(q, cgi) == 0.0
    assert rank(q, [cgi]) == []


def test_same_error_still_ranks():
    c = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    hits = rank({"err": "TypeError: params is a Promise", "eco": "npm", "dep": ["next@15.0.0"]}, [c])
    assert hits and hits[0][0].id == c.id and hits[0][1] >= 0.5


def test_disjoint_dep_packages_are_not_a_hit():
    c = _claim(
        "ModuleNotFoundError: No module named 'pydantic_core'",
        eco="py",
        dep=["pydantic@2.9.0"],
    )
    q = {
        "err": "ModuleNotFoundError: No module named 'pydantic_core'",
        "eco": "py",
        "dep": ["django@5.0.0"],
    }
    assert similarity(q, c) == 0.0
    assert rank(q, [c]) == []


def test_same_package_different_patch_still_ranks():
    c = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    q = {"err": "TypeError: params is a Promise", "eco": "npm", "dep": ["next@15.2.0"]}
    hits = rank(q, [c])
    assert hits and hits[0][0].id == c.id
    from claimidx.match import annotate, dep_drift

    drift = dep_drift(q["dep"], c.dep)
    assert drift == [{"name": "next", "query": "15.2.0", "claim": "15.0.0"}]
    meta = annotate(q, c, hits[0][1])
    assert any("next query=15.2.0" in w for w in meta["warn"])


def test_exact_dep_pin_ranks_above_drifted_pin():
    err = "TypeError: params is a Promise"
    exact = _claim(err, eco="npm", dep=["next@15.2.0"])
    old = _claim(err, eco="npm", dep=["next@15.0.0"])
    q = {"err": err, "eco": "npm", "dep": ["next@15.2.0"]}
    hits = rank(q, [old, exact], k=2)
    assert [h[0].id for h in hits][0] == exact.id


def test_tautological_eval_scores_below_replayable():
    weak = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    strong = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    strong.eval = EvalSpec(cmd="npx tsc --noEmit")
    assert strong.score() > weak.score()


def test_mcp_own_error_does_not_hit_tools_list():
    mcp = _claim(
        "Error: MCP error -32601: Method not found: tools/list",
        eco="mcp",
        cls="mcp_transport",
    )
    q = {
        "err": "MCP claimidx_ingest has no own field so subagent claims stamp the parent DID",
        "eco": "mcp",
    }
    assert rank(q, [mcp]) == []
