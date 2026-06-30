"""Phase 1 ingestion for CMS NCCI source files."""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import pdfplumber

from policyforge.schemas import ModifierIndicator, PTPRule


_PTP_HEADER_ROW = [
    "Column 1",
    "Column 2",
    "*=in existence",
    "Effective",
    "Deletion",
    "Modifier",
    "PTP Edit Rationale",
]
_PTP_HEADER_TO_FIELD = {
    "Column 1": "column_1",
    "Column 2": "column_2",
    "*=in existence prior to 1996": "in_existence_prior_1996",
    "Effective Date": "effective_date",
    "Deletion Date *=no data": "deletion_date",
    "Modifier 0=not allowed 1=allowed 9=not applicable": "modifier_indicator",
    "PTP Edit Rationale": "rationale",
}
_PTP_TABLE_SUFFIXES = {".csv", ".tsv", ".txt"}
_CHAPTER_HEADING = re.compile(r"^CHAPTER ([IVXLCDM]+)$", re.MULTILINE)
_ROMAN_CHAPTERS = {
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
    "IX": "9",
    "X": "10",
    "XI": "11",
    "XII": "12",
    "XIII": "13",
}


def load_ptp_table(path: str | Path) -> list[PTPRule]:
    source = Path(path)
    paths = (
        sorted(p for p in source.iterdir() if p.suffix.lower() in _PTP_TABLE_SUFFIXES)
        if source.is_dir()
        else [source]
    )

    rules = []
    for table_path in paths:
        rules.extend(_load_ptp_file(table_path))
    return rules


def load_policy_sections(path: str | Path) -> dict[str, str]:
    with pdfplumber.open(Path(path)) as pdf:
        manual_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    matches = list(_CHAPTER_HEADING.finditer(manual_text))
    sections = {}
    for index, match in enumerate(matches):
        label = f"Chapter {_ROMAN_CHAPTERS[match.group(1)]}"
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manual_text)
        sections[label] = manual_text[match.start():end].strip()
    return sections


def _load_ptp_file(path: Path) -> list[PTPRule]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.reader(source, delimiter="\t"))

    header_index = _ptp_header_index(rows)
    headers = _merged_ptp_headers(rows[header_index : header_index + 4])
    return [_ptp_rule(headers, row) for row in rows[header_index + 4 :] if any(row)]


def _ptp_header_index(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        if row[:7] == _PTP_HEADER_ROW:
            return index
    raise ValueError("PTP header row not found")


def _merged_ptp_headers(header_rows: list[list[str]]) -> list[str]:
    return [
        " ".join(row[column].strip() for row in header_rows if row[column].strip())
        for column in range(7)
    ]


def _ptp_rule(headers: list[str], row: list[str]) -> PTPRule:
    raw = {header: row[index].strip() for index, header in enumerate(headers)}
    fields = {_PTP_HEADER_TO_FIELD[header]: value for header, value in raw.items()}

    return PTPRule(
        column_1=fields["column_1"],
        column_2=fields["column_2"],
        modifier_indicator=ModifierIndicator(int(fields["modifier_indicator"])),
        effective_date=_yyyymmdd(fields["effective_date"]),
        deletion_date=_deletion_date(fields["deletion_date"]),
        rationale=fields["rationale"],
        in_existence_prior_1996=fields["in_existence_prior_1996"] == "*",
    )


def _yyyymmdd(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _deletion_date(value: str) -> date | None:
    if value in {"", "*"}:
        return None
    return _yyyymmdd(value)
