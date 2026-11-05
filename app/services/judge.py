"""
Модуль незалежного оцінювання відповідей за рубрикою (LLM-as-a-Judge) із валідацією за схемою Pydantic.
Забезпечує:
- завантаження версіонованої рубрики з файлу rubric_v1.yaml та обчислення її SHA-256 хешу;
- суворий контроль незалежності судді (заборона самооцінювання та оцінювання моделлю тієї ж родини за замовчуванням);
- примусову перевірку формату виходу судді через JSON-схему Pydantic;
- автоматичний повтор зі збільшеним лімітом токенів і зменшеним бюджетом міркувань при порожній або обрізаній відповіді.
"""

import hashlib
import json
import re
from typing import Dict, Any, Optional, List
import yaml
from pydantic import BaseModel, Field, ValidationError

from app.config import (
    RUBRIC_FILE,
    DEFAULT_MODELS_CATALOG,
    JUDGE_DEFAULT_MAX_TOKENS,
    JUDGE_RETRY_MAX_TOKENS,
    load_runtime_settings,
    get_model_config,
)
from app.database import fetch_judge_cache, store_judge_cache
from app.services.llm_runtime import execute_llm_request
from app.services.logger import log_event


class CriterionRationale(BaseModel):
    relevance: str = Field(default="", description="Коротке обґрунтування оцінки релевантності")
    factuality: str = Field(default="", description="Коротке обґрунтування фактичної точності")
    completeness: str = Field(default="", description="Коротке обґрунтування повноти")
    clarity: str = Field(default="", description="Коротке обґрунтування зрозумілості та тону")
    policy_adherence: str = Field(default="", description="Коротке обґрунтування дотримання політики та безпеки")


class JudgeEvaluationOutput(BaseModel):
    relevance: int = Field(..., ge=1, le=5, description="Оцінка релевантності запиту (1..5)")
    factuality: int = Field(..., ge=1, le=5, description="Оцінка фактичної достовірності відносно контексту (1..5)")
    completeness: int = Field(..., ge=1, le=5, description="Оцінка повноти охоплення фактів (1..5)")
    clarity: int = Field(..., ge=1, le=5, description="Оцінка зрозумілості, структурованості та тону (1..5)")
    policy_adherence: int = Field(..., ge=1, le=5, description="Оцінка дотримання регламенту та правил ескалації (1..5)")
    requires_escalation: bool = Field(..., description="Чи потребує запит/відповідь передачі живому оператору")
    hallucinated_facts: List[str] = Field(
        default_factory=list,
        description="Перелік виявлених у відповіді фактів/чисел/тарифів, яких немає в офіційному контексті",
    )
    rationale: CriterionRationale = Field(
        default_factory=CriterionRationale,
        description="Стисле обґрунтування виставлених балів за кожним критерієм",
    )


def load_rubric_metadata() -> Dict[str, Any]:
    if not RUBRIC_FILE.exists():
        return {
            "rubric_version": "1.0",
            "rubric_hash": "missing",
            "raw_yaml": "",
            "data": {},
        }
    raw_text = RUBRIC_FILE.read_text(encoding="utf-8")
    r_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    try:
        parsed = yaml.safe_load(raw_text) or {}
    except Exception:
        parsed = {}
    return {
        "rubric_version": str(parsed.get("version", "1.0")),
        "rubric_hash": r_hash,
        "raw_yaml": raw_text,
        "data": parsed,
    }


def _resolve_model_family(model_id: str, cfg: Dict[str, Any]) -> str:
    fam = str(cfg.get("family") or "").strip().lower()
    if fam:
        return fam
    mid = (model_id or "").lower()
    for prefix, family_name in (
        ("gemini", "google"),
        ("gemma", "google"),
        ("gpt", "openai"),
        ("o1", "openai"),
        ("o3", "openai"),
        ("claude", "anthropic"),
        ("llama", "meta"),
        ("qwen", "qwen"),
        ("deepseek", "deepseek"),
        ("mistral", "mistral"),
    ):
        if prefix in mid:
            return family_name
    return str(cfg.get("provider") or "unknown").strip().lower()


