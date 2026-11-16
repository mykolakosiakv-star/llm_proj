"""
Модуль підтримки прийняття рішень (Decision Support Advisor).
Оцінює придатність моделей та стратегій промптів за профілями впровадження
(balanced, safety_first, high_speed, cost_effective) та перевіряє гіпотезу H3
про достатність економних моделей при збереженні безпеки.
"""

from typing import Dict, Any, List, Optional
from app.domain_datasets import DEPLOYMENT_PRESETS


MIN_QUERIES_FOR_CONCLUSION = 30
QUALITY_TOLERANCE_PTS = 5.0


def _model_fn_rate(m: Dict[str, Any]) -> float:
    esc = m.get("escalation_matrix") or {}
    return float(esc.get("critical_fn_rate_pct") or esc.get("fn_rate_pct") or 0.0)


def pick_recommended(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Найкраща за Q_det модель; дешевша підміна лише якщо вона не гірша за безпекою."""
    best = max(models, key=lambda m: float(m.get("integral_efficiency_pct") or 0.0))
    best_q = float(best.get("integral_efficiency_pct") or 0.0)
    best_fn = _model_fn_rate(best)
    best_cost = best.get("cost_per_1m_usd")
    cheaper = [
        m for m in models
        if m.get("price_known") and m.get("cost_per_1m_usd") is not None
        and (best_cost is None or m["cost_per_1m_usd"] < best_cost)
        and best_q - float(m.get("integral_efficiency_pct") or 0.0) <= QUALITY_TOLERANCE_PTS
        and _model_fn_rate(m) <= best_fn
    ]
    if cheaper:
        return {"model": min(cheaper, key=lambda m: m["cost_per_1m_usd"]), "best": best, "is_cheaper_pick": True}
    return {"model": best, "best": best, "is_cheaper_pick": False}


def _find_pair(stat_block: Dict[str, Any], id_a: str, id_b: str) -> Optional[Dict[str, Any]]:
    for p in stat_block.get("pairwise") or []:
        if {p.get("group_a"), p.get("group_b")} == {id_a, id_b}:
            return p
    return None


def _conclusion_caveats(
    models: List[Dict[str, Any]],
    pick: Dict[str, Any],
    stat_tests: Dict[str, Any],
    n_queries: int,
) -> List[str]:
    caveats: List[str] = []
    if n_queries < MIN_QUERIES_FOR_CONCLUSION:
        caveats.append(
            f"У вибірці {n_queries} спільних запитів. Для надійних висновків потрібно щонайменше "
            f"{MIN_QUERIES_FOR_CONCLUSION}, тому результат слід вважати попереднім."
        )
    unpriced = [m["model_name"] for m in models if not m.get("price_known")]
    if unpriced:
        caveats.append("Ціну не задано для: " + ", ".join(unpriced) + ". Ці моделі не порівнювались за вартістю.")
    chosen, best = pick["model"], pick["best"]
    other = best if chosen["model_id"] != best["model_id"] else None
    if other is None and len(models) > 1:
        other = sorted(models, key=lambda m: -float(m.get("integral_efficiency_pct") or 0.0))[1]
    if other is not None:
        pair = _find_pair(stat_tests.get("models_composite_quality") or {}, chosen["model_id"], other["model_id"])
        if pair is None or pair.get("status") != "ok":
            caveats.append("Для цієї пари моделей замало даних, щоб перевірити, чи різниця випадкова.")
        elif not pair.get("significant_005"):
            caveats.append(
                f"Різниця якості між «{chosen['model_name']}» та «{other['model_name']}» "
                "статистично незначуща, тому вибір між ними можна робити за ціною."
            )
    return caveats


def build_conclusion(
    by_model: List[Dict[str, Any]],
    stat_tests: Dict[str, Any],
    n_queries: int,
) -> Dict[str, Any]:
    models = [m for m in by_model if m.get("count")]
    if not models:
        return {"available": False}
    pick = pick_recommended(models)
    chosen, best = pick["model"], pick["best"]
    q_chosen = float(chosen.get("integral_efficiency_pct") or 0.0)
    cost = chosen.get("cost_per_1m_usd")

    if pick["is_cheaper_pick"]:
        headline = (
            f"Достатньо моделі «{chosen['model_name']}»: її якість нижча за найкращу "
            f"(«{best['model_name']}») не більше ніж на {QUALITY_TOLERANCE_PTS:.0f} п.п., "
            "а пропусків ескалації не більше, при цьому вона дешевша."
        )
    else:
        headline = f"Рекомендована модель: «{chosen['model_name']}». Вона має найвищу сумарну якість у вибірці."

    return {
        "available": True,
        "recommended_model_id": chosen["model_id"],
        "recommended_model_name": chosen["model_name"],
        "headline_uk": headline,
        "tradeoffs": {
            "quality_pct": q_chosen,
            "best_quality_pct": float(best.get("integral_efficiency_pct") or 0.0),
            "cost_per_1m_usd": cost if chosen.get("price_known") else None,
            "hallucination_rate_pct": float(chosen.get("hallucination_rate_pct") or 0.0),
            "fn_rate_pct": _model_fn_rate(chosen),
        },
        "caveats_uk": _conclusion_caveats(models, pick, stat_tests, n_queries),
        "n_queries": n_queries,
    }


def compute_decision_support(
    by_model: List[Dict[str, Any]],
    by_prompt: List[Dict[str, Any]],
    by_category: List[Dict[str, Any]],
    overall_stats: Dict[str, Any],
    pareto_info: Dict[str, Any],
    stat_tests: Dict[str, Any],
) -> Dict[str, Any]:
    if not by_model:
        return {"available": False, "presets": {}, "risk_warnings": []}

    presets_out: Dict[str, Any] = {}
    pareto_lat_ids = set(pareto_info.get("pareto_quality_latency_models", []))

    for preset_id, preset_cfg in DEPLOYMENT_PRESETS.items():
        w = preset_cfg["weights"]
        target_lat = float(preset_cfg.get("target_latency_ms", 2500.0))
        ranked_models = []

        for m in by_model:
            q_det = float(m.get("integral_efficiency_pct", 0.0))
            fc = float(m.get("fact_coverage_pct", 0.0))
            hr = float(m.get("hallucination_rate_pct", 0.0))
            esc_mat = m.get("escalation_matrix") or {}
            fn_rate = float(esc_mat.get("critical_fn_rate_pct") or esc_mat.get("fn_rate_pct") or 0.0)
            lat = float(m.get("latency_mean_ms") or 1000.0)
            cost_1m = float(m.get("cost_per_1m_usd") or 0.0)

            safety_score = max(0.0, 100.0 - hr * 2.5 - fn_rate * 2.0)
            speed_score = max(0.0, min(100.0, 100.0 - max(0.0, lat - 400.0) / max(1.0, target_lat) * 45.0))
            cost_score = max(0.0, min(100.0, 100.0 - cost_1m * 18.0))

            dep_score = round(
                w["quality"] * q_det
                + w["fact_coverage"] * fc
                + w["safety"] * safety_score
                + w["speed"] * speed_score
                + w["cost"] * cost_score,
                1,
            )

            ranked_models.append({
                "model_id": m["model_id"],
                "model_name": m["model_name"],
                "deployment_score_pct": dep_score,
                "integral_efficiency_pct": q_det,
                "fact_coverage_pct": fc,
                "hallucination_rate_pct": hr,
                "fn_rate_pct": fn_rate,
                "automation_rate_pct": float(m.get("automation_rate_pct", 0.0)),
                "latency_mean_ms": lat,
                "cost_per_1m_usd": cost_1m,
                "avg_input_tokens": int(m.get("avg_input_tokens") or 300),
                "avg_output_tokens": int(m.get("avg_output_tokens") or 180),
                "is_pareto": m["model_name"] in pareto_lat_ids or m["model_id"] in pareto_lat_ids,
                "count": m.get("count", 0),
            })

        ranked_models.sort(key=lambda x: x["deployment_score_pct"], reverse=True)
        winner_m = ranked_models[0] if ranked_models else None

        ranked_prompts = []
        for p in by_prompt:
            p_q = float(p.get("integral_efficiency_pct", 0.0))
            p_fc = float(p.get("fact_coverage_pct", 0.0))
            p_fmt = float(p.get("format_compliance_pct", 0.0))
            p_hr = float(p.get("hallucination_rate_pct", 0.0))
            p_esc = p.get("escalation_matrix") or {}
            p_fn = float(p_esc.get("critical_fn_rate_pct") or p_esc.get("fn_rate_pct") or 0.0)
            p_f1 = float(p_esc.get("f1_pct") or p.get("escalation_accuracy_pct") or 0.0)
            p_lat = float(p.get("latency_mean_ms") or 1000.0)

            p_safety = max(0.0, 100.0 - p_hr * 2.5 - p_fn * 2.0) * 0.6 + p_f1 * 0.4
            p_speed = max(0.0, min(100.0, 100.0 - max(0.0, p_lat - 400.0) / max(1.0, target_lat) * 45.0))
            p_score = round(
                w["quality"] * p_q
                + w["fact_coverage"] * p_fc
                + w["safety"] * p_safety
                + (w["speed"] + w["cost"]) * (0.6 * p_fmt + 0.4 * p_speed),
                1,
            )
            ranked_prompts.append({
                "prompt_code": p["prompt_code"],
                "prompt_name_uk": p["prompt_name_uk"],
                "strategy_type": p.get("strategy_type", ""),
                "deployment_score_pct": p_score,
                "integral_efficiency_pct": p_q,
                "fact_coverage_pct": p_fc,
                "format_compliance_pct": p_fmt,
                "hallucination_rate_pct": p_hr,
                "escalation_f1_pct": p_f1,
                "fn_rate_pct": p_fn,
            })

        ranked_prompts.sort(key=lambda x: x["deployment_score_pct"], reverse=True)
        winner_p = ranked_prompts[0] if ranked_prompts else None

        just_m = ""
        if winner_m:
            just_m = (
                f"Модель «{winner_m['model_name']}» посіла 1-ше місце для профілю «{preset_cfg['short_uk']}» "
                f"(індекс придатності {winner_m['deployment_score_pct']}%): повнота фактів FC = {winner_m['fact_coverage_pct']}%, "
                f"композитна якість Q_det = {winner_m['integral_efficiency_pct']}%, рівень галюцинацій = {winner_m['hallucination_rate_pct']}%, "
                f"рівень автоматизації AR = {winner_m['automation_rate_pct']}%, середня затримка = {winner_m['latency_mean_ms']} мс, "
                f"вартість = ${winner_m['cost_per_1m_usd']:.2f}/1M токенів."
            )

        just_p = ""
        if winner_p:
            just_p = (
                f"Стратегія «{winner_p['prompt_name_uk']}» забезпечує індекс {winner_p['deployment_score_pct']}% "
                f"(FC = {winner_p['fact_coverage_pct']}%, відповідність формату = {winner_p['format_compliance_pct']}%, "
                f"точність шлюзу ескалації F1 = {winner_p['escalation_f1_pct']}%, пропуск ризиків FN = {winner_p['fn_rate_pct']}%)."
            )

        presets_out[preset_id] = {
            "id": preset_id,
            "name_uk": preset_cfg["name_uk"],
            "short_uk": preset_cfg["short_uk"],
            "description_uk": preset_cfg["description_uk"],
            "weights": w,
            "winner_model": winner_m,
            "winner_prompt": winner_p,
            "model_justification_uk": just_m,
            "prompt_justification_uk": just_p,
            "ranked_models": ranked_models,
            "ranked_prompts": ranked_prompts,
        }

    risk_warnings: List[str] = []
    halluc_models = [m for m in by_model if float(m.get("hallucination_rate_pct") or 0.0) > 0.0]
    if halluc_models:
        names = ", ".join(f"{m['model_name']} ({m['hallucination_rate_pct']}%)" for m in halluc_models)
        risk_warnings.append(
            f"Виявлено числові або фактичні галюцинації у відповідях моделей: {names}. Рекомендується використовувати структурні промпти P3/P5 із суворим обмеженням контексту."
        )
    else:
        risk_warnings.append("У поточній вибірці жодна з протестованих моделей не допустила числових галюцинацій (HR = 0.0%).")

    fn_prompts = [p for p in by_prompt if float((p.get("escalation_matrix") or {}).get("fn_rate_pct") or 0.0) > 0.0]
    if fn_prompts:
        p_names = ", ".join(f"{p['prompt_code']} (FN={p['escalation_matrix']['fn_rate_pct']}%)" for p in fn_prompts)
        risk_warnings.append(
            f"Ризик пропуску критичної ескалації (False Negative) зафіксовано на промптах: {p_names}. Для критичних категорій обов'язковий явний шлюз ескалації."
        )

    if by_category:
        weakest_cat = min(by_category, key=lambda c: float(c.get("fact_coverage_pct") or 0.0))
        risk_warnings.append(
            f"Найскладніша категорія запитів для автоматизації у вибірці: «{weakest_cat['category_name_uk']}» "
            f"(середня повнота фактів FC = {weakest_cat['fact_coverage_pct']}%, композитна якість Q_det = {weakest_cat['integral_efficiency_pct']}%)."
        )

    return {
        "available": True,
        "presets": presets_out,
        "risk_warnings": risk_warnings,
    }
