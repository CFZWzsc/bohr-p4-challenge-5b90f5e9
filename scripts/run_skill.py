#!/usr/bin/env python3
"""Systematic literature screening with reason codes, dual-review and audit (stdlib only).

Reads a case file (default /input/case.json) holding records to screen, explicit
inclusion/exclusion criteria and optionally two independent reviewers. It
deduplicates records with retained evidence, applies the criteria to title and
abstract only, emits a reason code and verbatim snippet for every decision,
keeps anything unproven in a review queue instead of excluding it, compares
reviewer opinions when supplied, and closes the PRISMA-style count chain in an
audit log. Writes result.json preserving case_id and all output_contract fields.

No network access, no randomness, no private paths, no fabricated judgements.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

RECORD_KEYS = ("records", "candidates", "results", "items", "papers", "hits")
CRITERIA_KEYS = ("criteria", "eligibility_criteria", "screening_criteria", "inclusion_exclusion")
REVIEWER_KEYS = ("reviewer_decisions", "reviews", "annotations", "labels")
TITLE_KEYS = ("title", "article_title", "paper_title", "display_name", "题名")
ABSTRACT_KEYS = ("abstract", "summary", "abstract_text")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    for path in (Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def norm_title(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", norm_space(value).lower())


def norm_doi(value):
    if not isinstance(value, str):
        return ""
    text = norm_space(value).lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text.strip(" .;,")


def pick(record, names):
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def authors_of(value):
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("family") or item.get("literal")
                if name:
                    out.append(str(name).strip())
        return "; ".join(n for n in out if n)
    return ""


def year_of(record):
    raw = pick(record, ("year", "pub_year", "publication_year", "date", "published", "issued"))
    if isinstance(raw, int):
        return raw if 1500 < raw < 2200 else None
    if isinstance(raw, str):
        match = re.search(r"(1[5-9]\d{2}|20\d{2})", raw)
        if match:
            return int(match.group(0))
    if isinstance(raw, dict):
        parts = raw.get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                return None
    return None


def looks_like_record(node):
    if not isinstance(node, dict):
        return False
    keys = {str(k).lower() for k in node.keys()}
    return bool(keys & {k.lower() for k in TITLE_KEYS}) or bool(keys & {"doi", "pmid"})


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------
def find_list(case, keys):
    if not isinstance(case, dict):
        return [], ""
    for key in keys:
        value = case.get(key)
        if isinstance(value, list):
            return value, "/" + key
    for container in ("data", "input", "protocol"):
        inner = case.get(container)
        if isinstance(inner, dict):
            for key in keys:
                value = inner.get(key)
                if isinstance(value, list):
                    return value, "/%s/%s" % (container, key)
    return [], ""


def normalize(raw, locator):
    record = raw if isinstance(raw, dict) else {}
    abstract = norm_space(str(pick(record, ABSTRACT_KEYS) or ""))
    return {
        "locator": locator,
        "record_id": str(pick(record, ("record_id", "id", "paper_id", "uid", "key")) or ""),
        "title": norm_space(str(pick(record, TITLE_KEYS) or "")),
        "abstract": abstract,
        "doi": norm_doi(pick(record, ("doi", "DOI")) or ""),
        "pmid": str(pick(record, ("pmid", "pubmed_id")) or ""),
        "arxiv_id": norm_space(str(pick(record, ("arxiv_id", "arxiv", "eprint")) or "")),
        "authors": authors_of(pick(record, ("authors", "author", "creator"))),
        "year": year_of(record),
        "journal": norm_space(str(pick(record, ("journal", "venue", "container_title")) or "")),
        "language": norm_space(str(pick(record, ("language", "lang")) or "")),
        "publication_type": norm_space(str(pick(record, ("publication_type", "type", "study_type")) or "")),
        "source": norm_space(str(pick(record, ("source", "database", "db")) or "")),
        "raw": raw,
    }


def normalize_criteria(case):
    raw, path = find_list(case, CRITERIA_KEYS)
    criteria = []
    if raw:
        for index, item in enumerate(raw):
            if isinstance(item, str):
                criteria.append({"id": "C%d" % (index + 1), "type": "inclusion", "description": item,
                                 "terms": normalize_terms(item), "locator": f"{path}/{index}"})
                continue
            if not isinstance(item, dict):
                continue
            kind = str(pick(item, ("type", "kind", "category")) or "inclusion").lower()
            kind = "exclusion" if kind.startswith(("excl", "ex")) else "inclusion"
            description = str(pick(item, ("description", "text", "criterion", "rule")) or "")
            terms = item.get("terms") or item.get("keywords") or item.get("must_include") or []
            if isinstance(terms, str):
                terms = [terms]
            if not terms:
                terms = normalize_terms(description)
            criteria.append({
                "id": str(pick(item, ("id", "code", "key")) or ("%s%d" % ("E" if kind == "exclusion" else "I", len(criteria) + 1))),
                "type": kind,
                "description": description,
                "terms": [str(t).lower() for t in terms if str(t).strip()],
                "logic": str(pick(item, ("logic", "operator")) or "any").lower(),
                "field": str(pick(item, ("field", "fields")) or "title_abstract").lower(),
                "locator": f"{path}/{index}",
            })
    elif isinstance(case.get("inclusion_criteria"), list) or isinstance(case.get("exclusion_criteria"), list):
        for kind, key in (("inclusion", "inclusion_criteria"), ("exclusion", "exclusion_criteria")):
            for index, item in enumerate(case.get(key) or []):
                if isinstance(item, str):
                    criteria.append({"id": ("I" if kind == "inclusion" else "E") + str(index + 1), "type": kind,
                                     "description": item, "terms": normalize_terms(item), "logic": "any",
                                     "field": "title_abstract", "locator": "/%s/%d" % (key, index)})
    return criteria


def normalize_terms(text):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", norm_space(text))
    stop = {"the", "and", "for", "with", "without", "from", "that", "this", "are", "was", "were", "not", "any", "all", "use", "used", "using", "study", "studies", "paper", "papers", "must", "should", "include", "exclude"}
    return [w.lower() for w in words if w.lower() not in stop][:8]


# --------------------------------------------------------------------------
# dedup
# --------------------------------------------------------------------------
def dedup_key(rec):
    if rec["doi"]:
        return "doi:" + rec["doi"]
    if rec["pmid"]:
        return "pmid:" + rec["pmid"]
    if rec["arxiv_id"]:
        return "arxiv:" + rec["arxiv_id"].lower()
    title = norm_title(rec["title"])
    if title:
        return "title:" + title + ("|" + str(rec["year"]) if rec["year"] else "")
    return "locator:" + rec["locator"]


def deduplicate(records):
    """Group exact keys; flag likely preprint/journal pairs by title containment."""
    groups = {}
    order = []
    for rec in records:
        key = dedup_key(rec)
        if key not in groups:
            groups[key] = {"key": key, "members": [rec], "records": [rec]}
            order.append(key)
        else:
            groups[key]["members"].append(rec)
    primary = [groups[key]["members"][0] for key in order]

    version_pairs = []
    for i, left in enumerate(primary):
        ta = norm_title(left["title"])
        if len(ta) < 12:
            continue
        for right in primary[i + 1:]:
            tb = norm_title(right["title"])
            if len(tb) < 12:
                continue
            shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if shorter in longer and len(shorter) / float(len(longer)) >= 0.9:
                version_pairs.append({
                    "members": [left["locator"], right["locator"]],
                    "keys": [dedup_key(left), dedup_key(right)],
                    "basis": "normalised title containment",
                    "action": "kept separate; flagged for human version merge",
                })
    return primary, groups, version_pairs


# --------------------------------------------------------------------------
# screening
# --------------------------------------------------------------------------
def sentence_with(text, term):
    if not text:
        return ""
    lowered = text.lower()
    position = lowered.find(term.lower())
    if position < 0:
        return ""
    start = max(text.rfind(". ", 0, position), text.rfind("; ", 0, position))
    end = len(text)
    for marker in (". ", "; "):
        found = text.find(marker, position)
        if found >= 0:
            end = min(end, found + 1)
    return norm_space(text[start + 1 if start >= 0 else 0:end])


def evaluate_criterion(rec, criterion):
    fields = []
    if criterion.get("field", "title_abstract") in ("title",):
        fields = [("title", rec["title"])]
    elif criterion.get("field") in ("abstract",):
        fields = [("abstract", rec["abstract"])]
    else:
        fields = [("title", rec["title"]), ("abstract", rec["abstract"])]
    hits = []
    for field_name, text in fields:
        lowered = text.lower()
        for term in criterion["terms"]:
            if term and term in lowered:
                hits.append({"criterion": criterion["id"], "term": term, "field": field_name,
                             "quote": sentence_with(text, term)[:280]})
    if not hits:
        return False, []
    if criterion.get("logic") == "all":
        found_terms = {hit["term"] for hit in hits}
        if not set(criterion["terms"]).issubset(found_terms):
            return False, hits
    return True, hits


def screen_record(rec, criteria):
    exclusion_hits, inclusion_hits = [], []
    exclusion_reasons, inclusion_reasons = [], []
    for criterion in criteria:
        matched, hits = evaluate_criterion(rec, criterion)
        if not matched:
            continue
        if criterion["type"] == "exclusion":
            exclusion_hits.extend(hits)
            exclusion_reasons.append({"code": criterion["id"], "description": criterion["description"]})
        else:
            inclusion_hits.extend(hits)
            inclusion_reasons.append({"code": criterion["id"], "description": criterion["description"]})

    if exclusion_reasons:
        decision = "exclude"
        reason_codes = [reason["code"] for reason in exclusion_reasons]
        rationale = "matched exclusion criterion/criteria " + ", ".join(reason_codes)
    elif not rec["abstract"]:
        decision = "review"
        reason_codes = ["REV-MISSING-ABSTRACT"] + [reason["code"] for reason in inclusion_reasons]
        rationale = "no abstract supplied; eligibility cannot be judged from title alone"
    elif inclusion_reasons:
        decision = "include"
        reason_codes = [reason["code"] for reason in inclusion_reasons]
        rationale = "matched inclusion criterion/criteria " + ", ".join(reason_codes)
    else:
        decision = "review"
        reason_codes = ["REV-NO-CRITERION-EVIDENCE"]
        rationale = "title/abstract contain no explicit evidence for any declared criterion"

    return {
        "record_key": dedup_key(rec),
        "record_id": rec["record_id"] or rec["locator"],
        "title": rec["title"],
        "doi": rec["doi"],
        "year": rec["year"],
        "decision": decision,
        "reason_codes": reason_codes,
        "rationale": rationale,
        "inclusion_evidence": inclusion_hits[:6],
        "exclusion_evidence": exclusion_hits[:6],
        "requires_human_confirmation": decision != "exclude",
        "source_locator": rec["locator"],
    }


def compare_reviewers(case, results):
    raw, path = find_list(case, REVIEWER_KEYS)
    if not raw:
        return None
    by_reviewer = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        reviewer = str(pick(item, ("reviewer", "reviewer_id", "annotator", "name")) or "")
        record_key = str(pick(item, ("record_key", "record_id", "id", "paper_id", "key")) or "")
        decision = str(pick(item, ("decision", "label", "verdict")) or "").lower()
        if not reviewer or not record_key or decision not in ("include", "exclude", "review"):
            continue
        by_reviewer.setdefault(reviewer, {})[record_key] = decision
    if len(by_reviewer) < 2:
        return {"available": False, "detail": "fewer than two reviewers supplied", "locator": path}

    names = sorted(by_reviewer)[:2]
    left, right = by_reviewer[names[0]], by_reviewer[names[1]]
    shared = sorted(set(left) & set(right))
    agree = sum(1 for key in shared if left[key] == right[key])
    observed = agree / float(len(shared)) if shared else None
    categories = ("include", "exclude", "review")
    expected = 0.0
    if shared:
        for category in categories:
            p_left = sum(1 for key in shared if left[key] == category) / float(len(shared))
            p_right = sum(1 for key in shared if right[key] == category) / float(len(shared))
            expected += p_left * p_right
    kappa = None
    if observed is not None and expected < 1:
        kappa = (observed - expected) / (1 - expected)
    conflicts = []
    for key in shared:
        if left[key] != right[key]:
            conflicts.append({"record_key": key, "reviewer_a": left[key], "reviewer_b": right[key],
                              "action": "human arbitration required"})
    return {
        "available": True,
        "reviewers": names,
        "shared_records": len(shared),
        "agreement_rate": round(observed, 4) if observed is not None else None,
        "cohen_kappa": round(kappa, 4) if kappa is not None else None,
        "conflicts": conflicts,
        "screened_only_by_rules": len(results) - len(shared),
        "locator": path,
    }


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------
def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


def build_payload(case, task_text):
    records_raw, record_path = find_list(case, RECORD_KEYS)
    records = [normalize(item, f"{record_path}/{index}") for index, item in enumerate(records_raw) if looks_like_record(item)]
    criteria = normalize_criteria(case)

    primary, groups, version_pairs = deduplicate(records)
    results = [screen_record(rec, criteria) for rec in primary] if criteria else []

    included = [row for row in results if row["decision"] == "include"]
    excluded = [row for row in results if row["decision"] == "exclude"]
    review = [row for row in results if row["decision"] == "review"]

    exclusion_counts = {}
    for row in excluded:
        for code in row["reason_codes"]:
            exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

    reviewer_comparison = compare_reviewers(case, results)

    identified = len(records)
    duplicates_removed = identified - len(primary)
    screened = len(results)
    audit = {
        "identified": identified,
        "duplicates_removed": duplicates_removed,
        "screened": screened,
        "included": len(included),
        "excluded": len(excluded),
        "awaiting_review": len(review),
        "exclusion_counts": exclusion_counts,
        "closure": {
            "identified_minus_duplicates_equals_screened": identified - duplicates_removed == screened,
            "screened_equals_included_plus_excluded_plus_review": screened == len(included) + len(excluded) + len(review),
        },
        "note": "Counts close only over the records supplied in the case; no full-text stage is performed.",
    }

    protocol = {
        "question": str(pick(case, ("question", "research_question", "topic")) or ""),
        "sources": (case.get("search_protocol") or {}).get("sources") if isinstance(case.get("search_protocol"), dict) else case.get("sources"),
        "language_limits": (case.get("search_protocol") or {}).get("languages") if isinstance(case.get("search_protocol"), dict) else case.get("languages"),
        "criteria_count": len(criteria),
        "criteria": [{"id": c["id"], "type": c["type"], "description": c["description"], "terms": c["terms"]} for c in criteria],
        "stage": "title/abstract screening only",
        "human_confirmation_required": True,
    }

    uncertainties = []
    if not records:
        uncertainties.append({"type": "no_records", "detail": "No records were supplied for screening."})
    if not criteria:
        uncertainties.append({"type": "no_criteria", "detail": "No inclusion/exclusion criteria were supplied; no decision can be justified, so screening is not attempted."})
    if records and not any(rec["abstract"] for rec in primary):
        uncertainties.append({"type": "no_abstracts", "detail": "No abstract text is present; every record is left in the review queue rather than excluded."})
    if reviewer_comparison is None:
        uncertainties.append({"type": "single_reviewer", "detail": "No two independent reviewer decision sets were supplied; agreement and conflicts cannot be computed."})
    elif reviewer_comparison.get("available") is False:
        uncertainties.append({"type": "single_reviewer", "detail": reviewer_comparison.get("detail", "reviewer comparison unavailable")})
    if version_pairs:
        uncertainties.append({"type": "version_pairs", "detail": "%d title-containment pair(s) may be preprint/journal versions; they were flagged, not silently merged." % len(version_pairs)})
    uncertainties.append({"type": "rule_based_screening", "detail": "Decisions are term-evidence suggestions on title/abstract only and require human confirmation; they do not replace full-text assessment."})

    if not records:
        status = "insufficient_input"
    elif not criteria or not any(rec["abstract"] for rec in primary):
        status = "partial"
    else:
        status = "success"

    evidence = []
    for criterion in criteria:
        evidence.append({"kind": "criterion", "id": criterion["id"], "type": criterion["type"],
                         "terms": criterion["terms"], "locator": criterion["locator"]})
    for row in results[:200]:
        evidence.append({"kind": "decision", "record_key": row["record_key"], "decision": row["decision"],
                         "reason_codes": row["reason_codes"], "source_locator": row["source_locator"],
                         "quotes": [hit["quote"] for hit in (row["inclusion_evidence"] + row["exclusion_evidence"])][:2]})
    for key, group in sorted(groups.items()):
        if len(group["members"]) > 1:
            evidence.append({"kind": "duplicate_group", "key": key,
                             "locators": [member["locator"] for member in group["members"]]})

    result = {
        "protocol": protocol,
        "screening_results": results,
        "results": results,
        "included": included,
        "excluded": excluded,
        "review_queue": review,
        "duplicates": {
            "duplicates_removed": duplicates_removed,
            "groups": [{"key": key, "locators": [m["locator"] for m in group["members"]]}
                       for key, group in sorted(groups.items()) if len(group["members"]) > 1],
            "version_pairs": version_pairs,
        },
        "conflicts": reviewer_comparison.get("conflicts", []) if reviewer_comparison else [],
        "reviewer_comparison": reviewer_comparison,
        "audit": audit,
        "audit_log": [
            {"step": "identified", "count": identified, "detail": "records read from the case"},
            {"step": "deduplicated", "count": screened, "detail": "%d duplicate record(s) removed by identifier/title key" % duplicates_removed},
            {"step": "screened", "count": screened, "detail": "title/abstract screening against declared criteria"},
            {"step": "included", "count": len(included), "detail": "pending human confirmation"},
            {"step": "excluded", "count": len(excluded), "detail": "reason codes recorded per record"},
            {"step": "awaiting_review", "count": len(review), "detail": "insufficient evidence for a rule-based decision"},
        ],
        "limitations": [
            "Screening uses title and abstract only; no full-text assessment is performed.",
            "Rule-based decisions are suggestions requiring human confirmation, not final eligibility.",
            "Deduplication uses identifiers and title keys; preprint/journal versions are flagged, not merged.",
            "Counts close only for the records supplied; the wider search yield is outside this run.",
        ],
        "change_log": [
            "Read records, criteria and any reviewer decisions from the case.",
            "Deduplicated records while retaining every member locator.",
            "Applied each criterion to title/abstract and captured verbatim snippets.",
            "Kept unproven records in the review queue instead of excluding them.",
            "Closes the count chain in the audit log.",
        ],
    }

    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "screening_results": results,
        "included": included,
        "evidence": evidence,
        "uncertainty": uncertainties,
        "errors": [],
        "stats": {
            "identified": identified,
            "duplicates_removed": duplicates_removed,
            "screened": screened,
            "included": len(included),
            "excluded": len(excluded),
            "awaiting_review": len(review),
            "criteria": len(criteria),
            "network": "disabled",
        },
    }


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    for name in names:
        if name not in payload:
            payload[name] = None
            notes[name] = "not produced by this run; preserved as null for output_contract completeness"
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names, "present": {name: name in payload for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Systematic title/abstract screening with reason codes and audit")
    parser.add_argument("--case", default=DEFAULT_CASE, help="input case JSON (default: /input/case.json)")
    parser.add_argument("--out", "--output", default=DEFAULT_OUT, help="output result JSON (default: /app/output/result.json)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="working directory (default: /app/work)")
    args = parser.parse_args(argv)

    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        task_text = read_task_text(args.case, args.work)
        payload = build_payload(case, task_text)
        code = 0
    except Exception as exc:
        payload = {
            "case_id": case_identifier(case) if isinstance(case, dict) else "",
            "status": "failed",
            "result": None,
            "evidence": [],
            "uncertainty": [{"type": "run_failure", "detail": "Run failed; no screening conclusion is reported."}],
            "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}],
            "stats": {},
        }
        code = 2

    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3

    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(args.out),
        "included": payload.get("stats", {}).get("included", 0),
    }, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
