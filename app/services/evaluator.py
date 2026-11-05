"""
Модуль багатовимірного оцінювання якості та ефективності відповідей LLM (Evaluation Engine).
Реалізує:
1. Розбір та валідацію єдиного JSON-контракту відповіді (ContractOutput);
2. Покриття обов'язкових фактів (Fact Coverage FC, %) з підтримкою синонімічних груп і нормалізації чисел;
3. Детектування фактичних галюцинацій (Hallucination Detector) відносно context_fact;
4. Лексико-семантичну близькість до еталону (SS, %: комбінація TF Cosine та ROUGE-L F1 на основах слів);
5. Універсальну перевірку надійності формату (Format Reliability) однакову для всіх промптів P1..P5;
6. Композитний індекс детермінованої якості Q_det на основі FC та SS із налаштовуваними вагами з app/config.py.
"""

from collections import Counter
import json
import math
import re
from typing import Dict, Any, List, Tuple, Optional, Union
from pydantic import BaseModel, Field

from app.config import (
    load_runtime_settings,
    DEFAULT_EVALUATION_WEIGHTS,
    AUTOMATION_FC_THRESHOLD_PCT,
    HALLUCINATION_PENALTY_PCT,
    FORMAT_MIN_CHARS,
    FORMAT_MAX_CHARS,
)
from app.services.classifier import normalize_numbers, tokenize_uk, stem_uk_token


class ContractOutput(BaseModel):
    answer: str = Field(..., min_length=1)
    escalate: bool
    reason: str = Field(default="")


def _extract_json_candidates(raw_text: str) -> List[str]:
    text = (raw_text or "").strip()
    if not text:
        return []
    candidates: List[str] = []

    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    for block in fenced:
        candidates.append(block.strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1].strip())

    return candidates


def parse_contract_output(raw_text: str, is_retry: bool = False) -> Dict[str, Any]:
    """
    Витягує та валідує JSON-контракт {"answer": str, "escalate": bool, "reason": str}.
    Якщо відповідь не є валідним JSON за схемою ContractOutput, повертає contract_ok=False
    та використовує резервний regex-парсер ескалації.
    """
    text = (raw_text or "").strip()
    candidates = _extract_json_candidates(text)

    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict) and "answer" in data and "escalate" in data:
                if isinstance(data["escalate"], str):
                    esc_low = data["escalate"].strip().lower()
                    if esc_low in ("true", "так", "yes", "1"):
                        data["escalate"] = True
                    elif esc_low in ("false", "ні", "no", "0"):
                        data["escalate"] = False
                validated = ContractOutput.model_validate(data)
                return {
                    "contract_ok": True,
                    "parse_method": "retry_json" if is_retry else "json",
                    "answer": validated.answer.strip(),
                    "escalate": bool(validated.escalate),
                    "reason": validated.reason.strip(),
                }
        except Exception:
            continue

    fallback_esc = detect_escalation_from_answer(text)
    return {
        "contract_ok": False,
        "parse_method": "fallback_regex",
        "answer": text,
        "escalate": fallback_esc,
        "reason": "",
    }


def _match_single_variant(text_norm: str, text_tokens: List[str], variant: str) -> bool:
    v_norm = normalize_numbers(variant.strip().lower())
    if not v_norm:
        return True
    if v_norm in text_norm:
        return True

    v_parts = tokenize_uk(v_norm)
    if not v_parts:
        return False

    for part in v_parts:
        if part[0].isdigit():
            if "-" in part:
                sub_nums = [x for x in part.split("-") if x]
                if not all(sn in text_tokens or sn in text_norm for sn in sub_nums):
                    return False
            elif part not in text_tokens and part not in text_norm:
                return False
        else:
            stem = stem_uk_token(part)
            if not any(t.startswith(stem) or stem in t for t in text_tokens):
                return False
    return True


