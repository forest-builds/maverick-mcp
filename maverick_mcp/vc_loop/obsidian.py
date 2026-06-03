"""Obsidian vault writer for the VC loop.

Pure path/string logic. The vault path is always passed in as an argument;
this module never reads global settings. All folder/file creation is
idempotent.

Folder layout follows the numbered "investment brain" vault scheme so the loop
writes *into* the existing structure (theses land where the Thesis Index
Dataview query looks, reviews land beside the Weekly Review, etc.)::

    <vault>/
        04-theses/generated/<TICKER>-<YYYY-MM-DD>.md
                                                generated thesis memos
        04-theses/promoted/                    human-reviewed thesis memos
        08-reviews/                          review notes (reserved)
        Companies/<TICKER>.md                graph-hub note per company
        Pipeline/<YYYY-MM-DD>.md             ranked daily pipeline
        Pipeline/Current.md                  latest ranked pipeline pointer
        VC Pipeline.base                     Obsidian Bases table view

Thesis notes use the shared `type: thesis` frontmatter (asset, account_fit,
status, confidence, last_reviewed) so they appear in the Thesis Index Dataview,
and include kill criteria / disconfirming signals / review trigger per the
thesis review rules.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

from datetime import date as _date_cls
from datetime import datetime, timedelta
from pathlib import Path

# Folder names within the vault. Aligned with the numbered investment-brain
# layout; companies/pipeline have no numbered equivalent so keep dedicated dirs.
THESES_DIR = "04-theses"
GENERATED_THESES_DIR = f"{THESES_DIR}/generated"
PROMOTED_THESES_DIR = f"{THESES_DIR}/promoted"
REVIEWS_DIR = "08-reviews"
COMPANIES_DIR = "Companies"
PIPELINE_DIR = "Pipeline"

_FOLDERS = (
    THESES_DIR,
    GENERATED_THESES_DIR,
    PROMOTED_THESES_DIR,
    REVIEWS_DIR,
    COMPANIES_DIR,
    PIPELINE_DIR,
)


def _vault(vault_path: str | Path) -> Path:
    return Path(vault_path)


def ensure_vault(vault_path: str | Path) -> None:
    """Create the VC-loop folders under ``vault_path`` if missing."""
    root = _vault(vault_path)
    root.mkdir(parents=True, exist_ok=True)
    for folder in _FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)


def _yaml_scalar(value) -> str:
    """Render a scalar for YAML frontmatter, quoting strings safely."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ':#"\n'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def write_company_note(
    vault_path: str | Path, ticker: str, sector: str | None = None
) -> str:
    """Create ``Companies/<TICKER>.md`` if absent. Idempotent.

    Returns the path relative to the vault root. Does not clobber an existing
    note.
    """
    ensure_vault(vault_path)
    ticker = str(ticker).upper()
    rel = f"{COMPANIES_DIR}/{ticker}.md"
    path = _vault(vault_path) / rel
    if path.exists():
        return rel

    lines = [
        "---",
        f"title: {_yaml_scalar(ticker)}",
        f"ticker: {_yaml_scalar(ticker)}",
        "type: company",
        f"created: {_yaml_scalar(_date_cls.today().isoformat())}",
    ]
    if sector:
        lines.append(f"sector: {_yaml_scalar(sector)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {ticker}")
    lines.append("")
    if sector:
        lines.append(f"Sector: [[{sector}]]")
        lines.append("")
    lines.append("## Theses")
    lines.append("")
    lines.append(
        "Thesis memos for this company are linked here automatically as they "
        "are generated."
    )
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return rel


def _breakdown_table(breakdown: dict) -> list[str]:
    """Render the score breakdown as a markdown table."""
    rows = [
        "| Component | Weight | Value | Contribution |",
        "| --- | --- | --- | --- |",
    ]
    for key, comp in breakdown.items():
        if isinstance(comp, dict):
            weight = comp.get("weight", "")
            value = comp.get("value", "")
            contribution = comp.get("contribution", "")
        else:
            # Allow a flat {component: contribution} mapping too.
            weight = ""
            value = ""
            contribution = comp
        rows.append(f"| {key} | {weight} | {value} | {contribution} |")
    return rows


