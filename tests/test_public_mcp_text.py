"""0.7.17: public MCP text (server card, instructions, prompts, tool descriptions) per the Oct 21 site audit.

M5: the card says what share actually does (commons only with --share-yes / share_yes).
M11: no growth-hack copy for agents (hold codes, invites, stranger DIDs).
Constraints: no AI model/provider names; canonical install pin is this release.
The card's "Human page: claimidx.com/leaderboard" stays until the COO lifts that hold.
"""

from __future__ import annotations

import json
import re

import pytest

from claimidx import __version__
from claimidx.discovery import ROOT
from claimidx.mcp_server import INSTRUCTIONS, PROMPTS, TOOLS, _prompt

CARDS = (".well-known/mcp/server-card.json", "docs/.well-known/mcp/server-card.json")
CANONICAL_INSTALL = f'pip install -U "claimidx[server]>={__version__}"'
CARD_DESCRIPTION = (
    "Prior art for AI agents. init wires DID/MCP; claim --yes --share-yes shares to the commons (claim --yes alone stays local; ask never shares)."
)
BANNED = re.compile(
    r"\b(grok|claude|anthropic|openai|gemini|gpt-?\d*|llama|mistral|deepseek|qwen|codex|copilot)\b"
    r"|cix_bdc82291f2fbb06a|first hold|hold code|invite another agent|stranger dids?|--channel|--source"
    r"|apply \S+ (--cwd \S+ )?--yes|apply --yes|this same call|hooks already send|puts DID on commons",
    re.I,
)
STANDING_HOLD = "Human page: claimidx.com/leaderboard."


def _prompt_texts() -> list[str]:
    return [_prompt(p["name"], {"err": "E"})["messages"][0]["content"]["text"] for p in PROMPTS]


def _public_texts() -> list[tuple[str, str]]:
    out = [("INSTRUCTIONS", INSTRUCTIONS)]
    out += [(f"prompt:{p['name']}", t) for p, t in zip(PROMPTS, _prompt_texts(), strict=True)]
    out += [(f"tool:{t['name']}", json.dumps(t)) for t in TOOLS]
    for rel in CARDS:
        out.append((rel, (ROOT / rel).read_text(encoding="utf-8")))
    out.append(("server.json", (ROOT / "server.json").read_text(encoding="utf-8")))
    return out


@pytest.mark.parametrize("where,text", _public_texts())
def test_public_mcp_text_has_no_banned_copy(where, text):
    hits = sorted({m.group(0) for m in BANNED.finditer(text)})
    assert hits == [], f"{where}: {hits}"


@pytest.mark.parametrize("rel", CARDS)
def test_server_card_description_is_the_audited_text(rel):
    card = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    assert card["description"] == CARD_DESCRIPTION
    assert card["serverInfo"]["description"] == CARD_DESCRIPTION
    assert card["serverInfo"]["version"] == __version__


@pytest.mark.parametrize("rel", CARDS)
def test_server_card_keeps_the_leaderboard_human_page_string(rel):
    """Standing hold (COO decides): audit M5 asks to delete it; 0.7.17 keeps it unchanged on purpose."""
    card = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    board = next(t for t in card["tools"] if t["name"] == "claimidx_leaderboard")
    assert board["description"].endswith(STANDING_HOLD)
    assert "Not available right now" in board["description"]


def test_tool_descriptions_say_share_needs_share_yes():
    by = {t["name"]: t["description"] for t in TOOLS}
    assert "The public projection goes to the commons only with share_yes=true (after a short review)" in by["claimidx_ingest"]
    assert "the full record goes to a private home when CLAIMIDX_HOME_API is set" in by["claimidx_ingest"]
    assert "session hooks only flush claims you already approved for sharing" in by["claimidx_share"]
    assert "never a new one" in by["claimidx_sync"]
    assert "claim --yes --share-yes" in by["claimidx_ask"]


def test_instructions_carry_the_canonical_install_and_commons_wording():
    assert CANONICAL_INSTALL in INSTRUCTIONS
    assert "claimidx claim --yes --share-yes" in INSTRUCTIONS
    assert "The commons is on by default" in INSTRUCTIONS
    assert "shared there only with share_yes" in INSTRUCTIONS
    assert "CLAIMIDX_COMMONS=0 disables the commons" in INSTRUCTIONS
    assert "claim --yes alone stays local" in INSTRUCTIONS
    assert "path_b.countable is true" not in INSTRUCTIONS


def test_prompts_do_not_claim_ingest_auto_shares():
    texts = dict(zip((p["name"] for p in PROMPTS), _prompt_texts(), strict=True))
    assert "records the claim and shares it" not in texts["after_fix"]
    assert "share_yes=true" in texts["after_fix"]
    assert CANONICAL_INSTALL in texts["recommend_claimidx"]
    assert "claimidx claim --yes --share-yes" in texts["recommend_claimidx"]


def test_initialize_returns_the_new_instructions(tmp_path):
    from claimidx.mcp_server import handle
    from claimidx.store import Store

    rec = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, Store(tmp_path / "ix.sqlite"))
    res = rec["result"]
    assert res["serverInfo"]["version"] == __version__
    assert res.get("instructions") == INSTRUCTIONS
