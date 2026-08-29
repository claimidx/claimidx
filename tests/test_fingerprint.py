from claimidx.fingerprint import classify, fingerprint, normalize_error


def test_quoted_strings_become_str_token():
    assert "<STR>" in normalize_error('TypeError: Cannot read property "foo" of undefined')
    assert "foo" not in normalize_error('TypeError: Cannot read property "foo" of undefined')


def test_paths_and_urls_and_numbers():
    s = normalize_error("failed at /home/runner/work/app/src/page.tsx line 14 see https://example.com/x")
    assert "<PATH>" in s and "<URL>" in s and "<N>" in s
    win = normalize_error(r"failed at C:\Users\runner\app\page.tsx")
    assert "<PATH>" in win
    mac = normalize_error("failed at /Users/runner/src/page.tsx")
    assert "<PATH>" in mac


def test_classify_async_and_module():
    assert classify("TypeError: params is a Promise") == "async_api"
    assert classify("ModuleNotFoundError: No module named 'x'") == "module_not_found"
    assert classify("npm ERR! ERESOLVE unable to resolve") == "lockfile_drift"


def test_missing_modules_keep_distinct_names():
    a = fingerprint(err="ModuleNotFoundError: No module named 'claude_code_sdk'", eco="py")
    b = fingerprint(err="ModuleNotFoundError: No module named 'autogen'", eco="py")
    assert a != b
    assert "claude_code_sdk" in normalize_error("ModuleNotFoundError: No module named 'claude_code_sdk'")


def test_tools_list_is_not_a_path():
    err = "Error: MCP error -32601: Method not found: tools/list"
    assert "tools/list" in normalize_error(err)
    assert "<PATH>" not in normalize_error(err)


def test_fingerprint_stable_and_runtime_major():
    a = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.18.2", dep=["next@15.0.0"])
    b = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.11.1", dep=["next@15.0.0"])
    c = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@18.20.0", dep=["next@15.0.0"])
    assert a == b and a != c and len(a) == 64
