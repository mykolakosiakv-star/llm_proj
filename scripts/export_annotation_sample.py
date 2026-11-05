"""
Скрипт стратифікованого експорту вибірки відповідей для ручної експертної розмітки
(scripts/export_annotation_sample.py).
Вибирає стратифіковану вибірку (за замовчуванням >= 60 записів за категоріями, моделями та промптами)
із таблиці evaluation_results та зберігає у файли exports/annotation_sample.csv і exports/annotation_sample.json
без зазначення назви моделі у полі для анотатора (для сліпої перевірки).
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import EXPORTS_DIR
from app.database import get_connection, init_db


def export_stratified_sample(
    target_size: int = 60,
    seed: int = 42,
    include_legacy: bool = True,
) -> Dict[str, Any]:
    """Формує стратифіковану вибірку замірів для ручної розмітки за шкалою 1..5."""
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    sql = """
        SELECT e.id AS eval_id, e.run_id, e.query_code, e.category, e.prompt_code, e.model_id,
               e.generated_answer, e.requires_escalation,
               q.user_query, q.context_fact, q.reference_answer, q.expected_escalation
        FROM evaluation_results e
        LEFT JOIN queries q ON e.query_id = q.id
        LEFT JOIN experiment_runs r ON e.run_id = r.id
    """
    if not include_legacy:
        sql += " WHERE COALESCE(r.is_legacy, 0) = 0"
    sql += " ORDER BY e.id ASC"

    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Стратифікація за (category, prompt_code)
    strata: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        key = f"{r['category']}|{r['prompt_code']}"
        strata.setdefault(key, []).append(r)

    rng = random.Random(seed)
    for k in strata:
        rng.shuffle(strata[k])

    selected: List[Dict[str, Any]] = []
    strata_keys = sorted(strata.keys())
    idx = 0
    while len(selected) < min(target_size, len(rows)) and any(strata.values()):
        k = strata_keys[idx % len(strata_keys)]
        if strata[k]:
            selected.append(strata[k].pop(0))
        idx += 1

    sample_items = []
    for item in selected:
        sample_items.append({
            "eval_id": item["eval_id"],
            "query_code": item["query_code"],
            "category": item["category"],
            "user_query": item["user_query"] or "",
            "context_fact": item["context_fact"] or "",
            "reference_answer": item["reference_answer"] or "",
            "expected_escalation": bool(item["expected_escalation"]),
            "generated_answer": item["generated_answer"],
            # Поля для заповнення людиною-експертом (1..5 та рішення ескалації true/false)
            "annotator_id": "expert_1",
            "human_relevance_1_5": "",
            "human_factuality_1_5": "",
            "human_completeness_1_5": "",
            "human_clarity_1_5": "",
            "human_policy_adherence_1_5": "",
            "human_escalation_decision": "",
            "notes": "",
        })

    json_path = EXPORTS_DIR / "annotation_sample.json"
    csv_path = EXPORTS_DIR / "annotation_sample.csv"

    json_path.write_text(json.dumps(sample_items, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "eval_id", "query_code", "category", "user_query", "context_fact",
        "reference_answer", "expected_escalation", "generated_answer",
        "annotator_id", "human_relevance_1_5", "human_factuality_1_5",
        "human_completeness_1_5", "human_clarity_1_5", "human_policy_adherence_1_5",
        "human_escalation_decision", "notes",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for row in sample_items:
            writer.writerow(row)

    return {
        "exported_count": len(sample_items),
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Експорт стратифікованої вибірки для ручної розмітки")
    parser.add_argument("--size", type=int, default=60, help="Кількість записів у вибірці")
    args = parser.parse_args()
    info = export_stratified_sample(target_size=args.size)
    print(f"[OK] Експортовано {info['exported_count']} записів у:\n  - {info['json_path']}\n  - {info['csv_path']}")
