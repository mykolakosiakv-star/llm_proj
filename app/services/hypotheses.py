from typing import Any, Dict, List, Optional, Tuple

from app.services.decision_support import MIN_QUERIES_FOR_CONCLUSION, QUALITY_TOLERANCE_PTS, pick_recommended

# Мінімум запитів на комірку модель x сфера, щоб порівнювати моделі всередині сфери
MIN_CELL_QUERIES = 10
# Нижче цієї різниці якості розрив між моделями вважається несуттєвим
PRACTICAL_GAP_PTS = 3.0

VERDICT_LABELS_UK = {
    "supported": "Підтверджується",
    "not_supported": "Не підтверджується",
    "insufficient": "Замало даних",
}

STATEMENTS_UK = {
    "H1": "Найкраща модель залежить від предметної сфери.",
    "H2": "Стратегія промпту і контекст впливають на якість не менше, ніж вибір моделі.",
    "H3": "Для більшості типових запитів достатньо дешевої моделі без втрати безпеки.",
}


def _hypothesis(code: str, verdict: str, summary: str, evidence: List[Tuple[str, str]], missing: str = "", preliminary: str = "") -> Dict[str, Any]:
    return {
        "id": code,
        "statement_uk": STATEMENTS_UK[code],
        "verdict": verdict,
        "verdict_label_uk": VERDICT_LABELS_UK[verdict],
        "summary_uk": summary,
        "preliminary_uk": preliminary,
        "missing_uk": missing,
        "evidence": [{"label_uk": label, "value_uk": value} for label, value in evidence],
    }


def _finalize(code: str, indicated: str, summary: str, evidence: List[Tuple[str, str]], n_queries: int) -> Dict[str, Any]:
    """Вердикт видається лише на достатній вибірці, інакше дані показуються як попередні."""
    if indicated != "insufficient" and n_queries < MIN_QUERIES_FOR_CONCLUSION:
        missing = f"Спільних запитів {n_queries}, для висновку потрібно щонайменше {MIN_QUERIES_FOR_CONCLUSION}."
        return _hypothesis(code, "insufficient", "Висновку поки немає.", evidence, missing=missing, preliminary=summary)
    return _hypothesis(code, indicated, summary, evidence)


def _spread(items: List[Dict[str, Any]], name_key: str) -> Optional[Dict[str, Any]]:
    values = [(i[name_key], float(i.get("integral_efficiency_pct") or 0.0)) for i in items if i.get("count")]
    if len(values) < 2:
        return None
    best, worst = max(values, key=lambda v: v[1]), min(values, key=lambda v: v[1])
    return {"range": round(best[1] - worst[1], 1), "best": best[0], "worst": worst[0]}


def assess_h1(
    matrix_model_domain: Dict[str, Dict[str, Dict[str, Any]]],
    model_names: Dict[str, str],
    domain_names: Dict[str, str],
    n_queries: int,
) -> Dict[str, Any]:
    domains = sorted({d for cells in matrix_model_domain.values() for d in cells})
    usable: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for domain in domains:
        cells = {m: c[domain] for m, c in matrix_model_domain.items() if domain in c and c[domain]["n"] >= MIN_CELL_QUERIES}
        if len(cells) >= 2:
            usable[domain] = cells
    if len(usable) < 2:
        missing = (
            f"Потрібні результати щонайменше двох сфер, у кожній не менше двох моделей і не менше {MIN_CELL_QUERIES} запитів на модель."
        )
        return _hypothesis("H1", "insufficient", "Порівняти сфери поки неможливо.", [], missing=missing)

    quality = lambda cell: float(cell["integral_efficiency_pct"])  # noqa: E731
    winners = {d: max(cells, key=lambda m: quality(cells[m])) for d, cells in usable.items()}
    evidence = [
        (domain_names.get(d, d), f"{model_names.get(w, w)}, якість {quality(usable[d][w]):.1f}%, запитів {usable[d][w]['n']}")
        for d, w in winners.items()
    ]
    if len(set(winners.values())) == 1:
        only = model_names.get(next(iter(winners.values())), "")
        return _finalize("H1", "not_supported", f"Модель «{only}» найкраща в усіх {len(usable)} сферах.", evidence, n_queries)

    means: Dict[str, List[float]] = {}
    for cells in usable.values():
        for m, cell in cells.items():
            means.setdefault(m, []).append(quality(cell))
    overall = max(means, key=lambda m: sum(means[m]) / len(means[m]))
    gaps = [
        quality(usable[d][w]) - quality(usable[d][overall])
        for d, w in winners.items() if w != overall and overall in usable[d]
    ]
    if gaps and max(gaps) >= PRACTICAL_GAP_PTS:
        return _finalize("H1", "supported", "Найкраща модель різниться залежно від сфери.", evidence, n_queries)
    summary = f"Переможці відрізняються, але різниця з загальним лідером менша за {PRACTICAL_GAP_PTS:.0f} п.п., тому це може бути випадковістю."
    return _hypothesis("H1", "insufficient", summary, evidence, missing="Потрібно більше запитів на сферу, щоб відрізнити розрив від шуму.")