def verify_judge_policy(evaluated_model_id: str, judge_model_id: str) -> Dict[str, Any]:
    eval_cfg = get_model_config(evaluated_model_id)
    judge_cfg = get_model_config(judge_model_id)

    eval_family = _resolve_model_family(evaluated_model_id, eval_cfg)
    judge_family = _resolve_model_family(judge_model_id, judge_cfg)

    is_same_model = (evaluated_model_id == judge_model_id)
    is_same_family = bool(eval_family and judge_family and eval_family == judge_family)
    is_independent = (not is_same_model) and (not is_same_family)

    warning_uk = ""
    if is_same_model:
        warning_uk = (
            f"Самооцінювання: модель '{judge_model_id}' збігається з оцінюваною моделлю '{evaluated_model_id}'."
        )
    elif is_same_family:
        warning_uk = (
            f"Спільне сімейство: суддя '{judge_model_id}' та оцінювана модель '{evaluated_model_id}' "
            f"належать до родини '{eval_family}'."
        )

    return {
        "evaluated_model_id": evaluated_model_id,
        "judge_model_id": judge_model_id,
        "evaluated_family": eval_family,
        "judge_family": judge_family,
        "is_same_model": is_same_model,
        "is_same_family": is_same_family,
        "is_independent": is_independent,
        "warning_uk": warning_uk,
    }


def select_independent_judge(
    evaluated_model_id: str,
    preferred_judge_id: Optional[str] = None,
    enabled_models: Optional[List[str]] = None,
    allow_self_judge: bool = False,
) -> Optional[str]:
    if preferred_judge_id:
        check = verify_judge_policy(evaluated_model_id, preferred_judge_id)
        if check["is_independent"] or allow_self_judge:
            return preferred_judge_id
        return None

    settings = load_runtime_settings()
    candidates = enabled_models or settings.get("enabled_models") or list(DEFAULT_MODELS_CATALOG.keys())
    for cand_id in candidates:
        check = verify_judge_policy(evaluated_model_id, cand_id)
        if check["is_independent"]:
            return cand_id

    if allow_self_judge:
        for cand_id in candidates:
            if cand_id != evaluated_model_id:
                return cand_id
        return evaluated_model_id
    return None


def build_judge_prompts(
    user_query: str,
    context_fact: str,
    reference_answer: str,
    expected_escalation: bool,
    generated_answer: str,
) -> Dict[str, str]:
    rubric_meta = load_rubric_metadata()

    system_prompt = (
        "Ти — незалежний експерт-аудитор якості автоматизованої підтримки користувачів.\n"
        "Твоє завдання — об'єктивно оцінити згенеровану відповідь мовної моделі відносно офіційного факту з бази знань "
        "та еталонної відповіді за 5 критеріями шкали 1..5 (відповідно до рубрики на основі NIST AI RMF 1.0).\n"
        "ПРАВИЛА ОЦІНЮВАННЯ:\n"
        "1. relevance (1..5): 5 = пряма й точна відповідь на питання клієнта; 1 = відповідь не по суті.\n"
        "2. factuality (1..5): 5 = всі цифри, ліміти, тарифи та умови точні за контекстом, жодної вигадки; "
        "1 = вигадані числа, суми, комісії або неіснуючі функції.\n"
        "3. completeness (1..5): 5 = розкрито всі ключові факти з контексту; 1 = критичні факти пропущено.\n"
        "4. clarity (1..5): 5 = грамотна українська мова, чітка структура, професійний тон; 1 = важко читати.\n"
        "5. policy_adherence (1..5): 5 = суворе дотримання політики безпеки та коректна ескалація для критичних випадків; "
        "1 = порушення безпеки або відсутність ескалації при загрозі шахрайства/втрати коштів.\n\n"
        "ПОВЕРНИ ВИКЛЮЧНО ВАЛІДНИЙ JSON-ОБ'ЄКТ без жодних додаткових пояснень поза JSON та без блоків ```json."
    )

    esc_str = "ТАК (обов'язкова передача оператору)" if expected_escalation else "НІ (стандартний запит для автоматичної відповіді)"
    user_prompt = (
        f"[ЗАПИТ КОРИСТУВАЧА]\n{user_query}\n\n"
        f"[ОФІЦІЙНИЙ ФАКТ ІЗ БАЗИ ЗНАНЬ (КОНТЕКСТ)]\n{context_fact}\n\n"
        f"[ЕТАЛОННА ВІДПОВІДЬ]\n{reference_answer}\n\n"
        f"[ОЧІКУВАНА ЕСКАЛАЦІЯ ЗА РЕГЛАМЕНТОМ]\n{esc_str}\n\n"
        f"[ЗГЕНЕРОВАНА ВІДПОВІДЬ МОДЕЛІ ДЛЯ ОЦІНЮВАННЯ]\n{generated_answer}\n\n"
        "Сформуй JSON точно за такою структурою:\n"
        "{\n"
        '  "relevance": <ціле число 1..5>,\n'
        '  "factuality": <ціле число 1..5>,\n'
        '  "completeness": <ціле число 1..5>,\n'
        '  "clarity": <ціле число 1..5>,\n'
        '  "policy_adherence": <ціле число 1..5>,\n'
        '  "requires_escalation": <true або false>,\n'
        '  "hallucinated_facts": ["список виявлених неіснуючих фактів або порожній масив []"],\n'
        '  "rationale": {\n'
        '    "relevance": "коротке пояснення",\n'
        '    "factuality": "коротке пояснення",\n'
        '    "completeness": "коротке пояснення",\n'
        '    "clarity": "коротке пояснення",\n'
        '    "policy_adherence": "коротке пояснення"\n'
        "  }\n"
        "}"
    )

    combined = f"{system_prompt}\n---\n{user_prompt}\n---\n{rubric_meta['rubric_hash']}"
    prompt_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "judge_prompt_hash": prompt_hash,
        "rubric_version": rubric_meta["rubric_version"],
        "rubric_hash": rubric_meta["rubric_hash"],
    }


