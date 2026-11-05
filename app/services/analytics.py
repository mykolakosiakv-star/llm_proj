"""
Модуль аналітичної агрегації, статистичних порівнянь та формування наукових висновків.
Включає порівняння трьох режимів контексту (no_context, gold_context, retrieved_context),
оцінку якості пошуку (hit@1, hit@3) та деталізацію паспорта окремого заміру.
"""

import csv
import io
import json
import random
from typing import Dict, Any, List, Optional, Tuple

from app.config import (
    DEFAULT_MODELS_CATALOG,
    QUERY_CATEGORIES,
    DEFAULT_EVALUATION_WEIGHTS,
    AUTOMATION_FC_THRESHOLD_PCT,
    WILCOXON_MIN_PAIRS,
    MANUAL_VALIDATION_SAMPLE_MAX,
    get_model_config,
)
from app.database import (
    get_connection,
    fetch_all_domains,
    fetch_all_queries,
    fetch_query_by_id,
    fetch_all_prompts,
    fetch_prompt_version_by_id,
    fetch_run_errors,
)
from app.services.prompt_builder import build_prompt_messages
from app.services.llm_runtime import calculate_cost_usd
from app.services.hypotheses import assess_hypotheses
from app.services.stats import (
    describe_sample,
    aggregate_repeats_by_query,
    compare_paired_conditions,
    compute_repeat_stability,
    compute_escalation_confusion_matrix,
    compute_weight_sensitivity_analysis,
    compute_metrics_correlation_matrix,
    compute_rubric_diagnostics,
    compute_judge_human_validation,
    compute_pareto_fronts,
)
from app.services.decision_support import build_conclusion, compute_decision_support

CONTEXT_MODE_LABELS_UK: Dict[str, str] = {
    "no_context": "Без контексту (Baseline)",
    "gold_context": "Еталонний контекст (Gold Ceiling)",
    "retrieved_context": "Знайдений контекст (BM25 Retrieval)",
}


