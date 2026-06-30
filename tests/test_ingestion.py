"""Phase 1 ingestion tests.

Each test is a policy scenario for loading CMS NCCI source data into the canonical
schemas. The assertions are about source semantics, not loader internals.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from policyforge.ingestion import load_policy_sections, load_ptp_table
from policyforge.schemas import ModifierIndicator


PTP_FIXTURE = Path("fixtures/sample_ptp.csv")


def _rules_by_id():
    return {rule.rule_id: rule for rule in load_ptp_table(PTP_FIXTURE)}


def _write_policy_manual_pdf(path: Path) -> None:
    pages = [
        [
            "CHAPTER I",
            "GENERAL CORRECT CODING POLICIES",
            "Column One code is eligible for payment.",
        ],
        [
            "CHAPTER II",
            "ANESTHESIA SERVICES",
            "Standard anesthesia coding applies to this chapter.",
        ],
    ]
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        "/MediaBox [0 0 612 792] /Contents 6 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        "/MediaBox [0 0 612 792] /Contents 7 0 R >>",
    ]
    for page in pages:
        lines = [f"({line}) Tj" if index == 0 else f"T* ({line}) Tj" for index, line in enumerate(page)]
        stream = "BT /F1 12 Tf 14 TL 72 720 Td " + " ".join(lines) + " ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")

    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    path.write_bytes(pdf)


def test_an_active_pair_loads_with_its_ccmi():
    rule = _rules_by_id()["PTP:11042:97597"]
    assert rule.modifier_indicator is ModifierIndicator.ALLOWED
    assert rule.deletion_date is None


def test_a_row_past_its_deletion_date_loads_with_the_deletion_date():
    rule = _rules_by_id()["PTP:36415:36416"]
    assert rule.deletion_date == date(2023, 12, 31)
    assert rule.is_active_on(date(2026, 7, 1)) is False


def test_an_active_deletion_sentinel_loads_as_an_active_edit():
    rule = _rules_by_id()["PTP:93000:93005"]
    assert rule.deletion_date is None


def test_a_yyyymmdd_effective_date_parses_to_a_real_date():
    rule = _rules_by_id()["PTP:11042:97597"]
    assert rule.effective_date == date(1996, 1, 1)


def test_the_prior_to_1996_marker_loads_as_a_boolean():
    rules = _rules_by_id()
    assert rules["PTP:11042:97597"].in_existence_prior_1996 is True
    assert rules["PTP:93000:93005"].in_existence_prior_1996 is False


def test_named_rows_load_their_authoritative_ccmi_values():
    rules = _rules_by_id()
    assert rules["PTP:11042:97597"].modifier_indicator is ModifierIndicator.ALLOWED
    assert rules["PTP:93000:93005"].modifier_indicator is ModifierIndicator.NOT_ALLOWED
    assert rules["PTP:36415:36416"].modifier_indicator is ModifierIndicator.NOT_APPLICABLE


def test_a_ccmi_outside_zero_one_or_nine_raises(tmp_path):
    bad_source = tmp_path / "bad_ccmi.csv"
    bad_source.write_text(
        PTP_FIXTURE.read_text().replace(
            "11042\t97597\t*\t19960101\t*\t1\tStandards of medical / surgical practice",
            "11042\t97597\t*\t19960101\t*\t7\tStandards of medical / surgical practice",
            1,
        )
    )

    with pytest.raises(ValueError):
        load_ptp_table(bad_source)


def test_a_known_authoritative_pair_is_present_after_load():
    assert "PTP:11042:97597" in _rules_by_id()


def test_no_data_row_is_silently_dropped():
    rules = load_ptp_table(PTP_FIXTURE)
    assert len(rules) == 6
    assert {rule.rule_id for rule in rules} == {
        "PTP:11042:97597",
        "PTP:93000:93005",
        "PTP:27447:27486",
        "PTP:80053:80048",
        "PTP:36415:36416",
        "PTP:99213:99214",
    }


def test_a_directory_of_ptp_segments_loads_each_segment(tmp_path):
    source_lines = PTP_FIXTURE.read_text().splitlines()
    header = "\n".join(source_lines[:6])
    (tmp_path / "segment_a.txt").write_text(f"{header}\n{source_lines[6]}\n")
    (tmp_path / "segment_b.txt").write_text(f"{header}\n{source_lines[7]}\n")

    rules = load_ptp_table(tmp_path)

    assert len(rules) == 2
    assert {rule.rule_id for rule in rules} == {
        "PTP:11042:97597",
        "PTP:93000:93005",
    }


def test_a_malformed_column_code_raises(tmp_path):
    bad_source = tmp_path / "bad_ptp.csv"
    bad_source.write_text(PTP_FIXTURE.read_text().replace("11042\t97597", "1104\t97597", 1))

    with pytest.raises(ValidationError):
        load_ptp_table(bad_source)


def test_a_known_policy_chapter_loads_keyed_by_its_label_with_text(tmp_path):
    manual = tmp_path / "manual.pdf"
    _write_policy_manual_pdf(manual)

    sections = load_policy_sections(manual)

    assert "Chapter 1" in sections
    assert "Column One code is eligible for payment." in sections["Chapter 1"]
    assert "Standard anesthesia coding applies to this chapter." not in sections["Chapter 1"]