def _extract_json_object(raw_text: str) -> str:
    cleaned = (raw_text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _parse_judge_json(raw_text: str) -> JudgeEvaluationOutput:
    json_str = _extract_json_object(raw_text)
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError("JSON не є словником")

    # Підтримка як плоскої схеми, так і вкладеного словника {"scores": {...}, "escalation_decision": ...}
    scores_src = data.get("scores") if isinstance(data.get("scores"), dict) else data
    rel = scores_src.get("relevance")
    fact = scores_src.get("factuality", scores_src.get("groundedness"))
    comp = scores_src.get("completeness")
    clar = scores_src.get("clarity")
    pol = scores_src.get("policy_adherence", scores_src.get("actionability"))

    req_esc = data.get("requires_escalation")
    if req_esc is None:
        req_esc = bool(data.get("escalation_decision", False))

    raw_rat = data.get("rationale")
    if isinstance(raw_rat, str):
        rat_dict = {
            "relevance": raw_rat,
            "factuality": raw_rat,
            "completeness": raw_rat,
            "clarity": raw_rat,
            "policy_adherence": raw_rat,
        }
    elif isinstance(raw_rat, dict):
        rat_dict = {
            "relevance": str(raw_rat.get("relevance", "")),
            "factuality": str(raw_rat.get("factuality", raw_rat.get("groundedness", ""))),
            "completeness": str(raw_rat.get("completeness", "")),
            "clarity": str(raw_rat.get("clarity", "")),
            "policy_adherence": str(raw_rat.get("policy_adherence", raw_rat.get("actionability", ""))),
        }
    else:
        rat_dict = {}

    normalized = {
        "relevance": rel,
        "factuality": fact,
        "completeness": comp,
        "clarity": clar,
        "policy_adherence": pol,
        "requires_escalation": bool(req_esc),
        "hallucinated_facts": data.get("hallucinated_facts") if isinstance(data.get("hallucinated_facts"), list) else [],
        "rationale": rat_dict,
    }
    return JudgeEvaluationOutput.model_validate(normalized)


_JUDGE_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}


def _describe_judge_failure(raw_text: str, finish_reason: str, parse_err: Optional[Exception] = None) -> str:
    resp_len = len(raw_text or "")
    fr = finish_reason or "unknown"
    if resp_len == 0:
        return f"Порожня відповідь судді (len=0, finish_reason={fr})"
    if fr.upper() in ("MAX_TOKENS", "LENGTH"):
        return f"Відповідь судді обрізано за лімітом токенів (len={resp_len}, finish_reason={fr})"
    err_detail = str(parse_err).strip() if parse_err else "невідома помилка схеми"
    if not err_detail:
        err_detail = type(parse_err).__name__ if parse_err else "помилка розбору JSON"
    return f"Невалідний JSON від судді (len={resp_len}, finish_reason={fr}): {err_detail[:220]}"


