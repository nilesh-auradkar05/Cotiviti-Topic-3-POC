"""SQLite-backed versioned rule store for Phase 6."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

from policyforge.schemas import ModifierIndicator, PTPRule, RuleCandidate


class RuleStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def seed_authoritative(self, rules: list[PTPRule], *, ruleset_version: str) -> None:
        for rule in rules:
            self._insert_rule(
                rule,
                ruleset_version=ruleset_version,
                origin="authoritative",
            )

    def add_approved(
        self,
        rule: PTPRule,
        candidate: RuleCandidate,
        *,
        ruleset_version: str,
        approver: str,
        approved_at: datetime,
        quote_grounded: bool = True,
    ) -> None:
        self._insert_rule(
            rule,
            ruleset_version=ruleset_version,
            origin="human_gated",
            approver=approver,
            approved_at=approved_at,
            source_chapter=candidate.source_chapter,
            source_quote=candidate.source_quote,
            extraction_confidence=candidate.extraction_confidence,
            quote_grounded=quote_grounded,
        )

    def load_ruleset(self, ruleset_version: str) -> list[PTPRule]:
        rows = self._connection.execute(
            """
            SELECT *
            FROM rules
            WHERE ruleset_version = ?
            ORDER BY id
            """,
            (ruleset_version,),
        ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def versions(self) -> list[str]:
        rows = self._connection.execute(
            """
            SELECT ruleset_version
            FROM rules
            GROUP BY ruleset_version
            ORDER BY MIN(id)
            """
        ).fetchall()
        return [row["ruleset_version"] for row in rows]

    def provenance_for(self, rule_id: str, ruleset_version: str) -> dict:
        row = self._connection.execute(
            """
            SELECT *
            FROM rules
            WHERE rule_id = ? AND ruleset_version = ?
            ORDER BY id
            LIMIT 1
            """,
            (rule_id, ruleset_version),
        ).fetchone()
        if row is None:
            raise KeyError(f"rule {rule_id!r} not found in ruleset {ruleset_version!r}")
        return {
            "rule_id": row["rule_id"],
            "ruleset_version": row["ruleset_version"],
            "origin": row["origin"],
            "json_logic": json.loads(row["json_logic"]),
            "approver": row["approver"],
            "approved_at": row["approved_at"],
            "source_chapter": row["source_chapter"],
            "source_quote": row["source_quote"],
            "extraction_confidence": row["extraction_confidence"],
            "quote_grounded": _bool_or_none(row["quote_grounded"]),
        }

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT NOT NULL,
                column_1 TEXT NOT NULL,
                column_2 TEXT NOT NULL,
                modifier_indicator INTEGER NOT NULL,
                effective_date TEXT NOT NULL,
                deletion_date TEXT,
                rationale TEXT NOT NULL,
                in_existence_prior_1996 INTEGER NOT NULL,
                json_logic TEXT NOT NULL,
                ruleset_version TEXT NOT NULL,
                origin TEXT NOT NULL,
                approver TEXT,
                approved_at TEXT,
                source_chapter TEXT,
                source_quote TEXT,
                extraction_confidence REAL,
                quote_grounded INTEGER
            )
            """
        )
        self._connection.commit()

    def _insert_rule(
        self,
        rule: PTPRule,
        *,
        ruleset_version: str,
        origin: str,
        approver: str | None = None,
        approved_at: datetime | None = None,
        source_chapter: str | None = None,
        source_quote: str | None = None,
        extraction_confidence: float | None = None,
        quote_grounded: bool | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO rules (
                rule_id,
                column_1,
                column_2,
                modifier_indicator,
                effective_date,
                deletion_date,
                rationale,
                in_existence_prior_1996,
                json_logic,
                ruleset_version,
                origin,
                approver,
                approved_at,
                source_chapter,
                source_quote,
                extraction_confidence,
                quote_grounded
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.rule_id,
                rule.column_1,
                rule.column_2,
                rule.modifier_indicator.value,
                rule.effective_date.isoformat(),
                _date_or_none(rule.deletion_date),
                rule.rationale,
                int(rule.in_existence_prior_1996),
                json.dumps(rule.to_json_logic(), sort_keys=True),
                ruleset_version,
                origin,
                approver,
                None if approved_at is None else approved_at.isoformat(),
                source_chapter,
                source_quote,
                extraction_confidence,
                None if quote_grounded is None else int(quote_grounded),
            ),
        )
        self._connection.commit()


def _rule_from_row(row: sqlite3.Row) -> PTPRule:
    return PTPRule(
        column_1=row["column_1"],
        column_2=row["column_2"],
        modifier_indicator=ModifierIndicator(row["modifier_indicator"]),
        effective_date=date.fromisoformat(row["effective_date"]),
        deletion_date=None
        if row["deletion_date"] is None
        else date.fromisoformat(row["deletion_date"]),
        rationale=row["rationale"],
        in_existence_prior_1996=bool(row["in_existence_prior_1996"]),
    )


def _date_or_none(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _bool_or_none(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)
