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


def test_eval_proof_is_false_for_tautology_and_ranks_under_recipe():
    from claimidx.match import annotate, rank
    from claimidx.public import eval_is_proof, refine_eval

    err = "TypeError: params is a Promise"
    hint = _claim(err, eco="npm", dep=["next@15.0.0"])
    proof = _claim(err, eco="npm", dep=["next@15.0.0"])
    proof.eval.cmd = "npx tsc --noEmit"
    q = {"err": err, "eco": "npm", "dep": ["next@15.0.0"]}
    assert eval_is_proof("true") is False
    assert eval_is_proof("npx tsc --noEmit") is True
    meta = annotate(q, hint, 0.9)
    assert meta["eval_proof"] is False
    assert any("eval is not proof" in w for w in meta["warn"])
    hits = rank(q, [hint, proof], k=2)
    assert hits[0][0].eval.cmd == "npx tsc --noEmit"
    assert not any("recipe-per-fp" in w for w in annotate(q, proof, hits[0][1])["warn"])
    rng = refine_eval("true", fix_k="pin", fix_b="pydantic>=2.7", eco="py")
    assert "importlib.metadata" in rng and eval_is_proof(rng) is True
    assert refine_eval("true", fix_k="patch", fix_b="await params", eco="npm") == "true"


def test_eval_proof_weight_does_not_break_recipe_sibling_ties():
    """*1.08 lifts every recipe sibling equally; it is not an err-match."""
    from claimidx.match import annotate, rank

    err = "TypeError: params is a Promise"
    a = _claim(err, eco="npm", dep=["next@15.0.0"])
    b = _claim(err, eco="npm", dep=["next@15.2.0"])
    a.eval.cmd = "npx tsc --noEmit"
    b.eval.cmd = "npx tsc --noEmit"
    q = {"err": err, "eco": "npm", "dep": ["next@15.1.0"]}
    hits = rank(q, [a, b], k=2)
    assert len(hits) == 2
    assert abs(hits[0][1] - hits[1][1]) < 1e-9
    for c, s in hits:
        meta = annotate(q, c, s)
        assert meta["eval_proof"] is True
        assert not any("recipe-per-fp" in w for w in meta["warn"])


def test_eval_proof_warns_only_when_query_err_differs():
    from claimidx.match import annotate

    err = "TypeError: params is a Promise"
    proof = _claim(err, eco="npm", dep=["next@15.0.0"])
    proof.eval.cmd = "npx tsc --noEmit"
    same = {"err": err, "eco": "npm", "dep": ["next@15.0.0"]}
    assert not any("recipe-per-fp" in w for w in annotate(same, proof, 0.9)["warn"])
    sib = {"err": "TypeError: searchParams is a Promise", "eco": "npm", "dep": ["next@15.0.0"]}
    assert any("recipe-per-fp" in w for w in annotate(sib, proof, 0.9)["warn"])


def test_eval_proof_warns_when_quoted_value_only_matches_after_normalize():
    from claimidx.match import annotate

    raw = "pydantic.ValidationError: 1 validation error for Model\nstatus\n  Input should be 'thumbs_up' [type=literal_error, input_value='👍']"
    other = "pydantic.ValidationError: 1 validation error for Model\nstatus\n  Input should be 'thumbs_up' [type=literal_error, input_value='👎']"
    proof = _claim(raw, eco="py")
    proof.eval.cmd = 'python -c "import pydantic"'
    assert "<STR>" in proof.err
    assert normalize_error(other) == proof.err
    other_meta = annotate({"err": other, "eco": "py"}, proof, 0.9)
    assert any("recipe-per-fp" in w for w in other_meta["warn"])
    assert any("normalization_risk" in w and "str" in w for w in other_meta["warn"])
    stored = annotate({"err": proof.err, "eco": "py"}, proof, 0.9)
    assert not any("recipe-per-fp" in w for w in stored["warn"])
    assert any("normalization_risk" in w and "str" in w for w in stored["warn"])


