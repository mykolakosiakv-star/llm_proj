"""
Модуль непараметричної статистики, бутстреп-оцінювання, узгодженості з людиною та Парето-аналізу.
Одиниця статистичного аналізу — запит (query_code): повтори (repeat_index) попередньо усереднюються
всередині кожного запиту, щоб уникнути псевдореплікації.
"""

from itertools import combinations
import json
import math
import random
from typing import Dict, Any, List, Optional, Tuple
from scipy import stats as sp_stats

from app.config import (
    WILCOXON_MIN_PAIRS,
    FRIEDMAN_MIN_BLOCKS_WARN,
    HALLUCINATION_PENALTY_PCT,
    HALLUCINATION_PENALTY_GRID,
    CEILING_EFFECT_SHARE_PCT,
    CEILING_EFFECT_THRESHOLD_PCT,
)


def sample_stdev(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mean_val = sum(vals) / n
    variance = sum((x - mean_val) ** 2 for x in vals) / (n - 1)
    return round(math.sqrt(variance), 2)


def quantile_linear(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo]) + frac * (float(sorted_vals[hi]) - float(sorted_vals[lo]))


def bootstrap_mean_ci(
    vals: List[float],
    confidence: float = 0.95,
    n_boot: int = 2000,
    seed: int = 42,
) -> Tuple[float, float]:
    if not vals:
        return (0.0, 0.0)
    n = len(vals)
    if n == 1:
        v = round(float(vals[0]), 2)
        return (v, v)
    if min(vals) == max(vals):
        v = round(float(vals[0]), 2)
        return (v, v)

    rng = random.Random(seed)
    boot_means: List[float] = []
    for _ in range(n_boot):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()

    alpha = (1.0 - confidence) / 2.0
    ci_low = round(quantile_linear(boot_means, alpha), 2)
    ci_high = round(quantile_linear(boot_means, 1.0 - alpha), 2)
    return (ci_low, ci_high)


def describe_sample(vals: List[float], n_boot: int = 2000) -> Dict[str, Any]:
    clean = [float(x) for x in vals if x is not None]
    n = len(clean)
    if n == 0:
        return {
            "n": 0,
            "mean": 0.0,
            "sd": 0.0,
            "median": 0.0,
            "iqr": 0.0,
            "q25": 0.0,
            "q75": 0.0,
            "p95": 0.0,
            "min": 0.0,
            "max": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "insufficient_n": True,
        }

    s = sorted(clean)
    mean_v = round(sum(s) / n, 2)
    sd_v = sample_stdev(s)
    med_v = round(quantile_linear(s, 0.50), 2)
    q25 = round(quantile_linear(s, 0.25), 2)
    q75 = round(quantile_linear(s, 0.75), 2)
    iqr_v = round(q75 - q25, 2)
    p95_v = round(quantile_linear(s, 0.95), 2)
    ci_low, ci_high = bootstrap_mean_ci(s, n_boot=n_boot if n >= 2 else 100)

    return {
        "n": n,
        "mean": mean_v,
        "sd": sd_v,
        "median": med_v,
        "iqr": iqr_v,
        "q25": q25,
        "q75": q75,
        "p95": p95_v,
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "insufficient_n": n < WILCOXON_MIN_PAIRS,
    }


