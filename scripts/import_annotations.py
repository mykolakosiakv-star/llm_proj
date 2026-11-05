"""
Скрипт імпорту результатів ручної експертної розмітки (scripts/import_annotations.py).
Зчитує заповнений файл CSV або JSON із експертними балами (1..5) та рішеннями щодо ескалації
і записує їх у таблицю evaluation_results (manual_scored=1, manual_annotator, relevance_score тощо).
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, init_db


def import_annotations_from_file(file_path: Path) -> Dict[str, Any]:
    """Імпортує ручну розмітку з JSON або CSV у базу даних SQLite."""
    init_db()
    if not file_path.exists():
        raise FileNotFoundError(f"Файл розмітки не знайдено: {file_path}")

    records: List[Dict[str, Any]] = []
    if file_path.suffix.lower() == ".json":
        records = json.loads(file_path.read_text(encoding="utf-8"))
    else:
        with file_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            records = list(reader)

    conn = get_connection()
    cur = conn.cursor()
    updated_count = 0

    for row in records:
        eval_id = int(row.get("eval_id") or 0)
        if not eval_id:
            continue

        raw_rel = str(row.get("human_relevance_1_5", "")).strip()
        if not raw_rel:
            continue

        rel = float(raw_rel)
        fact = float(row.get("human_factuality_1_5") or rel)
        comp = float(row.get("human_completeness_1_5") or rel)
        clar = float(row.get("human_clarity_1_5") or rel)
        pol = float(row.get("human_policy_adherence_1_5") or rel)
        avg_5 = (rel + fact + comp + clar + pol) / 5.0
        rubric_pct = round(((avg_5 - 1.0) / 4.0) * 100.0, 1)

        annotator = str(row.get("annotator_id") or "expert_1")
        notes = str(row.get("notes") or "")

        cur.execute(
            """
            UPDATE evaluation_results
            SET relevance_score = ?, factuality_score = ?, completeness_score = ?,
                clarity_score = ?, format_score = ?, policy_adherence_score = ?,
                nist_composite_pct = ?, rubric_composite_pct = ?,
                rubric_evaluated = 1, manual_scored = 1,
                manual_annotator = ?, manual_notes = ?
            WHERE id = ?
            """,
            (rel, fact, comp, clar, pol, pol, rubric_pct, rubric_pct, annotator, notes, eval_id),
        )
        if cur.rowcount > 0:
            updated_count += 1

    conn.commit()
    conn.close()
    return {"updated_count": updated_count, "total_rows": len(records)}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Імпорт ручної експертної розмітки у БД")
    parser.add_argument("file", type=str, help="Шлях до файлу annotation_sample.json або .csv")
    args = parser.parse_args()
    res = import_annotations_from_file(Path(args.file))
    print(f"[OK] Імпортовано експертних оцінок: {res['updated_count']} із {res['total_rows']}.")
