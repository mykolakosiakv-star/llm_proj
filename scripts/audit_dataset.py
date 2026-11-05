"""
Скрипт автоматичного аудиту еталонної бази запитів (seed_data.py та SQLite):
- звітує кількість запитів у кожній предметній сфері та категорії;
- попереджає, якщо у сфері менше 30 запитів або є сильний перекіс між категоріями;
- перевіряє, що кожен елемент required_keywords реально присутній у reference_answer
  за правилами нормалізації та морфологічної стемізації evaluator.compute_fact_coverage;
- виявляє занадто загальні стоп-слова або однолітерні токени без смислового навантаження;
- шукає повтори запитів і запити, що майже дослівно збігаються з фрагментом-відповіддю (пошук стане тривіальним);
- рахує чернетки, що чекають на перевірку людиною: вони не потрапляють у серії.
"""

from pathlib import Path
import sys
from typing import Dict, Any, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import QUERY_CATEGORIES, DOMAIN_MIN_QUERIES_WARN
from app.database import init_db, fetch_all_queries
from app.seed_data import seed_database_if_needed
from app.services.classifier import jaccard_stem_similarity, tokenize_uk
from app.services.evaluator import compute_fact_coverage

# Запит, що настільки збігається з відповіддю, робить перевірку пошуку безглуздою
LEAK_SIMILARITY_THRESHOLD = 0.6

TOO_GENERIC_STOPWORDS = {
    "та", "і", "й", "у", "в", "на", "до", "за", "із", "зі", "по",
    "що", "як", "це", "чи", "або", "але", "для", "про", "при", "від",
}