def _default_kill_criteria(conviction: float) -> list[str]:
    floor = 45
    return [
        f"Conviction falls below {floor} (proposed at {conviction}).",
        "Daily close breaks decisively below the 200-day moving average.",
    ]


def _default_disconfirming(features: dict) -> list[str]:
    signals = ["News sentiment flips and stays bearish."]
    if features.get("technical_strength") is not None:
        signals.append("Technical strength drops below 0.4.")
    signals.append("Momentum rolls over relative to the screen.")
    return signals


def _default_review_trigger(date: str, catalysts: list | None) -> str:
    # Prefer the nearest catalyst date; otherwise review ~30 days out.
    if catalysts:
        dates = []
        for cat in catalysts:
            ed = cat.get("event_date") if isinstance(cat, dict) else None
            if ed:
                dates.append(str(ed))
        if dates:
            return f"Next catalyst on {min(dates)}."
    try:
        base = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        base = _date_cls.today()
    return f"Review by {(base + timedelta(days=30)).isoformat()}."


def write_thesis_note(
    vault_path: str | Path,
    *,
    ticker: str,
    date: str,
    conviction: float,
    action: str,
    thesis: str,
    breakdown: dict,
    features: dict,
    sector: str | None = None,
    catalysts: list | None = None,
    account_fit: str | None = None,
) -> str:
    """Write a generated thesis note and return its relative path.

    Emits the shared ``type: thesis`` frontmatter (so the Thesis Index Dataview
    picks it up) and the required review sections.
    """
    ensure_vault(vault_path)
    ticker = str(ticker).upper()
    rel = f"{GENERATED_THESES_DIR}/{ticker}-{date}.md"
    path = _vault(vault_path) / rel

    confidence = round(conviction / 100.0, 3)

    lines = [
        "---",
        f"title: {_yaml_scalar(f'{ticker} thesis - {date}')}",
        "type: thesis",
        f"asset: {_yaml_scalar(ticker)}",
        f"ticker: {_yaml_scalar(ticker)}",
        f"account_fit: {_yaml_scalar(account_fit or '')}",
        "status: proposed",
        f"confidence: {_yaml_scalar(confidence)}",
        f"conviction: {_yaml_scalar(conviction)}",
        f"action: {_yaml_scalar(action)}",
        f"created: {_yaml_scalar(date)}",
        f"last_reviewed: {_yaml_scalar(date)}",
        f"source: {_yaml_scalar('vc_loop')}",
    ]
    if sector:
        lines.append(f"sector: {_yaml_scalar(sector)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {ticker} thesis — {date}")
    lines.append("")
    lines.append(f"Company: [[{ticker}]]")
    if sector:
        lines.append(f"Sector: [[{sector}]]")
    lines.append("")

    lines.append("## One-Line Thesis")
    lines.append("")
    lines.append(thesis or "_No thesis text provided._")
    lines.append("")

    lines.append("## Why Now")
    lines.append("")
    lines.append(f"Surfaced by the screen with conviction {conviction} → **{action}**.")
    lines.append("")

    if catalysts:
        lines.append("## Catalysts")
        lines.append("")
        for cat in catalysts:
            if isinstance(cat, dict):
                etype = cat.get("event_type", "event")
                edate = cat.get("event_date", "")
                label = f"{etype} ({edate})".strip()
            else:
                label = str(cat)
            lines.append(f"- [[{label}]]")
        lines.append("")

    lines.append("## Evidence")
    lines.append("")
    lines.extend(_breakdown_table(breakdown))
    lines.append("")
    if features:
        lines.append("| Feature | Value |")
        lines.append("| --- | --- |")
        for key, value in features.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    lines.append("## Kill Criteria")
    lines.append("")
    for item in _default_kill_criteria(conviction):
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Disconfirming Signals")
    lines.append("")
    for item in _default_disconfirming(features):
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Review Trigger")
    lines.append("")
    lines.append(_default_review_trigger(date, catalysts))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return rel


