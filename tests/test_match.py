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


def test_same_error_still_ranks():
    c = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    hits = rank({"err": "TypeError: params is a Promise", "eco": "npm", "dep": ["next@15.0.0"]}, [c])
    assert hits and hits[0][0].id == c.id and hits[0][1] >= 0.5


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