def test_rt_drift_warns_and_ranks_under_matching_runtime():
    from claimidx.match import annotate, hit_warn, hold_applies, rank, rt_drift

    err = "ModuleNotFoundError: No module named 'demo_rt'"
    c = _claim(err, eco="py", dep=["demo@1"])
    c.rt = "py@3.12"
    c.nr = 4
    q = {"err": err, "eco": "py", "dep": ["demo@1"], "rt": "py@3.9"}
    assert rt_drift("py@3.9", "py@3.12") == {"query": "py@3.9", "claim": "py@3.12"}
    assert hold_applies("py@3.9", "py@3.12") is False
    assert hold_applies("py@3.12", "py@3.12") is True
    assert hold_applies("", "py@3.12") is False
    w = hit_warn(q, c)
    assert any(x.startswith("rt query=py@3.9") for x in w)
    assert any("unproven here" in x for x in w)
    meta = annotate(q, c, 0.9)
    assert meta["rt_drift"]["query"] == "py@3.9"
    assert meta["nr"] == 0
    same = {"err": err, "eco": "py", "dep": ["demo@1"], "rt": "py@3.12"}
    assert annotate(same, c, 0.9)["nr"] == 4
    hits = rank(same, [c], k=1)
    drifted = rank(q, [c], k=1)
    assert hits and drifted
    assert drifted[0][1] < hits[0][1]


def test_nc_without_replay_and_omitted_runtime_warn():
    from claimidx.match import hit_warn

    c = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    c.nc = 2
    c.nr = 0
    c.rt = "node@20"
    q = {"err": "TypeError: params is a Promise", "eco": "npm", "dep": ["next@15.0.0"]}
    w = hit_warn(q, c)
    assert "nc without replay" in w
    assert any(x.startswith("rt omitted") for x in w)
    c.nr = 2
    q["rt"] = "node@20"
    w2 = hit_warn(q, c)
    assert "nc without replay" not in w2
    assert not any(x.startswith("rt omitted") for x in w2)


def test_fail_count_and_contested_surface_on_ask():
    from claimidx.match import hit_warn

    c = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    c.nf = 3
    c.nc = 1
    c.st = "contested"
    q = {"err": "TypeError: params is a Promise", "eco": "npm", "dep": ["next@15.0.0"]}
    w = hit_warn(q, c)
    assert any(x.startswith("nf=3") for x in w)
    assert any("contested" in x for x in w)


def test_tautological_eval_scores_below_replayable():
    weak = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    strong = _claim("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"])
    strong.eval = EvalSpec(cmd="npx tsc --noEmit")
    assert strong.score() > weak.score()


def test_schema_payload_outranks_validation_skeleton():
    q = (
        "pydantic.ValidationError: 1 validation error for Reaction\n"
        "emoji\n"
        "  Input should be 'thumbs_up' [type=literal_error, input_value='thumbs_down', input_type=str]"
    )
    payload = _claim("Input should be 'thumbs_up'", eco="py", dep=["pydantic@2"])
    payload.fix = Fix(k="patch", b="Literal thumbs_up")
    skeleton = _claim(
        "pydantic.ValidationError: 1 validation error for Reaction\n"
        "user\n"
        "  Input should be a valid string [type=string_type, input_value='@foo', input_type=str]",
        eco="py",
        dep=["pydantic@2"],
    )
    skeleton.fix = Fix(k="patch", b="coerce @foo")
    qrow = {"err": q, "eco": "py", "dep": ["pydantic@2"]}
    hits = rank(qrow, [skeleton, payload], k=2)
    assert hits, "payload sibling must remain a hit"
    assert hits[0][0].id == payload.id
    assert similarity(qrow, skeleton) < similarity(qrow, payload)


def test_disjoint_schema_literals_are_not_the_same_hit():
    thumbs = _claim(
        "pydantic.ValidationError: 1 validation error for Reaction\n"
        "emoji\n"
        "  Input should be 'thumbs_up' [type=literal_error, input_value='thumbs_down', input_type=str]",
        eco="py",
        dep=["pydantic@2"],
    )
    q = {
        "err": (
            "pydantic.ValidationError: 1 validation error for Reaction\n"
            "user\n"
            "  Input should be a valid string [type=string_type, input_value='@foo', input_type=str]"
        ),
        "eco": "py",
        "dep": ["pydantic@2"],
    }
    assert rank(q, [thumbs]) == []


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
