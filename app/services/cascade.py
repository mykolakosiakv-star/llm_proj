from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.database import get_connection
from app.services.analytics import _enrich_rows_with_ground_truth
from app.services.decision_support import MIN_QUERIES_FOR_CONCLUSION

MIN_ESCALATION_CASES = 5


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _per_query_outcomes(rows: List[Dict[str, Any]], model_id: str) -> Dict[str, Dict[str, Any]]:
    """Усереднює повтори й стратегії в один результат моделі на запит, бо рішення каскаду приймається по запиту."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["model_id"] == model_id:
            grouped[r["query_code"]].append(r)
    out: Dict[str, Dict[str, Any]] = {}
    for code, items in grouped.items():
        costs = [float(i["cost_usd"]) for i in items if i.get("cost_usd") is not None]
        out[code] = {
            "category": items[0]["category"],
            "escalate": _mean([1.0 if i.get("requires_escalation") else 0.0 for i in items]) >= 0.5,
            "expected_escalation": bool(items[0]["expected_escalation"]),
            "quality": _mean([float(i.get("integral_efficiency_pct") or 0.0) for i in items]),
            "hallucination": _mean([1.0 if i.get("has_hallucination") else 0.0 for i in items]) >= 0.5,
            "cost": _mean(costs) if costs else None,
        }
    return out


def _route(cheap: Dict[str, Any], strong: Dict[str, Any], policy: str, direct_to_strong: bool) -> Dict[str, Any]:
    """Повертає, хто дав фінальну відповідь (None означає оператора), і які виклики були оплачені."""
    if policy == "cheap_only":
        return {"answered_by": None if cheap["escalate"] else cheap, "calls": [cheap]}
    if policy == "strong_only" or direct_to_strong:
        return {"answered_by": None if strong["escalate"] else strong, "calls": [strong]}
    if not cheap["escalate"]:
        return {"answered_by": cheap, "calls": [cheap]}
    return {"answered_by": None if strong["escalate"] else strong, "calls": [cheap, strong]}


def _summarize(routes: List[Dict[str, Any]], expected: List[bool], prices_known: bool) -> Dict[str, Any]:
    n = len(routes)
    auto = [r["answered_by"] for r in routes if r["answered_by"] is not None]
    needed = [i for i, e in enumerate(expected) if e]
    missed = [i for i in needed if routes[i]["answered_by"] is not None]
    total_cost: Optional[float] = None
    if prices_known:
        total_cost = sum(c["cost"] for r in routes for c in r["calls"])
    return {
        "auto_pct": round(100.0 * len(auto) / n, 1),
        "operator_pct": round(100.0 * (n - len(auto)) / n, 1),
        "missed_escalation_pct": round(100.0 * len(missed) / len(needed), 1) if needed else None,
        "quality_of_auto_answers_pct": round(_mean([a["quality"] for a in auto]), 1) if auto else None,
        "hallucination_in_auto_pct": round(100.0 * sum(a["hallucination"] for a in auto) / len(auto), 1) if auto else None,
        "cost_per_1000_usd": round(total_cost / n * 1000.0, 4) if total_cost is not None else None,
    }


def _caveats(n: int, expected_escalations: int, prices_known: bool) -> List[str]:
    caveats: List[str] = []
    if n < MIN_QUERIES_FOR_CONCLUSION:
        caveats.append(
            f"У розрахунку {n} спільних запитів, для надійних висновків потрібно щонайменше {MIN_QUERIES_FOR_CONCLUSION}. "
            "Результат показує лише, як працює механізм."
        )
    if expected_escalations < MIN_ESCALATION_CASES:
        caveats.append(
            f"Запитів, що справді потребують оператора, лише {expected_escalations}, тому частка пропущених ескалацій оцінена ненадійно."
        )
    if not prices_known:
        caveats.append("Для однієї з моделей ціну не задано, тому вартість не розраховано.")
    return caveats


def simulate_cascade(
    rows: List[Dict[str, Any]],
    cheap_model_id: str,
    strong_model_id: str,
    strong_first_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Симулює каскад на вже зібраних результатах без нових викликів API.

    Дешева модель відповідає першою. Якщо вона сама вирішила передати запит далі, його отримує сильна модель,
    а якщо й вона просить оператора, запит іде оператору. Ризикові категорії одразу йдуть сильній моделі.
    """
    if cheap_model_id == strong_model_id:
        return {"available": False, "reason_uk": "Оберіть дві різні моделі."}
    cheap = _per_query_outcomes(rows, cheap_model_id)
    strong = _per_query_outcomes(rows, strong_model_id)
    common = sorted(set(cheap) & set(strong))
    if not common:
        return {"available": False, "reason_uk": "Немає запитів, які виконали обидві моделі."}

    risky = set(strong_first_categories or [])
    expected = [cheap[q]["expected_escalation"] for q in common]
    prices_known = all(cheap[q]["cost"] is not None and strong[q]["cost"] is not None for q in common) and all(
        bool(r.get("price_known")) for r in rows if r["model_id"] in (cheap_model_id, strong_model_id)
    )

    policies = {}
    for name in ("cheap_only", "strong_only", "cascade"):
        routes = [_route(cheap[q], strong[q], name, cheap[q]["category"] in risky) for q in common]
        policies[name] = _summarize(routes, expected, prices_known)

    saving = None
    base, casc = policies["strong_only"]["cost_per_1000_usd"], policies["cascade"]["cost_per_1000_usd"]
    if base and casc is not None:
        saving = round(100.0 * (base - casc) / base, 1)

    return {
        "available": True,
        "n_queries": len(common),
        "policies": policies,
        "cost_saving_vs_strong_pct": saving,
        "caveats_uk": _caveats(len(common), sum(expected), prices_known),
    }


def simulate_cascade_for_runs(
    run_ids: List[int],
    cheap_model_id: str,
    strong_model_id: str,
    strong_first_categories: Optional[List[str]] = None,
    prompt_code: Optional[str] = None,
    context_mode: Optional[str] = None,
) -> Dict[str, Any]:
    placeholders = ",".join("?" for _ in run_ids)
    sql = f"SELECT * FROM evaluation_results WHERE run_id IN ({placeholders}) AND model_id IN (?, ?)"
    params: List[Any] = [*run_ids, cheap_model_id, strong_model_id]
    for column, value in (("prompt_code", prompt_code), ("context_mode", context_mode)):
        if value:
            sql += f" AND {column} = ?"
            params.append(value)
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return simulate_cascade(_enrich_rows_with_ground_truth(rows), cheap_model_id, strong_model_id, strong_first_categories)