def compute_fact_coverage(
    generated_answer: str,
    required_keywords: List[Union[str, Dict[str, Any]]],
) -> Tuple[float, List[str], List[str]]:
    if not required_keywords:
        return 100.0, [], []

    ans_norm = normalize_numbers(generated_answer.lower())
    ans_tokens = tokenize_uk(generated_answer)

    matched: List[str] = []
    missing: List[str] = []

    for entry in required_keywords:
        if isinstance(entry, dict):
            label = str(entry.get("id") or (entry.get("any_of") or [""])[0])
            variants = [str(x) for x in (entry.get("any_of") or []) if str(x).strip()]
        else:
            label = str(entry).strip()
            variants = [label] if label else []

        if not variants:
            continue

        if any(_match_single_variant(ans_norm, ans_tokens, v) for v in variants):
            matched.append(label)
        else:
            missing.append(label)

    total = len(matched) + len(missing)
    coverage_pct = round((len(matched) / total) * 100.0, 1) if total > 0 else 100.0
    return coverage_pct, matched, missing


def detect_hallucinations(
    generated_answer: str,
    context_fact: str,
    user_query: str = "",
) -> Dict[str, Any]:
    if not generated_answer or not context_fact:
        return {"hallucinated_items": [], "hallucination_count": 0, "has_hallucination": False}

    cleaned_ans = re.sub(r"(?m)^\s*\d+[.)]\s+", " ", generated_answer)
    cleaned_ans = re.sub(r"\bкрок\s*\d+\b", " ", cleaned_ans, flags=re.IGNORECASE)
    cleaned_ans = re.sub(r"\bприкла[д]\s*\d+\b", " ", cleaned_ans, flags=re.IGNORECASE)
    cleaned_ans = re.sub(r"\b24/7\b", " ", cleaned_ans)

    ans_norm = normalize_numbers(cleaned_ans.lower())
    ctx_norm = normalize_numbers((context_fact + " " + user_query).lower())
    ctx_tokens = set(tokenize_uk(context_fact + " " + user_query))

    expanded_ctx_nums = set()
    for tok in ctx_tokens:
        if tok[0].isdigit():
            expanded_ctx_nums.add(tok)
            expanded_ctx_nums.add(tok.rstrip("%"))
            for part in tok.rstrip("%").split("-"):
                if part:
                    expanded_ctx_nums.add(part)

    hallucinated: List[str] = []

    num_matches = re.findall(r"\b\d+(?:\.\d+)?%?(?!\s*[.)])", ans_norm)
    for num_tok in num_matches:
        base_num = num_tok.rstrip("%")
        if num_tok in ("1", "2") and "%" not in num_tok:
            continue
        if (
            num_tok not in expanded_ctx_nums
            and base_num not in expanded_ctx_nums
            and num_tok not in ctx_norm
        ):
            item_label = f"число/тариф '{num_tok}'"
            if item_label not in hallucinated:
                hallucinated.append(item_label)

    quoted_spans = re.findall(r"«([^»]{3,45})»", generated_answer)
    allowed_prompt_meta = {
        "коротка відповідь", "деталі та порядок дій", "статус обслуговування",
        "покупка частинами", "гнучкий", "банка", "скарбничка",
    }
    for span in quoted_spans:
        span_low = span.strip().lower()
        if span_low in allowed_prompt_meta:
            continue
        span_tokens = [t for t in tokenize_uk(span_low) if len(t) >= 3]
        if not span_tokens:
            continue
        matched_roots = sum(
            1 for st in span_tokens
            if any(ct.startswith(stem_uk_token(st)) or stem_uk_token(st) in ct for ct in ctx_tokens)
        )
        if matched_roots == 0:
            item_label = f"розділ/термін «{span.strip()}»"
            if item_label not in hallucinated:
                hallucinated.append(item_label)

    return {
        "hallucinated_items": hallucinated,
        "hallucination_count": len(hallucinated),
        "has_hallucination": len(hallucinated) > 0,
    }