def _pipeline_table_lines(date: str, ranked: list[dict]) -> list[str]:
    lines = [
        "| Rank | Ticker | Conviction | Action | Thesis |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, entry in enumerate(ranked, start=1):
        ticker = str(entry.get("ticker", "")).upper()
        conviction = entry.get("conviction", "")
        action = entry.get("action", "")
        thesis_path = entry.get("thesis_path")
        if thesis_path:
            note_name = Path(str(thesis_path)).stem
            thesis_link = f"[[{note_name}]]"
        else:
            thesis_link = f"[[{ticker}-{date}]]"
        lines.append(
            f"| {i} | [[{ticker}]] | {conviction} | {action} | {thesis_link} |"
        )
    return lines


def _write_current_pipeline_note(
    vault_path: str | Path, *, date: str, ranked: list[dict]
) -> str:
    rel = f"{PIPELINE_DIR}/Current.md"
    path = _vault(vault_path) / rel

    lines = [
        "---",
        f"title: {_yaml_scalar('Current VC pipeline')}",
        f"date: {_yaml_scalar(date)}",
        f"created: {_yaml_scalar(date)}",
        "type: current-pipeline",
        f"source_note: {_yaml_scalar(f'{PIPELINE_DIR}/{date}.md')}",
        "---",
        "",
        "# Current VC pipeline",
        "",
        f"Source: [[{date}]]",
        "",
    ]
    lines.extend(_pipeline_table_lines(date, ranked))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return rel


def write_pipeline_note(
    vault_path: str | Path, *, date: str, ranked: list[dict]
) -> str:
    """Write ``Pipeline/<YYYY-MM-DD>.md`` with a ranked table.

    Each ``ranked`` entry should provide ``ticker``, ``conviction``, and
    ``action``. A ``thesis_path`` (relative path) links to the thesis note when
    present.

    Returns the relative path.
    """
    ensure_vault(vault_path)
    rel = f"{PIPELINE_DIR}/{date}.md"
    path = _vault(vault_path) / rel

    lines = [
        "---",
        f"title: {_yaml_scalar(f'VC pipeline - {date}')}",
        f"date: {_yaml_scalar(date)}",
        f"created: {_yaml_scalar(date)}",
        "type: pipeline",
        "---",
        "",
        f"# VC pipeline — {date}",
        "",
    ]
    lines.extend(_pipeline_table_lines(date, ranked))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    _write_current_pipeline_note(vault_path, date=date, ranked=ranked)
    return rel


def write_pipeline_base(vault_path: str | Path) -> str:
    """Write/refresh ``VC Pipeline.base`` at the vault root.

    Produces a minimal, valid Obsidian Bases file with a table view over the
    theses folder, surfacing conviction, action, and status. Returns the
    relative path.
    """
    ensure_vault(vault_path)
    rel = "VC Pipeline.base"
    path = _vault(vault_path) / rel

    content = (
        "filters:\n"
        "  and:\n"
        f'    - file.inFolder("{GENERATED_THESES_DIR}")\n'
        '    - type == "thesis"\n'
        "properties:\n"
        "  conviction:\n"
        "    displayName: Conviction\n"
        "  action:\n"
        "    displayName: Action\n"
        "  status:\n"
        "    displayName: Status\n"
        "  confidence:\n"
        "    displayName: Confidence\n"
        "views:\n"
        "  - type: table\n"
        "    name: Pipeline\n"
        "    order:\n"
        "      - file.name\n"
        "      - conviction\n"
        "      - action\n"
        "      - status\n"
        "    sort:\n"
        "      - property: conviction\n"
        "        direction: DESC\n"
    )
    path.write_text(content, encoding="utf-8")
    return rel