def aggregate_repeats_by_query(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Усереднює повтори (repeat_index) всередині кожної комірки (query_code, model_id, prompt_code, context_mode),
    щоб одиницею статистичного аналізу був запит, а не окремий повтор.
    """
    if not rows:
        return []

    grouped: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    order_keys: List[Tuple[str, str, str, str]] = []
    for r in rows:
        key = (
            str(r.get("query_code") or ""),
            str(r.get("model_id") or ""),
            str(r.get("prompt_code") or ""),
            str(r.get("context_mode") or "gold_context"),
        )
        if key not in grouped:
            grouped[key] = []
            order_keys.append(key)
        grouped[key].append(r)

    float_keys = (
        "integral_efficiency_pct",
        "fact_coverage_pct",
        "semantic_similarity_pct",
        "format_compliance_pct",
        "nist_composite_pct",
        "rubric_composite_pct",
        "relevance_score",
        "factuality_score",
        "completeness_score",
        "clarity_score",
        "format_score",
        "policy_adherence_score",
        "confidence_score",
        "latency_ms",
        "call_ms",
        "wall_ms",
        "cost_usd",
        "price_list_cost_usd",
        "rouge_l_f1_pct",
        "stem_cosine_pct",
    )
    int_keys = ("input_tokens", "output_tokens", "thinking_tokens", "hallucination_count")
    bool_keys = (
        "requires_escalation",
        "expected_escalation",
        "escalation_correct",
        "automation_success",
        "has_hallucination",
        "contract_ok",
        "truncated",
        "tokens_estimated",
        "rubric_evaluated",
        "manual_scored",
        "self_judged",
    )

    aggregated: List[Dict[str, Any]] = []
    for key in order_keys:
        cell = grouped[key]
        if len(cell) == 1:
            item = dict(cell[0])
            item["repeats_aggregated"] = 1
            aggregated.append(item)
            continue

        base = dict(cell[0])
        k_len = len(cell)
        base["repeats_aggregated"] = k_len
        base["repeat_index"] = 1

        for fk in float_keys:
            vals = [float(x[fk]) for x in cell if x.get(fk) is not None]
            if vals:
                base[fk] = round(sum(vals) / len(vals), 4 if "cost" in fk else 2)

        for ik in int_keys:
            vals = [int(x.get(ik) or 0) for x in cell]
            base[ik] = int(round(sum(vals) / k_len))

        for bk in bool_keys:
            vals = [1 if x.get(bk) else 0 for x in cell if x.get(bk) is not None]
            if vals:
                base[bk] = 1 if (sum(vals) / len(vals)) >= 0.5 else 0

        for rk in ("retrieval_hit_at_1", "retrieval_hit_at_3"):
            r_vals = [float(x[rk]) for x in cell if x.get(rk) is not None]
            if r_vals:
                base[rk] = round(sum(r_vals) / len(r_vals), 2)

        aggregated.append(base)

    return aggregated


def cliffs_delta_paired(x: List[float], y: List[float]) -> Dict[str, Any]:
    if not x or not y or len(x) != len(y):
        return {"delta": 0.0, "magnitude_uk": "немає даних"}
    n = len(x)
    pos = sum(1 for xi, yi in zip(x, y) if xi > yi)
    neg = sum(1 for xi, yi in zip(x, y) if xi < yi)
    delta = round((pos - neg) / n, 3)
    abs_d = abs(delta)
    if abs_d < 0.147:
        mag = "нехтуваний"
    elif abs_d < 0.33:
        mag = "малий"
    elif abs_d < 0.474:
        mag = "середній"
    else:
        mag = "великий"
    return {"delta": delta, "magnitude_uk": mag}


def wilcoxon_paired_test(x: List[float], y: List[float]) -> Dict[str, Any]:
    n = min(len(x), len(y))
    if n == 0:
        return {
            "n_paired": 0,
            "statistic": None,
            "p_value": None,
            "mean_diff": 0.0,
            "median_diff": 0.0,
            "diff_ci95_low": 0.0,
            "diff_ci95_high": 0.0,
            "cliffs_delta": 0.0,
            "effect_magnitude_uk": "недостатньо даних для критерію",
            "status_uk": "недостатньо даних для критерію",
        }

    diffs = [round(float(xi) - float(yi), 4) for xi, yi in zip(x[:n], y[:n])]
    mean_diff = round(sum(diffs) / n, 2)
    med_diff = round(quantile_linear(sorted(diffs), 0.50), 2)
    ci_low, ci_high = bootstrap_mean_ci(diffs, n_boot=1500)
    eff = cliffs_delta_paired(x[:n], y[:n])

    if n < WILCOXON_MIN_PAIRS:
        return {
            "n_paired": n,
            "statistic": None,
            "p_value": None,
            "mean_diff": mean_diff,
            "median_diff": med_diff,
            "diff_ci95_low": ci_low,
            "diff_ci95_high": ci_high,
            "cliffs_delta": eff["delta"],
            "effect_magnitude_uk": eff["magnitude_uk"],
            "status_uk": "недостатньо даних для критерію",
        }

    non_zero = [d for d in diffs if abs(d) > 1e-9]
    if not non_zero:
        return {
            "n_paired": n,
            "statistic": 0.0,
            "p_value": 1.0,
            "mean_diff": 0.0,
            "median_diff": 0.0,
            "diff_ci95_low": 0.0,
            "diff_ci95_high": 0.0,
            "cliffs_delta": 0.0,
            "effect_magnitude_uk": "нехтуваний (тотожні значення)",
            "status_uk": "ok",
        }

    try:
        res = sp_stats.wilcoxon(x[:n], y[:n], zero_method="wilcox", alternative="two-sided")
        stat_val = round(float(res.statistic), 3)
        p_val = round(float(res.pvalue), 5)
    except Exception:
        stat_val = None
        p_val = 1.0

    return {
        "n_paired": n,
        "statistic": stat_val,
        "p_value": p_val,
        "mean_diff": mean_diff,
        "median_diff": med_diff,
        "diff_ci95_low": ci_low,
        "diff_ci95_high": ci_high,
        "cliffs_delta": eff["delta"],
        "effect_magnitude_uk": eff["magnitude_uk"],
        "status_uk": "ok",
    }


def holm_bonferroni_correction(p_values: List[Optional[float]]) -> List[Optional[float]]:
    m_total = sum(1 for p in p_values if p is not None)
    if m_total == 0:
        return [None for _ in p_values]

    indexed = [(idx, p) for idx, p in enumerate(p_values) if p is not None]
    indexed.sort(key=lambda item: float(item[1]))

    adjusted: List[Optional[float]] = [None] * len(p_values)
    running_max = 0.0
    for rank_idx, (orig_idx, p_val) in enumerate(indexed):
        mult = m_total - rank_idx
        adj = min(1.0, float(p_val) * mult)
        running_max = max(running_max, adj)
        adjusted[orig_idx] = round(running_max, 5)
    return adjusted


def friedman_omnibus_test(matrix_by_condition: Dict[str, List[float]]) -> Dict[str, Any]:
    cond_names = list(matrix_by_condition.keys())
    k = len(cond_names)
    if k < 3:
        return {
            "applicable": False,
            "k_conditions": k,
            "n_blocks": len(next(iter(matrix_by_condition.values()), [])),
            "statistic": None,
            "p_value": None,
            "significant_005": False,
            "warning_uk": "",
            "note_uk": "Критерій Фрідмана застосовується при кількості порівнюваних умов k >= 3.",
        }

    n_blocks = min(len(v) for v in matrix_by_condition.values())
    if n_blocks < WILCOXON_MIN_PAIRS:
        return {
            "applicable": False,
            "k_conditions": k,
            "n_blocks": n_blocks,
            "statistic": None,
            "p_value": None,
            "significant_005": False,
            "warning_uk": f"недостатньо даних для критерію (n = {n_blocks} < {WILCOXON_MIN_PAIRS})",
            "note_uk": f"недостатньо даних для критерію (n = {n_blocks} < {WILCOXON_MIN_PAIRS})",
        }

    warn_msg = ""
    if n_blocks < FRIEDMAN_MIN_BLOCKS_WARN:
        warn_msg = (
            f"Увага: кількість спільних запитів-блоків мала (n = {n_blocks} < {FRIEDMAN_MIN_BLOCKS_WARN}); "
            "хі-квадрат апроксимація критерію Фрідмана має обмежену потужність."
        )

    arrays = [matrix_by_condition[c][:n_blocks] for c in cond_names]
    try:
        res = sp_stats.friedmanchisquare(*arrays)
        stat_v = 0.0 if math.isnan(float(res.statistic)) else round(float(res.statistic), 3)
        p_v = 1.0 if math.isnan(float(res.pvalue)) else round(float(res.pvalue), 5)
        return {
            "applicable": True,
            "k_conditions": k,
            "n_blocks": n_blocks,
            "statistic": stat_v,
            "p_value": p_v,
            "significant_005": p_v < 0.05,
            "warning_uk": warn_msg,
            "note_uk": warn_msg or "Непараметричний критерій Фрідмана для зв'язаних вибірок.",
        }
    except Exception:
        return {
            "applicable": True,
            "k_conditions": k,
            "n_blocks": n_blocks,
            "statistic": 0.0,
            "p_value": 1.0,
            "significant_005": False,
            "warning_uk": warn_msg,
            "note_uk": "Усі порівнювані умови мають однакові ранги.",
        }


def compare_paired_conditions(
    rows: List[Dict[str, Any]],
    group_key: str = "model_id",
    metric_key: str = "fact_coverage_pct",
    min_pairs: int = WILCOXON_MIN_PAIRS,
    group_field: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Будує збалансований перетин за запитами (query_code) між умовами group_key.
    Повтори та супутні умови спочатку усереднюються в межах (group_val, query_code),
    щоб одиницею статистичного аналізу був запит.
    """
    if group_field:
        group_key = group_field
    if rows and metric_key not in rows[0] and "integral_efficiency_pct" in rows[0]:
        metric_key = "integral_efficiency_pct"
    unit_rows = aggregate_repeats_by_query(rows)
    by_group_raw: Dict[str, Dict[str, List[float]]] = {}
    for r in unit_rows:
        g_val = str(r.get(group_key, ""))
        q_code = str(r.get("query_code") or "")
        if not g_val or not q_code or r.get(metric_key) is None:
            continue
        by_group_raw.setdefault(g_val, {}).setdefault(q_code, []).append(float(r[metric_key]))

    by_group: Dict[str, Dict[str, float]] = {
        g: {q: sum(vals) / len(vals) for q, vals in q_map.items() if vals}
        for g, q_map in by_group_raw.items()
    }

    groups = sorted(by_group.keys())
    if len(groups) < 2:
        return {
            "group_key": group_key,
            "metric_key": metric_key,
            "n_groups": len(groups),
            "n_paired_units": 0,
            "status": "single_group",
            "message_uk": "Для парного статистичного порівняння потрібно щонайменше 2 умови.",
            "omnibus": None,
            "omnibus_test": None,
            "pairwise": [],
            "pairwise_comparisons": [],
        }

    common_queries = set.intersection(*(set(by_group[g].keys()) for g in groups))
    sorted_queries = sorted(common_queries)
    n_common = len(sorted_queries)

    matrix = {g: [by_group[g][q] for q in sorted_queries] for g in groups}
    omnibus = friedman_omnibus_test(matrix) if len(groups) >= 3 else None

    raw_pairs: List[Dict[str, Any]] = []
    raw_pvals: List[Optional[float]] = []

    for g1, g2 in combinations(groups, 2):
        common_pair_qs = sorted(set(by_group[g1].keys()) & set(by_group[g2].keys()))
        vec1 = [by_group[g1][q] for q in common_pair_qs]
        vec2 = [by_group[g2][q] for q in common_pair_qs]
        w_res = wilcoxon_paired_test(vec1, vec2)
        raw_pvals.append(w_res["p_value"])
        mean1 = round(sum(vec1) / len(vec1), 2) if vec1 else 0.0
        mean2 = round(sum(vec2) / len(vec2), 2) if vec2 else 0.0
        raw_pairs.append({
            "group_a": g1,
            "group_b": g2,
            "condition_a": g1,
            "condition_b": g2,
            "n_paired": w_res["n_paired"],
            "n_pairs": w_res["n_paired"],
            "mean_a": mean1,
            "mean_b": mean2,
            "mean_diff": w_res["mean_diff"],
            "median_diff": w_res["median_diff"],
            "diff_ci95_low": w_res["diff_ci95_low"],
            "diff_ci95_high": w_res["diff_ci95_high"],
            "wilcoxon_stat": w_res["statistic"],
            "p_raw": w_res["p_value"],
            "p_value_raw": w_res["p_value"],
            "cliffs_delta": w_res["cliffs_delta"],
            "effect_magnitude_uk": w_res["effect_magnitude_uk"],
            "effect_size_label_uk": w_res["effect_magnitude_uk"],
            "status": "insufficient_data" if w_res["n_paired"] < min_pairs else "ok",
            "status_label": w_res["status_uk"],
            "status_uk": w_res["status_uk"],
        })

    adj_pvals = holm_bonferroni_correction(raw_pvals)
    for idx, pair_item in enumerate(raw_pairs):
        p_h = adj_pvals[idx] if pair_item["n_paired"] >= min_pairs else None
        pair_item["p_holm"] = p_h
        pair_item["p_value_holm"] = p_h
        pair_item["significant_005"] = (
            bool(p_h is not None and p_h < 0.05) if pair_item["n_paired"] >= min_pairs else False
        )
        pair_item["significant_001"] = (
            bool(p_h is not None and p_h < 0.01) if pair_item["n_paired"] >= min_pairs else False
        )

    if n_common < min_pairs:
        return {
            "group_key": group_key,
            "metric_key": metric_key,
            "n_groups": len(groups),
            "n_paired_units": n_common,
            "status": "insufficient_data",
            "message_uk": (
                f"недостатньо даних для критерію: знайдено лише n = {n_common} спільних запитів "
                f"(для двостороннього тесту Вілкоксона при alpha=0.05 потрібно щонайменше n >= {min_pairs})."
            ),
            "omnibus": omnibus,
            "omnibus_test": omnibus,
            "pairwise": raw_pairs,
            "pairwise_comparisons": raw_pairs,
        }

    return {
        "group_key": group_key,
        "metric_key": metric_key,
        "n_groups": len(groups),
        "n_paired_units": n_common,
        "status": "ok",
        "message_uk": f"Парне порівняння виконано на збалансованому перетині з n = {n_common} спільних запитів.",
        "omnibus": omnibus,
        "omnibus_test": omnibus,
        "pairwise": raw_pairs,
        "pairwise_comparisons": raw_pairs,
    }


def compute_repeat_stability(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cells: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (str(r.get("model_id")), str(r.get("prompt_code")), str(r.get("query_code")))
        cells.setdefault(key, []).append(r)

    multi_cells = {k: v for k, v in cells.items() if len(v) >= 2}
    if not multi_cells:
        return {
            "available": False,
            "multi_repeat_cells": 0,
            "multi_repeat_cells_count": 0,
            "mean_within_sd_fc": None,
            "mean_within_sd": None,
            "escalation_consistency_pct": None,
            "icc": None,
            "label_uk": "немає даних (repeats = 1)",
        }

    within_sds: List[float] = []
    within_vars: List[float] = []
    cell_means: List[float] = []
    esc_consistent_count = 0

    for items in multi_cells.values():
        fcs = [float(x["fact_coverage_pct"]) for x in items]
        sd_val = sample_stdev(fcs)
        within_sds.append(sd_val)
        within_vars.append(sd_val ** 2)
        cell_means.append(sum(fcs) / len(fcs))
        esc_set = {bool(x.get("requires_escalation", x.get("escalation_Triggered", False))) for x in items}
        if len(esc_set) == 1:
            esc_consistent_count += 1

    mean_within_sd = round(sum(within_sds) / len(within_sds), 2)
    esc_consistency = round((esc_consistent_count / len(multi_cells)) * 100.0, 1)

    ms_within = sum(within_vars) / len(within_vars) if within_vars else 0.0
    between_var = (sample_stdev(cell_means) ** 2) if len(cell_means) >= 2 else 0.0
    icc_val = round(between_var / (between_var + ms_within), 3) if (between_var + ms_within) > 1e-9 else 1.0

    return {
        "available": True,
        "multi_repeat_cells": len(multi_cells),
        "multi_repeat_cells_count": len(multi_cells),
        "mean_within_sd_fc": mean_within_sd,
        "mean_within_sd": mean_within_sd,
        "escalation_consistency_pct": esc_consistency,
        "icc": icc_val,
        "label_uk": f"SD_within = {mean_within_sd}%, узгодженість ескалації = {esc_consistency}%",
    }


def compute_escalation_confusion_matrix(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = fp = fn = tn = 0
    non_esc_total = 0
    non_esc_success = 0

    for r in rows:
        expected = bool(r.get("expected_escalation", False))
        predicted = bool(r.get("requires_escalation", False))
        if expected and predicted:
            tp += 1
        elif (not expected) and predicted:
            fp += 1
        elif expected and (not predicted):
            fn += 1
        else:
            tn += 1

        if not expected:
            non_esc_total += 1
            if r.get("automation_success"):
                non_esc_success += 1

    total = tp + fp + fn + tn
    accuracy = round(((tp + tn) / total) * 100.0, 1) if total > 0 else 0.0
    precision = round((tp / (tp + fp)) * 100.0, 1) if (tp + fp) > 0 else 100.0
    recall = round((tp / (tp + fn)) * 100.0, 1) if (tp + fn) > 0 else 100.0
    f1 = (
        round((2.0 * precision * recall) / (precision + recall), 1)
        if (precision + recall) > 0
        else 0.0
    )
    fn_rate = round((fn / (tp + fn)) * 100.0, 1) if (tp + fn) > 0 else 0.0
    fp_rate = round((fp / (fp + tn)) * 100.0, 1) if (fp + tn) > 0 else 0.0
    auto_non_esc_rate = (
        round((non_esc_success / non_esc_total) * 100.0, 1) if non_esc_total > 0 else 0.0
    )

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total": total,
        "accuracy_pct": accuracy,
        "precision_pct": precision,
        "recall_pct": recall,
        "f1_pct": f1,
        "critical_fn_rate_pct": fn_rate,
        "fn_rate_pct": fn_rate,
        "false_escalation_fp_rate_pct": fp_rate,
        "non_escalation_queries": non_esc_total,
        "automation_success_non_esc_pct": auto_non_esc_rate,
    }


def kendall_tau_b(rank_a: List[float], rank_b: List[float]) -> float:
    if len(rank_a) < 2 or len(rank_a) != len(rank_b):
        return 1.0
    try:
        res = sp_stats.kendalltau(rank_a, rank_b)
        if math.isnan(float(res.statistic)):
            return 1.0
        return round(float(res.statistic), 3)
    except Exception:
        return 1.0


def spearman_rho(x: List[float], y: List[float]) -> Optional[float]:
    if len(x) < 3 or len(x) != len(y):
        return None
    if min(x) == max(x) or min(y) == max(y):
        return None
    try:
        res = sp_stats.spearmanr(x, y)
        val = float(res.statistic if hasattr(res, "statistic") else res[0])
        if math.isnan(val):
            return None
        return round(val, 3)
    except Exception:
        return None


def compute_weight_sensitivity_analysis(by_model: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Перевіряє стійкість ранжування моделей до зміни як вагових коефіцієнтів (w_FC, w_SS),
    так і штрафу за фактичні галюцинації (HALLUCINATION_PENALTY_GRID).
    """
    if len(by_model) < 2:
        return {
            "available": False,
            "grid_size": 0,
            "grid_points_tested": 0,
            "mean_kendall_tau": None,
            "min_kendall_tau": None,
            "top1_stability_pct": None,
            "top1_model_stability_pct": None,
            "message_uk": "Для аналізу чутливості ранжування потрібно щонайменше 2 моделі.",
        }

    def _model_q_score(m: Dict[str, Any], w_fc: float, w_ss: float, pen: float) -> float:
        fc = float(m.get("fact_coverage_pct") or 0.0)
        ss = float(m.get("semantic_similarity_pct") or 0.0)
        hr_frac = float(m.get("hallucination_rate_pct") or 0.0) / 100.0
        return max(0.0, w_fc * fc + w_ss * ss - pen * hr_frac)

    base_scores = [_model_q_score(m, 0.65, 0.35, HALLUCINATION_PENALTY_PCT) for m in by_model]
    base_winner_idx = max(range(len(by_model)), key=lambda idx: base_scores[idx])
    base_winner_id = by_model[base_winner_idx]["model_id"]
    base_winner_name = by_model[base_winner_idx].get("model_name", base_winner_id)

    w_fc_grid = [0.30, 0.40, 0.50, 0.60, 0.65, 0.70, 0.80, 0.90]
    pen_grid = list(HALLUCINATION_PENALTY_GRID)

    taus: List[float] = []
    same_winner = 0
    ranks_by_model: Dict[str, List[int]] = {m["model_id"]: [] for m in by_model}
    grid_total = 0

    for w_fc in w_fc_grid:
        w_ss = round(1.0 - w_fc, 2)
        for pen in pen_grid:
            grid_total += 1
            alt_scores = [_model_q_score(m, w_fc, w_ss, pen) for m in by_model]
            taus.append(kendall_tau_b(base_scores, alt_scores))
            alt_winner_idx = max(range(len(by_model)), key=lambda idx: alt_scores[idx])
            if by_model[alt_winner_idx]["model_id"] == base_winner_id:
                same_winner += 1

            sorted_indices = sorted(range(len(by_model)), key=lambda idx: alt_scores[idx], reverse=True)
            for rank_pos, m_idx in enumerate(sorted_indices, start=1):
                ranks_by_model[by_model[m_idx]["model_id"]].append(rank_pos)

    rank_stability_summary = []
    for m in by_model:
        mid = m["model_id"]
        r_list = ranks_by_model.get(mid) or [1]
        top1_cnt = sum(1 for r in r_list if r == 1)
        rank_stability_summary.append({
            "model_id": mid,
            "model_name": m.get("model_name", mid),
            "min_rank": min(r_list),
            "max_rank": max(r_list),
            "top1_share_pct": round((top1_cnt / len(r_list)) * 100.0, 1),
        })

    top1_stab = round((same_winner / max(1, grid_total)) * 100.0, 1)
    return {
        "available": True,
        "grid_size": grid_total,
        "grid_points_tested": grid_total,
        "weights_grid": w_fc_grid,
        "penalty_grid": pen_grid,
        "mean_kendall_tau": round(sum(taus) / len(taus), 3) if taus else 1.0,
        "min_kendall_tau": round(min(taus), 3) if taus else 1.0,
        "top1_model_id": base_winner_id,
        "top1_model_name": base_winner_name,
        "top1_stability_pct": top1_stab,
        "top1_model_stability_pct": top1_stab,
        "rank_stability": rank_stability_summary,
    }


def compute_metrics_correlation_matrix(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Обчислює матрицю рангових кореляцій Спірмена між ключовими метриками
    (FC, SS, Q_det, rubric, latency, cost) на рівні усереднених запитів.
    """
    unit_rows = aggregate_repeats_by_query(rows)
    metric_defs = [
        ("FC", "FC (Повнота, %)", lambda r: float(r["fact_coverage_pct"]) if r.get("fact_coverage_pct") is not None else None),
        ("SS", "SS (Схожість, %)", lambda r: float(r["semantic_similarity_pct"]) if r.get("semantic_similarity_pct") is not None else None),
        ("Q_det", "Q_det (Композитний, %)", lambda r: float(r["integral_efficiency_pct"]) if r.get("integral_efficiency_pct") is not None else None),
        (
            "rubric",
            "Rubric (Рубрика, %)",
            lambda r: float(r.get("rubric_composite_pct") if r.get("rubric_composite_pct") is not None else r.get("nist_composite_pct"))
            if (r.get("rubric_composite_pct") is not None or r.get("nist_composite_pct") is not None)
            else None,
        ),
        (
            "latency",
            "Latency (Час, мс)",
            lambda r: float(r.get("call_ms") or r.get("latency_ms")) if (r.get("call_ms") or r.get("latency_ms")) is not None else None,
        ),
        (
            "cost",
            "Cost (Прайс, $)",
            lambda r: float(r.get("price_list_cost_usd") if r.get("price_list_cost_usd") is not None else r.get("cost_usd"))
            if not r.get("cost_unknown") and (r.get("price_list_cost_usd") is not None or r.get("cost_usd") is not None)
            else None,
        ),
    ]

    matrix: Dict[str, Dict[str, Any]] = {}
    for k1, _, fn1 in metric_defs:
        matrix[k1] = {}
        for k2, _, fn2 in metric_defs:
            if k1 == k2:
                n_valid = sum(1 for r in unit_rows if fn1(r) is not None)
                matrix[k1][k2] = {"rho": 1.0 if n_valid > 0 else None, "n": n_valid}
                continue
            pairs = [(fn1(r), fn2(r)) for r in unit_rows]
            valid_pairs = [(v1, v2) for v1, v2 in pairs if v1 is not None and v2 is not None]
            xs = [p[0] for p in valid_pairs]
            ys = [p[1] for p in valid_pairs]
            rho_val = spearman_rho(xs, ys)
            matrix[k1][k2] = {"rho": rho_val, "n": len(valid_pairs)}

    fc_ss_rho = matrix.get("FC", {}).get("SS", {}).get("rho")
    fc_q_rho = matrix.get("FC", {}).get("Q_det", {}).get("rho")
    note_uk = (
        f"Матриця рангових кореляцій Спірмена (n = {len(unit_rows)} запитів-спостережень) показує емпіричний зв'язок між метриками "
        f"(зокрема FC vs SS: rho = {fc_ss_rho if fc_ss_rho is not None else 'н/д'}, "
        f"FC vs Q_det: rho = {fc_q_rho if fc_q_rho is not None else 'н/д'})."
    )

    return {
        "n_units": len(unit_rows),
        "metrics": [{"key": k, "label_uk": lbl} for k, lbl, _ in metric_defs],
        "matrix": matrix,
        "note_uk": note_uk,
    }


def compute_rubric_diagnostics(
    rows: List[Dict[str, Any]],
    include_self_judged: bool = False,
) -> Dict[str, Any]:
    """
    Аналізує незалежність судді, однорідність судді в серії, розподіл оцінок рубрики та ефект стелі.
    """
    judged_rows = [r for r in rows if int(r.get("rubric_evaluated") or 0) == 1 and int(r.get("manual_scored") or 0) == 0]
    self_judged_rows = [r for r in judged_rows if int(r.get("self_judged") or 0) == 1]
    skipped_rows = [r for r in rows if str(r.get("judge_status") or "") == "skipped_no_independent_judge"]
    error_json_rows = [r for r in rows if str(r.get("judge_status") or "") == "error_json"]

    eligible_rows = judged_rows if include_self_judged else [r for r in judged_rows if int(r.get("self_judged") or 0) == 0]

    judge_counts: Dict[str, int] = {}
    for r in eligible_rows:
        jm = str(r.get("judge_model") or "").strip()
        if jm:
            judge_counts[jm] = judge_counts.get(jm, 0) + 1

    dominant_judge = max(judge_counts.keys(), key=lambda k: judge_counts[k]) if judge_counts else ""
    multiple_judges = len(judge_counts) > 1
    multi_judge_warning_uk = ""
    if multiple_judges:
        judges_str = ", ".join(f"{jm} (n={cnt})" for jm, cnt in sorted(judge_counts.items(), key=lambda x: -x[1]))
        multi_judge_warning_uk = (
            f"Увага: у вибірці виявлено декілька різних моделей-суддів ({judges_str}). "
            f"Для порівнянності показники рубрики обчислено за основним суддею '{dominant_judge}'."
        )
        eligible_rows = [r for r in eligible_rows if str(r.get("judge_model") or "").strip() == dominant_judge]

    buckets = {
        "1.0-1.8": 0,
        "1.8-2.6": 0,
        "2.6-3.4": 0,
        "3.4-4.2": 0,
        "4.2-5.0": 0,
    }
    top_10pct_count = 0
    scores_pct: List[float] = []

    for r in eligible_rows:
        r_pct = r.get("rubric_composite_pct")
        if r_pct is None:
            r_pct = r.get("nist_composite_pct")
        if r_pct is None:
            continue
        pct_val = float(r_pct)
        scores_pct.append(pct_val)
        score_5 = 1.0 + (pct_val / 100.0) * 4.0
        if score_5 < 1.8:
            buckets["1.0-1.8"] += 1
        elif score_5 < 2.6:
            buckets["1.8-2.6"] += 1
        elif score_5 < 3.4:
            buckets["2.6-3.4"] += 1
        elif score_5 < 4.2:
            buckets["3.4-4.2"] += 1
        else:
            buckets["4.2-5.0"] += 1

        if pct_val >= CEILING_EFFECT_THRESHOLD_PCT:
            top_10pct_count += 1

    n_scored = len(scores_pct)
    top_10pct_share = round((top_10pct_count / n_scored) * 100.0, 1) if n_scored > 0 else 0.0
    ceiling_detected = (n_scored >= 5) and (top_10pct_share > CEILING_EFFECT_SHARE_PCT)
    ceiling_warning_uk = (
        f"Виявлено ефект стелі рубрики: {top_10pct_share}% оцінок судді потрапили у верхні 10% шкали (>= {CEILING_EFFECT_THRESHOLD_PCT}% / >= 4.6 із 5). "
        "Дискримінаційна здатність рубрики на цій вибірці обмежена."
        if ceiling_detected
        else ""
    )

    return {
        "total_judged_count": len(judged_rows),
        "independent_judged_count": len(judged_rows) - len(self_judged_rows),
        "self_judged_count": len(self_judged_rows),
        "skipped_no_independent_judge_count": len(skipped_rows),
        "error_json_count": len(error_json_rows),
        "include_self_judged": include_self_judged,
        "dominant_judge_model": dominant_judge,
        "judges_used": judge_counts,
        "multiple_judges_detected": multiple_judges,
        "multiple_judges_warning_uk": multi_judge_warning_uk,
        "eligible_scored_count": n_scored,
        "distribution_buckets": buckets,
        "top_10pct_share_pct": top_10pct_share,
        "ceiling_effect_detected": ceiling_detected,
        "ceiling_warning_uk": ceiling_warning_uk,
        "mean_rubric_pct": round(sum(scores_pct) / n_scored, 1) if n_scored > 0 else None,
    }


def weighted_cohens_kappa(
    ratings_a: List[int],
    ratings_b: List[int],
    min_rating: int = 1,
    max_rating: int = 5,
) -> Optional[float]:
    """
    Обчислює квадратичну зважену каппу Коена (quadratic weighted Cohen's kappa)
    для порядкової шкали min_rating..max_rating.
    """
    if len(ratings_a) < 2 or len(ratings_a) != len(ratings_b):
        return None
    n = len(ratings_a)
    categories = list(range(min_rating, max_rating + 1))
    k = len(categories)
    if k < 2:
        return None

    obs = [[0.0 for _ in range(k)] for _ in range(k)]
    hist_a = [0.0 for _ in range(k)]
    hist_b = [0.0 for _ in range(k)]

    for ra, rb in zip(ratings_a, ratings_b):
        ia = max(0, min(k - 1, int(round(ra)) - min_rating))
        ib = max(0, min(k - 1, int(round(rb)) - min_rating))
        obs[ia][ib] += 1.0
        hist_a[ia] += 1.0
        hist_b[ib] += 1.0

    num_obs = 0.0
    num_exp = 0.0
    denom = float((k - 1) ** 2)
    for i in range(k):
        for j in range(k):
            w_ij = ((i - j) ** 2) / denom
            p_obs = obs[i][j] / n
            p_exp = (hist_a[i] * hist_b[j]) / (n * n)
            num_obs += w_ij * p_obs
            num_exp += w_ij * p_exp

    if num_exp <= 1e-12:
        return 1.0 if num_obs <= 1e-12 else 0.0
    return round(1.0 - (num_obs / num_exp), 3)


def compute_judge_human_validation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Розраховує узгодженість між LLM-суддею та ручною розміткою експерта:
    - зважена каппа Коена (quadratic weighted kappa) та рангова кореляція Спірмена за 5 критеріями;
    - кореляція Спірмена між автоматичною повнотою фактів FC та ручною оцінкою повноти (completeness).
    """
    criteria_keys = ["relevance", "factuality", "completeness", "clarity", "policy_adherence"]
    criteria_labels_uk = {
        "relevance": "Релевантність",
        "factuality": "Фактична точність",
        "completeness": "Повнота охоплення",
        "clarity": "Зрозумілість і тон",
        "policy_adherence": "Дотримання політики",
    }

    paired_judge_human: Dict[str, Tuple[List[int], List[int]]] = {k: ([], []) for k in criteria_keys}
    judge_means: List[float] = []
    human_means: List[float] = []
    fc_auto_vals: List[float] = []
    human_comp_vals: List[float] = []
    manual_count = 0

    for r in rows:
        has_manual = int(r.get("manual_scored") or 0) == 1
        human_scores: Dict[str, Any] = {}
        raw_human_json = r.get("human_scores_json")
        if raw_human_json and raw_human_json != "{}":
            try:
                human_scores = json.loads(raw_human_json)
            except Exception:
                human_scores = {}

        if not human_scores and has_manual:
            human_scores = {
                "relevance": r.get("relevance_score"),
                "factuality": r.get("factuality_score"),
                "completeness": r.get("completeness_score"),
                "clarity": r.get("clarity_score"),
                "policy_adherence": r.get("policy_adherence_score") or r.get("format_score"),
            }

        if not human_scores or human_scores.get("completeness") is None:
            continue

        manual_count += 1
        if r.get("fact_coverage_pct") is not None:
            fc_auto_vals.append(float(r["fact_coverage_pct"]))
            human_comp_vals.append(float(human_scores["completeness"]))

        judge_scores: Dict[str, Any] = {}
        if r.get("judge_scores_json") and r.get("judge_scores_json") != "{}":
            try:
                judge_scores = json.loads(r["judge_scores_json"])
            except Exception:
                judge_scores = {}

        if judge_scores:
            j_vals_row = []
            h_vals_row = []
            for ck in criteria_keys:
                jv = judge_scores.get(ck)
                hv = human_scores.get(ck)
                if jv is not None and hv is not None:
                    paired_judge_human[ck][0].append(int(round(float(jv))))
                    paired_judge_human[ck][1].append(int(round(float(hv))))
                    j_vals_row.append(float(jv))
                    h_vals_row.append(float(hv))
            if len(j_vals_row) == 5 and len(h_vals_row) == 5:
                judge_means.append(sum(j_vals_row) / 5.0)
                human_means.append(sum(h_vals_row) / 5.0)

    fc_vs_human_rho = spearman_rho(fc_auto_vals, human_comp_vals)
    by_criterion: List[Dict[str, Any]] = []
    kappas: List[float] = []
    rhos: List[float] = []

    for ck in criteria_keys:
        j_list, h_list = paired_judge_human[ck]
        kw = weighted_cohens_kappa(j_list, h_list) if len(j_list) >= 3 else None
        rho = spearman_rho([float(x) for x in j_list], [float(x) for x in h_list]) if len(j_list) >= 3 else None
        mae = round(sum(abs(a - b) for a, b in zip(j_list, h_list)) / len(j_list), 2) if j_list else None
        if kw is not None:
            kappas.append(kw)
        if rho is not None:
            rhos.append(rho)
        by_criterion.append({
            "criterion": ck,
            "label_uk": criteria_labels_uk[ck],
            "n_paired": len(j_list),
            "weighted_kappa": kw,
            "spearman_rho": rho,
            "mae": mae,
        })

    n_both = len(judge_means)
    overall_rho = spearman_rho(judge_means, human_means) if n_both >= 3 else None
    mean_kappa = round(sum(kappas) / len(kappas), 3) if kappas else None
    mean_mae = round(sum(abs(a - b) for a, b in zip(judge_means, human_means)) / n_both, 2) if n_both > 0 else None

    if manual_count < 5:
        trust_level = "insufficient_data"
        interpretation_uk = (
            f"У серії розмічено людиною {manual_count} відповідей (потрібно щонайменше 5 для оцінки узгодженості). "
            "Вивантажте стратифіковану вибірку у CSV, заповніть оцінки 1..5 та імпортуйте файл назад."
        )
    elif n_both < 5:
        trust_level = "partial_fc_only"
        interpretation_uk = (
            f"Розмічено людиною {manual_count} відповідей (кореляція автоматичного FC із ручною повнотою: "
            f"Spearman rho = {fc_vs_human_rho if fc_vs_human_rho is not None else 'н/д'}). "
            f"Для перевірки LLM-судді потрібні також оцінки судді на цих самих записах (зараз спільних пар: {n_both})."
        )
    else:
        k_val = mean_kappa if mean_kappa is not None else 0.0
        r_val = overall_rho if overall_rho is not None else 0.0
        if k_val >= 0.61 and r_val >= 0.65:
            trust_level = "high"
            verdict = "Узгодженість висока (суттєва за шкалою Landis-Koch): оцінкам LLM-судді можна довіряти як проксі експертної рубрики."
        elif k_val >= 0.41:
            trust_level = "moderate"
            verdict = "Узгодженість помірна: LLM-суддю доцільно використовувати лише як допоміжний індикатор поряд із детермінованими метриками."
        else:
            trust_level = "low"
            verdict = "Узгодженість низька: оцінки LLM-судді суттєво розходяться з людиною, висновки мають спиратися на детерміновані метрики та ручну перевірку."
        interpretation_uk = (
            f"На вибірці з n = {n_both} відповідей (суддя vs людина): середня зважена каппа Коена kappa_w = {mean_kappa}, "
            f"рангова кореляція Спірмена rho = {overall_rho}, середня похибка MAE = {mean_mae} бала. "
            f"Кореляція автоматичного FC із ручною повнотою: rho = {fc_vs_human_rho if fc_vs_human_rho is not None else 'н/д'}. "
            f"{verdict}"
        )

    return {
        "available": manual_count >= 5,
        "manual_scored_count": manual_count,
        "paired_judge_human_count": n_both,
        "mean_weighted_kappa": mean_kappa,
        "overall_spearman_rho": overall_rho,
        "overall_mae": mean_mae,
        "fc_vs_human_completeness_rho": fc_vs_human_rho,
        "fc_vs_human_n": len(fc_auto_vals),
        "by_criterion": by_criterion,
        "trust_level": trust_level,
        "interpretation_uk": interpretation_uk,
    }


def compute_pareto_fronts(by_model: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Визначає Парето-оптимальні моделі у двох просторах:
    1) Якість (Q_det) vs Середня затримка (latency_mean_ms);
    2) Якість (Q_det) vs Прайс-еквівалентна вартість 1000 запитів (price_list_cost_per_1k_usd).
    Моделі без заданої ціни виключаються з рейтингу за вартістю.
    """
    if not by_model:
        return {
            "quality_vs_latency": [],
            "quality_vs_cost": [],
            "pareto_quality_latency_models": [],
            "pareto_quality_cost_models": [],
        }

    ql_front: List[Dict[str, Any]] = []
    qc_front: List[Dict[str, Any]] = []
    ql_ids: List[str] = []
    qc_ids: List[str] = []

    cost_eligible = [
        m for m in by_model
        if m.get("price_known", True) and not m.get("cost_unknown", False)
    ]

    for m in by_model:
        m_id = m["model_id"]
        q_m = float(m["integral_efficiency_pct"])
        l_m = float(m["latency_mean_ms"])

        dominated_ql = False
        for other in by_model:
            if other["model_id"] == m_id:
                continue
            q_o = float(other["integral_efficiency_pct"])
            l_o = float(other["latency_mean_ms"])
            if (q_o >= q_m and l_o <= l_m) and (q_o > q_m or l_o < l_m):
                dominated_ql = True
                break

        ql_front.append({
            "model_id": m_id,
            "model_name": m.get("model_name", m_id),
            "quality_pct": q_m,
            "fact_coverage_pct": float(m["fact_coverage_pct"]),
            "latency_mean_ms": l_m,
            "pareto_optimal": not dominated_ql,
        })
        if not dominated_ql:
            ql_ids.append(m_id)

        price_known = bool(m.get("price_known", True)) and not bool(m.get("cost_unknown", False))
        if not price_known:
            qc_front.append({
                "model_id": m_id,
                "model_name": m.get("model_name", m_id),
                "quality_pct": q_m,
                "fact_coverage_pct": float(m["fact_coverage_pct"]),
                "cost_per_1k_usd": None,
                "price_known": False,
                "cost_label_uk": "ціна не задана",
                "pareto_optimal": False,
            })
            continue

        c_m = float(m.get("price_list_cost_per_1k_usd", m.get("cost_per_1k_usd", 0.0)))
        dominated_qc = False
        for other in cost_eligible:
            if other["model_id"] == m_id:
                continue
            q_o = float(other["integral_efficiency_pct"])
            c_o = float(other.get("price_list_cost_per_1k_usd", other.get("cost_per_1k_usd", 0.0)))
            if (q_o >= q_m and c_o <= c_m) and (q_o > q_m or c_o < c_m):
                dominated_qc = True
                break

        qc_front.append({
            "model_id": m_id,
            "model_name": m.get("model_name", m_id),
            "quality_pct": q_m,
            "fact_coverage_pct": float(m["fact_coverage_pct"]),
            "cost_per_1k_usd": c_m,
            "price_known": True,
            "is_free_tier": bool(m.get("is_free_tier", False)),
            "pareto_optimal": not dominated_qc,
        })
        if not dominated_qc:
            qc_ids.append(m_id)

    return {
        "quality_vs_latency": ql_front,
        "quality_vs_cost": qc_front,
        "pareto_quality_latency_models": ql_ids,
        "pareto_quality_cost_models": qc_ids,
    }