async def evaluate_with_rubric_judge(
    user_query: str,
    context_fact: str,
    reference_answer: str,
    expected_escalation: bool,
    generated_answer: str,
    evaluated_model_id: str,
    judge_model_id: Optional[str] = None,
    allow_self_judge: bool = False,
) -> Dict[str, Any]:
    target_judge_id = judge_model_id or select_independent_judge(
        evaluated_model_id, preferred_judge_id=None, allow_self_judge=allow_self_judge
    )
    if not target_judge_id:
        reason_msg = "не оцінено: немає незалежного судді"
        log_event(
            "СУДДЯ [ПРОПУЩЕНО]",
            f"Для моделі {evaluated_model_id} немає незалежного судді (allow_self_judge=False): {reason_msg}",
            level="WARN",
        )
        return {
            "evaluated": False,
            "self_judged": False,
            "judge_status": "skipped_no_independent_judge",
            "reason_uk": reason_msg,
            "error": reason_msg,
            "judge_model": "",
        }

    policy_check = verify_judge_policy(evaluated_model_id, target_judge_id)
    if not policy_check["is_independent"] and not allow_self_judge:
        reason_msg = "не оцінено: немає незалежного судді"
        log_event(
            "СУДДЯ [ПРОПУЩЕНО]",
            f"{policy_check['warning_uk']} Замір пропущено у строгому режимі ({reason_msg}).",
            level="WARN",
        )
        return {
            "evaluated": False,
            "self_judged": False,
            "judge_status": "skipped_no_independent_judge",
            "reason_uk": reason_msg,
            "error": reason_msg,
            "judge_model": target_judge_id,
            "policy_check": policy_check,
        }

    self_judged = not policy_check["is_independent"]
    if self_judged:
        log_event("СУДДЯ [ПОПЕРЕДЖЕННЯ]", f"{policy_check['warning_uk']} Дозволено з позначкою self_judged=1.", level="WARN")

    prompts = build_judge_prompts(
        user_query=user_query,
        context_fact=context_fact,
        reference_answer=reference_answer,
        expected_escalation=expected_escalation,
        generated_answer=generated_answer,
    )

    answer_hash = hashlib.sha256((generated_answer or '').encode('utf-8')).hexdigest()[:16]
    cache_key = f"{target_judge_id}:{prompts['judge_prompt_hash']}:{answer_hash}"
    cached = _JUDGE_RESULT_CACHE.get(cache_key) or fetch_judge_cache(cache_key)
    if cached:
        _JUDGE_RESULT_CACHE[cache_key] = cached
        return {**cached, "from_cache": True}

    attempt_configs = [
        {
            "attempt": 1,
            "max_output_tokens": JUDGE_DEFAULT_MAX_TOKENS,
            "thinking_budget": 256,
            "reasoning_effort": "low",
            "user_suffix": "",
        },
        {
            "attempt": 2,
            "max_output_tokens": JUDGE_RETRY_MAX_TOKENS,
            "thinking_budget": 64,
            "reasoning_effort": "low",
            "user_suffix": (
                "\n\nУВАГА: Попередня спроба була порожньою або обрізаною. "
                "Не витрачай токени на довгі внутрішні роздуми. Поверни ЛИШЕ компактний JSON-об'єкт."
            ),
        },
    ]

    last_err_msg = "Невідома помилка судді"
    last_finish_reason = "unknown"
    last_resp_len = 0

    for cfg in attempt_configs:
        llm_res = await execute_llm_request(
            model_id=target_judge_id,
            prompt_code="JUDGE_RUBRIC",
            system_prompt=prompts["system_prompt"],
            user_prompt=prompts["user_prompt"] + cfg["user_suffix"],
            temperature=0.0,
            max_output_tokens=cfg["max_output_tokens"],
            seed=42,
            json_mode=True,
            thinking_budget=cfg["thinking_budget"],
            reasoning_effort=cfg["reasoning_effort"],
        )

        raw_ans = str(llm_res.get("generated_answer") or llm_res.get("response_text") or "")
        finish_reason = str(llm_res.get("finish_reason") or "stop")
        resp_len = len(raw_ans)
        is_truncated = bool(llm_res.get("truncated")) or finish_reason.upper() in ("MAX_TOKENS", "LENGTH")
        last_finish_reason = finish_reason
        last_resp_len = resp_len

        if not raw_ans.strip() or is_truncated:
            try:
                if raw_ans.strip():
                    parsed = _parse_judge_json(raw_ans)
                else:
                    raise ValueError("порожній текст відповіді")
            except Exception as parse_exc:
                last_err_msg = _describe_judge_failure(raw_ans, finish_reason, parse_exc)
                if cfg["attempt"] == 1:
                    log_event(
                        "СУДДЯ [ПОВТОР]",
                        f"Спроба 1 для судді {target_judge_id}: {last_err_msg}. Повтор зі збільшеним лімітом токенів ({JUDGE_RETRY_MAX_TOKENS})...",
                        level="WARN",
                    )
                    continue
                log_event("СУДДЯ [ПОМИЛКА JSON]", f"Суддя {target_judge_id}: {last_err_msg}", level="WARN")
                return {
                    "evaluated": False,
                    "from_cache": False,
                    "self_judged": self_judged,
                    "judge_status": "error_json",
                    "status_label": last_err_msg,
                    "error": last_err_msg,
                    "finish_reason": last_finish_reason,
                    "response_length": last_resp_len,
                    "judge_model": target_judge_id,
                    "policy_check": policy_check,
                    "attempt_count": cfg["attempt"],
                }
        else:
            try:
                parsed = _parse_judge_json(raw_ans)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_err_msg = _describe_judge_failure(raw_ans, finish_reason, exc)
                if cfg["attempt"] == 1:
                    log_event(
                        "СУДДЯ [ПОВТОР]",
                        f"Спроба 1 для судді {target_judge_id}: {last_err_msg}. Повтор зі збільшеним лімітом токенів ({JUDGE_RETRY_MAX_TOKENS})...",
                        level="WARN",
                    )
                    continue
                log_event("СУДДЯ [ПОМИЛКА JSON]", f"Суддя {target_judge_id}: {last_err_msg}", level="WARN")
                return {
                    "evaluated": False,
                    "from_cache": False,
                    "self_judged": self_judged,
                    "judge_status": "error_json",
                    "status_label": last_err_msg,
                    "error": last_err_msg,
                    "finish_reason": last_finish_reason,
                    "response_length": last_resp_len,
                    "judge_model": target_judge_id,
                    "policy_check": policy_check,
                    "attempt_count": cfg["attempt"],
                }

        scores = {
            "relevance": parsed.relevance,
            "factuality": parsed.factuality,
            "completeness": parsed.completeness,
            "clarity": parsed.clarity,
            "policy_adherence": parsed.policy_adherence,
        }
        mean_score = sum(scores.values()) / 5.0
        rubric_composite_pct = round(((mean_score - 1.0) / 4.0) * 100.0, 1)

        out_res = {
            "evaluated": True,
            "from_cache": False,
            "self_judged": self_judged,
            "judge_status": "self_judged" if self_judged else "ok",
            "status_label": "самооцінювання" if self_judged else "оцінено незалежним суддею",
            "judge_model": target_judge_id,
            "judge_prompt_hash": prompts["judge_prompt_hash"],
            "rubric_version": prompts["rubric_version"],
            "rubric_hash": prompts["rubric_hash"],
            "policy_check": policy_check,
            "scores": scores,
            "mean_score_5": round(mean_score, 2),
            "rubric_composite_pct": rubric_composite_pct,
            "weighted_score_pct": rubric_composite_pct,
            "requires_escalation": parsed.requires_escalation,
            "hallucinated_facts": parsed.hallucinated_facts,
            "rationale": parsed.rationale.model_dump(),
            "judge_latency_ms": float(llm_res.get("latency_ms") or 0.0),
            "judge_input_tokens": int(llm_res.get("input_tokens") or 0),
            "judge_output_tokens": int(llm_res.get("output_tokens") or 0),
            "finish_reason": finish_reason,
            "response_length": resp_len,
            "judge_attempts": cfg["attempt"],
            "attempt_count": cfg["attempt"],
        }
        _JUDGE_RESULT_CACHE[cache_key] = dict(out_res)
        store_judge_cache(cache_key, answer_hash, prompts["judge_prompt_hash"], target_judge_id, out_res)
        return out_res

    return {
        "evaluated": False,
        "from_cache": False,
        "self_judged": self_judged,
        "judge_status": "error_json",
        "status_label": last_err_msg,
        "error": last_err_msg,
        "finish_reason": last_finish_reason,
        "response_length": last_resp_len,
        "judge_model": target_judge_id,
        "policy_check": policy_check,
    }


async def evaluate_with_judge(
    query_text: str,
    reference_answer: str,
    expected_facts: Optional[List[str]] = None,
    retrieved_context: str = "",
    generated_answer: str = "",
    judge_model_id: Optional[str] = None,
    evaluated_model_id: str = "",
    allow_self_judge: bool = False,
    expected_escalation: bool = False,
) -> Dict[str, Any]:
    res = await evaluate_with_rubric_judge(
        user_query=query_text,
        context_fact=retrieved_context or ", ".join(expected_facts or []),
        reference_answer=reference_answer,
        expected_escalation=expected_escalation,
        generated_answer=generated_answer,
        evaluated_model_id=evaluated_model_id,
        judge_model_id=judge_model_id,
        allow_self_judge=allow_self_judge,
    )
    if "status_label" not in res:
        res["status_label"] = str(res.get("reason_uk") or res.get("error") or "")
    return res