def lcs_length(seq_a: List[str], seq_b: List[str]) -> int:
    m, n = len(seq_a), len(seq_b)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        stem_a = stem_uk_token(seq_a[i - 1])
        for j in range(1, n + 1):
            stem_b = stem_uk_token(seq_b[j - 1])
            if stem_a == stem_b:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]


def compute_lexical_similarity_metrics(generated_answer: str, reference_answer: str) -> Dict[str, Any]:
    gen_tokens = tokenize_uk(generated_answer)
    ref_tokens = tokenize_uk(reference_answer)
    if not gen_tokens or not ref_tokens:
        return {
            "semantic_similarity_pct": 0.0,
            "stem_cosine_pct": 0.0,
            "rouge_l_f1_pct": 0.0,
            "embedding_available": False,
        }

    gen_stems = [stem_uk_token(t) for t in gen_tokens]
    ref_stems = [stem_uk_token(t) for t in ref_tokens]

    vec_a = Counter(gen_stems)
    vec_b = Counter(ref_stems)
    common_keys = set(vec_a.keys()).intersection(set(vec_b.keys()))
    dot_product = sum(vec_a[k] * vec_b[k] for k in common_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    cosine_sim = (dot_product / (norm_a * norm_b)) if (norm_a > 0 and norm_b > 0) else 0.0

    lcs = lcs_length(gen_stems[:160], ref_stems[:160])
    prec = lcs / max(1, len(gen_stems[:160]))
    rec = lcs / max(1, len(ref_stems[:160]))
    rouge_l_f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

    stem_cosine_pct = round(cosine_sim * 100.0, 1)
    rouge_l_f1_pct = round(rouge_l_f1 * 100.0, 1)
    combined_pct = round((0.55 * cosine_sim + 0.45 * rouge_l_f1) * 100.0, 1)

    return {
        "semantic_similarity_pct": min(100.0, max(0.0, combined_pct)),
        "stem_cosine_pct": min(100.0, max(0.0, stem_cosine_pct)),
        "rouge_l_f1_pct": min(100.0, max(0.0, rouge_l_f1_pct)),
        "embedding_available": False,
    }


def evaluate_format_checklist(
    generated_answer: str,
    prompt_code: str = "P1",
    contract_ok: bool = True,
) -> Tuple[float, Dict[str, bool]]:
    """
    Перевіряє універсальні інженерні інваріанти надійності формату (Format Reliability),
    однакові для всіх стратегій промптів P1..P5:
    1) валідність JSON-контракту (contract_ok);
    2) українська мова відповіді (ukrainian_language);
    3) адекватна довжина тексту для клієнта (length_in_bounds: FORMAT_MIN_CHARS..FORMAT_MAX_CHARS);
    4) відсутність витоку службових тегів у текст клієнта (no_service_tag_leak).
    Цей показник фіксується окремо і не входить до композитного індексу змістовної якості Q_det.
    """
    text = (generated_answer or "").strip()
    if not text:
        return 0.0, {
            "contract_ok": False,
            "ukrainian_language": False,
            "length_in_bounds": False,
            "no_service_tag_leak": False,
        }

    has_uk_chars = bool(re.search(r"[іїєґІЇЄҐа-яА-Я]", text))
    length_ok = FORMAT_MIN_CHARS <= len(text) <= FORMAT_MAX_CHARS
    has_service_leak = bool(
        re.search(r"\[SYSTEM|\bUSER PROMPT\b|\"escalate\"\s*:|\"answer\"\s*:", text, flags=re.IGNORECASE)
    )

    checks = {
        "contract_ok": bool(contract_ok),
        "ukrainian_language": has_uk_chars,
        "length_in_bounds": length_ok,
        "no_service_tag_leak": not has_service_leak,
    }
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    pct = round((passed / total) * 100.0, 1) if total > 0 else 0.0
    return pct, checks


def detect_escalation_from_answer(generated_answer: str) -> bool:
    """
    Резервний парсер рішення щодо ескалації з тексту відповіді (використовується при fallback_regex
    або для аналізу історичних відповідей без JSON-контракту).
    """
    if not generated_answer:
        return False

    clean = re.sub(r"[*_`#~>]", "", generated_answer.lower())
    clean = re.sub(r"\s+", " ", clean).strip()

    explicit_matches = list(
        re.finditer(
            r"(?:ескалація|маршрутизація|рішення\s+шлюзу(?:\s+ескалації)?)\s*[:\-—=]+\s*(так|ні|yes|no|потрібна|не\s+потрібна)\b",
            clean,
        )
    )
    if explicit_matches:
        last_val = explicit_matches[-1].group(1).strip()
        if last_val in ("ні", "no") or "не потрібна" in last_val:
            return False
        if last_val in ("так", "yes", "потрібна"):
            return True

    negative_resolution_patterns = [
        r"вирішується\s+автоматично",
        r"опрацьовується\s+автоматично",
        r"без\s+залучення\s+(?:оператора|спеціаліста|фахівця)",
        r"без\s+передачі\s+(?:оператору|спеціалісту|фахівцю)",
        r"ескалація\s+не\s+(?:потрібна|вимагається|передбачена)",
        r"тікет(?:\s+ескалації)?\s+не\s+(?:створено|потрібен|створюється)",
        r"передача\s+(?:запиту\s+)?(?:оператору|спеціалісту)\s+не\s+потрібна",
    ]
    positive_escalation_patterns = [
        r"(?:створено|сформовано|відкрито|зареєстровано)\s+(?:тікет|заявку)(?:\s+ескалації)?\s+(?:на|для)\s+(?:профільного\s+)?(?:оператор|спеціаліст|фахівц|інспектор|офіцер|юрист)",
        r"створено\s+тікет\s+ескалації",
        r"(?:запит|звернення|кейс|питання)\s+(?:автоматично\s+|позачергово\s+|обов'язково\s+)?(?:передається|передано|ескалюється|ескальовано|скеровано|направлено)\s+(?:на\s+|до\s+)?(?:профільному\s+|профільного\s+)?(?:оператор|спеціаліст|фахівц|інспектор|офіцер|юрист)",
        r"(?:передаю|передаємо|передали|передав|передала|скеровую|скеровуємо|ескалюємо)\s+(?:ваш\s+|цей\s+)?(?:запит|звернення|кейс)\s+(?:на\s+|до\s+)?(?:профільному\s+|профільного\s+)?(?:оператор|спеціаліст|фахівц|інспектор|офіцер|юрист)",
        r"потребує\s+(?:обов'язкової\s+)?(?:ескалації|залучення|втручання)\s+(?:на\s+)?(?:профільного\s+)?(?:оператор|спеціаліст|фахівц|інспектор|офіцер|юрист)",
        r"підлягає\s+(?:обов'язковій\s+)?ескалації",
    ]

    for pat in positive_escalation_patterns:
        for m in re.finditer(pat, clean):
            prefix = clean[max(0, m.start() - 25) : m.start()]
            if re.search(r"\b(?:не|без|ні)\s+$", prefix) or re.search(r"\b(?:не|без)\s+\w+\s+$", prefix):
                continue
            return True

    if any(re.search(pat, clean) for pat in negative_resolution_patterns):
        return False

    return False


def compute_composite_quality_index(
    fact_coverage_pct: float,
    semantic_similarity_pct: float,
    format_compliance_pct: float = 100.0,
    weights: Optional[Dict[str, float]] = None,
    has_hallucination: bool = False,
) -> float:
    """
    Обчислює композитний індекс змістовної якості відповіді Q_det (0..100%)
    на основі повноти обов'язкових фактів (FC) та лексико-семантичної близькості до еталону (SS)
    із вагами, заданими в app/config.py (DEFAULT_EVALUATION_WEIGHTS).
    За наявності фактичної галюцинації застосовується штраф HALLUCINATION_PENALTY_PCT.
    """
    w = weights or DEFAULT_EVALUATION_WEIGHTS
    w_fc = float(w.get("fact_coverage", DEFAULT_EVALUATION_WEIGHTS["fact_coverage"]))
    w_ss = float(w.get("semantic_similarity", DEFAULT_EVALUATION_WEIGHTS["semantic_similarity"]))
    w_sum = max(0.001, w_fc + w_ss)
    base_q = (w_fc * fact_coverage_pct + w_ss * semantic_similarity_pct) / w_sum
    if has_hallucination:
        base_q = max(0.0, base_q - HALLUCINATION_PENALTY_PCT)
    return round(min(100.0, max(0.0, base_q)), 1)


def evaluate_response_comprehensive(
    generated_answer: str,
    reference_answer: str,
    required_keywords: List[Union[str, Dict[str, Any]]],
    expected_escalation: bool,
    prompt_code: str,
    latency_ms: float,
    context_fact: str = "",
    user_query: str = "",
    judge_result: Optional[Dict[str, Any]] = None,
    parsed_contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Виконує повний розрахунок детермінованих метрик якості, галюцинацій, шлюзу ескалації,
    надійності JSON-контракту та (за наявності) незалежної оцінки за рубрикою.
    """
    settings = load_runtime_settings()
    weights = settings.get("weights", DEFAULT_EVALUATION_WEIGHTS)

    if parsed_contract is None:
        raw_str = (generated_answer or "").strip()
        if raw_str.startswith("{") or "```" in raw_str:
            parsed_contract = parse_contract_output(raw_str)
        else:
            parsed_contract = {
                "contract_ok": True,
                "parse_method": "plain_text",
                "answer": raw_str,
                "escalate": detect_escalation_from_answer(raw_str),
                "reason": "",
            }

    answer_text = str(parsed_contract.get("answer") or generated_answer or "").strip()
    contract_ok = bool(parsed_contract.get("contract_ok", True))
    parse_method = str(parsed_contract.get("parse_method") or "json_direct")
    parsed_reason = str(parsed_contract.get("reason") or "")

    fact_cov_pct, matched_kw, missing_kw = compute_fact_coverage(answer_text, required_keywords)

    ground_ctx = context_fact or reference_answer
    halluc_info = detect_hallucinations(answer_text, ground_ctx, user_query)

    sim_info = compute_lexical_similarity_metrics(answer_text, reference_answer)
    sem_sim_pct = sim_info["semantic_similarity_pct"]

    fmt_comp_pct, fmt_checks = evaluate_format_checklist(
        answer_text, prompt_code, contract_ok=contract_ok
    )

    escalation_regex = detect_escalation_from_answer(answer_text)
    if parse_method in ("json_direct", "json_retry") and "escalate" in parsed_contract:
        primary_escalation = bool(parsed_contract["escalate"])
        escalation_method = parse_method
    else:
        primary_escalation = escalation_regex
        escalation_method = "regex"

    escalation_judge: Optional[bool] = None
    escalation_disagreement = False

    if judge_result and judge_result.get("evaluated"):
        escalation_judge = bool(judge_result.get("escalation_decision"))
        escalation_disagreement = (primary_escalation != escalation_judge)
        requires_esc = primary_escalation if parse_method in ("json_direct", "json_retry") else escalation_judge
        escalation_method = f"{escalation_method}+judge"
    else:
        requires_esc = primary_escalation

    escalation_correct = (requires_esc == bool(expected_escalation))

    if judge_result and judge_result.get("evaluated"):
        j_scores = judge_result["scores"]
        relevance_score = float(j_scores["relevance"])
        factuality_score = float(j_scores["factuality"])
        completeness_score = float(j_scores["completeness"])
        clarity_score = float(j_scores["clarity"])
        policy_adherence_score = float(j_scores["policy_adherence"])
        format_score = policy_adherence_score
        avg_rubric_5 = round(
            (relevance_score + factuality_score + completeness_score + clarity_score + policy_adherence_score) / 5.0,
            2,
        )
        rubric_composite_pct = round(((avg_rubric_5 - 1.0) / 4.0) * 100.0, 1)
        rubric_evaluated = True
    else:
        relevance_score = round(1.0 + 4.0 * (sem_sim_pct / 100.0), 2)
        halluc_penalty = min(1.0, 0.5 * halluc_info["hallucination_count"])
        factuality_score = round(max(1.0, 1.0 + 4.0 * (fact_cov_pct / 100.0) - halluc_penalty), 2)
        completeness_score = round(1.0 + 4.0 * (fact_cov_pct / 100.0), 2)
        clarity_score = round(1.0 + 4.0 * (fmt_comp_pct / 100.0), 2)
        policy_adherence_score = (
            5.0 if (escalation_correct and not halluc_info["has_hallucination"]) else (3.0 if escalation_correct else 1.0)
        )
        format_score = round(1.0 + 4.0 * (fmt_comp_pct / 100.0), 2)
        avg_rubric_5 = round(
            (relevance_score + factuality_score + completeness_score + clarity_score + format_score) / 5.0,
            2,
        )
        rubric_composite_pct = round(((avg_rubric_5 - 1.0) / 4.0) * 100.0, 1)
        rubric_evaluated = False

    if not expected_escalation:
        automation_success = bool(
            (not requires_esc)
            and (fact_cov_pct >= AUTOMATION_FC_THRESHOLD_PCT)
            and (not halluc_info["has_hallucination"])
        )
    else:
        automation_success = bool(requires_esc)

    integral_efficiency_pct = compute_composite_quality_index(
        fact_coverage_pct=fact_cov_pct,
        semantic_similarity_pct=sem_sim_pct,
        format_compliance_pct=fmt_comp_pct,
        weights=weights,
        has_hallucination=halluc_info["has_hallucination"],
    )

    confidence_score = round(min(1.0, max(0.0, integral_efficiency_pct / 100.0)), 2)

    return {
        "answer_text": answer_text,
        "contract_ok": contract_ok,
        "parse_method": parse_method,
        "parsed_reason": parsed_reason,
        "fact_coverage_pct": fact_cov_pct,
        "semantic_similarity_pct": sem_sim_pct,
        "stem_cosine_pct": sim_info["stem_cosine_pct"],
        "rouge_l_f1_pct": sim_info["rouge_l_f1_pct"],
        "embedding_available": sim_info["embedding_available"],
        "format_compliance_pct": fmt_comp_pct,
        "format_checks": fmt_checks,
        "hallucinated_items": halluc_info["hallucinated_items"],
        "hallucination_count": halluc_info["hallucination_count"],
        "has_hallucination": halluc_info["has_hallucination"],
        "relevance_score": relevance_score,
        "factuality_score": factuality_score,
        "completeness_score": completeness_score,
        "clarity_score": clarity_score,
        "format_score": format_score,
        "policy_adherence_score": policy_adherence_score,
        "nist_composite_pct": rubric_composite_pct,
        "rubric_composite_pct": rubric_composite_pct if rubric_evaluated else None,
        "rubric_evaluated": rubric_evaluated,
        "confidence_score": confidence_score,
        "requires_escalation": requires_esc,
        "escalation_regex": escalation_regex,
        "escalation_judge": escalation_judge,
        "escalation_disagreement": escalation_disagreement,
        "escalation_method": escalation_method,
        "escalation_correct": escalation_correct,
        "automation_success": automation_success,
        "integral_efficiency_pct": integral_efficiency_pct,
        "matched_keywords": matched_kw,
        "missing_keywords": missing_kw,
    }