def audit_domain_category_balance(queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_domain: Dict[str, int] = {}
    by_domain_cat: Dict[str, Dict[str, int]] = {}
    by_source: Dict[str, int] = {"manual": 0, "llm_draft": 0}
    by_review: Dict[str, int] = {"reviewed": 0, "draft": 0}
    warnings: List[str] = []

    all_cats = list(QUERY_CATEGORIES.keys())
    for q in queries:
        dom = str(q.get("domain") or "fintech")
        cat = str(q.get("category") or "informational")
        src = str(q.get("source") or "manual")
        rev = str(q.get("review_status") or "reviewed")
        by_domain[dom] = by_domain.get(dom, 0) + 1
        if dom not in by_domain_cat:
            by_domain_cat[dom] = {c: 0 for c in all_cats}
        by_domain_cat[dom][cat] = by_domain_cat[dom].get(cat, 0) + 1
        by_source[src] = by_source.get(src, 0) + 1
        by_review[rev] = by_review.get(rev, 0) + 1

    for dom, total_cnt in sorted(by_domain.items()):
        if total_cnt < DOMAIN_MIN_QUERIES_WARN:
            warnings.append(
                f"Сфера '{dom}' містить лише {total_cnt} запитів (рекомендовано >= {DOMAIN_MIN_QUERIES_WARN} для надійної перевірки гіпотези H1)."
            )
        cat_map = by_domain_cat.get(dom, {})
        counts = [cat_map.get(c, 0) for c in all_cats]
        min_c = min(counts) if counts else 0
        max_c = max(counts) if counts else 0
        if min_c == 0:
            missing_cats = [c for c in all_cats if cat_map.get(c, 0) == 0]
            warnings.append(f"Сфера '{dom}': відсутні запити у категоріях {missing_cats}.")
        elif max_c > min_c * 2.5:
            warnings.append(
                f"Сфера '{dom}': сильний перекіс між категоріями (min={min_c}, max={max_c})."
            )

    return {
        "total_queries": len(queries),
        "by_domain": by_domain,
        "by_domain_category": by_domain_cat,
        "by_source": by_source,
        "by_review_status": by_review,
        "warnings": warnings,
    }


def inspect_dataset_audit(queries: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    if queries is None:
        init_db()
        seed_database_if_needed()
        target_queries = fetch_all_queries(domain="all")
    else:
        target_queries = list(queries)
    balance_report = audit_domain_category_balance(target_queries)

    total = len(target_queries)
    perfect_100 = 0
    missing_in_ref = []
    generic_warnings = []
    empty_kw_queries = []

    for q in target_queries:
        code = q.get("code") or q.get("query_code") or ""
        ref = q.get("reference_answer") or ""
        kws = q.get("required_keywords") or q.get("expected_facts") or []

        if not kws:
            empty_kw_queries.append(code)
            continue

        cov, matched, missing = compute_fact_coverage(ref, kws)
        if cov == 100.0 and not missing:
            perfect_100 += 1
        else:
            missing_in_ref.append({
                "code": code,
                "coverage_pct": cov,
                "missing": missing,
                "reference_answer": ref,
            })

        for entry in kws:
            variants = entry if isinstance(entry, list) else [entry]
            for v in variants:
                v_clean = str(v).strip().lower()
                if v_clean in TOO_GENERIC_STOPWORDS or len(v_clean) < 1:
                    generic_warnings.append((code, v))

    seen: Dict[tuple, List[str]] = {}
    leaky_queries = []
    for q in target_queries:
        code = q.get("code") or ""
        key = (q.get("domain") or "fintech", " ".join(tokenize_uk(q.get("user_query") or "")))
        seen.setdefault(key, []).append(code)
        if not q.get("variant_of"):
            overlap = jaccard_stem_similarity(tokenize_uk(q.get("user_query") or ""), tokenize_uk(q.get("context_fact") or ""))
            if overlap >= LEAK_SIMILARITY_THRESHOLD:
                leaky_queries.append((code, round(overlap, 2)))
    duplicates = [codes for codes in seen.values() if len(codes) > 1]

    return {
        "total": total,
        "perfect_100": perfect_100,
        "duplicates": duplicates,
        "leaky_queries": leaky_queries,
        "missing_in_ref": missing_in_ref,
        "generic_warnings": generic_warnings,
        "empty_kw_queries": empty_kw_queries,
        "balance": balance_report,
    }


def run_dataset_audit(queries: List[Dict[str, Any]] = None) -> int:
    res = inspect_dataset_audit(queries)
    bal = res["balance"]

    print(f"=== АУДИТ ЕТАЛОННОГО ДАТАСЕТУ (N = {res['total']}) ===")
    print("Розподіл за предметними сферами та категоріями:")
    for dom, total_cnt in sorted(bal["by_domain"].items()):
        cats_str = ", ".join(f"{c}={cnt}" for c, cnt in bal["by_domain_category"][dom].items())
        print(f"  - {dom}: всього {total_cnt} ({cats_str})")
    print(f"Джерела: {bal['by_source']} | Статуси перевірки: {bal['by_review_status']}")

    if bal["warnings"]:
        print("Попередження щодо балансу вибірки:")
        for w in bal["warnings"]:
            print(f"  [WARN] {w}")
    else:
        print(f"Баланс сфер і категорій: OK (не менше {DOMAIN_MIN_QUERIES_WARN} запитів у кожній сфері, без перекосів)")
    drafts = bal["by_review_status"].get("draft", 0)
    if drafts:
        print(f"Чернеток, що чекають на перевірку: {drafts}. У серії вони не потрапляють, доки не затверджені.")

    print(f"100% покриття фактів на еталонних відповідях: {res['perfect_100']} / {res['total']}")

    if res["empty_kw_queries"]:
        print(f"[ПОМИЛКА] Запити без жодного required_keywords: {res['empty_kw_queries']}")

    if res["missing_in_ref"]:
        print(f"[ПОМИЛКА] Виявлено {len(res['missing_in_ref'])} запитів, де еталон не покриває required_keywords:")
        for item in res["missing_in_ref"]:
            print(f"  - {item['code']}: FC={item['coverage_pct']}%, пропущено={item['missing']}")
            print(f"    Еталон: {item['reference_answer']}")

    if res["duplicates"]:
        print(f"[ПОПЕРЕДЖЕННЯ] Повтори запитів: {res['duplicates']}")
    if res["leaky_queries"]:
        print(f"[ПОПЕРЕДЖЕННЯ] Запит майже збігається з фрагментом-відповіддю (пошук стане тривіальним): {res['leaky_queries']}")

    if res["generic_warnings"]:
        print(f"[ПОПЕРЕДЖЕННЯ] Надто загальні ключові слова: {res['generic_warnings']}")

    if res["missing_in_ref"] or res["empty_kw_queries"]:
        return 1
    has_warnings = bool(bal["warnings"] or res["duplicates"] or res["leaky_queries"] or res["generic_warnings"])
    if has_warnings:
        print("СТАТУС АУДИТУ: помилок немає, але є попередження вище. Для висновків за гіпотезами їх треба усунути.")
    else:
        print("СТАТУС АУДИТУ: OK (еталони покривають усі факти, баланс і унікальність у нормі)")
    return 0


def main() -> int:
    return run_dataset_audit()


if __name__ == "__main__":
    raise SystemExit(main())