def _mean(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _filter_balanced_intersection(
    rows: List[Dict[str, Any]],
    balanced_only: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not rows:
        return [], {
            "balanced_only_requested": balanced_only,
            "balanced_applied": False,
            "raw_count": 0,
            "balanced_count": 0,
            "common_queries_count": 0,
            "warning_uk": "",
        }

    condition_pairs = sorted(
        list(
            {
                (r["model_id"], r["prompt_code"], str(r.get("context_mode") or "gold_context"))
                for r in rows
            }
        )
    )
    if len(condition_pairs) <= 1 or not balanced_only:
        unique_qs = sorted(list({r["query_code"] for r in rows}))
        return rows, {
            "balanced_only_requested": balanced_only,
            "balanced_applied": False,
            "raw_count": len(rows),
            "balanced_count": len(rows),
            "common_queries_count": len(unique_qs),
            "warning_uk": "",
        }

    cells_by_cond: Dict[Tuple[str, str, str], set] = {cp: set() for cp in condition_pairs}
    for r in rows:
        cp = (r["model_id"], r["prompt_code"], str(r.get("context_mode") or "gold_context"))
        cell_key = (r["query_code"], int(r.get("repeat_index") or 1))
        cells_by_cond[cp].add(cell_key)

    common_cells = set.intersection(*(cells_by_cond[cp] for cp in condition_pairs))
    if not common_cells:
        return rows, {
            "balanced_only_requested": balanced_only,
            "balanced_applied": False,
            "raw_count": len(rows),
            "balanced_count": 0,
            "common_queries_count": 0,
            "warning_uk": (
                "Увага: збалансований перетин між обраними моделями та промптами порожній "
                "(різні серії містять неперетинні набори запитів або промптів). Показано незбалансовану вибірку."
            ),
        }

    seen_full_cells = set()
    balanced_rows: List[Dict[str, Any]] = []
    for r in rows:
        ctx_m = str(r.get("context_mode") or "gold_context")
        cell_key = (r["query_code"], int(r.get("repeat_index") or 1))
        if cell_key in common_cells:
            full_key = (r["model_id"], r["prompt_code"], ctx_m, r["query_code"], int(r.get("repeat_index") or 1))
            if full_key not in seen_full_cells:
                seen_full_cells.add(full_key)
                balanced_rows.append(r)

    common_qs = sorted(list({c[0] for c in common_cells}))
    dropped = len(rows) - len(balanced_rows)
    warn_msg = (
        f"Застосовано фільтр збалансованого перетину: залишено {len(balanced_rows)} із {len(rows)} замірів "
        f"({len(common_qs)} спільних запитів для всіх порівнюваних умов; відсіяно {dropped} непарних замірів)."
        if dropped > 0
        else ""
    )
    return balanced_rows, {
        "balanced_only_requested": balanced_only,
        "balanced_applied": True,
        "raw_count": len(rows),
        "balanced_count": len(balanced_rows),
        "common_queries_count": len(common_qs),
        "warning_uk": warn_msg,
    }


def _enrich_rows_with_ground_truth(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    queries_by_code = {q["code"]: q for q in fetch_all_queries()}
    for r in rows:
        r["domain"] = (queries_by_code.get(r.get("query_code", "")) or {}).get("domain") or "fintech"
        if "expected_escalation" not in r or r["expected_escalation"] is None:
            q_obj = queries_by_code.get(r.get("query_code", ""))
            if q_obj is not None:
                r["expected_escalation"] = bool(q_obj["expected_escalation"])
            else:
                req = bool(r.get("requires_escalation"))
                corr = bool(r.get("escalation_correct", True))
                r["expected_escalation"] = req if corr else (not req)

        m_id = str(r.get("model_id") or "")
        m_cfg = get_model_config(m_id)
        p_known = bool(m_cfg.get("price_known", False))
        r["price_known"] = p_known
        r["cost_unknown"] = not p_known
        r["is_free_tier"] = bool(m_cfg.get("is_free_tier", False))
        if p_known:
            plist_cost = calculate_cost_usd(
                m_id,
                int(r.get("input_tokens") or 0),
                int(r.get("output_tokens") or 0),
                int(r.get("thinking_tokens") or 0),
            )
            r["price_list_cost_usd"] = plist_cost
            if not r.get("cost_usd") or float(r.get("cost_usd") or 0.0) <= 0.0:
                r["cost_usd"] = plist_cost
        else:
            r["price_list_cost_usd"] = None
    return rows


def _calc_group_metrics(
    subset: List[Dict[str, Any]],
    include_self_judged: bool = False,
    dominant_judge: Optional[str] = None,
) -> Dict[str, Any]:
    if not subset:
        empty_desc = describe_sample([])
        return {
            "count": 0,
            "raw_measurements_count": 0,
            "insufficient_n": True,
            "insufficient_sample": True,
            "integral_efficiency_pct": 0.0,
            "efficiency_std": 0.0,
            "efficiency_ci95": [0.0, 0.0],
            "fact_coverage_pct": 0.0,
            "fact_coverage_std": 0.0,
            "fact_coverage_ci95": [0.0, 0.0],
            "semantic_similarity_pct": 0.0,
            "semantic_similarity_std": 0.0,
            "semantic_similarity_ci95": [0.0, 0.0],
            "format_compliance_pct": 0.0,
            "format_compliance_std": 0.0,
            "format_compliance_ci95": [0.0, 0.0],
            "stats": {
                "integral_efficiency_pct": empty_desc,
                "fact_coverage_pct": empty_desc,
                "semantic_similarity_pct": empty_desc,
                "format_compliance_pct": empty_desc,
                "latency_ms": empty_desc,
            },
            "contract_ok_rate_pct": 0.0,
            "retrieval_hit_at_1_pct": None,
            "retrieval_hit_at_3_pct": None,
            "nist_composite_pct": 0.0,
            "rubric_evaluated_count": 0,
            "self_judged_count": 0,
            "skipped_no_independent_judge_count": 0,
            "manual_scored_count": 0,
            "relevance_score": 0.0,
            "factuality_score": 0.0,
            "completeness_score": 0.0,
            "clarity_score": 0.0,
            "format_score": 0.0,
            "avg_nist_score_5": 0.0,
            "latency_mean_ms": 0.0,
            "latency_median_ms": 0.0,
            "latency_std_ms": 0.0,
            "latency_ci95_ms": [0.0, 0.0],
            "latency_p95_ms": 0.0,
            "avg_input_tokens": 0,
            "avg_output_tokens": 0,
            "avg_thinking_tokens": 0,
            "avg_total_tokens": 0,
            "tokens_estimated_count": 0,
            "truncated_count": 0,
            "truncated_rate_pct": 0.0,
            "hallucination_count_total": 0,
            "hallucination_rate_pct": 0.0,
            "price_known": False,
            "cost_unknown": True,
            "cost_label_uk": "ціна не задана",
            "avg_cost_usd": None,
            "cost_per_1k_usd": None,
            "cost_per_1m_usd": None,
            "price_list_cost_per_1k_usd": None,
            "price_list_cost_per_1m_usd": None,
            "automation_rate_pct": 0.0,
            "automation_success_non_esc_pct": 0.0,
            "escalation_accuracy_pct": 0.0,
            "escalation_recall_pct": 0.0,
            "escalation_precision_pct": 0.0,
            "escalation_f1_pct": 0.0,
            "critical_fn_rate_pct": 0.0,
            "confusion_matrix": {},
            "stability_pct": None,
            "stability_info": {"available": False, "label_uk": "немає даних"},
        }

    stab_info = compute_repeat_stability(subset)
    stab_pct = stab_info["escalation_consistency_pct"] if stab_info["available"] else None

    unit_subset = aggregate_repeats_by_query(subset)

    effs = [float(x.get("integral_efficiency_pct") or 0.0) for x in unit_subset]
    fcs = [float(x.get("fact_coverage_pct") or 0.0) for x in unit_subset]
    sims = [float(x.get("semantic_similarity_pct") or 0.0) for x in unit_subset]
    fmts = [float(x.get("format_compliance_pct") if x.get("format_compliance_pct") is not None else x.get("conciseness_pct", 100.0)) for x in unit_subset]
    lats = [float(x.get("call_ms") or x.get("latency_ms") or 0.0) for x in unit_subset]
    in_toks = [int(x.get("input_tokens") or 0) for x in unit_subset]
    out_toks = [int(x.get("output_tokens") or 0) for x in unit_subset]
    think_toks = [int(x.get("thinking_tokens") or 0) for x in unit_subset]

    eligible_rubric_rows: List[Dict[str, Any]] = []
    for x in unit_subset:
        if int(x.get("manual_scored") or 0) == 1:
            eligible_rubric_rows.append(x)
            continue
        if int(x.get("rubric_evaluated") or 0) == 1:
            if not include_self_judged and int(x.get("self_judged") or 0) == 1:
                continue
            if dominant_judge and str(x.get("judge_model") or "").strip() != dominant_judge:
                continue
            eligible_rubric_rows.append(x)

    rubric_source_rows = eligible_rubric_rows if eligible_rubric_rows else unit_subset
    nists = [
        float(
            x.get("rubric_composite_pct")
            if x.get("rubric_composite_pct") is not None
            else (x.get("nist_composite_pct") if x.get("nist_composite_pct") is not None else 0.0)
        )
        for x in rubric_source_rows
    ]
    rels = [float(x.get("relevance_score") or 0.0) for x in rubric_source_rows]
    facts = [float(x.get("factuality_score") or 0.0) for x in rubric_source_rows]
    comps = [float(x.get("completeness_score") or 0.0) for x in rubric_source_rows]
    clars = [float(x.get("clarity_score") or 0.0) for x in rubric_source_rows]
    fscores = [float(x.get("policy_adherence_score") or x.get("format_score") or 0.0) for x in rubric_source_rows]

    priced_units = [x for x in unit_subset if x.get("price_known", False) and not x.get("cost_unknown", True)]
    all_prices_known = len(priced_units) == len(unit_subset) and len(unit_subset) > 0
    any_free_tier = any(bool(x.get("is_free_tier")) for x in unit_subset)

    if priced_units:
        plist_costs = [
            float(x["price_list_cost_usd"])
            if x.get("price_list_cost_usd") is not None
            else calculate_cost_usd(
                x["model_id"],
                int(x.get("input_tokens") or 0),
                int(x.get("output_tokens") or 0),
                int(x.get("thinking_tokens") or 0),
            )
            for x in priced_units
        ]
        raw_mean_cost = sum(plist_costs) / len(plist_costs)
        cost_per_1k_val = round(raw_mean_cost * 1000.0, 4)
        total_tok_sum = sum(
            max(1, int(x.get("input_tokens") or 0) + int(x.get("output_tokens") or 0) + int(x.get("thinking_tokens") or 0))
            for x in priced_units
        )
        cost_per_1m_val = round((sum(plist_costs) / max(1, total_tok_sum)) * 1_000_000.0, 4)
        avg_cost_val = round(raw_mean_cost, 6)
        cost_label_uk = (
            f"${cost_per_1m_val:.2f}/1M (безкоштовний тариф)"
            if any_free_tier
            else f"${cost_per_1m_val:.2f}/1M"
        )
    else:
        avg_cost_val = None
        cost_per_1k_val = None
        cost_per_1m_val = None
        cost_label_uk = "ціна не задана"

    auto_ok = [100.0 if x.get("automation_success") else 0.0 for x in unit_subset]
    esc_ok = [100.0 if x.get("escalation_correct") else 0.0 for x in unit_subset]
    contract_oks = [100.0 if int(x.get("contract_ok", 1)) == 1 else 0.0 for x in unit_subset]

    hit1_vals = [100.0 * float(x["retrieval_hit_at_1"]) for x in unit_subset if x.get("retrieval_hit_at_1") is not None]
    hit3_vals = [100.0 * float(x["retrieval_hit_at_3"]) for x in unit_subset if x.get("retrieval_hit_at_3") is not None]

    desc_eff = describe_sample(effs)
    desc_fc = describe_sample(fcs)
    desc_sim = describe_sample(sims)
    desc_fmt = describe_sample(fmts)
    desc_lat = describe_sample(lats)

    cm = compute_escalation_confusion_matrix(unit_subset)

    has_halluc_list = [100.0 if x.get("has_hallucination") else 0.0 for x in unit_subset]
    halluc_total = sum(int(x.get("hallucination_count") or 0) for x in subset)
    trunc_count = sum(1 for x in subset if x.get("truncated"))
    est_tok_count = sum(1 for x in subset if x.get("tokens_estimated"))
    rubric_eval_cnt = len(eligible_rubric_rows)
    self_judged_cnt = sum(1 for x in subset if int(x.get("self_judged") or 0) == 1)
    skipped_judge_cnt = sum(1 for x in subset if str(x.get("judge_status") or "") == "skipped_no_independent_judge")
    manual_cnt = sum(1 for x in subset if x.get("manual_scored"))

    avg_5 = round((_mean(rels) + _mean(facts) + _mean(comps) + _mean(clars) + _mean(fscores)) / 5.0, 2)
    n_units = len(unit_subset)

    return {
        "count": n_units,
        "query_units_count": n_units,
        "raw_measurements_count": len(subset),
        "insufficient_n": n_units < WILCOXON_MIN_PAIRS,
        "insufficient_sample": n_units < WILCOXON_MIN_PAIRS,
        "integral_efficiency_pct": round(desc_eff["mean"], 1),
        "efficiency_median_pct": round(desc_eff["median"], 1),
        "efficiency_std": desc_eff["sd"],
        "efficiency_iqr": desc_eff["iqr"],
        "efficiency_ci95": [desc_eff["ci95_low"], desc_eff["ci95_high"]],
        "fact_coverage_pct": round(desc_fc["mean"], 1),
        "fact_coverage_median_pct": round(desc_fc["median"], 1),
        "fact_coverage_std": desc_fc["sd"],
        "fact_coverage_ci95": [desc_fc["ci95_low"], desc_fc["ci95_high"]],
        "semantic_similarity_pct": round(desc_sim["mean"], 1),
        "semantic_similarity_std": desc_sim["sd"],
        "semantic_similarity_ci95": [desc_sim["ci95_low"], desc_sim["ci95_high"]],
        "format_compliance_pct": round(desc_fmt["mean"], 1),
        "format_compliance_std": desc_fmt["sd"],
        "format_compliance_ci95": [desc_fmt["ci95_low"], desc_fmt["ci95_high"]],
        "stats": {
            "integral_efficiency_pct": desc_eff,
            "fact_coverage_pct": desc_fc,
            "semantic_similarity_pct": desc_sim,
            "format_compliance_pct": desc_fmt,
            "latency_ms": desc_lat,
        },
        "contract_ok_rate_pct": round(_mean(contract_oks), 1),
        "retrieval_hit_at_1_pct": round(_mean(hit1_vals), 1) if hit1_vals else None,
        "retrieval_hit_at_3_pct": round(_mean(hit3_vals), 1) if hit3_vals else None,
        "nist_composite_pct": round(_mean(nists), 1),
        "rubric_evaluated_count": rubric_eval_cnt,
        "self_judged_count": self_judged_cnt,
        "skipped_no_independent_judge_count": skipped_judge_cnt,
        "manual_scored_count": manual_cnt,
        "relevance_score": round(_mean(rels), 2),
        "factuality_score": round(_mean(facts), 2),
        "completeness_score": round(_mean(comps), 2),
        "clarity_score": round(_mean(clars), 2),
        "format_score": round(_mean(fscores), 2),
        "policy_adherence_score": round(_mean(fscores), 2),
        "avg_nist_score_5": avg_5,
        "latency_mean_ms": round(desc_lat["mean"], 1),
        "latency_median_ms": round(desc_lat["median"], 1),
        "latency_std_ms": desc_lat["sd"],
        "latency_ci95_ms": [desc_lat["ci95_low"], desc_lat["ci95_high"]],
        "latency_p95_ms": desc_lat["p95"],
        "avg_input_tokens": int(round(_mean(in_toks))),
        "avg_output_tokens": int(round(_mean(out_toks))),
        "avg_thinking_tokens": int(round(_mean(think_toks))),
        "avg_total_tokens": int(round(_mean(in_toks) + _mean(out_toks) + _mean(think_toks))),
        "tokens_estimated_count": est_tok_count,
        "truncated_count": trunc_count,
        "truncated_rate_pct": round((trunc_count / max(1, len(subset))) * 100.0, 1),
        "hallucination_count_total": halluc_total,
        "hallucination_rate_pct": round(_mean(has_halluc_list), 1),
        "price_known": all_prices_known,
        "cost_unknown": not all_prices_known,
        "is_free_tier": any_free_tier,
        "cost_label_uk": cost_label_uk,
        "avg_cost_usd": avg_cost_val,
        "cost_per_1k_usd": cost_per_1k_val,
        "cost_per_1m_usd": cost_per_1m_val,
        "price_list_cost_per_1k_usd": cost_per_1k_val,
        "price_list_cost_per_1m_usd": cost_per_1m_val,
        "automation_rate_pct": round(_mean(auto_ok), 1),
        "automation_success_non_esc_pct": cm["automation_success_non_esc_pct"],
        "escalation_accuracy_pct": round(_mean(esc_ok), 1),
        "escalation_recall_pct": cm["recall_pct"],
        "escalation_precision_pct": cm["precision_pct"],
        "escalation_f1_pct": cm["f1_pct"],
        "critical_fn_rate_pct": cm["critical_fn_rate_pct"],
        "confusion_matrix": cm,
        "escalation_matrix": cm,
        "stability_pct": stab_pct,
        "stability_info": stab_info,
    }


def _compute_context_mode_comparison(by_context_mode: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_map = {item["context_mode"]: item for item in by_context_mode}
    gold = by_map.get("gold_context")
    ret = by_map.get("retrieved_context")
    no_ctx = by_map.get("no_context")

    retrieval_loss_fc = (
        round(gold["fact_coverage_pct"] - ret["fact_coverage_pct"], 1)
        if (gold and ret)
        else None
    )
    retrieval_loss_q = (
        round(gold["integral_efficiency_pct"] - ret["integral_efficiency_pct"], 1)
        if (gold and ret)
        else None
    )
    rag_gain_over_no_context_fc = (
        round(ret["fact_coverage_pct"] - no_ctx["fact_coverage_pct"], 1)
        if (ret and no_ctx)
        else None
    )
    gold_gain_over_no_context_fc = (
        round(gold["fact_coverage_pct"] - no_ctx["fact_coverage_pct"], 1)
        if (gold and no_ctx)
        else None
    )

    return {
        "modes_present": list(by_map.keys()),
        "retrieval_loss_fc_pct": retrieval_loss_fc,
        "retrieval_loss_q_det_pct": retrieval_loss_q,
        "rag_gain_over_no_context_fc_pct": rag_gain_over_no_context_fc,
        "gold_gain_over_no_context_fc_pct": gold_gain_over_no_context_fc,
        "retrieved_hit_at_1_pct": ret.get("retrieval_hit_at_1_pct") if ret else None,
        "retrieved_hit_at_3_pct": ret.get("retrieval_hit_at_3_pct") if ret else None,
    }


def _generate_scientific_findings(
    by_model: List[Dict[str, Any]],
    by_prompt: List[Dict[str, Any]],
    by_category: List[Dict[str, Any]],
    by_context_mode: List[Dict[str, Any]],
    context_comparison: Dict[str, Any],
    stat_tests: Dict[str, Any],
    pareto_info: Dict[str, Any],
    sensitivity_info: Dict[str, Any],
    overall_stats: Dict[str, Any],
) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []

    if by_model:
        best_fc_m = max(by_model, key=lambda x: x["fact_coverage_pct"])
        fastest_m = min(by_model, key=lambda x: x["latency_mean_ms"])
        m_test = stat_tests.get("models_fact_coverage", {})
        m_pairs = m_test.get("pairwise", [])

        sig_pairs = [p for p in m_pairs if p.get("significant_005") and (p.get("n_paired") or 0) >= WILCOXON_MIN_PAIRS]
        if m_test.get("status") == "insufficient_data" or (m_test.get("n_paired_units") or 0) < WILCOXON_MIN_PAIRS:
            stat_note = (
                f"Статистичний критерій Вілкоксона/Фрідмана не застосовувався через малий обсяг спільних запитів "
                f"(n = {m_test.get('n_paired_units', 0)} < {WILCOXON_MIN_PAIRS}); на малих вибірках висновки про перевагу не робляться."
            )
        elif sig_pairs:
            top_pair = sig_pairs[0]
            stat_note = (
                f"Парний тест Вілкоксона з поправкою Холма підтвердив статистично значущу різницю між "
                f"{top_pair['group_a']} та {top_pair['group_b']} (n = {top_pair['n_paired']}, "
                f"різниця = {top_pair['mean_diff']}%, 95% ДІ [{top_pair.get('diff_ci95_low', 0.0)}%, {top_pair.get('diff_ci95_high', 0.0)}%], "
                f"p_Holm = {top_pair['p_holm']}, Cliff's delta = {top_pair['cliffs_delta']}, {top_pair['effect_magnitude_uk']})."
            )
        elif len(by_model) >= 2:
            stat_note = (
                f"За результатами парного тесту на збалансованому перетині (n = {m_test.get('n_paired_units', 0)} запитів) "
                f"різниця між моделями за фактичною повнотою не досягла порогу статистичної значущості після поправки Холма (p_Holm >= 0.05)."
            )
        else:
            stat_note = "У вибірці представлена лише одна модель LLM."

        pareto_lat = ", ".join(pareto_info.get("pareto_quality_latency_models", []))
        sens_note = ""
        if sensitivity_info.get("available"):
            sens_note = (
                f" Аналіз чутливості ваг композитного індексу (сітка з {sensitivity_info['grid_size']} комбінацій) "
                f"показав середню рангову кореляцію Кендалла tau = {sensitivity_info['mean_kendall_tau']} "
                f"(мінімальна tau = {sensitivity_info['min_kendall_tau']})."
            )

        findings.append({
            "title": "Порівняння моделей та Парето-оптимальність (Якість / Затримка / Вартість)",
            "text": (
                f"Найвищу середню фактичну повноту продемонструвала модель {best_fc_m['model_name']}: "
                f"FC = {best_fc_m['fact_coverage_pct']}% (95% ДІ [{best_fc_m['fact_coverage_ci95'][0]}%, {best_fc_m['fact_coverage_ci95'][1]}%], "
                f"SD = {best_fc_m['fact_coverage_std']}%, n = {best_fc_m['count']}), "
                f"композитний індекс Q_det = {best_fc_m['integral_efficiency_pct']}% "
                f"(95% ДІ [{best_fc_m['efficiency_ci95'][0]}%, {best_fc_m['efficiency_ci95'][1]}%]). "
                f"Найменшу затримку забезпечила модель {fastest_m['model_name']}: середній час {fastest_m['latency_mean_ms']} мс "
                f"(медіана {fastest_m['latency_median_ms']} мс, P95 = {fastest_m['latency_p95_ms']} мс, n = {fastest_m['count']}). "
                f"На Парето-фронті за співвідношенням якості та затримки перебувають: [{pareto_lat}]. "
                f"{stat_note}{sens_note}"
            ),
        })

    if by_prompt and len(by_prompt) >= 2:
        best_p = max(by_prompt, key=lambda x: x["fact_coverage_pct"])
        base_p = next((p for p in by_prompt if p["prompt_code"] == "P1"), by_prompt[0])
        diff_fc = round(best_p["fact_coverage_pct"] - base_p["fact_coverage_pct"], 1)
        p_test = stat_tests.get("prompts_fact_coverage", {})
        p_pairs = p_test.get("pairwise", [])
        sig_p = [p for p in p_pairs if p.get("significant_005") and (p.get("n_paired") or 0) >= WILCOXON_MIN_PAIRS]

        if p_test.get("status") == "insufficient_data" or (p_test.get("n_paired_units") or 0) < WILCOXON_MIN_PAIRS:
            p_stat_str = (
                f"Обсяг вибірки (n = {p_test.get('n_paired_units', 0)} < {WILCOXON_MIN_PAIRS}) недостатній для тесту Фрідмана/Вілкоксона; "
                "твердження про статистичну перевагу не формулюються."
            )
        elif sig_p:
            bp = sig_p[0]
            p_stat_str = (
                f"Статистично значуща різниця підтверджена для пари {bp['group_a']} vs {bp['group_b']} "
                f"(різниця = {bp['mean_diff']}%, 95% ДІ [{bp.get('diff_ci95_low', 0.0)}%, {bp.get('diff_ci95_high', 0.0)}%], "
                f"p_Holm = {bp['p_holm']}, Cliff's delta = {bp['cliffs_delta']})."
            )
        else:
            p_stat_str = (
                f"На поточній вибірці (n = {p_test.get('n_paired_units', 0)}) відмінності між стратегіями промптів "
                f"за фактичною повнотою є статистично незначущими (p_Holm >= 0.05)."
            )

        findings.append({
            "title": "Вплив стратегії конструювання промпту (P1-P5)",
            "text": (
                f"Найвищу фактичну повноту отримано для стратегії {best_p['prompt_code']} ({best_p['prompt_name_uk']}): "
                f"FC = {best_p['fact_coverage_pct']}% (95% ДІ [{best_p['fact_coverage_ci95'][0]}%, {best_p['fact_coverage_ci95'][1]}%], n = {best_p['count']}), "
                f"надійність формату = {best_p['format_compliance_pct']}% (валідний JSON-контракт = {best_p.get('contract_ok_rate_pct', 100.0)}%). "
                f"Різниця відносно базової лінії {base_p['prompt_code']} (FC = {base_p['fact_coverage_pct']}%, n = {base_p['count']}) "
                f"становить {diff_fc:+.1f} в.п. {p_stat_str}"
            ),
        })

    if by_context_mode and len(by_context_mode) >= 2:
        parts = [
            f"{m['context_mode_name_uk']}: FC = {m['fact_coverage_pct']}%, Q_det = {m['integral_efficiency_pct']}%, HR = {m['hallucination_rate_pct']}% (n = {m['count']})"
            for m in by_context_mode
        ]
        loss_note = ""
        if context_comparison.get("retrieval_loss_fc_pct") is not None:
            loss_note = (
                f" Втрата повноти фактів на етапі пошуку (різниця між gold_context та retrieved_context) "
                f"становить {context_comparison['retrieval_loss_fc_pct']:+.1f} в.п. "
                f"(hit@1 = {context_comparison.get('retrieved_hit_at_1_pct')}%, hit@3 = {context_comparison.get('retrieved_hit_at_3_pct')}%)."
            )
        findings.append({
            "title": "Вплив режиму подачі контексту та якості пошуку знань (Гіпотеза H2)",
            "text": "Порівняння умов контекстуалізації: " + "; ".join(parts) + "." + loss_note,
        })

    if by_category and len(by_category) >= 2:
        easiest_cat = max(by_category, key=lambda x: x["fact_coverage_pct"])
        hardest_cat = min(by_category, key=lambda x: x["fact_coverage_pct"])
        findings.append({
            "title": "Специфіка автоматизації за категоріями запитів",
            "text": (
                f"Найвища повнота покриття фактів спостерігається у категорії {easiest_cat['category_name_uk']}: "
                f"FC = {easiest_cat['fact_coverage_pct']}% (95% ДІ [{easiest_cat['fact_coverage_ci95'][0]}%, {easiest_cat['fact_coverage_ci95'][1]}%], n = {easiest_cat['count']}). "
                f"Найскладнішою є категорія {hardest_cat['category_name_uk']}: "
                f"FC = {hardest_cat['fact_coverage_pct']}% (95% ДІ [{hardest_cat['fact_coverage_ci95'][0]}%, {hardest_cat['fact_coverage_ci95'][1]}%], n = {hardest_cat['count']})."
            ),
        })

    cm = overall_stats.get("confusion_matrix", {})
    if cm and cm.get("total", 0) > 0:
        findings.append({
            "title": "Надійність шлюзу ескалації та безпека автоматизації",
            "text": (
                f"Загальна точність маршрутизації становить {cm['accuracy_pct']}% (n = {cm['total']}): "
                f"TP = {cm['tp']}, TN = {cm['tn']}, FP (зайва ескалація) = {cm['fp']}, "
                f"FN (критичний пропуск обов'язкової ескалації) = {cm['fn']}. "
                f"Повнота виявлення критичних інцидентів (Escalation Recall) = {cm['recall_pct']}%, "
                f"точність (Precision) = {cm['precision_pct']}%, F1 = {cm['f1_pct']}%. "
                f"Частка критичних помилок безпеки (FN-rate) = {cm['critical_fn_rate_pct']}%. "
                f"Рівень безпечної автоматизації стандартних запитів (без ескалації та без галюцинацій при FC >= {AUTOMATION_FC_THRESHOLD_PCT}%) "
                f"становить {cm['automation_success_non_esc_pct']}%. "
                f"Частка відповідей із виявленими потенційними галюцинаціями: {overall_stats.get('hallucination_rate_pct', 0.0)}%."
            ),
        })

    return findings


def _matrix_cell(sub: List[Dict[str, Any]]) -> Dict[str, Any]:
    desc_fc = describe_sample([float(x["fact_coverage_pct"]) for x in sub], n_boot=2000)
    desc_eff = describe_sample([float(x["integral_efficiency_pct"]) for x in sub], n_boot=2000)
    return {
        "n": len(sub),
        "insufficient_n": len(sub) < WILCOXON_MIN_PAIRS,
        "insufficient_sample": len(sub) < WILCOXON_MIN_PAIRS,
        "integral_efficiency_pct": round(desc_eff["mean"], 1),
        "efficiency_ci95": [desc_eff["ci95_low"], desc_eff["ci95_high"]],
        "fact_coverage_pct": round(desc_fc["mean"], 1),
        "fact_coverage_ci95": [desc_fc["ci95_low"], desc_fc["ci95_high"]],
        "format_compliance_pct": round(_mean([float(x["format_compliance_pct"]) for x in sub]), 1),
        "relevance_score": round(_mean([float(x["relevance_score"]) for x in sub]), 2),
        "automation_rate_pct": round(_mean([100.0 if x["automation_success"] else 0.0 for x in sub]), 1),
        "latency_mean_ms": round(_mean([float(x["latency_ms"]) for x in sub]), 1),
    }


def _build_matrix(
    unit_rows: List[Dict[str, Any]], row_ids: List[str], col_ids: List[str], row_key: str, col_key: str
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Матриця «рядок x стовпець» із середніми метриками, довірчими інтервалами та розміром вибірки в клітинці."""
    matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row_id in row_ids:
        matrix[row_id] = {}
        for col_id in col_ids:
            sub = [r for r in unit_rows if r[row_key] == row_id and r[col_key] == col_id]
            if sub:
                matrix[row_id][col_id] = _matrix_cell(sub)
    return matrix


def _build_analytics_payload(
    rows: List[Dict[str, Any]],
    prompts_map: Dict[str, Any],
    include_self_judged: bool = False,
) -> Dict[str, Any]:
    rubric_diag = compute_rubric_diagnostics(rows, include_self_judged=include_self_judged)
    dom_judge = rubric_diag.get("dominant_judge_model") or None

    models_present: List[str] = []
    for m_id in DEFAULT_MODELS_CATALOG.keys():
        if any(r["model_id"] == m_id for r in rows):
            models_present.append(m_id)
    for r in rows:
        if r["model_id"] not in models_present:
            models_present.append(r["model_id"])

    by_model: List[Dict[str, Any]] = []
    for m_id in models_present:
        sub = [r for r in rows if r["model_id"] == m_id]
        m_cfg = get_model_config(m_id)
        stats = _calc_group_metrics(sub, include_self_judged=include_self_judged, dominant_judge=dom_judge)
        stats["model_id"] = m_id
        stats["model_name"] = m_cfg.get("name", m_id)
        stats["model_class_uk"] = m_cfg.get("class_uk", "")
        stats["price_known"] = bool(m_cfg.get("price_known", False))
        stats["cost_unknown"] = not bool(m_cfg.get("price_known", False))
        stats["price_verified"] = bool(m_cfg.get("price_verified", False))
        stats["is_free_tier"] = bool(m_cfg.get("is_free_tier", False))
        by_model.append(stats)

    prompts_present = sorted(list({r["prompt_code"] for r in rows}))
    by_prompt: List[Dict[str, Any]] = []
    for p_code in prompts_present:
        sub = [r for r in rows if r["prompt_code"] == p_code]
        p_info = prompts_map.get(p_code, {"name_uk": p_code, "strategy_type": p_code})
        stats = _calc_group_metrics(sub, include_self_judged=include_self_judged, dominant_judge=dom_judge)
        stats["prompt_code"] = p_code
        stats["prompt_name_uk"] = p_info.get("name_uk", p_code)
        stats["strategy_type"] = p_info.get("strategy_type", "")
        by_prompt.append(stats)

    cats_present = [c for c in QUERY_CATEGORIES.keys() if any(r["category"] == c for r in rows)]
    by_category: List[Dict[str, Any]] = []
    for c_code in cats_present:
        sub = [r for r in rows if r["category"] == c_code]
        c_info = QUERY_CATEGORIES.get(c_code, {"name_uk": c_code})
        stats = _calc_group_metrics(sub, include_self_judged=include_self_judged, dominant_judge=dom_judge)
        stats["category_code"] = c_code
        stats["category_name_uk"] = c_info.get("name_uk", c_code)
        by_category.append(stats)

    modes_order = ["no_context", "retrieved_context", "gold_context"]
    modes_present = [
        cm for cm in modes_order if any((r.get("context_mode") or "gold_context") == cm for r in rows)
    ]
    for r in rows:
        cm_val = str(r.get("context_mode") or "gold_context")
        if cm_val not in modes_present:
            modes_present.append(cm_val)

    by_context_mode: List[Dict[str, Any]] = []
    for cm_code in modes_present:
        sub = [r for r in rows if (r.get("context_mode") or "gold_context") == cm_code]
        if sub:
            stats = _calc_group_metrics(sub, include_self_judged=include_self_judged, dominant_judge=dom_judge)
            stats["context_mode"] = cm_code
            stats["context_mode_name_uk"] = CONTEXT_MODE_LABELS_UK.get(cm_code, cm_code)
            by_context_mode.append(stats)

    context_mode_comparison = _compute_context_mode_comparison(by_context_mode)

    unit_rows = aggregate_repeats_by_query(rows)
    domain_names = {d["code"]: d["domain_name"] for d in fetch_all_domains()}
    domains_present = [d for d in domain_names if any(r.get("domain") == d for r in rows)]
    by_domain: List[Dict[str, Any]] = []
    for d_code in domains_present:
        stats = _calc_group_metrics(
            [r for r in rows if r.get("domain") == d_code], include_self_judged=include_self_judged, dominant_judge=dom_judge
        )
        stats["domain_code"] = d_code
        stats["domain_name_uk"] = domain_names[d_code]
        by_domain.append(stats)

    matrix_model_prompt = _build_matrix(unit_rows, models_present, prompts_present, "model_id", "prompt_code")
    matrix_model_category = _build_matrix(unit_rows, models_present, cats_present, "model_id", "category")
    matrix_model_domain = _build_matrix(unit_rows, models_present, domains_present, "model_id", "domain")
    matrix_prompt_category = _build_matrix(unit_rows, prompts_present, cats_present, "prompt_code", "category")

    overall_stats = _calc_group_metrics(rows, include_self_judged=include_self_judged, dominant_judge=dom_judge)

    stat_tests = {
        "models_fact_coverage": compare_paired_conditions(rows, group_key="model_id", metric_key="fact_coverage_pct"),
        "models_composite_quality": compare_paired_conditions(rows, group_key="model_id", metric_key="integral_efficiency_pct"),
        "prompts_fact_coverage": compare_paired_conditions(rows, group_key="prompt_code", metric_key="fact_coverage_pct"),
        "prompts_composite_quality": compare_paired_conditions(rows, group_key="prompt_code", metric_key="integral_efficiency_pct"),
    }
    if len(by_context_mode) >= 2:
        stat_tests["context_modes_fact_coverage"] = compare_paired_conditions(
            rows, group_key="context_mode", metric_key="fact_coverage_pct"
        )

    pareto_info = compute_pareto_fronts(by_model)
    sensitivity_info = compute_weight_sensitivity_analysis(by_model)
    metrics_corr = compute_metrics_correlation_matrix(rows)
    judge_val = compute_judge_human_validation(rows)

    scientific_findings = _generate_scientific_findings(
        by_model=by_model,
        by_prompt=by_prompt,
        by_category=by_category,
        by_context_mode=by_context_mode,
        context_comparison=context_mode_comparison,
        stat_tests=stat_tests,
        pareto_info=pareto_info,
        sensitivity_info=sensitivity_info,
        overall_stats=overall_stats,
    )

    decision_support = compute_decision_support(
        by_model=by_model,
        by_prompt=by_prompt,
        by_category=by_category,
        overall_stats=overall_stats,
        pareto_info=pareto_info,
        stat_tests=stat_tests,
    )

    return {
        "overall": overall_stats,
        "by_model": by_model,
        "by_prompt": by_prompt,
        "by_category": by_category,
        "by_context_mode": by_context_mode,
        "context_mode_comparison": context_mode_comparison,
        "matrix_model_prompt": matrix_model_prompt,
        "matrix_model_category": matrix_model_category,
        "matrix_prompt_category": matrix_prompt_category,
        "matrix_model_domain": matrix_model_domain,
        "by_domain": by_domain,
        "statistical_tests": stat_tests,
        "pareto_fronts": pareto_info,
        "weight_sensitivity": sensitivity_info,
        "metrics_correlation_matrix": metrics_corr,
        "rubric_diagnostics": rubric_diag,
        "judge_validation": judge_val,
        "repeat_stability": overall_stats.get("stability_info", {}),
        "scientific_findings": scientific_findings,
        "decision_support": decision_support,
        "hypotheses": assess_hypotheses(
            by_model=by_model,
            by_prompt=by_prompt,
            by_context_mode=by_context_mode,
            matrix_model_domain=matrix_model_domain,
            by_domain=by_domain,
            n_queries=len({r["query_code"] for r in rows}),
        ),
        "conclusion": build_conclusion(
            by_model=by_model,
            stat_tests=stat_tests,
            n_queries=int(stat_tests["models_composite_quality"].get("n_paired_units") or 0),
        ),
    }


def compute_hypotheses_overview() -> Dict[str, Any]:
    """Вердикти за гіпотезами по всіх завершених серіях: показуються на головній сторінці."""
    conn = get_connection()
    try:
        run_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM experiment_runs WHERE execution_mode != 'sandbox' AND completed_items > 0 ORDER BY id"
            )
        ]
    finally:
        conn.close()
    if not run_ids:
        return {"available": False, "runs": 0}
    payload = compute_flexible_analytics(run_ids=run_ids, balanced_only=True)
    hypotheses = payload.get("hypotheses")
    if not hypotheses:
        return {"available": False, "runs": len(run_ids)}
    return {"available": True, "runs": len(run_ids), "hypotheses": hypotheses}


def compute_run_analytics(
    run_id: int,
    balanced_only: bool = True,
    include_self_judged: bool = False,
) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiment_runs WHERE id = ?", (run_id,))
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        return {}

    run_meta = dict(run_row)
    cur.execute("SELECT * FROM evaluation_results WHERE run_id = ? ORDER BY id ASC", (run_id,))
    raw_rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    run_errors = fetch_run_errors(run_id)
    if not raw_rows:
        return {
            "run": run_meta,
            "total_measurements": 0,
            "balanced_pairs_count": 0,
            "run_errors": run_errors,
            "overall": {"count": 0},
        }

    raw_rows = _enrich_rows_with_ground_truth(raw_rows)
    rows, balance_info = _filter_balanced_intersection(raw_rows, balanced_only=balanced_only)
    prompts_map = {p["code"]: p for p in fetch_all_prompts()}
    payload = _build_analytics_payload(rows, prompts_map, include_self_judged=include_self_judged)

    payload["run"] = {
        "id": run_meta["id"],
        "title": run_meta["title"],
        "description": run_meta["description"],
        "created_at": run_meta["created_at"],
        "execution_mode": run_meta["execution_mode"],
        "repeats": run_meta["repeats"],
        "total_items": run_meta["total_items"],
        "completed_items": len(raw_rows),
        "status": run_meta["status"],
        "context_mode": run_meta.get("context_mode", "gold_context"),
        "top_k": int(run_meta.get("top_k") or 3),
        "dataset_version": run_meta.get("dataset_version", "2.0"),
        "dataset_hash": run_meta.get("dataset_hash", ""),
        "code_version": run_meta.get("code_version", ""),
        "errors_count": len(run_errors),
    }
    payload["balance_info"] = balance_info
    payload["balanced_pairs_count"] = balance_info.get("common_queries_count", 0)
    payload["run_errors"] = run_errors
    return payload


def compute_flexible_analytics(
    run_ids: List[int],
    model_ids: Optional[List[str]] = None,
    prompt_codes: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    balanced_only: bool = True,
    include_self_judged: bool = False,
    domains: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not run_ids:
        return {"runs": [], "overall": {"count": 0}}

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in run_ids)
    cur.execute(f"SELECT * FROM experiment_runs WHERE id IN ({placeholders}) ORDER BY id ASC", tuple(run_ids))
    run_rows = [dict(r) for r in cur.fetchall()]
    if not run_rows:
        conn.close()
        return {"runs": [], "overall": {"count": 0}}

    cur.execute(
        f"SELECT * FROM evaluation_results WHERE run_id IN ({placeholders}) ORDER BY run_id ASC, id ASC",
        tuple(run_ids),
    )
    all_rows_in_runs = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not all_rows_in_runs:
        return {"runs": run_rows, "overall": {"count": 0}}

    all_rows_in_runs = _enrich_rows_with_ground_truth(all_rows_in_runs)
    prompts_map = {p["code"]: p for p in fetch_all_prompts()}

    avail_models = []
    for m_id in DEFAULT_MODELS_CATALOG.keys():
        if any(r["model_id"] == m_id for r in all_rows_in_runs):
            avail_models.append({
                "id": m_id,
                "name": DEFAULT_MODELS_CATALOG[m_id].get("name", m_id),
                "class_uk": DEFAULT_MODELS_CATALOG[m_id].get("class_uk", ""),
            })
    for r in all_rows_in_runs:
        if not any(m["id"] == r["model_id"] for m in avail_models):
            m_cfg = get_model_config(r["model_id"])
            avail_models.append({
                "id": r["model_id"],
                "name": m_cfg.get("name", r["model_id"]),
                "class_uk": m_cfg.get("class_uk", "LLM"),
            })

    avail_prompts = []
    for p_code in sorted(list({r["prompt_code"] for r in all_rows_in_runs})):
        p_info = prompts_map.get(p_code, {"name_uk": p_code, "strategy_type": p_code})
        avail_prompts.append({
            "code": p_code,
            "name_uk": p_info.get("name_uk", p_code),
            "strategy_type": p_info.get("strategy_type", ""),
        })

    domain_names = {d["code"]: d["domain_name"] for d in fetch_all_domains()}
    avail_domains = [
        {"code": code, "name_uk": name}
        for code, name in domain_names.items()
        if any(r["domain"] == code for r in all_rows_in_runs)
    ]

    avail_categories = []
    for c_code in QUERY_CATEGORIES.keys():
        if any(r["category"] == c_code for r in all_rows_in_runs):
            avail_categories.append({
                "code": c_code,
                "name_uk": QUERY_CATEGORIES[c_code]["name_uk"],
            })

    rows = all_rows_in_runs
    if model_ids is not None and len(model_ids) > 0:
        model_set = set(model_ids)
        rows = [r for r in rows if r["model_id"] in model_set]
    if prompt_codes is not None and len(prompt_codes) > 0:
        prompt_set = set(prompt_codes)
        rows = [r for r in rows if r["prompt_code"] in prompt_set]
    if categories is not None and len(categories) > 0:
        cat_set = set(categories)
        rows = [r for r in rows if r["category"] in cat_set]
    if domains:
        rows = [r for r in rows if r["domain"] in set(domains)]

    balanced_rows, balance_info = _filter_balanced_intersection(rows, balanced_only=balanced_only)
    payload = _build_analytics_payload(balanced_rows, prompts_map, include_self_judged=include_self_judged)

    by_run: List[Dict[str, Any]] = []
    for r_meta in run_rows:
        sub = [r for r in balanced_rows if r["run_id"] == r_meta["id"]]
        if sub:
            stats = _calc_group_metrics(sub, include_self_judged=include_self_judged)
            stats["run_id"] = r_meta["id"]
            stats["run_title"] = r_meta["title"]
            stats["created_at"] = r_meta["created_at"]
            stats["status"] = r_meta.get("status", "completed")
            by_run.append(stats)

    runs_label = ", ".join(f"№{r['id']} «{r['title']}»" for r in run_rows)
    first_run = run_rows[0]
    has_incomplete = any(r.get("status") != "completed" for r in run_rows)

    payload["run"] = {
        "id": first_run["id"],
        "run_ids": [r["id"] for r in run_rows],
        "title": runs_label if len(run_rows) > 1 else first_run["title"],
        "created_at": first_run["created_at"],
        "total_items": len(all_rows_in_runs),
        "filtered_items": len(balanced_rows),
        "raw_filtered_items": len(rows),
        "has_incomplete": has_incomplete,
        "status": first_run.get("status", "completed"),
    }
    payload["balance_info"] = balance_info
    payload["balanced_pairs_count"] = balance_info.get("common_queries_count", 0)
    payload["available_filters"] = {
        "models": avail_models,
        "prompts": avail_prompts,
        "categories": avail_categories,
        "domains": avail_domains,
    }
    payload["by_run"] = by_run
    return payload


def get_evaluation_item_detail(item_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM evaluation_results WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    rec = dict(row)

    cur.execute("SELECT * FROM experiment_runs WHERE id = ?", (rec["run_id"],))
    run_row = cur.fetchone()
    run_meta = dict(run_row) if run_row else {"id": rec["run_id"], "title": f"Серія №{rec['run_id']}"}
    conn.close()

    query_item = fetch_query_by_id(rec["query_id"])
    if not query_item:
        all_qs = fetch_all_queries()
        query_item = next((q for q in all_qs if q["code"] == rec["query_code"]), None)

    cat_info = QUERY_CATEGORIES.get(rec["category"], {"name_uk": rec["category"]})
    model_info = get_model_config(rec["model_id"])

    user_query_str = query_item["user_query"] if query_item else rec["query_code"]
    context_fact_str = query_item["context_fact"] if query_item else ""
    reference_answer_str = query_item["reference_answer"] if query_item else ""
    required_keywords = query_item["required_keywords"] if query_item else []
    expected_escalation = bool(query_item["expected_escalation"]) if query_item else False
    item_domain = query_item.get("domain", "fintech") if query_item else "fintech"

    prompts_all = {p["code"]: p for p in fetch_all_prompts()}
    prompt_raw = prompts_all.get(rec["prompt_code"], {})

    hist_ver = None
    if rec.get("prompt_version_id"):
        hist_ver = fetch_prompt_version_by_id(int(rec["prompt_version_id"]))

    stored_sys = rec.get("rendered_system_prompt") or ""
    stored_usr = rec.get("rendered_user_prompt") or ""
    if stored_sys or stored_usr:
        sys_prompt = stored_sys
        usr_prompt = stored_usr
        t_hash = rec.get("template_hash") or ""
        p_ver = int(rec.get("prompt_version") or 1)
        full_prompt = f"[SYSTEM PROMPT ({rec['prompt_code']} v{p_ver})]\n{sys_prompt}\n\n[USER PROMPT]\n{usr_prompt}"
    else:
        prompt_pack = build_prompt_messages(
            prompt_code=rec["prompt_code"],
            user_query=user_query_str,
            context_fact=context_fact_str,
            category_code=rec["category"],
            category_name_uk=cat_info.get("name_uk", rec["category"]),
            exclude_query_code=rec["query_code"],
            domain=item_domain,
        )
        sys_prompt = prompt_pack["system_prompt"]
        usr_prompt = prompt_pack["user_prompt"]
        t_hash = prompt_pack["template_hash"]
        p_ver = prompt_pack["prompt_version"]
        full_prompt = prompt_pack["full_rendered_prompt"]

    matched_keywords = json.loads(rec.get("matched_keywords_json") or "[]")
    missing_keywords = json.loads(rec.get("missing_keywords_json") or "[]")
    few_shot_used = json.loads(rec.get("few_shot_used_json") or "[]")
    hallucinated_items = json.loads(rec.get("hallucinated_items_json") or "[]")
    format_checks = json.loads(rec.get("format_checks_json") or "{}")
    judge_scores = json.loads(rec.get("judge_scores_json") or "{}")
    judge_rationale = json.loads(rec.get("judge_rationale_json") or "{}")
    retrieved_ids = json.loads(rec.get("retrieved_ids_json") or "[]")

    cost_val = (
        float(rec["cost_usd"])
        if rec.get("cost_usd") and float(rec["cost_usd"]) > 0
        else calculate_cost_usd(
            rec["model_id"],
            int(rec.get("input_tokens") or 0),
            int(rec.get("output_tokens") or 0),
            int(rec.get("thinking_tokens") or 0),
        )
    )
    cost_per_1k_val = round(cost_val * 1000.0, 4)
    tot_tok_item = max(
        1,
        int(rec.get("input_tokens") or 0)
        + int(rec.get("output_tokens") or 0)
        + int(rec.get("thinking_tokens") or 0),
    )
    cost_per_1m_val = round((cost_val / tot_tok_item) * 1_000_000.0, 4)

    latency_ms = float(rec.get("latency_ms", 0.0))
    call_ms = float(rec.get("call_ms") or latency_ms)
    wall_ms = float(rec.get("wall_ms") or latency_ms)
    avg_5 = round(
        (
            rec["relevance_score"]
            + rec["factuality_score"]
            + rec["completeness_score"]
            + rec["clarity_score"]
            + rec["format_score"]
        )
        / 5.0,
        2,
    )

    w_fc = DEFAULT_EVALUATION_WEIGHTS["fact_coverage"]
    w_ss = DEFAULT_EVALUATION_WEIGHTS["semantic_similarity"]

    return {
        "id": rec["id"],
        "item_id": rec["id"],
        "run_id": rec["run_id"],
        "run_title": run_meta.get("title", ""),
        "source": rec.get("source", "batch"),
        "created_at": rec.get("created_at", ""),
        "repeat_index": rec.get("repeat_index", 1),
        "context_mode": rec.get("context_mode", "gold_context"),
        "top_k": int(rec.get("top_k") or 3),
        "retrieved_ids": retrieved_ids,
        "retrieval_hit_at_1": rec.get("retrieval_hit_at_1"),
        "retrieval_hit_at_3": rec.get("retrieval_hit_at_3"),
        "query": {
            "id": rec["query_id"],
            "code": rec["query_code"],
            "domain": item_domain,
            "category": rec["category"],
            "category_name_uk": cat_info.get("name_uk", rec["category"]),
            "user_query": user_query_str,
            "context_fact": context_fact_str,
            "reference_answer": reference_answer_str,
            "required_keywords": required_keywords,
            "expected_escalation": expected_escalation,
        },
        "model": {
            "id": rec["model_id"],
            "name": model_info.get("name", rec["model_id"]),
            "class_uk": model_info.get("class_uk", ""),
            "provider_used": rec.get("provider_used", ""),
            "price_verified": bool(model_info.get("price_verified", False)),
            "is_free_tier": bool(model_info.get("is_free_tier", True)),
        },
        "prompt": {
            "code": rec["prompt_code"],
            "name_uk": (hist_ver or prompt_raw).get("name_uk", rec["prompt_code"]),
            "strategy_type": (hist_ver or prompt_raw).get("strategy_type", ""),
            "version": p_ver,
            "template_hash": t_hash,
            "few_shot_used": few_shot_used,
            "system_template": (hist_ver or prompt_raw).get("system_template", ""),
            "user_template": (hist_ver or prompt_raw).get("user_template", ""),
            "system_prompt": sys_prompt,
            "user_prompt": usr_prompt,
            "full_rendered_prompt": full_prompt,
        },
        "generation": {
            "generated_answer": rec.get("generated_answer", ""),
            "parsed_reason": rec.get("parsed_reason", ""),
            "contract_ok": bool(rec.get("contract_ok", 1)),
            "parse_method": rec.get("parse_method", "json_direct"),
            "latency_ms": round(latency_ms, 1),
            "call_ms": round(call_ms, 1),
            "wall_ms": round(wall_ms, 1),
            "attempts": int(rec.get("attempts") or 1),
            "finish_reason": rec.get("finish_reason", "stop"),
            "truncated": bool(rec.get("truncated", 0)),
            "temperature": float(rec.get("temperature", 0.0)),
            "max_output_tokens": int(rec.get("max_output_tokens", 2000)),
            "seed": rec.get("seed"),
            "input_tokens": int(rec.get("input_tokens", 0)),
            "output_tokens": int(rec.get("output_tokens", 0)),
            "thinking_tokens": int(rec.get("thinking_tokens") or 0),
            "tokens_estimated": bool(rec.get("tokens_estimated", 0)),
            "total_tokens": int(rec.get("input_tokens", 0))
            + int(rec.get("output_tokens", 0))
            + int(rec.get("thinking_tokens") or 0),
            "cost_usd": round(cost_val, 6),
            "cost_per_1k_usd": cost_per_1k_val,
            "cost_per_1m_usd": cost_per_1m_val,
        },
        "evaluation": {
            "integral_efficiency_pct": rec["integral_efficiency_pct"],
            "fact_coverage_pct": rec["fact_coverage_pct"],
            "semantic_similarity_pct": rec["semantic_similarity_pct"],
            "stem_cosine_pct": float(rec.get("stem_cosine_pct") or 0.0),
            "rouge_l_f1_pct": float(rec.get("rouge_l_f1_pct") or 0.0),
            "format_compliance_pct": rec["format_compliance_pct"],
            "format_checks": format_checks,
            "contract_ok": bool(rec.get("contract_ok", 1)),
            "parse_method": rec.get("parse_method", "json_direct"),
            "hallucinated_items": hallucinated_items,
            "hallucination_count": int(rec.get("hallucination_count") or 0),
            "has_hallucination": bool(rec.get("has_hallucination", 0)),
            "nist_composite_pct": rec["nist_composite_pct"],
            "rubric_evaluated": bool(rec.get("rubric_evaluated", 0)),
            "manual_scored": bool(rec.get("manual_scored", 0)),
            "manual_annotator": rec.get("manual_annotator", ""),
            "manual_notes": rec.get("manual_notes", ""),
            "judge_model": rec.get("judge_model", ""),
            "judge_scores": judge_scores,
            "judge_rationale": judge_rationale,
            "avg_nist_score_5": avg_5,
            "relevance_score": rec["relevance_score"],
            "factuality_score": rec["factuality_score"],
            "completeness_score": rec["completeness_score"],
            "clarity_score": rec["clarity_score"],
            "format_score": rec["format_score"],
            "policy_adherence_score": float(rec.get("policy_adherence_score") or rec["format_score"]),
            "confidence_score": rec["confidence_score"],
            "requires_escalation": bool(rec["requires_escalation"]),
            "escalation_regex": bool(rec.get("escalation_regex", rec["requires_escalation"])),
            "escalation_judge": bool(rec["escalation_judge"]) if rec.get("escalation_judge") is not None else None,
            "escalation_disagreement": bool(rec.get("escalation_disagreement", 0)),
            "escalation_method": rec.get("escalation_method", "regex"),
            "escalation_correct": bool(rec["escalation_correct"]),
            "automation_success": bool(rec["automation_success"]),
            "matched_keywords": matched_keywords,
            "missing_keywords": missing_keywords,
            "formula_breakdown_uk": (
                f"Q_det = {w_fc:.2f} * FC ({rec['fact_coverage_pct']}%) + "
                f"{w_ss:.2f} * SS ({rec['semantic_similarity_pct']}%) = {rec['integral_efficiency_pct']}%"
            ),
        },
    }


def export_manual_validation_sample_csv(
    run_id: int,
    max_items: int = MANUAL_VALIDATION_SAMPLE_MAX,
    seed: int = 42,
) -> str:
    """
    Формує стратифіковану вибірку (до max_items відповідей) із серії run_id у форматі CSV
    для ручного оцінювання експертом за 5 критеріями рубрики (1..5).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.*, q.user_query, q.context_fact, q.reference_answer, q.domain
        FROM evaluation_results e
        LEFT JOIN queries q ON e.query_id = q.id
        WHERE e.run_id = ? AND e.source != 'sandbox'
        ORDER BY e.id ASC
        """,
        (run_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not rows:
        return ""

    strata: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (str(r.get("model_id") or ""), str(r.get("category") or ""))
        strata.setdefault(key, []).append(r)

    rng = random.Random(seed)
    for key in strata:
        items = strata[key]
        judged = [x for x in items if int(x.get("rubric_evaluated") or 0) == 1]
        unjudged = [x for x in items if int(x.get("rubric_evaluated") or 0) == 0]
        rng.shuffle(judged)
        rng.shuffle(unjudged)
        strata[key] = judged + unjudged

    selected: List[Dict[str, Any]] = []
    stratum_keys = sorted(strata.keys())
    idx = 0
    while len(selected) < max_items:
        added_in_round = False
        for sk in stratum_keys:
            if idx < len(strata[sk]) and len(selected) < max_items:
                selected.append(strata[sk][idx])
                added_in_round = True
        if not added_in_round:
            break
        idx += 1

    selected.sort(key=lambda x: int(x["id"]))

    buf = io.StringIO()
    fieldnames = [
        "item_id",
        "run_id",
        "query_code",
        "domain",
        "category",
        "model_id",
        "prompt_code",
        "user_query",
        "context_fact",
        "reference_answer",
        "generated_answer",
        "fact_coverage_pct",
        "judge_model",
        "human_relevance",
        "human_factuality",
        "human_completeness",
        "human_clarity",
        "human_policy_adherence",
        "human_notes",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()

    for r in selected:
        h_scores = {}
        if r.get("human_scores_json") and r.get("human_scores_json") != "{}":
            try:
                h_scores = json.loads(r["human_scores_json"])
            except Exception:
                h_scores = {}
        elif int(r.get("manual_scored") or 0) == 1:
            h_scores = {
                "relevance": r.get("relevance_score", ""),
                "factuality": r.get("factuality_score", ""),
                "completeness": r.get("completeness_score", ""),
                "clarity": r.get("clarity_score", ""),
                "policy_adherence": r.get("policy_adherence_score") or r.get("format_score", ""),
            }
        writer.writerow({
            "item_id": r["id"],
            "run_id": r["run_id"],
            "query_code": r.get("query_code", ""),
            "domain": r.get("domain") or "fintech",
            "category": r.get("category", ""),
            "model_id": r.get("model_id", ""),
            "prompt_code": r.get("prompt_code", ""),
            "user_query": r.get("user_query") or "",
            "context_fact": r.get("context_fact") or "",
            "reference_answer": r.get("reference_answer") or "",
            "generated_answer": r.get("generated_answer") or "",
            "fact_coverage_pct": r.get("fact_coverage_pct", 0.0),
            "judge_model": r.get("judge_model") or "",
            "human_relevance": h_scores.get("relevance", ""),
            "human_factuality": h_scores.get("factuality", ""),
            "human_completeness": h_scores.get("completeness", ""),
            "human_clarity": h_scores.get("clarity", ""),
            "human_policy_adherence": h_scores.get("policy_adherence", ""),
            "human_notes": r.get("manual_notes") or "",
        })

    return buf.getvalue()


def import_manual_validation_csv(
    run_id: int,
    csv_text: str,
    annotator: str = "expert",
) -> Dict[str, Any]:
    """
    Імпортує заповнений CSV із ручними оцінками (1..5), зберігає human_scores_json у БД
    (не затираючи judge_scores_json судді) і повертає блок узгодженості judge_validation.
    """
    cleaned = (csv_text or "").lstrip("\ufeff").strip()
    if not cleaned:
        raise ValueError("Файл CSV порожній.")

    first_line = cleaned.splitlines()[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)

    conn = get_connection()
    cur = conn.cursor()
    imported_count = 0

    def _parse_score_1_5(row_dict: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
        for k in keys:
            raw_v = row_dict.get(k)
            if raw_v is not None and str(raw_v).strip() != "":
                val = float(str(raw_v).strip().replace(",", "."))
                if 1.0 <= val <= 5.0:
                    return round(val, 2)
        return None

    for row in reader:
        raw_id = str(row.get("item_id") or row.get("id") or "").strip()
        rel = _parse_score_1_5(row, ("human_relevance", "relevance", "relevance_score"))
        fact = _parse_score_1_5(row, ("human_factuality", "factuality", "factuality_score"))
        comp = _parse_score_1_5(row, ("human_completeness", "completeness", "completeness_score"))
        clar = _parse_score_1_5(row, ("human_clarity", "clarity", "clarity_score"))
        pol = _parse_score_1_5(row, ("human_policy_adherence", "policy_adherence", "format_score"))
        if None in (rel, fact, comp, clar, pol):
            continue

        notes = str(row.get("human_notes") or row.get("manual_notes") or "").strip()
        h_json = json.dumps(
            {
                "relevance": rel,
                "factuality": fact,
                "completeness": comp,
                "clarity": clar,
                "policy_adherence": pol,
            },
            ensure_ascii=False,
        )

        target_id: Optional[int] = None
        if raw_id.isdigit():
            target_id = int(raw_id)
        else:
            q_code = str(row.get("query_code") or "").strip()
            m_id = str(row.get("model_id") or "").strip()
            p_code = str(row.get("prompt_code") or "").strip()
            if q_code and m_id:
                cur.execute(
                    "SELECT id FROM evaluation_results WHERE run_id = ? AND query_code = ? AND model_id = ? AND (? = '' OR prompt_code = ?) LIMIT 1",
                    (run_id, q_code, m_id, p_code, p_code),
                )
                found = cur.fetchone()
                if found:
                    target_id = int(found["id"])

        if target_id is None:
            continue

        cur.execute("SELECT judge_scores_json, rubric_evaluated FROM evaluation_results WHERE id = ?", (target_id,))
        existing = cur.fetchone()
        if not existing:
            continue

        has_judge_scores = bool(existing["judge_scores_json"] and existing["judge_scores_json"] != "{}")
        if has_judge_scores:
            cur.execute(
                """
                UPDATE evaluation_results
                SET human_scores_json = ?, manual_scored = 1, manual_annotator = ?, manual_notes = ?
                WHERE id = ?
                """,
                (h_json, annotator, notes, target_id),
            )
        else:
            avg_5 = (float(rel) + float(fact) + float(comp) + float(clar) + float(pol)) / 5.0
            rubric_pct = round(((avg_5 - 1.0) / 4.0) * 100.0, 1)
            cur.execute(
                """
                UPDATE evaluation_results
                SET relevance_score = ?, factuality_score = ?, completeness_score = ?,
                    clarity_score = ?, format_score = ?, policy_adherence_score = ?,
                    nist_composite_pct = ?, rubric_composite_pct = ?,
                    human_scores_json = ?, manual_scored = 1, rubric_evaluated = 1,
                    manual_annotator = ?, manual_notes = ?
                WHERE id = ?
                """,
                (rel, fact, comp, clar, pol, pol, rubric_pct, rubric_pct, h_json, annotator, notes, target_id),
            )
        imported_count += 1

    conn.commit()
    cur.execute("SELECT * FROM evaluation_results WHERE run_id = ? ORDER BY id ASC", (run_id,))
    updated_rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    validation = compute_judge_human_validation(updated_rows)
    return {
        "status": "ok",
        "run_id": run_id,
        "imported_count": imported_count,
        "judge_validation": validation,
    }

