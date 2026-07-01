"""Phase 5 evaluation for extraction fidelity and adjudication correctness."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

from policyforge.engine import adjudicate
from policyforge.extraction import extract_rules
from policyforge.ingestion import load_policy_sections
from policyforge.retriever import DirectInjectionRetriever, build_chroma_index
from policyforge.schemas import (
    Claim,
    ClaimLine,
    DispositionStatus,
    EvalReport,
    ModifierIndicator,
    PTPRule,
    RuleCandidate,
    TrackAResult,
    TrackBResult,
)


ClaimCase = tuple[Claim, dict[str, DispositionStatus]]
ExtractFn = Callable[[str, str], list[RuleCandidate]]
_ROOT = Path(__file__).resolve().parents[2]
_PTP_TABLE_DIR = _ROOT / "data" / "ccipra-v322r0-f1"
_POLICY_MANUAL_PATH = _ROOT / "data" / "2026_ncci_medicare_policy_manual_all-chapters.pdf"
_GOLD_SET_PATH = _PTP_TABLE_DIR / "ncci_ptp_goldset_100.xlsx"
_XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_UNCERTAIN_DECISION = "UNCERTAIN_REVIEW_REQUIRED"
_DECISION_TO_STATUS = {
    "DENY_COLUMN_TWO": DispositionStatus.DENY,
    "DENY_COLUMN_TWO_MODIFIER_NOT_ALLOWED": DispositionStatus.DENY,
    "ALLOW_WITH_MODIFIER_REVIEW": DispositionStatus.FLAG,
    "ALLOW_DIFFERENT_DATE": DispositionStatus.PAY,
    "ALLOW_DIFFERENT_BENEFICIARY": DispositionStatus.PAY,
    "ALLOW_NO_ACTIVE_PTP_EDIT": DispositionStatus.PAY,
    "ALLOW_INACTIVE_EDIT_FOR_DOS": DispositionStatus.PAY,
}


def extractable_gold(gold: list[PTPRule], corpus_text: str) -> list[PTPRule]:
    """Gold pairs whose BOTH codes actually appear in the policy prose.

    Track A recall over the full gold set understates extraction quality: the PTP
    table holds hundreds of thousands of pairs, but the manual states only a small
    subset as explicit code pairs. A pair whose codes never appear in prose cannot
    be extracted from prose, so scoring against it measures the corpus, not the
    model. This is the honest denominator to report recall over.
    """
    normalized = _normalize_whitespace(corpus_text)
    return [
        rule
        for rule in gold
        if rule.column_1 in normalized and rule.column_2 in normalized
    ]


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def score_track_a(
    candidates: list[RuleCandidate],
    gold: list[PTPRule],
    retriever_name: str,
) -> TrackAResult:
    matched_gold = set()
    true_positives = 0
    false_positives = 0

    for candidate in candidates:
        match_index = next(
            (
                index
                for index, gold_rule in enumerate(gold)
                if index not in matched_gold and candidate.matches(gold_rule)
            ),
            None,
        )
        if match_index is None:
            false_positives += 1
        else:
            matched_gold.add(match_index)
            true_positives += 1

    false_negatives = len(gold) - true_positives
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = _ratio(2 * precision * recall, precision + recall)
    return TrackAResult(
        retriever_name=retriever_name,
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        n_examples=len(gold),
    )


def score_track_b(
    cases: list[ClaimCase],
    rules: list[PTPRule],
    ruleset_version: str,
) -> TrackBResult:
    n_correct = 0
    confusion: dict[str, int] = {}

    for claim, expected in cases:
        disposition = adjudicate(claim, rules, ruleset_version)
        predicted = {line.line_id: line.status for line in disposition.line_dispositions}
        claim_correct = True

        for line_id in sorted(set(predicted) | set(expected)):
            expected_status = expected.get(line_id)
            predicted_status = predicted.get(line_id)
            if predicted_status is not expected_status:
                claim_correct = False
            key = (
                f"expected={_status_label(expected_status)},"
                f"predicted={_status_label(predicted_status)}"
            )
            confusion[key] = confusion.get(key, 0) + 1

        if claim_correct:
            n_correct += 1

    return TrackBResult(
        n_claims=len(cases),
        n_correct=n_correct,
        accuracy=_ratio(n_correct, len(cases)),
        confusion=confusion,
    )


def evaluate(
    *,
    rules: list[PTPRule],
    gold_examples: list[PTPRule],
    claim_cases: list[ClaimCase],
    retrievers: Iterable,
    extract_fn: ExtractFn,
    ruleset_version: str,
) -> EvalReport:
    track_a = []
    for retriever in retrievers:
        candidates = []
        seen_chunks = set()
        for gold_rule in gold_examples:
            query = f"{gold_rule.column_1} {gold_rule.column_2}"
            for chunk in retriever.retrieve(query, k=5):
                chunk_key = (chunk.chapter, chunk.text)
                if chunk_key in seen_chunks:
                    continue
                seen_chunks.add(chunk_key)
                candidates.extend(extract_fn(chunk.text, chunk.chapter))
        track_a.append(
            score_track_a(_dedupe_candidates(candidates), gold_examples, retriever.name)
        )

    return EvalReport(
        ruleset_version=ruleset_version,
        track_a=track_a,
        track_b=score_track_b(claim_cases, rules, ruleset_version),
    )


def run_eval() -> EvalReport:
    report, _ = _run_eval_with_excluded_count()
    return report


def main() -> None:
    report, excluded_count = _run_eval_with_excluded_count()
    corpus_text = "\n".join(load_policy_sections(_POLICY_MANUAL_PATH).values())
    gold_rows = _load_xlsx_rows(_GOLD_SET_PATH)
    gold_examples = _rules_from_gold_rows(gold_rows)
    extractable = extractable_gold(gold_examples, corpus_text)
    print(
        "Track A is scoped to the 100-row gold set; source PTP rows are the scored "
        "extractable examples, and low recall over the full set is expected because "
        "most gold pairs never appear verbatim in policy-manual prose."
    )
    print(
        f"Extractable denominator: {len(extractable)} of {len(gold_examples)} gold "
        "pairs are stated as explicit code pairs in the prose. Recall over this "
        "subset is the honest extraction-fidelity number to report:"
    )
    for track in report.track_a:
        recall_extractable = _ratio(track.true_positives, len(extractable))
        print(
            f"  {track.retriever_name}: recall_over_extractable="
            f"{recall_extractable:.3f} "
            f"(tp={track.true_positives}, extractable={len(extractable)}); "
            f"recall_over_full_gold={track.recall:.3f}"
        )
    print(
        "Track B excluded "
        f"{excluded_count} UNCERTAIN_REVIEW_REQUIRED cases from deterministic accuracy."
    )
    print(report.model_dump_json(indent=2))


def _run_eval_with_excluded_count() -> tuple[EvalReport, int]:
    gold_rows = _load_xlsx_rows(_GOLD_SET_PATH)
    gold_examples = _rules_from_gold_rows(gold_rows)
    claim_cases, excluded_count = _claim_cases_from_gold_rows(gold_rows)
    corpus = load_policy_sections(_POLICY_MANUAL_PATH)
    retrievers = [
        DirectInjectionRetriever(corpus),
        build_chroma_index(corpus, collection_name="policyforge-eval"),
    ]
    report = evaluate(
        rules=gold_examples,
        gold_examples=gold_examples,
        claim_cases=claim_cases,
        retrievers=retrievers,
        extract_fn=extract_rules,
        ruleset_version=_ruleset_version(gold_rows),
    )
    return report, excluded_count


def _ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _status_label(status: DispositionStatus | None) -> str:
    if status is None:
        return "missing"
    return status.value


def _dedupe_candidates(candidates: list[RuleCandidate]) -> list[RuleCandidate]:
    by_key = {}
    for candidate in candidates:
        key = (candidate.column_1, candidate.column_2, candidate.modifier_indicator)
        by_key.setdefault(key, candidate)
    return list(by_key.values())


def _status_for_expected_decision(decision: str) -> DispositionStatus | None:
    if decision == _UNCERTAIN_DECISION:
        return None
    return _DECISION_TO_STATUS[decision]


def _claim_cases_from_gold_rows(rows: Iterable[Mapping[str, str]]) -> tuple[list[ClaimCase], int]:
    cases = []
    excluded_count = 0

    for row in rows:
        expected_status = _status_for_expected_decision(row["expected_decision"])
        if expected_status is None:
            excluded_count += 1
            continue

        if row["expected_decision"] == "ALLOW_DIFFERENT_BENEFICIARY":
            cases.extend(_different_beneficiary_cases(row))
            continue

        claim = Claim(
            claim_id=row["claim_id"],
            beneficiary_id=row["member_id_col1"],
            provider_id=row["provider_id"],
            lines=[
                ClaimLine(
                    line_id="L1",
                    code=row["line1_code"],
                    date_of_service=_date_value(row["date_of_service_col1"]),
                ),
                ClaimLine(
                    line_id="L2",
                    code=row["line2_code"],
                    modifiers=_modifiers(row["line2_modifiers"]),
                    date_of_service=_date_value(row["date_of_service_col2"]),
                ),
            ],
        )
        cases.append(
            (
                claim,
                {
                    "L1": DispositionStatus.PAY,
                    "L2": expected_status,
                },
            )
        )

    return cases, excluded_count


def _different_beneficiary_cases(row: Mapping[str, str]) -> list[ClaimCase]:
    return [
        (
            Claim(
                claim_id=f"{row['claim_id']}:L1",
                beneficiary_id=row["member_id_col1"],
                provider_id=row["provider_id"],
                lines=[
                    ClaimLine(
                        line_id="L1",
                        code=row["line1_code"],
                        date_of_service=_date_value(row["date_of_service_col1"]),
                    )
                ],
            ),
            {"L1": DispositionStatus.PAY},
        ),
        (
            Claim(
                claim_id=f"{row['claim_id']}:L2",
                beneficiary_id=row["member_id_col2"],
                provider_id=row["provider_id"],
                lines=[
                    ClaimLine(
                        line_id="L2",
                        code=row["line2_code"],
                        modifiers=_modifiers(row["line2_modifiers"]),
                        date_of_service=_date_value(row["date_of_service_col2"]),
                    )
                ],
            ),
            {"L2": DispositionStatus.PAY},
        ),
    ]


def _rules_from_gold_rows(rows: Iterable[Mapping[str, str]]) -> list[PTPRule]:
    return [
        PTPRule(
            column_1=row["source_column_1"],
            column_2=row["source_column_2"],
            modifier_indicator=ModifierIndicator(int(row["modifier_indicator"])),
            effective_date=_date_value(row["source_effective_date"]),
            deletion_date=_optional_date(row["source_deletion_date"]),
            rationale=row["ptp_edit_rationale"],
        )
        for row in rows
        if row["is_source_ptp_pair"] == "1"
    ]


def _ruleset_version(rows: list[Mapping[str, str]]) -> str:
    return rows[0]["source_version"]


def _modifiers(value: str) -> list[str]:
    return [part.strip().upper() for part in re.split(r"[,;\s]+", value) if part.strip()]


def _optional_date(value: str) -> date | None:
    if value == "":
        return None
    return _date_value(value)


def _date_value(value: str) -> date:
    if "-" in value:
        return date.fromisoformat(value)
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _load_xlsx_rows(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    row_values = []
    for row in sheet.findall("x:sheetData/x:row", _XLSX_NS):
        cells = {}
        for cell in row.findall("x:c", _XLSX_NS):
            cells[_column_number(cell.attrib["r"])] = _cell_value(cell, shared_strings)
        if not cells:
            continue
        width = max(max(cells), len(row_values[0]) if row_values else 0)
        row_values.append([cells.get(index, "") for index in range(1, width + 1)])

    headers = row_values[0]
    return [
        dict(zip(headers, (values + [""] * len(headers))[: len(headers)]))
        for values in row_values[1:]
    ]


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(text.itertext()) for text in root.findall("x:si", _XLSX_NS)]


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    value = cell.find("x:v", _XLSX_NS)
    if cell.attrib.get("t") == "inlineStr":
        inline = cell.find("x:is", _XLSX_NS)
        return "" if inline is None else "".join(inline.itertext())
    if value is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared_strings[int(value.text or "0")]
    return value.text or ""


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


if __name__ == "__main__":
    main()
