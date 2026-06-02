"""Unit tests for the VC-loop Obsidian writer (pure path/string logic)."""

from __future__ import annotations

from maverick_mcp.vc_loop.obsidian import (
    ensure_vault,
    write_company_note,
    write_pipeline_base,
    write_pipeline_note,
    write_thesis_note,
)


def test_ensure_vault_creates_folders(tmp_path):
    ensure_vault(tmp_path)
    for folder in ("04-theses", "08-reviews", "Companies", "Pipeline"):
        assert (tmp_path / folder).is_dir()


def test_write_company_note_idempotent(tmp_path):
    rel = write_company_note(tmp_path, "aapl", sector="Technology")
    assert rel == "Companies/AAPL.md"
    path = tmp_path / rel
    assert path.exists()
    content = path.read_text()
    assert "ticker: AAPL" in content
    assert "## Theses" in content
    assert "[[Technology]]" in content

    # Overwrite with sentinel, ensure re-call does not clobber.
    path.write_text("SENTINEL")
    rel2 = write_company_note(tmp_path, "AAPL")
    assert rel2 == rel
    assert path.read_text() == "SENTINEL"


def test_write_thesis_note_has_links_and_table(tmp_path):
    breakdown = {
        "screening_score": {"weight": 0.35, "value": 0.8, "contribution": 28.0},
        "momentum": {"weight": 0.10, "value": 0.9, "contribution": 9.0},
    }
    features = {"screening_score": 0.8, "momentum": 0.9}
    catalysts = [{"event_type": "earnings", "event_date": "2026-06-15"}]

    rel = write_thesis_note(
        tmp_path,
        ticker="msft",
        date="2026-06-01",
        conviction=82.5,
        action="BUY (high conviction)",
        thesis="Strong cloud momentum.",
        breakdown=breakdown,
        features=features,
        sector="Technology",
        catalysts=catalysts,
    )
    assert rel == "04-theses/MSFT-2026-06-01.md"
    content = (tmp_path / rel).read_text()

    assert "type: thesis" in content
    assert "asset: MSFT" in content
    assert "ticker: MSFT" in content
    assert "status: proposed" in content
    assert "conviction: 82.5" in content
    assert "confidence: 0.825" in content
    assert "[[MSFT]]" in content
    assert "[[Technology]]" in content
    assert "Strong cloud momentum." in content
    # Breakdown table.
    assert "| Component | Weight | Value | Contribution |" in content
    assert "| screening_score | 0.35 | 0.8 | 28.0 |" in content
    # Catalyst link.
    assert "earnings" in content
    # Review-rule sections required by the Thesis Index conventions.
    assert "## Kill Criteria" in content
    assert "## Disconfirming Signals" in content
    assert "## Review Trigger" in content


def test_write_pipeline_note_ranked_table(tmp_path):
    ranked = [
        {
            "ticker": "nvda",
            "conviction": 90.0,
            "action": "BUY (high conviction)",
            "thesis_path": "04-theses/NVDA-2026-06-01.md",
        },
        {"ticker": "tsla", "conviction": 50.0, "action": "Watch"},
    ]
    rel = write_pipeline_note(tmp_path, date="2026-06-01", ranked=ranked)
    assert rel == "Pipeline/2026-06-01.md"
    content = (tmp_path / rel).read_text()

    assert "| Rank | Ticker | Conviction | Action | Thesis |" in content
    assert "| 1 | [[NVDA]] | 90.0 | BUY (high conviction) | [[NVDA-2026-06-01]] |" in content
    # Fallback thesis link for entry without thesis_path.
    assert "[[TSLA]]" in content
    assert "[[TSLA-2026-06-01]]" in content


def test_write_pipeline_base(tmp_path):
    rel = write_pipeline_base(tmp_path)
    assert rel == "VC Pipeline.base"
    content = (tmp_path / rel).read_text()
    assert 'file.inFolder("04-theses")' in content
    assert "conviction" in content
    assert "type: table" in content
