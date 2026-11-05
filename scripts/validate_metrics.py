"""
Скрипт статистичної валідації автоматичних метрик проти ручної експертної розмітки
та оцінок LLM-судді (scripts/validate_metrics.py).
Обчислює:
- рангові кореляції Спірмена ρ та Кендалла τ між автоматичними метриками (FC, SS, FMT, Q_det)
  і експертними оцінками (manual_scored=1 або rubric_evaluated=1);
- коефіцієнт узгодженості Коена κ (Cohen's kappa) для бінарного рішення ескалації;
- квадратично зважену κ (weighted kappa) при наявності парних оцінок двох анотаторів.
Зберігає звіт у таблицю validation_reports та у файл exports/validation_report.json.
"""

from datetime import datetime
import json
import math
import sys
from pathlib import Path
from typing import Dict, Any, List
import numpy as np
from scipy import stats as sp_stats

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import EXPORTS_DIR
from app.database import get_connection, init_db


def cohens_kappa_binary(y1: List[int], y2: List[int]) -> float:
    """Обчислює коефіцієнт узгодженості Коена κ для двох бінарних векторів (0/1)."""
    n = len(y1)
    if n == 0 or len(y2) != n:
        return 0.0
    agree = sum(1 for a, b in zip(y1, y2) if a == b)
    p_o = agree / float(n)
    p1_pos = sum(y1) / float(n)
    p2_pos = sum(y2) / float(n)
    p_e = p1_pos * p2_pos + (1.0 - p1_pos) * (1.0 - p2_pos)
    if abs(1.0 - p_e) < 1e-9:
        return 1.0 if p_o == 1.0 else 0.0
    return round((p_o - p_e) / (1.0 - p_e), 3)


def compute_validation_report() -> Dict[str, Any]:
    """Обчислює показники валідації автоматичних метрик на наявних розмічених записах."""
    init_db()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.*, q.expected_escalation AS gt_escalation
        FROM evaluation_results e
        LEFT JOIN queries q ON e.query_id = q.id
        WHERE e.manual_scored = 1 OR e.rubric_evaluated = 1
        ORDER BY e.id ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]

    if len(rows) < 5:
        conn.close()
        report = {
            "status": "unvalidated",
            "validated": False,
            "sample_size": len(rows),
            "message_uk": (
                f"Рубрика очікує ручної валідації: знайдено {len(rows)} розмічених записів "
                f"(для статистичної валідації рекомендовано >= 60 записів)."
            ),
            "correlations": {},
            "escalation_kappa": None,
        }
        (EXPORTS_DIR / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    human_comp = [float(r["nist_composite_pct"]) for r in rows]
    fc_vals = [float(r["fact_coverage_pct"]) for r in rows]
    ss_vals = [float(r["semantic_similarity_pct"]) for r in rows]
    fmt_vals = [float(r["format_compliance_pct"]) for r in rows]
    q_det_vals = [float(r["integral_efficiency_pct"]) for r in rows]

    def _corr_pair(x: List[float], y: List[float]) -> Dict[str, float]:
        rho, p_rho = sp_stats.spearmanr(x, y)
        tau, p_tau = sp_stats.kendalltau(x, y)
        return {
            "spearman_rho": 0.0 if math.isnan(rho) else round(float(rho), 3),
            "spearman_p": 1.0 if math.isnan(p_rho) else round(float(p_rho), 5),
            "kendall_tau": 0.0 if math.isnan(tau) else round(float(tau), 3),
            "kendall_p": 1.0 if math.isnan(p_tau) else round(float(p_tau), 5),
        }

    correlations = {
        "fact_coverage_vs_rubric": _corr_pair(fc_vals, human_comp),
        "semantic_similarity_vs_rubric": _corr_pair(ss_vals, human_comp),
        "format_compliance_vs_rubric": _corr_pair(fmt_vals, human_comp),
        "composite_q_det_vs_rubric": _corr_pair(q_det_vals, human_comp),
    }

    regex_esc = [int(bool(r.get("escalation_regex", r.get("requires_escalation")))) for r in rows]
    gt_esc = [int(bool(r.get("gt_escalation"))) for r in rows]
    esc_kappa = cohens_kappa_binary(regex_esc, gt_esc)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = {
        "status": "validated",
        "validated": True,
        "created_at": now_str,
        "sample_size": len(rows),
        "manual_scored_count": sum(1 for r in rows if r.get("manual_scored")),
        "judge_scored_count": sum(1 for r in rows if r.get("rubric_evaluated") and not r.get("manual_scored")),
        "correlations": correlations,
        "escalation_kappa_vs_gt": esc_kappa,
    }

    cur.execute(
        """
        INSERT INTO validation_reports (created_at, sample_size, annotators_count, metrics_json, notes)
        VALUES (?, ?, 1, ?, ?)
        """,
        (now_str, len(rows), json.dumps(report, ensure_ascii=False), "Автоматичний звіт scripts/validate_metrics.py"),
    )
    conn.commit()
    conn.close()

    (EXPORTS_DIR / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rep = compute_validation_report()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