def assess_h2(
    by_model: List[Dict[str, Any]],
    by_prompt: List[Dict[str, Any]],
    by_context_mode: List[Dict[str, Any]],
    n_queries: int,
) -> Dict[str, Any]:
    model = _spread(by_model, "model_name")
    prompt = _spread(by_prompt, "prompt_name_uk")
    context = _spread(by_context_mode, "context_mode_name_uk")
    if model is None or (prompt is None and context is None):
        missing = "Потрібні щонайменше дві моделі та або дві стратегії промпту, або два режими контексту."
        return _hypothesis("H2", "insufficient", "Порівняти вплив чинників поки неможливо.", [], missing=missing)

    evidence = [("Вибір моделі", f"розбіг {model['range']} п.п. ({model['best']} проти {model['worst']})")]
    if prompt:
        evidence.append(("Стратегія промпту", f"розбіг {prompt['range']} п.п. ({prompt['best']} проти {prompt['worst']})"))
    if context:
        evidence.append(("Режим контексту", f"розбіг {context['range']} п.п. ({context['best']} проти {context['worst']})"))
    others = max(s["range"] for s in (prompt, context) if s)
    if others >= model["range"]:
        summary = "Промпт і контекст змінюють якість не менше, ніж вибір моделі."
        indicated = "supported"
    else:
        summary = "Вибір моделі впливає сильніше, ніж промпт і контекст."
        indicated = "not_supported"
    return _finalize("H2", indicated, summary, evidence, n_queries)


def assess_h3(by_model: List[Dict[str, Any]], n_queries: int) -> Dict[str, Any]:
    models = [m for m in by_model if m.get("count")]
    priced = [m for m in models if m.get("price_known") and m.get("cost_per_1m_usd") is not None]
    if len(models) < 2 or len(priced) < 2:
        missing = "Потрібні щонайменше дві моделі, для яких задано ціну: без неї не можна знайти дешевшу."
        return _hypothesis("H3", "insufficient", "Порівняти вартість поки неможливо.", [], missing=missing)

    pick = pick_recommended(models)
    best, chosen = pick["best"], pick["model"]
    best_cost = best.get("cost_per_1m_usd")
    best_is_cheapest = bool(best.get("price_known")) and best_cost is not None and not any(m["cost_per_1m_usd"] < best_cost for m in priced)
    evidence = [
        ("Лідер за якістю", f"{best['model_name']}, {float(best.get('integral_efficiency_pct') or 0):.1f}%"),
    ]
    if best_is_cheapest:
        return _finalize("H3", "supported", "Найкраща модель водночас найдешевша, тож дешевша заміна не потрібна.", evidence, n_queries)
    if pick["is_cheaper_pick"]:
        gap = float(best.get("integral_efficiency_pct") or 0) - float(chosen.get("integral_efficiency_pct") or 0)
        evidence.append(("Дешевша модель", f"{chosen['model_name']}, на {gap:.1f} п.п. нижче, ${chosen['cost_per_1m_usd']:.2f} за 1 млн токенів"))
        evidence.append(("Пропущені ескалації", f"{chosen.get('escalation_matrix', {}).get('fn_rate_pct', 0)}% проти {best.get('escalation_matrix', {}).get('fn_rate_pct', 0)}% у лідера"))
        return _finalize("H3", "supported", f"Модель «{chosen['model_name']}» дешевша й поступається не більш ніж на {QUALITY_TOLERANCE_PTS:.0f} п.п.", evidence, n_queries)
    summary = f"Немає дешевшої моделі, яка поступалася б лідеру менш ніж на {QUALITY_TOLERANCE_PTS:.0f} п.п. без зростання пропусків ескалації."
    return _finalize("H3", "not_supported", summary, evidence, n_queries)


def assess_hypotheses(
    by_model: List[Dict[str, Any]],
    by_prompt: List[Dict[str, Any]],
    by_context_mode: List[Dict[str, Any]],
    matrix_model_domain: Dict[str, Dict[str, Dict[str, Any]]],
    by_domain: List[Dict[str, Any]],
    n_queries: int,
) -> Dict[str, Any]:
    model_names = {m["model_id"]: m["model_name"] for m in by_model}
    domain_names = {d["domain_code"]: d["domain_name_uk"] for d in by_domain}
    return {
        "n_queries": n_queries,
        "min_queries": MIN_QUERIES_FOR_CONCLUSION,
        "h1": assess_h1(matrix_model_domain, model_names, domain_names, n_queries),
        "h2": assess_h2(by_model, by_prompt, by_context_mode, n_queries),
        "h3": assess_h3(by_model, n_queries),
    }
