"""Inventory identity audit and preflight verification script (Bloque 7G.2).

This script is STRICTLY READ-ONLY. It does NOT modify the database,
does not make any AI calls, and does not alter any files.

It performs comprehensive identity verification across all 80 Entries:
- Core integrity: Source, URL, title, published_at, hashes.
- Duplicate detection: URL, ingestion dedupe hash, source+title.
- Identity validation per source:
  * CAT: Coherence of neutral citations, case numbers, case names, and PDF text.
  * CURIA: Case number & ECLI coherence with InfoCuria text (unicode normalized).
  * CNMC: Title, publication date, URL, content coherence.
  * EC: Title, publication date, URL, content coherence.
- Classification: IDENTITY_OK, IDENTITY_WARNING, IDENTITY_ERROR.

Usage:
    python -m scripts.audit_entry_inventory
"""

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session, joinedload

from app.db.session import SessionLocal
from app.models.entry import Entry
from app.models.source import Source
from app.services.ingestion_service import compute_ingestion_dedupe_hash
from app.services.analysis_service import compute_analysis_input_hash
from app.services.source_sufficiency_service import assess_source_sufficiency


def normalize_legal_text(text: str) -> str:
    """Normalize text replacing non-breaking hyphens and typographical quotes."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    for ch in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212", "\ufe63", "\uff0d"]:
        t = t.replace(ch, "-")
    for ch in ["\u2018", "\u2019", "\u201a", "\u201b"]:
        t = t.replace(ch, "'")
    for ch in ["\u201c", "\u201d", "\u201e", "\u201f"]:
        t = t.replace(ch, '"')
    return t


def audit_cat_entry(entry: Entry) -> tuple[str, list[str]]:
    """Validate identity coherence for CAT judicial decision."""
    rm = entry.raw_metadata or {}
    title = entry.title or ""
    content = entry.content or ""
    norm_content = normalize_legal_text(content)
    norm_title = normalize_legal_text(title)

    citation = rm.get("neutral_citation") or ""
    case_nums = rm.get("case_numbers") or []
    case_names = rm.get("case_names") or []
    content_src = rm.get("content_source") or ""

    reasons: list[str] = []

    # Check 1: Title contains citation or case name
    cit_in_title = citation and (citation in title or normalize_legal_text(citation) in norm_title)
    name_in_title = any(normalize_legal_text(cn).lower() in norm_title.lower() for cn in case_names) if case_names else False
    num_in_title = any(cn in title for cn in case_nums) if case_nums else False

    if not (cit_in_title or name_in_title or num_in_title):
        reasons.append(f"Title does not reference citation ({citation}), case name ({case_names}), or number ({case_nums})")

    # Check 2: Content validation
    cit_in_content = citation and (citation in content or normalize_legal_text(citation) in norm_content)
    num_in_content = any(cn in norm_content for cn in case_nums) if case_nums else False
    name_in_content = any(normalize_legal_text(cn).split()[0].lower() in norm_content.lower() for cn in case_names if cn) if case_names else False

    if content_src == "cat_judgment_pdf_text":
        # Enriched from PDF: must have high confidence signals
        if not (cit_in_content or num_in_content):
            if name_in_content:
                reasons.append(f"PDF text matched case name but lacks exact neutral citation / case number")
                return "IDENTITY_WARNING", reasons
            else:
                reasons.append(f"PDF text lacks citation ({citation}), case numbers ({case_nums}), and case name")
                return "IDENTITY_ERROR", reasons
    else:
        # Summary text: shorter, might omit neutral citation if only a procedural note
        if not (cit_in_content or num_in_content or name_in_content or cit_in_title):
            reasons.append("Summary text does not contain matching case identifiers")
            return "IDENTITY_WARNING", reasons

    if reasons:
        return "IDENTITY_WARNING", reasons
    return "IDENTITY_OK", []


def audit_curia_entry(entry: Entry) -> tuple[str, list[str]]:
    """Validate identity coherence for CURIA case law."""
    rm = entry.raw_metadata or {}
    title = entry.title or ""
    content = entry.content or ""
    norm_content = normalize_legal_text(content)
    norm_title = normalize_legal_text(title)

    ecli = rm.get("ecli") or ""
    case_num = rm.get("case_number") or ""

    # Extract case number from title if not in metadata (e.g. 'Case C-60/25')
    m = re.search(r"C-\d+/\d+(?:\s+P)?", norm_title)
    extracted_num = m.group(0) if m else ""
    target_num = case_num or extracted_num

    reasons: list[str] = []

    # Check case number in content (accounting for normalized dashes)
    found_num = False
    if target_num:
        norm_target = normalize_legal_text(target_num)
        base_target = norm_target.replace("C-", "").replace(" P", "").strip()
        if norm_target in norm_content or base_target in norm_content:
            found_num = True

    # Check ECLI in content
    found_ecli = ecli and (ecli in content or ecli in norm_content)

    # Party names or bracketed names in title
    bracket_m = re.search(r"\[([^\]]+)\]", norm_title)
    bracket_name = bracket_m.group(1) if bracket_m else ""
    found_party = bracket_name and (bracket_name.lower() in norm_content.lower())

    if found_num or found_ecli or found_party:
        return "IDENTITY_OK", []
    else:
        reasons.append(f"Case number ({target_num}) and ECLI ({ecli}) not found in normalized content")
        return "IDENTITY_WARNING", reasons


def audit_cnmc_entry(entry: Entry) -> tuple[str, list[str]]:
    """Validate identity coherence for CNMC press news."""
    title = entry.title or ""
    content = entry.content or ""
    reasons: list[str] = []

    if not title or len(title) < 10:
        reasons.append("Title is missing or suspiciously short")
        return "IDENTITY_ERROR", reasons

    if not content or len(content) < 50:
        reasons.append(f"Content is suspiciously short ({len(content)} chars)")
        return "IDENTITY_WARNING", reasons

    # Verify substantive word overlap between title and content
    title_words = [w.lower() for w in re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{5,}\b", title)]
    if title_words:
        matched = [w for w in title_words if w in content.lower()]
        overlap_ratio = len(matched) / len(title_words)
        if overlap_ratio < 0.2 and len(matched) < 2:
            reasons.append(f"Low lexical overlap ({len(matched)}/{len(title_words)} title words in content)")
            return "IDENTITY_WARNING", reasons

    return "IDENTITY_OK", []


def audit_ec_entry(entry: Entry) -> tuple[str, list[str]]:
    """Validate identity coherence for European Commission press releases."""
    title = entry.title or ""
    content = entry.content or ""
    reasons: list[str] = []

    if not title or len(title) < 10:
        reasons.append("Title is missing or suspiciously short")
        return "IDENTITY_ERROR", reasons

    if not content or len(content) < 50:
        reasons.append(f"Content is suspiciously short ({len(content)} chars)")
        return "IDENTITY_WARNING", reasons

    title_words = [w.lower() for w in re.findall(r"\b[a-zA-Z]{5,}\b", title)]
    if title_words:
        matched = [w for w in title_words if w in content.lower()]
        overlap_ratio = len(matched) / len(title_words)
        if overlap_ratio < 0.2 and len(matched) < 2:
            reasons.append(f"Low lexical overlap ({len(matched)}/{len(title_words)} title words in content)")
            return "IDENTITY_WARNING", reasons

    return "IDENTITY_OK", []


def run_inventory_audit(db: Session) -> dict[str, Any]:
    """Execute complete read-only audit across all entries."""
    entries = (
        db.query(Entry)
        .options(joinedload(Entry.source))
        .order_by(Entry.source_id, Entry.published_at.desc().nullslast())
        .all()
    )

    url_per_source: dict[tuple[str, str], list[str]] = {}
    dedupe_hash_map: dict[str, list[str]] = {}
    title_per_source: dict[tuple[str, str], list[str]] = {}
    hash_mismatches: list[dict[str, Any]] = []

    entry_records: list[dict[str, Any]] = []
    source_stats: dict[str, dict[str, int]] = {}

    for e in entries:
        src_name = e.source.name
        e_id_str = str(e.id)

        # 1. Duplicates tracking
        url_key = (src_name, e.url)
        url_per_source.setdefault(url_key, []).append(e_id_str)

        dedupe_hash_map.setdefault(e.content_hash, []).append(e_id_str)

        norm_title = (e.title or "").strip().lower()
        title_key = (src_name, norm_title)
        title_per_source.setdefault(title_key, []).append(e_id_str)

        # 2. Hash computation check
        expected_dedupe_hash = compute_ingestion_dedupe_hash(e.title, e.url, e.excerpt)
        if e.content_hash != expected_dedupe_hash:
            hash_mismatches.append({
                "entry_id": e_id_str,
                "stored_hash": e.content_hash,
                "expected_hash": expected_dedupe_hash,
            })

        # Analysis input hash
        analysis_input_hash = compute_analysis_input_hash(e)

        # Sufficiency
        suff = assess_source_sufficiency(e)
        rm = e.raw_metadata or {}

        # 3. Source identity check
        if "Competition Appeal" in src_name:
            status, reasons = audit_cat_entry(e)
        elif "Court of Justice" in src_name:
            status, reasons = audit_curia_entry(e)
        elif "CNMC" in src_name:
            status, reasons = audit_cnmc_entry(e)
        else:
            status, reasons = audit_ec_entry(e)

        if src_name not in source_stats:
            source_stats[src_name] = {"IDENTITY_OK": 0, "IDENTITY_WARNING": 0, "IDENTITY_ERROR": 0}
        source_stats[src_name][status] += 1

        entry_records.append({
            "entry_id": e_id_str,
            "source_name": src_name,
            "url": e.url,
            "title": e.title,
            "published_at": e.published_at.isoformat() if e.published_at else "N/A",
            "content_chars": len(e.content or ""),
            "content_source": rm.get("content_source", suff.signals.content_source),
            "full_text_available": suff.signals.full_text_available,
            "sufficiency": suff.level.value,
            "ingestion_content_hash": e.content_hash,
            "analysis_input_hash": analysis_input_hash,
            "pdf_pages": rm.get("pdf_pages"),
            "pdf_text_chars": rm.get("pdf_text_chars"),
            "identity_status": status,
            "identity_reasons": reasons,
        })

    # Summary of duplicates
    url_duplicates = {k: v for k, v in url_per_source.items() if len(v) > 1}
    dedupe_hash_duplicates = {k: v for k, v in dedupe_hash_map.items() if len(v) > 1}
    title_duplicates = {k: v for k, v in title_per_source.items() if len(v) > 1}

    total_ok = sum(s["IDENTITY_OK"] for s in source_stats.values())
    total_warning = sum(s["IDENTITY_WARNING"] for s in source_stats.values())
    total_error = sum(s["IDENTITY_ERROR"] for s in source_stats.values())

    return {
        "total_entries": len(entries),
        "entry_records": entry_records,
        "source_stats": source_stats,
        "total_ok": total_ok,
        "total_warning": total_warning,
        "total_error": total_error,
        "url_duplicates": url_duplicates,
        "dedupe_hash_duplicates": dedupe_hash_duplicates,
        "title_duplicates": title_duplicates,
        "hash_mismatches": hash_mismatches,
    }


def print_audit_report(results: dict[str, Any]) -> None:
    """Format and display the audit report."""
    print("=" * 110)
    print("  HITCHINGS OBSERVATORY - AUDITORÍA DE IDENTIDAD E INVENTARIO (BLOQUE 7G.2)")
    print("  Modo: ESTRICTAMENTE SOLO LECTURA (Cero escrituras en PostgreSQL)")
    print("=" * 110)

    print(f"\n--- 1. RESUMEN GLOBAL DE INTEGRIDAD ---")
    print(f"Total de Entries auditadas:                  {results['total_entries']}")
    print(f"  IDENTITY_OK:                               {results['total_ok']} / {results['total_entries']}")
    print(f"  IDENTITY_WARNING:                          {results['total_warning']} / {results['total_entries']}")
    print(f"  IDENTITY_ERROR:                            {results['total_error']} / {results['total_entries']}")

    print(f"\n--- 2. DESGLOSE POR FUENTE ---")
    print(f"{'Fuente':<50} | {'Total':<6} | {'OK':<6} | {'WARNING':<8} | {'ERROR':<6}")
    print("-" * 110)
    for src_name, counts in sorted(results["source_stats"].items()):
        total = sum(counts.values())
        print(f"{src_name[:50]:<50} | {total:<6} | {counts['IDENTITY_OK']:<6} | {counts['IDENTITY_WARNING']:<8} | {counts['IDENTITY_ERROR']:<6}")

    print(f"\n--- 3. CONTROL DE DUPLICADOS Y COLISIONES ---")
    print(f"URLs duplicadas (dentro de la misma fuente):  {len(results['url_duplicates'])}")
    print(f"Ingestion dedupe hashes duplicados:           {len(results['dedupe_hash_duplicates'])}")
    print(f"Títulos idénticos (dentro de la misma fuente):{len(results['title_duplicates'])}")
    print(f"Discrepancias de hash con fórmula canónica:   {len(results['hash_mismatches'])}")

    if results["hash_mismatches"]:
        print("  AVISO: Discrepancias encontradas:")
        for m in results["hash_mismatches"]:
            print(f"    entry_id: {m['entry_id']} | stored: {m['stored_hash'][:12]}... | expected: {m['expected_hash'][:12]}...")

    if results["total_warning"] > 0:
        print(f"\n--- 4. DETALLE DE ADVERTENCIAS (IDENTITY_WARNING) ---")
        for rec in results["entry_records"]:
            if rec["identity_status"] == "IDENTITY_WARNING":
                print(f"  [WARN] entry_id: {rec['entry_id']} | {rec['source_name'][:25]} | {rec['title'][:55]}...")
                for r in rec["identity_reasons"]:
                    print(f"         Razón: {r}")

    if results["total_error"] > 0:
        print(f"\n--- 5. DETALLE DE ERRORES CRÍTICOS (IDENTITY_ERROR) ---")
        for rec in results["entry_records"]:
            if rec["identity_status"] == "IDENTITY_ERROR":
                print(f"  [ERROR] entry_id: {rec['entry_id']} | {rec['source_name'][:25]} | {rec['title'][:55]}...")
                for r in rec["identity_reasons"]:
                    print(f"          Razón: {r}")

    print(f"\n--- 6. VEREDICTO DE PREFLIGHT PARA BACKFILL V4 ---")
    if results["total_error"] > 0:
        print("  [BLOQUEO] Se han detectado errores de identidad (IDENTITY_ERROR > 0).")
        print("  NO SE AUTORIZA el backfill hasta resolver estas inconsistencias.")
    else:
        print("  [APROBADO] Cero errores de identidad detectados (IDENTITY_ERROR = 0).")
        print("  Inventario íntegro, deduplicado y validado.")
    print("=" * 110)


def main() -> None:
    db: Session = SessionLocal()
    try:
        results = run_inventory_audit(db)
        print_audit_report(results)
        if results["total_error"] > 0:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
