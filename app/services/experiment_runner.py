"""
Модуль виконання конвеєра автоматизації та серійних експериментів.
Забезпечує:
- єдиний JSON-контракт відповіді (ContractOutput) з 1 автоматичним повтором при невалідному JSON;
- підтримку трьох режимів подачі контексту (no_context, gold_context, retrieved_context) та top_k;
- асинхронний фоновий запуск (asyncio.create_task) для уникнення блокування HTTP-запитів браузера.
"""

import asyncio
from datetime import datetime
import json
import random
import time
from typing import Dict, Any, List, Optional, Tuple

from app.config import (
    DEFAULT_MODELS_CATALOG,
    QUERY_CATEGORIES,
    ROUTE_OPENROUTER,
    DEFAULT_TOP_K,
    load_runtime_settings,
    get_model_config,
)
from app.database import (
    get_connection,
    fetch_all_queries,
    sync_run_counters,
    compute_code_version_hash,
    compute_dataset_hash,
    compute_kb_hash,
    log_run_error,
    ensure_sandbox_run_id,
)
from app.seed_data import DATASET_VERSION
from app.services.classifier import classify_and_ground_query
from app.services.prompt_builder import build_prompt_messages
from app.services.retrieval import resolve_experiment_context
from app.services.llm_runtime import (
    execute_llm_request,
    DailyQuotaExceededError,
    ModelAccessError,
    set_session_route,
    clear_session_routes,
    resolve_route,
    alternate_route,
)
from app.services.run_prompts import wait_for_user_decision, clear_active_prompt
from app.services.evaluator import parse_contract_output, evaluate_response_comprehensive
from app.services.judge import evaluate_with_rubric_judge, load_rubric_metadata
from app.services.logger import log_event, update_runtime_status, reset_runtime_status, is_cancel_requested
from app.services.analytics import (
    compute_flexible_analytics,
    compute_run_analytics,
    get_evaluation_item_detail,
)
from app.services.export_service import export_run_tables_for_thesis

_ACTIVE_BACKGROUND_TASKS: Dict[str, asyncio.Task] = {}

CONTRACT_RETRY_SUFFIX_UK: str = (
    "\n\nУВАГА: Попередня відповідь не була валідним JSON-об'єктом за схемою. "
    "Поверни ВИКЛЮЧНО чистий JSON без жодного тексту до або після нього:\n"
    '{"answer": "текст відповіді клієнту українською мовою", "escalate": true або false, "reason": "коротке пояснення рішення"}'
)


def _register_background_task(key: str, coro: Any) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _ACTIVE_BACKGROUND_TASKS[key] = task

    def _cleanup(_t: asyncio.Task) -> None:
        _ACTIVE_BACKGROUND_TASKS.pop(key, None)

    task.add_done_callback(_cleanup)
    return task


async def _generate_with_contract_retry(
    model_id: str,
    prompt_code: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = 0.0,
    max_output_tokens: Optional[int] = 2000,
    seed: Optional[int] = 42,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Виконує запит до LLM та перевіряє відповідність єдиному JSON-контракту ContractOutput.
    Якщо з першої спроби JSON невалідний, виконує 1 повтор із явним нагадуванням схеми.
    """
    llm_out = await execute_llm_request(
        model_id=model_id,
        prompt_code=prompt_code,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        seed=seed,
    )
    parsed = parse_contract_output(llm_out["generated_answer"], is_retry=False)
    if parsed["contract_ok"]:
        llm_out["raw_model_output"] = llm_out["generated_answer"]
        llm_out["generated_answer"] = parsed["answer"]
        return llm_out, parsed

    log_event(
        "КОНТРАКТ [ПОВТОР]",
        f"Модель {model_id} ({prompt_code}) повернула відповідь поза JSON-контрактом. Виконується 1 повторна спроба...",
        level="WARN",
    )
    retry_out = await execute_llm_request(
        model_id=model_id,
        prompt_code=prompt_code,
        system_prompt=system_prompt,
        user_prompt=user_prompt + CONTRACT_RETRY_SUFFIX_UK,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        seed=seed,
    )
    parsed_retry = parse_contract_output(retry_out["generated_answer"], is_retry=True)

    combined_out = dict(retry_out)
    combined_out["latency_ms"] = round(float(llm_out["latency_ms"]) + float(retry_out["latency_ms"]), 1)
    combined_out["call_ms"] = round(
        float(llm_out.get("call_ms", llm_out["latency_ms"])) + float(retry_out.get("call_ms", retry_out["latency_ms"])),
        1,
    )
    combined_out["wall_ms"] = round(
        float(llm_out.get("wall_ms", llm_out["latency_ms"])) + float(retry_out.get("wall_ms", retry_out["latency_ms"])),
        1,
    )
    combined_out["input_tokens"] = int(llm_out["input_tokens"]) + int(retry_out["input_tokens"])
    combined_out["output_tokens"] = int(llm_out["output_tokens"]) + int(retry_out["output_tokens"])
    combined_out["thinking_tokens"] = int(llm_out.get("thinking_tokens", 0)) + int(retry_out.get("thinking_tokens", 0))
    combined_out["cost_usd"] = round(float(llm_out["cost_usd"]) + float(retry_out["cost_usd"]), 6)
    combined_out["attempts"] = int(llm_out.get("attempts", 1)) + int(retry_out.get("attempts", 1))
    combined_out["raw_model_output"] = retry_out["generated_answer"]
    combined_out["generated_answer"] = parsed_retry["answer"]
    return combined_out, parsed_retry


def _is_model_unusable_error(exc: Exception) -> bool:
    """Ошибка означает, что модель в этой серии больше не ответит: лимит, нет ключа или отказ в доступе."""
    # тимчасові збої (ліміт за хвилину, немає зв'язку) модель не вимикають: їх рахує лічильник помилок поспіль
    if getattr(exc, "reason", None) in ("rate_limited", "offline"):
        return False
    return isinstance(exc, (DailyQuotaExceededError, ModelAccessError))


def _quota_fields(quota_skipped: Dict[str, str]) -> Dict[str, Any]:
    """Поля summary серии, по которым интерфейс показывает, какие модели остановил суточный лимит."""
    if not quota_skipped:
        return {}
    return {
        "quota_paused": True,
        "quota_models": sorted(quota_skipped),
        "quota_message": "; ".join(quota_skipped.values())[:400],
        "resume_after": "після оновлення добової квоти (Gemini: опівночі за тихоокеанським часом, інші: 00:00 UTC)",
    }


def _insert_evaluation_record(
    cur: Any,
    run_id: int,
    query_id: int,
    query_code: str,
    category: str,
    prompt_pack: Dict[str, Any],
    model_id: str,
    repeat_index: int,
    llm_out: Dict[str, Any],
    metrics: Dict[str, Any],
    source: str = "batch",
    judge_result: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    context_info: Optional[Dict[str, Any]] = None,
) -> int:
    now_str = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rubric_meta = load_rubric_metadata()

    j_scores = judge_result.get("scores", {}) if judge_result else {}
    j_rat = judge_result.get("rationale", {}) if judge_result else {}
    j_model = judge_result.get("judge_model", "") if judge_result else ""
    j_phash = judge_result.get("judge_prompt_hash", "") if judge_result else ""
    r_ver = judge_result.get("rubric_version", rubric_meta["rubric_version"]) if judge_result else rubric_meta["rubric_version"]
    r_hash = judge_result.get("rubric_hash", rubric_meta["rubric_hash"]) if judge_result else rubric_meta["rubric_hash"]
    self_judged_flag = 1 if (judge_result and judge_result.get("self_judged")) else 0
    j_status = str(judge_result.get("judge_status") or ("ok" if judge_result.get("evaluated") else "unjudged")) if judge_result else "unjudged"

    ctx_info = context_info or {}
    context_mode = str(ctx_info.get("context_mode") or "gold_context")
    top_k = int(ctx_info.get("top_k") or DEFAULT_TOP_K)
    retrieved_ids_json = json.dumps(ctx_info.get("retrieved_ids") or [], ensure_ascii=False)
    hit_1 = ctx_info.get("retrieval_hit_at_1")
    hit_3 = ctx_info.get("retrieval_hit_at_3")

    cur.execute(
        """
        INSERT INTO evaluation_results (
            run_id, query_id, query_code, category, prompt_code, model_id, repeat_index,
            generated_answer, latency_ms, input_tokens, output_tokens, cost_usd,
            fact_coverage_pct, semantic_similarity_pct, format_compliance_pct,
            relevance_score, factuality_score, completeness_score, clarity_score, format_score,
            nist_composite_pct, confidence_score, requires_escalation, escalation_correct,
            automation_success, integral_efficiency_pct, matched_keywords_json,
            missing_keywords_json, provider_used, created_at,
            prompt_version_id, prompt_version, rendered_system_prompt, rendered_user_prompt,
            template_hash, few_shot_used_json, temperature, max_output_tokens, seed,
            finish_reason, truncated, attempts, raw_usage_json, thinking_tokens,
            tokens_estimated, call_ms, wall_ms, source,
            rouge_l_f1_pct, stem_cosine_pct, embedding_available, format_checks_json,
            hallucinated_items_json, hallucination_count, has_hallucination,
            escalation_regex, escalation_judge, escalation_disagreement, escalation_method,
            judge_scores_json, judge_rationale_json, judge_model, judge_prompt_hash,
            rubric_version, rubric_hash, policy_adherence_score, rubric_composite_pct,
            rubric_evaluated, self_judged, judge_status,
            context_mode, top_k, retrieved_ids_json, retrieval_hit_at_1, retrieval_hit_at_3,
            contract_ok, parse_method, parsed_reason
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            run_id,
            query_id,
            query_code,
            category,
            prompt_pack["prompt_code"],
            model_id,
            repeat_index,
            metrics.get("answer_text") or llm_out["generated_answer"],
            llm_out["latency_ms"],
            llm_out["input_tokens"],
            llm_out["output_tokens"],
            llm_out["cost_usd"],
            metrics["fact_coverage_pct"],
            metrics["semantic_similarity_pct"],
            metrics["format_compliance_pct"],
            metrics["relevance_score"],
            metrics["factuality_score"],
            metrics["completeness_score"],
            metrics["clarity_score"],
            metrics["format_score"],
            metrics["nist_composite_pct"],
            metrics["confidence_score"],
            1 if metrics["requires_escalation"] else 0,
            1 if metrics["escalation_correct"] else 0,
            1 if metrics["automation_success"] else 0,
            metrics["integral_efficiency_pct"],
            json.dumps(metrics["matched_keywords"], ensure_ascii=False),
            json.dumps(metrics["missing_keywords"], ensure_ascii=False),
            llm_out["provider_used"],
            now_str,
            prompt_pack.get("prompt_version_id"),
            int(prompt_pack.get("prompt_version") or 1),
            prompt_pack.get("system_prompt", ""),
            prompt_pack.get("user_prompt", ""),
            prompt_pack.get("template_hash", ""),
            json.dumps(prompt_pack.get("few_shot_used", []), ensure_ascii=False),
            float(llm_out.get("temperature", 0.0)),
            int(llm_out.get("max_output_tokens", 2000)),
            llm_out.get("seed"),
            str(llm_out.get("finish_reason", "stop")),
            1 if llm_out.get("truncated") else 0,
            int(llm_out.get("attempts", 1)),
            str(llm_out.get("raw_usage_json", "{}")),
            int(llm_out.get("thinking_tokens", 0)),
            1 if llm_out.get("tokens_estimated") else 0,
            float(llm_out.get("call_ms", llm_out["latency_ms"])),
            float(llm_out.get("wall_ms", llm_out["latency_ms"])),
            source,
            float(metrics.get("rouge_l_f1_pct", 0.0)),
            float(metrics.get("stem_cosine_pct", 0.0)),
            1 if metrics.get("embedding_available") else 0,
            json.dumps(metrics.get("format_checks", {}), ensure_ascii=False),
            json.dumps(metrics.get("hallucinated_items", []), ensure_ascii=False),
            int(metrics.get("hallucination_count", 0)),
            1 if metrics.get("has_hallucination") else 0,
            1 if metrics.get("escalation_regex") else 0,
            (1 if metrics["escalation_judge"] else 0) if metrics.get("escalation_judge") is not None else None,
            1 if metrics.get("escalation_disagreement") else 0,
            str(metrics.get("escalation_method", "regex")),
            json.dumps(j_scores, ensure_ascii=False),
            json.dumps(j_rat, ensure_ascii=False),
            j_model,
            j_phash,
            r_ver,
            r_hash,
            float(metrics.get("policy_adherence_score", 0.0)),
            metrics.get("rubric_composite_pct"),
            1 if metrics.get("rubric_evaluated") else 0,
            self_judged_flag,
            j_status,
            context_mode,
            top_k,
            retrieved_ids_json,
            hit_1,
            hit_3,
            1 if metrics.get("contract_ok", True) else 0,
            str(metrics.get("parse_method", "json")),
            str(metrics.get("parsed_reason", "")),
        ),
    )
    return int(cur.lastrowid)


async def run_single_automation_pipeline(
    user_query: str,
    model_id: str,
    prompt_code: str,
    query_id: Optional[int] = None,
    custom_context: Optional[str] = None,
    temperature: Optional[float] = None,
    save_to_db: bool = True,
    judge_model_id: Optional[str] = None,
    context_mode: str = "gold_context",
    top_k: int = DEFAULT_TOP_K,
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    model_info = get_model_config(model_id)

    update_runtime_status(
        is_busy=True,
        operation_type="single_query",
        stage_number=1,
        current_stage="Вхідний аналіз та класифікація категорії запиту",
        active_model=model_info.get("name", model_id),
        active_provider=model_info.get("provider", "api").upper(),
        active_prompt=prompt_code,
        active_query_text=user_query[:90],
        progress_current=1,
        progress_total=5,
    )
    log_event(
        "КЛАСИФІКАТОР",
        f"Аналіз вхідного запиту: {user_query[:80]}",
        level="STEP",
    )

    grounding = classify_and_ground_query(
        user_query=user_query,
        query_id=query_id,
        custom_context=custom_context,
    )
    matched_q = grounding.get("matched_query")
    q_code = matched_q["code"] if matched_q else "CUSTOM"
    q_db_id = int(matched_q["id"]) if matched_q and matched_q.get("id") else 0
    exclude_code = matched_q["code"] if matched_q else None
    eff_domain = domain or (matched_q.get("domain") if matched_q else "fintech") or "fintech"

    if custom_context:
        ctx_info = {
            "context_mode": "gold_context",
            "top_k": top_k,
            "prompt_context": custom_context,
            "gold_context_fact": custom_context,
            "retrieved_ids": [q_code] if q_code != "CUSTOM" else [],
            "retrieval_hit_at_1": 1 if q_code != "CUSTOM" else None,
            "retrieval_hit_at_3": 1 if q_code != "CUSTOM" else None,
            "retrieved_chunks": [],
        }
    else:
        q_item_for_ctx = matched_q or {
            "code": q_code,
            "user_query": user_query,
            "context_fact": grounding["context_fact"],
        }
        ctx_info = resolve_experiment_context(
            query_item=q_item_for_ctx,
            context_mode=context_mode,
            domain=eff_domain,
            top_k=top_k,
        )

    update_runtime_status(
        stage_number=2,
        current_stage=f"Пошук у базі знань ({ctx_info['context_mode']}): еталон {q_code} ({grounding['category_name_uk']})",
        active_query_code=q_code,
        progress_current=2,
    )
    log_event(
        "БАЗА ЗНАНЬ",
        f"Категорія: {grounding['category_name_uk']} | Еталон: {q_code} | Режим: {ctx_info['context_mode']} | Ключових фактів: {len(grounding['required_keywords'])}",
        level="STEP",
    )

    update_runtime_status(
        stage_number=3,
        current_stage=f"Конструювання промпту за стратегією {prompt_code}",
        progress_current=3,
    )
    prompt_pack = build_prompt_messages(
        prompt_code=prompt_code,
        user_query=user_query,
        context_fact=ctx_info["prompt_context"],
        category_code=grounding["detected_category"],
        category_name_uk=grounding["category_name_uk"],
        exclude_query_code=exclude_code,
        domain=eff_domain,
    )
    log_event(
        "ПРОМПТ",
        f"Сформовано промпт {prompt_code} v{prompt_pack['prompt_version']} ({prompt_pack['strategy_type']}) | Довжина: {len(prompt_pack['full_rendered_prompt'])} симв.",
        level="STEP",
    )

    try:
        llm_out, parsed_contract = await _generate_with_contract_retry(
            model_id=model_id,
            prompt_code=prompt_code,
            system_prompt=prompt_pack["system_prompt"],
            user_prompt=prompt_pack["user_prompt"],
            temperature=temperature,
        )
    except Exception as exc:
        reset_runtime_status(f"Помилка виклику LLM: {exc}")
        raise

    judge_res = None
    if judge_model_id:
        try:
            update_runtime_status(
                stage_number=5,
                current_stage=f"Оцінювання LLM-суддею ({judge_model_id})",
                progress_current=5,
            )
            judge_res = await evaluate_with_rubric_judge(
                user_query=user_query,
                context_fact=grounding["context_fact"],
                reference_answer=grounding["reference_answer"],
                expected_escalation=grounding["expected_escalation"],
                generated_answer=parsed_contract["answer"],
                evaluated_model_id=model_id,
                judge_model_id=judge_model_id,
            )
        except Exception as j_exc:
            log_event("СУДДЯ [ПОМИЛКА]", f"Не вдалося виконати оцінювання суддею {judge_model_id}: {j_exc}", level="WARN")

    update_runtime_status(
        stage_number=5,
        current_stage="Розрахунок метрик якості та перевірка шлюзу ескалації",
        progress_current=5,
    )
    eval_metrics = evaluate_response_comprehensive(
        generated_answer=parsed_contract["answer"],
        reference_answer=grounding["reference_answer"],
        required_keywords=grounding["required_keywords"],
        expected_escalation=grounding["expected_escalation"],
        prompt_code=prompt_code,
        latency_ms=llm_out["latency_ms"],
        context_fact=grounding["context_fact"],
        user_query=user_query,
        judge_result=judge_res,
        parsed_contract=parsed_contract,
    )

    eval_id = None
    if save_to_db:
        try:
            sandbox_run_id = ensure_sandbox_run_id()
            conn = get_connection()
            cur = conn.cursor()
            eval_id = _insert_evaluation_record(
                cur=cur,
                run_id=sandbox_run_id,
                query_id=q_db_id,
                query_code=q_code,
                category=grounding["detected_category"],
                prompt_pack=prompt_pack,
                model_id=model_id,
                repeat_index=1,
                llm_out=llm_out,
                metrics=eval_metrics,
                source="sandbox",
                judge_result=judge_res,
                context_info=ctx_info,
            )
            cur.execute(
                "UPDATE experiment_runs SET completed_items = (SELECT COUNT(*) FROM evaluation_results WHERE run_id = ?) WHERE id = ?",
                (sandbox_run_id, sandbox_run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            eval_id = None

    log_event(
        "ОЦІНЮВАННЯ",
        (
            f"Завершено оцінювання для {model_info.get('name', model_id)} ({prompt_code}, {ctx_info['context_mode']}): "
            f"FC = {eval_metrics['fact_coverage_pct']}%, "
            f"SS = {eval_metrics['semantic_similarity_pct']}%, "
            f"Q_det = {eval_metrics['integral_efficiency_pct']}%, "
            f"Контракт = {'OK' if eval_metrics['contract_ok'] else 'FALLBACK'}, "
            f"Ескалація = {'ТАК' if eval_metrics['requires_escalation'] else 'НІ'}"
        ),
        level="EVAL",
    )
    reset_runtime_status(f"Готово: оброблено запит {q_code} моделлю {model_info.get('name', model_id)} за {llm_out['latency_ms']} мс")

    return {
        "evaluation_id": eval_id,
        "source": "sandbox",
        "user_query": user_query,
        "domain": eff_domain,
        "context_mode": ctx_info["context_mode"],
        "top_k": ctx_info["top_k"],
        "retrieved_ids": ctx_info["retrieved_ids"],
        "retrieval_hit_at_1": ctx_info["retrieval_hit_at_1"],
        "retrieval_hit_at_3": ctx_info["retrieval_hit_at_3"],
        "model_id": model_id,
        "model_name": model_info.get("name", model_id),
        "model_class_uk": model_info.get("class_uk", ""),
        "price_verified": bool(model_info.get("price_verified", False)),
        "is_free_tier": bool(model_info.get("is_free_tier", True)),
        "prompt_code": prompt_code,
        "prompt_name_uk": prompt_pack["prompt_name_uk"],
        "strategy_type": prompt_pack["strategy_type"],
        "classification": {
            "detected_category": grounding["detected_category"],
            "category_name_uk": grounding["category_name_uk"],
            "category_confidence": grounding["category_confidence"],
            "matching_method": grounding["matching_method"],
            "matched_query_code": q_code,
            "expected_escalation": grounding["expected_escalation"],
        },
        "grounding": {
            "context_fact": grounding["context_fact"],
            "prompt_context": ctx_info["prompt_context"],
            "reference_answer": grounding["reference_answer"],
            "required_keywords": grounding["required_keywords"],
            "retrieved_chunks": ctx_info.get("retrieved_chunks", []),
        },
        "prompt_details": prompt_pack,
        "generation": {
            "generated_answer": parsed_contract["answer"],
            "parsed_reason": parsed_contract["reason"],
            "contract_ok": parsed_contract["contract_ok"],
            "parse_method": parsed_contract["parse_method"],
            "latency_ms": llm_out["latency_ms"],
            "call_ms": llm_out.get("call_ms", llm_out["latency_ms"]),
            "wall_ms": llm_out.get("wall_ms", llm_out["latency_ms"]),
            "attempts": llm_out.get("attempts", 1),
            "input_tokens": llm_out["input_tokens"],
            "output_tokens": llm_out["output_tokens"],
            "thinking_tokens": llm_out.get("thinking_tokens", 0),
            "tokens_estimated": llm_out.get("tokens_estimated", False),
            "finish_reason": llm_out.get("finish_reason", "stop"),
            "truncated": llm_out.get("truncated", False),
            "cost_usd": llm_out["cost_usd"],
            "cost_per_1k_usd": round(llm_out["cost_usd"] * 1000.0, 4),
            "cost_per_1m_usd": round(
                (
                    llm_out["cost_usd"]
                    / max(
                        1,
                        int(llm_out["input_tokens"])
                        + int(llm_out["output_tokens"])
                        + int(llm_out.get("thinking_tokens") or 0),
                    )
                )
                * 1_000_000.0,
                4,
            ),
            "provider_used": llm_out["provider_used"],
        },
        "evaluation": eval_metrics,
    }


def _normalize_context_modes(
    context_mode: Optional[str] = None,
    context_modes: Optional[List[str]] = None,
) -> List[str]:
    allowed = ("no_context", "gold_context", "retrieved_context")
    modes: List[str] = []
    for item in (context_modes or []):
        m = str(item or "").strip().lower()
        if m in allowed and m not in modes:
            modes.append(m)
    if not modes and context_mode:
        m = str(context_mode).strip().lower()
        if m in allowed:
            modes.append(m)
    if not modes:
        modes.append("gold_context")
    return modes


def _query_sort_key(q: Dict[str, Any]) -> Tuple[int, str]:
    return int(q.get("id") or 0), str(q.get("code") or q.get("query_code") or "")


def select_stratified_queries(
    domain: str = "fintech",
    domains: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    max_queries_per_category: int = 12,
    equal_domain_sample: bool = False,
    seed: int = 42,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Вибирає запити для серії: до max_queries_per_category з кожної пари (сфера, категорія).

    Якщо в парі запитів більше, ніж потрібно, вибірка випадкова, але відтворювана: вона залежить лише від seed.
    З equal_domain_sample у кожній категорії береться однакова кількість запитів з усіх сфер
    (не більше, ніж у найменшій сфері), щоб порівняння сфер для H1 було чесним.
    """
    direct_list_mode = isinstance(domain, list)
    if direct_list_mode:
        all_corpus = list(domain)
        available_domains = sorted(list({str(q.get("domain") or "fintech") for q in all_corpus})) or ["fintech"]
        cats = categories or sorted(list({str(q.get("category") or "informational") for q in all_corpus}))
        target_domains = available_domains
    else:
        cats = categories or list(QUERY_CATEGORIES.keys())
        all_corpus = fetch_all_queries(domain="all", reviewed_only=True)
        available_domains = sorted(list({str(q.get("domain") or "fintech") for q in all_corpus})) or ["fintech"]

        if domains and len(domains) > 0:
            if "all" in domains:
                target_domains = available_domains
            else:
                target_domains = [d.strip() for d in domains if d and d.strip()]
        elif domain and "," in domain:
            target_domains = [d.strip() for d in domain.split(",") if d.strip()]
        elif domain == "all":
            target_domains = available_domains
        else:
            target_domains = [(domain or "fintech").strip()]

    if not target_domains:
        target_domains = ["fintech"]

    domain_set = set(target_domains)
    filtered_corpus = [q for q in all_corpus if str(q.get("domain") or "fintech") in domain_set]
    if not filtered_corpus:
        filtered_corpus = [q for q in all_corpus if str(q.get("domain") or "fintech") == "fintech"]
        target_domains = ["fintech"]

    by_dom_cat: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for d in target_domains:
        for cat in cats:
            cell = [q for q in filtered_corpus if str(q.get("domain") or "fintech") == d and q["category"] == cat]
            cell.sort(key=_query_sort_key)
            by_dom_cat[(d, cat)] = cell

    selected: List[Dict[str, Any]] = []
    for cat in cats:
        k_take = max_queries_per_category
        if equal_domain_sample and len(target_domains) >= 2:
            non_zero = [len(by_dom_cat[(d, cat)]) for d in target_domains if by_dom_cat[(d, cat)]]
            k_take = min([max_queries_per_category] + non_zero) if non_zero else 0
        for d in target_domains:
            pool = by_dom_cat[(d, cat)]
            if not pool or k_take <= 0:
                continue
            if len(pool) > k_take:
                sampled = random.Random(f"{seed}:{d}:{cat}").sample(pool, k_take)
                sampled.sort(key=_query_sort_key)
                selected.extend(sampled)
            else:
                selected.extend(pool)

    return selected if direct_list_mode else (selected, filtered_corpus, target_domains)


def _create_batch_run_record(
    title: str,
    description: str,
    model_ids: List[str],
    prompt_codes: List[str],
    categories: List[str],
    max_queries_per_category: int = 12,
    repeats: int = 1,
    use_judge: bool = False,
    judge_model_id: Optional[str] = None,
    allow_self_judge: bool = False,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    seed: Optional[int] = None,
    domain: str = "fintech",
    domains: Optional[List[str]] = None,
    equal_domain_sample: bool = False,
    context_mode: Optional[str] = None,
    context_modes: Optional[List[str]] = None,
    top_k: int = DEFAULT_TOP_K,
) -> Tuple[int, Dict[str, Any]]:
    settings = load_runtime_settings()
    eff_temp = float(temperature) if temperature is not None else float(settings.get("temperature", 0.0))
    eff_max_tokens = int(max_output_tokens) if max_output_tokens is not None else int(settings.get("max_output_tokens", 2000))
    eff_seed = int(seed) if seed is not None else int(settings.get("seed", 42))

    selected_queries, all_queries, target_domains = select_stratified_queries(
        domain=domain,
        domains=domains,
        categories=categories,
        max_queries_per_category=max_queries_per_category,
        equal_domain_sample=equal_domain_sample,
        seed=eff_seed,
    )
    eff_domain = ",".join(target_domains)

    modes = _normalize_context_modes(context_mode=context_mode, context_modes=context_modes)
    eff_top_k = max(1, int(top_k or DEFAULT_TOP_K))
    total_items = len(selected_queries) * len(modes) * len(prompt_codes) * len(model_ids) * repeats
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_str = "live_api"

    settings_snapshot = {
        "domain": eff_domain,
        "domains": target_domains,
        "equal_domain_sample": bool(equal_domain_sample),
        "context_modes": modes,
        "top_k": eff_top_k,
        "max_queries_per_category": max_queries_per_category,
        "temperature": eff_temp,
        "max_output_tokens": eff_max_tokens,
        "seed": eff_seed,
        "weights": settings.get("weights", {}),
        "use_judge": bool(judge_model_id) or bool(use_judge),
        "judge_model": judge_model_id or "",
        "allow_self_judge": bool(allow_self_judge),
        "selected_query_codes": [q.get("code") or q.get("query_code") for q in selected_queries],
    }
    code_ver = compute_code_version_hash()
    ds_hash = compute_dataset_hash()
    corpus_ver = compute_kb_hash()
    ctx_mode_col = ",".join(modes)

    sync_run_counters()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO experiment_runs (
            title, description, created_at, execution_mode,
            models_json, prompts_json, categories_json,
            repeats, total_items, completed_items, status,
            dataset_version, dataset_hash, code_version, settings_snapshot_json,
            context_mode, top_k, corpus_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'running', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            description,
            now_str,
            mode_str,
            json.dumps(model_ids, ensure_ascii=False),
            json.dumps(prompt_codes, ensure_ascii=False),
            json.dumps(categories, ensure_ascii=False),
            repeats,
            total_items,
            DATASET_VERSION,
            ds_hash,
            code_ver,
            json.dumps(settings_snapshot, ensure_ascii=False),
            ctx_mode_col,
            eff_top_k,
            corpus_ver,
        ),
    )
    run_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    return run_id, {
        "run_id": run_id,
        "title": title,
        "description": description,
        "domain": eff_domain,
        "all_queries": all_queries,
        "selected_queries": selected_queries,
        "context_modes": modes,
        "top_k": eff_top_k,
        "model_ids": model_ids,
        "prompt_codes": prompt_codes,
        "categories": categories,
        "repeats": repeats,
        "total_items": total_items,
        "eff_temp": eff_temp,
        "eff_max_tokens": eff_max_tokens,
        "eff_seed": eff_seed,
        "use_judge": bool(judge_model_id) or bool(use_judge),
        "judge_model_id": judge_model_id,
        "allow_self_judge": bool(allow_self_judge),
        "code_ver": code_ver,
        "ds_hash": ds_hash,
        "now_str": now_str,
    }


# Сколько ошибок подряд терпим, прежде чем остановить модель или судью до следующего «Довиконати»
MAX_CONSECUTIVE_MODEL_ERRORS = 5
MAX_CONSECUTIVE_JUDGE_ERRORS = 5
JUDGE_ATTEMPTS = 2
_LOCAL_MODEL_LOCK = asyncio.Lock()


async def _judge_cell(state: Dict[str, Any], q: Dict[str, Any], answer: str, model_id: str) -> Optional[Dict[str, Any]]:
    """Оценка судьёй без участия человека: при сбое замер сохраняется без оценки и дооценивается позже."""
    if not state["judge_on"]:
        return None
    last_exc: Optional[Exception] = None
    for attempt in range(1, JUDGE_ATTEMPTS + 1):
        try:
            res = await evaluate_with_rubric_judge(
                user_query=q["user_query"],
                context_fact=q["context_fact"],
                reference_answer=q["reference_answer"],
                expected_escalation=q["expected_escalation"],
                generated_answer=answer,
                evaluated_model_id=model_id,
                judge_model_id=state["judge_model"],
                allow_self_judge=state["allow_self_judge"],
            )
            state["judge_streak"] = 0
            return res
        except Exception as exc:
            last_exc = exc
            if _is_model_unusable_error(exc):
                break
            if attempt < JUDGE_ATTEMPTS:
                await asyncio.sleep(10.0)
    state["judge_streak"] += 1
    state["judge_failures"] += 1
    log_event(
        "СУДДЯ [ПОМИЛКА]",
        f"Суддя {state['judge_model']} не оцінив відповідь {q['code']} ({model_id}): {last_exc}. Замір збережено без оцінки, його можна дооцінити пізніше.",
        level="WARN",
    )
    if _is_model_unusable_error(last_exc) or state["judge_streak"] >= MAX_CONSECUTIVE_JUDGE_ERRORS:
        state["judge_on"] = False
        log_event(
            "СУДДЯ [ВИМКНЕНО]",
            f"Суддю {state['judge_model']} вимкнено до кінця цього запуску. Решту замірів дооцініть кнопкою «Дооцінити суддею».",
            level="ERROR",
        )
    return None


async def _run_series_cells(
    run_id: int,
    selected_queries: List[Dict[str, Any]],
    modes: List[str],
    prompt_codes: List[str],
    model_ids: List[str],
    repeats: int,
    total_items: int,
    existing_cells: set,
    eff_temp: float,
    eff_max_tokens: int,
    eff_seed: int,
    eff_top_k: int,
    run_domain: str,
    judge_model: str,
    allow_self_judge: bool,
    now_str: str,
) -> Dict[str, Any]:
    """Выполняет недостающие замеры серии. Модели идут параллельно, каждая своей задачей и по порядку,
    поэтому медленная или остановленная лимитом модель не задерживает остальные."""
    state: Dict[str, Any] = {
        "completed": len(existing_cells),
        "errors": 0,
        "last_error": "",
        "quota_skipped": {},
        "judge_on": bool(judge_model),
        "judge_model": judge_model,
        "allow_self_judge": allow_self_judge,
        "judge_streak": 0,
        "judge_failures": 0,
    }
    conn = get_connection()
    cur = conn.cursor()
    packs: Dict[Tuple[str, str, str], Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    contexts: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def get_pack(q: Dict[str, Any], ctx_mode: str, p_code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        q_domain = str(q.get("domain") or run_domain or "fintech")
        ctx_key = (q["code"], ctx_mode)
        if ctx_key not in contexts:
            contexts[ctx_key] = resolve_experiment_context(query_item=q, context_mode=ctx_mode, domain=q_domain, top_k=eff_top_k)
        key = (q["code"], ctx_mode, p_code)
        if key not in packs:
            cat_info = QUERY_CATEGORIES.get(q["category"], QUERY_CATEGORIES["informational"])
            packs[key] = (
                contexts[ctx_key],
                build_prompt_messages(
                    prompt_code=p_code,
                    user_query=q["user_query"],
                    context_fact=contexts[ctx_key]["prompt_context"],
                    category_code=q["category"],
                    category_name_uk=cat_info["name_uk"],
                    exclude_query_code=q["code"],
                    domain=q_domain,
                ),
            )
        return packs[key]

    def stop_model(m_id: str, reason: str) -> None:
        state["quota_skipped"][m_id] = reason[:240]
        log_event(
            "ЕКСПЕРИМЕНТ [ПРОПУСК МОДЕЛІ]",
            f"Модель {m_id} зупинено в цьому запуску: {reason[:200]}. Решта моделей продовжують роботу, серію можна довиконати пізніше.",
            level="WARN",
        )

    async def run_model(m_id: str) -> None:
        model_cfg = get_model_config(m_id)
        is_local = model_cfg.get("provider") == "ollama"
        error_streak = 0
        cells = (
            (q, ctx_mode, p_code, rep)
            for q in selected_queries for ctx_mode in modes for p_code in prompt_codes for rep in range(1, repeats + 1)
        )
        for q, ctx_mode, p_code, rep in cells:
            if is_cancel_requested():
                return
            cell_key = (q["code"], m_id, p_code, rep, ctx_mode)
            if cell_key in existing_cells:
                continue
            ctx_info, prompt_pack = get_pack(q, ctx_mode, p_code)
            update_runtime_status(
                is_busy=True,
                operation_type="batch_experiment",
                stage_number=4,
                current_stage=f"Замір {state['completed'] + 1}/{total_items}: {q['code']} | {model_cfg['name']} | {p_code} | {ctx_mode}",
                active_query_code=q["code"],
                active_query_text=q["user_query"][:85],
                progress_current=state["completed"],
                progress_total=total_items,
            )
            try:
                generate = _generate_with_contract_retry(
                    model_id=m_id,
                    prompt_code=p_code,
                    system_prompt=prompt_pack["system_prompt"],
                    user_prompt=prompt_pack["user_prompt"],
                    temperature=eff_temp,
                    max_output_tokens=eff_max_tokens,
                    seed=eff_seed + (rep - 1),
                )
                if is_local:
                    # локальные модели делят один процессор: по одной за раз
                    async with _LOCAL_MODEL_LOCK:
                        llm_out, parsed_contract = await generate
                else:
                    llm_out, parsed_contract = await generate
            except Exception as item_exc:
                err_text = str(item_exc) or repr(item_exc)
                state["errors"] += 1
                state["last_error"] = err_text
                log_run_error(
                    run_id=run_id, query_code=q["code"], model_id=m_id, prompt_code=p_code,
                    repeat_index=rep, error_type=type(item_exc).__name__, message=err_text,
                )
                log_event(
                    "ЕКСПЕРИМЕНТ [ПОМИЛКА]",
                    f"Помилка на запиті {q['code']} ({m_id}, {p_code}, {ctx_mode}, повтор {rep}): {err_text}",
                    level="ERROR",
                )
                if _is_model_unusable_error(item_exc):
                    stop_model(m_id, err_text)
                    return
                error_streak += 1
                if error_streak >= MAX_CONSECUTIVE_MODEL_ERRORS:
                    stop_model(m_id, f"{MAX_CONSECUTIVE_MODEL_ERRORS} помилок поспіль, остання: {err_text}")
                    return
                continue
            error_streak = 0

            judge_res = await _judge_cell(state, q, parsed_contract["answer"], m_id)
            metrics = evaluate_response_comprehensive(
                generated_answer=parsed_contract["answer"],
                reference_answer=q["reference_answer"],
                required_keywords=q["required_keywords"],
                expected_escalation=q["expected_escalation"],
                prompt_code=p_code,
                latency_ms=llm_out["latency_ms"],
                context_fact=q["context_fact"],
                user_query=q["user_query"],
                judge_result=judge_res,
                parsed_contract=parsed_contract,
            )
            # между вставкой и commit нет await, поэтому задачи разных моделей не пересекаются в одной транзакции
            _insert_evaluation_record(
                cur=cur, run_id=run_id, query_id=q["id"], query_code=q["code"], category=q["category"],
                prompt_pack=prompt_pack, model_id=m_id, repeat_index=rep, llm_out=llm_out, metrics=metrics,
                source="batch", judge_result=judge_res, created_at=now_str, context_info=ctx_info,
            )
            existing_cells.add(cell_key)
            state["completed"] += 1
            cur.execute("UPDATE experiment_runs SET completed_items = ? WHERE id = ?", (state["completed"], run_id))
            conn.commit()
            log_event(
                "ЕКСПЕРИМЕНТ [ЗАМІР]",
                (
                    f"[{state['completed']}/{total_items}] {q['code']} | {m_id} | {p_code} | {ctx_mode} -> "
                    f"FC={metrics['fact_coverage_pct']}%, Q_det={metrics['integral_efficiency_pct']}%, Час={llm_out['latency_ms']} мс"
                ),
                level="EVAL",
            )

    try:
        results = await asyncio.gather(*(run_model(m) for m in model_ids), return_exceptions=True)
        for m_id, res in zip(model_ids, results):
            if isinstance(res, Exception):
                state["errors"] += 1
                state["last_error"] = repr(res)
                stop_model(m_id, f"внутрішня помилка: {res!r}")
    finally:
        clear_active_prompt()
        conn.close()
    return state


def _final_series_status(state: Dict[str, Any], total_items: int) -> str:
    if is_cancel_requested():
        return "cancelled"
    if state["completed"] >= total_items:
        return "completed"
    if state["quota_skipped"]:
        return "paused_quota"
    return "failed" if state["completed"] == 0 else "incomplete"


def _save_series_status(run_id: int, state: Dict[str, Any], total_items: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE experiment_runs SET completed_items = ?, status = ?, summary_json = ? WHERE id = ?",
        (
            state["completed"], _final_series_status(state, total_items),
            json.dumps(_quota_fields(state["quota_skipped"]), ensure_ascii=False), run_id,
        ),
    )
    conn.commit()
    conn.close()


async def _run_batch_experiment_loop(run_ctx: Dict[str, Any]) -> Dict[str, Any]:
    run_id = int(run_ctx["run_id"])
    title = run_ctx["title"]
    eff_domain = run_ctx["domain"]
    all_queries = run_ctx["all_queries"]
    selected_queries = run_ctx["selected_queries"]
    modes = run_ctx["context_modes"]
    eff_top_k = int(run_ctx["top_k"])
    model_ids = run_ctx["model_ids"]
    prompt_codes = run_ctx["prompt_codes"]
    repeats = int(run_ctx["repeats"])
    total_items = int(run_ctx["total_items"])
    eff_temp = float(run_ctx["eff_temp"])
    eff_max_tokens = int(run_ctx["eff_max_tokens"])
    eff_seed = int(run_ctx["eff_seed"])
    use_judge = bool(run_ctx["use_judge"])
    judge_model_id = run_ctx["judge_model_id"]
    allow_self_judge = bool(run_ctx.get("allow_self_judge", False))
    now_str = run_ctx["now_str"]

    settings = load_runtime_settings()
    log_event(
        "ЕКСПЕРИМЕНТ [СТАРТ]",
        (
            f"Запуск серії #{run_id} «{title}»: запитів={len(selected_queries)}, режимів контексту={len(modes)} ({','.join(modes)}), "
            f"промптів={len(prompt_codes)}, моделей={len(model_ids)}, повторів={repeats} (Всього замірів: {total_items})"
        ),
        level="STEP",
    )

    update_runtime_status(
        is_busy=True,
        operation_type="batch_experiment",
        stage_number=1,
        current_stage=f"Підготовка серії #{run_id} ({total_items} замірів)...",
        progress_current=0,
        progress_total=total_items,
        cancel_requested=False,
    )

    judge_model = (judge_model_id or str(settings.get("judge_model", ""))) if use_judge else ""
    if judge_model:
        log_event("СУДДЯ [АКТИВОВАНО]", f"Оцінювання за рубрикою виконує LLM-суддя: {judge_model}", level="STEP")

    state = await _run_series_cells(
        run_id=run_id, selected_queries=selected_queries, modes=modes, prompt_codes=prompt_codes,
        model_ids=model_ids, repeats=repeats, total_items=total_items, existing_cells=set(),
        eff_temp=eff_temp, eff_max_tokens=eff_max_tokens, eff_seed=eff_seed, eff_top_k=eff_top_k,
        run_domain=(eff_domain.split(",")[0] if eff_domain else "fintech"),
        judge_model=judge_model, allow_self_judge=allow_self_judge, now_str=now_str,
    )
    _save_series_status(run_id, state, total_items)
    completed = state["completed"]
    quota_skipped = state["quota_skipped"]
    last_fatal_error = state["last_error"]

    if completed == 0:
        reset_runtime_status(f"Серію #{run_id} завершено зі статусом failed: {last_fatal_error or '0 виконаних замірів'}")
        return compute_run_analytics(run_id)

    analytics = compute_run_analytics(run_id)
    conn2 = get_connection()
    conn2.execute(
        "UPDATE experiment_runs SET summary_json = ? WHERE id = ?",
        (json.dumps({**analytics.get("overall", {}), **_quota_fields(quota_skipped)}, ensure_ascii=False), run_id),
    )
    conn2.commit()
    conn2.close()

    log_event(
        "ЕКСПЕРИМЕНТ [ЗАВЕРШЕНО]",
        f"Серію #{run_id} завершено зі статусом '{analytics['run']['status']}'. Виконано замірів: {completed}/{total_items}. Середня повнота FC = {analytics['overall']['fact_coverage_pct']}%",
        level="STEP",
    )
    reset_runtime_status(f"Експеримент #{run_id} ({analytics['run']['status']}): {completed}/{total_items} замірів")
    return analytics


async def execute_batch_experiment(
    title: str,
    description: str,
    model_ids: List[str],
    prompt_codes: List[str],
    categories: List[str],
    max_queries_per_category: int = 12,
    repeats: int = 1,
    execution_mode: Optional[str] = None,
    use_judge: bool = False,
    judge_model_id: Optional[str] = None,
    allow_self_judge: bool = False,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    seed: Optional[int] = None,
    domain: str = "fintech",
    domains: Optional[List[str]] = None,
    equal_domain_sample: bool = False,
    context_mode: Optional[str] = None,
    context_modes: Optional[List[str]] = None,
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    _, run_ctx = _create_batch_run_record(
        title=title,
        description=description,
        model_ids=model_ids,
        prompt_codes=prompt_codes,
        categories=categories,
        max_queries_per_category=max_queries_per_category,
        repeats=repeats,
        use_judge=use_judge,
        judge_model_id=judge_model_id,
        allow_self_judge=allow_self_judge,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        seed=seed,
        domain=domain,
        domains=domains,
        equal_domain_sample=equal_domain_sample,
        context_mode=context_mode,
        context_modes=context_modes,
        top_k=top_k,
    )
    return await _run_batch_experiment_loop(run_ctx)


def start_batch_experiment_task(
    title: str,
    description: str,
    model_ids: List[str],
    prompt_codes: List[str],
    categories: List[str],
    max_queries_per_category: int = 12,
    repeats: int = 1,
    use_judge: bool = False,
    judge_model_id: Optional[str] = None,
    allow_self_judge: bool = False,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    seed: Optional[int] = None,
    domain: str = "fintech",
    domains: Optional[List[str]] = None,
    equal_domain_sample: bool = False,
    context_mode: Optional[str] = None,
    context_modes: Optional[List[str]] = None,
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    """
    Створює запис серії в БД, запускає виконання у фоновій asyncio-задачі
    та негайно повертає run_id і статус 'running' для неблокуючої відповіді HTTP.
    """
    run_id, run_ctx = _create_batch_run_record(
        title=title,
        description=description,
        model_ids=model_ids,
        prompt_codes=prompt_codes,
        categories=categories,
        max_queries_per_category=max_queries_per_category,
        repeats=repeats,
        use_judge=use_judge,
        judge_model_id=judge_model_id,
        allow_self_judge=allow_self_judge,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        seed=seed,
        domain=domain,
        domains=domains,
        equal_domain_sample=equal_domain_sample,
        context_mode=context_mode,
        context_modes=context_modes,
        top_k=top_k,
    )
    _register_background_task(f"batch_run_{run_id}", _run_batch_experiment_loop(run_ctx))
    return {
        "status": "running",
        "background": True,
        "run_id": run_id,
        "run": {
            "id": run_id,
            "title": title,
            "description": description,
            "status": "running",
            "total_items": run_ctx["total_items"],
            "completed_items": 0,
            "context_mode": ",".join(run_ctx["context_modes"]),
            "top_k": run_ctx["top_k"],
        },
    }


async def resume_batch_experiment(
    run_id: int,
    judge_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiment_runs WHERE id = ?", (run_id,))
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        raise RuntimeError(f"Серію #{run_id} не знайдено.")
    run_meta = dict(run_row)

    model_ids = json.loads(run_meta.get("models_json") or "[]")
    prompt_codes = json.loads(run_meta.get("prompts_json") or "[]")
    categories = json.loads(run_meta.get("categories_json") or "[]")
    repeats = max(1, int(run_meta.get("repeats") or 1))
    total_items = int(run_meta.get("total_items") or 0)
    snapshot = json.loads(run_meta.get("settings_snapshot_json") or "{}")

    modes = _normalize_context_modes(
        context_mode=run_meta.get("context_mode"),
        context_modes=snapshot.get("context_modes"),
    )
    eff_top_k = int(snapshot.get("top_k") or run_meta.get("top_k") or DEFAULT_TOP_K)

    cur.execute(
        "SELECT query_code, model_id, prompt_code, repeat_index, COALESCE(context_mode, 'gold_context') AS context_mode FROM evaluation_results WHERE run_id = ?",
        (run_id,),
    )
    existing_cells = {
        (r["query_code"], r["model_id"], r["prompt_code"], int(r["repeat_index"] or 1), str(r["context_mode"] or "gold_context"))
        for r in cur.fetchall()
    }

    denom = max(1, len(categories) * len(modes) * len(prompt_codes) * len(model_ids) * repeats)
    max_queries_per_category = int(snapshot.get("max_queries_per_category") or max(1, round(total_items / denom)))

    run_domain = str(snapshot.get("domain") or "fintech")
    saved_codes = snapshot.get("selected_query_codes")
    if saved_codes:
        by_code = {q["code"]: q for q in fetch_all_queries(domain="all")}
        selected_queries = [by_code[c] for c in saved_codes if c in by_code]
    else:
        # Серії, створені до збереження списку запитів, відновлюються за старим правилом
        all_queries = fetch_all_queries(domain=run_domain, reviewed_only=True) or fetch_all_queries(domain="fintech", reviewed_only=True)
        selected_queries = []
        for cat in categories:
            selected_queries.extend([q for q in all_queries if q["category"] == cat][:max_queries_per_category])

    expected_total = len(selected_queries) * len(modes) * len(prompt_codes) * len(model_ids) * repeats
    if total_items < expected_total:
        total_items = expected_total

    settings = load_runtime_settings()
    eff_temp = float(snapshot.get("temperature", settings.get("temperature", 0.0)))
    eff_max_tokens = int(snapshot.get("max_output_tokens", settings.get("max_output_tokens", 2000)))
    eff_seed = int(snapshot.get("seed", settings.get("seed", 42)))

    active_judge_model = judge_model_id or snapshot.get("judge_model") or str(settings.get("judge_model", ""))
    active_judge = bool(judge_model_id) or bool(snapshot.get("use_judge"))

    completed = len(existing_cells)
    if completed >= total_items:
        cur.execute("UPDATE experiment_runs SET completed_items = ?, status = 'completed' WHERE id = ?", (completed, run_id))
        conn.commit()
        conn.close()
        return compute_run_analytics(run_id)

    clear_session_routes()
    cur.execute("UPDATE experiment_runs SET status = 'running', total_items = ? WHERE id = ?", (total_items, run_id))
    conn.commit()
    update_runtime_status(
        is_busy=True,
        operation_type="batch_experiment",
        stage_number=1,
        current_stage=f"Продовження серії #{run_id} з кроку {completed + 1}/{total_items}...",
        progress_current=completed,
        progress_total=total_items,
        cancel_requested=False,
    )
    log_event(
        "ЕКСПЕРИМЕНТ [ПРОДОВЖЕННЯ]",
        f"Продовження серії #{run_id} ({run_meta['title']}): вже виконано {completed}/{total_items}, залишилося {total_items - completed} замірів.",
        level="STEP",
    )

    conn.close()
    state = await _run_series_cells(
        run_id=run_id, selected_queries=selected_queries, modes=modes, prompt_codes=prompt_codes,
        model_ids=model_ids, repeats=repeats, total_items=total_items, existing_cells=existing_cells,
        eff_temp=eff_temp, eff_max_tokens=eff_max_tokens, eff_seed=eff_seed, eff_top_k=eff_top_k,
        run_domain=run_domain, judge_model=str(active_judge_model or "") if active_judge else "",
        allow_self_judge=bool(snapshot.get("allow_self_judge", False)),
        now_str=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    _save_series_status(run_id, state, total_items)
    completed = state["completed"]
    quota_skipped = state["quota_skipped"]

    analytics = compute_run_analytics(run_id)
    conn2 = get_connection()
    conn2.execute(
        "UPDATE experiment_runs SET summary_json = ? WHERE id = ?",
        (json.dumps({**analytics.get("overall", {}), **_quota_fields(quota_skipped)}, ensure_ascii=False), run_id),
    )
    conn2.commit()
    conn2.close()
    reset_runtime_status(f"Серію #{run_id} оновлено ({analytics['run']['status']}): {completed}/{total_items} замірів")
    return analytics


def start_resume_experiment_task(
    run_id: int,
    judge_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    _register_background_task(f"resume_run_{run_id}", resume_batch_experiment(run_id=run_id, judge_model_id=judge_model_id))
    return {
        "status": "running",
        "background": True,
        "run_id": run_id,
    }


async def evaluate_run_with_judge(
    run_id: int,
    judge_model_id: str,
    only_unjudged: bool = True,
    allow_self_judge: bool = False,
) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiment_runs WHERE id = ?", (run_id,))
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        raise RuntimeError(f"Серію #{run_id} не знайдено.")

    sql = "SELECT * FROM evaluation_results WHERE run_id = ? AND source != 'sandbox'"
    if only_unjudged:
        sql += " AND COALESCE(rubric_evaluated, 0) = 0"
    sql += " ORDER BY id ASC"
    cur.execute(sql, (run_id,))
    rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        conn.close()
        log_event("СУДДЯ [ДООЦІНЮВАННЯ]", f"У серії #{run_id} всі заміри вже мають оцінку LLM-судді.", level="INFO")
        return compute_run_analytics(run_id)

    queries_by_code = {q["code"]: q for q in fetch_all_queries(domain="all")}
    settings = load_runtime_settings()
    active_judge_model = judge_model_id
    total_to_judge = len(rows)
    judged_done = 0
    clear_session_routes()

    update_runtime_status(
        is_busy=True,
        operation_type="judge_evaluation",
        stage_number=5,
        current_stage=f"Дооцінювання серії #{run_id} суддею {active_judge_model} (0/{total_to_judge})...",
        active_model=active_judge_model,
        progress_current=0,
        progress_total=total_to_judge,
        cancel_requested=False,
    )
    log_event(
        "СУДДЯ [ДООЦІНЮВАННЯ]",
        f"Запуск дооцінювання серії #{run_id} моделлю-суддею {active_judge_model}: записів до оцінки = {total_to_judge}.",
        level="STEP",
    )

    try:
        for idx, rec in enumerate(rows, start=1):
            if is_cancel_requested():
                break
            q_obj = queries_by_code.get(rec["query_code"])
            if not q_obj:
                continue

            update_runtime_status(
                is_busy=True,
                operation_type="judge_evaluation",
                stage_number=5,
                current_stage=f"Оцінювання суддею ({active_judge_model}): замір {idx}/{total_to_judge} ({rec['query_code']} | {rec['model_id']})",
                active_model=active_judge_model,
                active_prompt=rec["prompt_code"],
                active_query_code=rec["query_code"],
                progress_current=judged_done,
                progress_total=total_to_judge,
            )

            judge_res = None
            stop_judging = False
            skip_current_item = False
            while True:
                try:
                    judge_res = await evaluate_with_rubric_judge(
                        user_query=q_obj["user_query"],
                        context_fact=q_obj["context_fact"],
                        reference_answer=q_obj["reference_answer"],
                        expected_escalation=bool(q_obj["expected_escalation"]),
                        generated_answer=rec["generated_answer"],
                        evaluated_model_id=rec["model_id"],
                        judge_model_id=active_judge_model,
                        allow_self_judge=allow_self_judge,
                    )
                    break
                except Exception as j_exc:
                    prompt_id = f"rejudge_{int(time.time()*1000)}"
                    options = [
                        {"action": "wait_retry", "label": f"Зачекати 20 сек і повторити оцінку суддею ({active_judge_model})", "primary": True},
                    ]
                    j_alt = alternate_route(active_judge_model, resolve_route(active_judge_model, settings), settings)
                    if j_alt:
                        j_alt_label = "OpenRouter" if j_alt == ROUTE_OPENROUTER else "Прямий API"
                        options.append({
                            "action": "switch_judge_route",
                            "target_route": j_alt,
                            "label": f"Перемкнути суддю ({active_judge_model}) на {j_alt_label}",
                            "primary": True,
                        })
                    enabled_list = settings.get("enabled_models") or list(DEFAULT_MODELS_CATALOG.keys())
                    alt_judges = [m for m in enabled_list if m != active_judge_model][:3]
                    for alt_jm in alt_judges:
                        alt_cfg = get_model_config(alt_jm)
                        options.append({
                            "action": "switch_judge_model",
                            "target_model": alt_jm,
                            "label": f"Змінити суддю на {alt_cfg.get('name', alt_jm)}",
                            "primary": True,
                        })
                    options.append({"action": "skip_item", "label": f"Пропустити замір #{rec['id']} і продовжити оцінювання наступних"})
                    options.append({"action": "stop_judge", "label": f"Зупинити дооцінювання (зберегти {judged_done} оцінених записів)"})

                    decision = await wait_for_user_decision(
                        prompt_id=prompt_id,
                        prompt_type="judge_failed",
                        title="Помилка або ліміт моделі-судді",
                        message=f"Суддя {active_judge_model} повернув помилку на замірі #{rec['id']} ({rec['query_code']} | {rec['model_id']}): {j_exc}. Оберіть дію:",
                        options=options,
                        extra_context={"current_judge": active_judge_model, "record_id": rec["id"], "judged_done": judged_done},
                        timeout_seconds=120.0,
                    )
                    act = (decision or {}).get("action")
                    if act == "wait_retry":
                        await asyncio.sleep(20.0)
                        continue
                    elif act == "switch_judge_route" and j_alt:
                        set_session_route(active_judge_model, j_alt)
                        continue
                    elif act == "switch_judge_model":
                        active_judge_model = (decision.get("data") or {}).get("target_model") or (alt_judges[0] if alt_judges else active_judge_model)
                        continue
                    elif act == "skip_item":
                        skip_current_item = True
                        break
                    else:
                        stop_judging = True
                        break

            if skip_current_item:
                continue
            if stop_judging or not judge_res:
                break

            if not judge_res.get("evaluated"):
                j_status = str(judge_res.get("judge_status") or "skipped_no_independent_judge")
                cur.execute(
                    """
                    UPDATE evaluation_results
                    SET rubric_evaluated = 0, self_judged = 0, judge_status = ?, judge_model = ?
                    WHERE id = ?
                    """,
                    (j_status, judge_res.get("judge_model", active_judge_model), rec["id"]),
                )
                conn.commit()
                continue

            stored_contract = {
                "contract_ok": bool(rec.get("contract_ok", 1)),
                "parse_method": str(rec.get("parse_method") or "json_direct"),
                "answer": str(rec.get("generated_answer") or ""),
                "escalate": bool(rec.get("requires_escalation", 0)),
                "reason": str(rec.get("parsed_reason") or ""),
            }
            metrics = evaluate_response_comprehensive(
                generated_answer=rec["generated_answer"],
                reference_answer=q_obj["reference_answer"],
                required_keywords=q_obj["required_keywords"],
                expected_escalation=bool(q_obj["expected_escalation"]),
                prompt_code=rec["prompt_code"],
                latency_ms=float(rec.get("latency_ms") or 0.0),
                context_fact=q_obj["context_fact"],
                user_query=q_obj["user_query"],
                judge_result=judge_res,
                parsed_contract=stored_contract,
            )

            j_scores = judge_res.get("scores", {})
            j_rat = judge_res.get("rationale") or judge_res.get("rationales") or {"rationale_uk": judge_res.get("rationale_uk", "")}
            self_judged_flag = 1 if judge_res.get("self_judged") else 0
            j_status = str(judge_res.get("judge_status") or ("self_judged" if self_judged_flag else "ok"))
            cur.execute(
                """
                UPDATE evaluation_results
                SET relevance_score = ?, factuality_score = ?, completeness_score = ?,
                    clarity_score = ?, format_score = ?, policy_adherence_score = ?,
                    nist_composite_pct = ?, rubric_composite_pct = ?, rubric_evaluated = 1,
                    self_judged = ?, judge_status = ?,
                    integral_efficiency_pct = ?,
                    judge_scores_json = ?, judge_rationale_json = ?, judge_model = ?,
                    judge_prompt_hash = ?, rubric_version = ?, rubric_hash = ?,
                    escalation_judge = ?, escalation_disagreement = ?, escalation_method = ?
                WHERE id = ?
                """,
                (
                    metrics["relevance_score"],
                    metrics["factuality_score"],
                    metrics["completeness_score"],
                    metrics["clarity_score"],
                    metrics["format_score"],
                    metrics.get("policy_adherence_score", metrics["format_score"]),
                    metrics["nist_composite_pct"],
                    metrics.get("rubric_composite_pct"),
                    self_judged_flag,
                    j_status,
                    metrics["integral_efficiency_pct"],
                    json.dumps(j_scores, ensure_ascii=False),
                    json.dumps(j_rat, ensure_ascii=False),
                    judge_res.get("judge_model", active_judge_model),
                    judge_res.get("judge_prompt_hash", ""),
                    judge_res.get("rubric_version", ""),
                    judge_res.get("rubric_hash", ""),
                    (1 if metrics["escalation_judge"] else 0) if metrics.get("escalation_judge") is not None else None,
                    1 if metrics.get("escalation_disagreement") else 0,
                    str(metrics.get("escalation_method", "regex")),
                    rec["id"],
                ),
            )
            conn.commit()
            judged_done += 1
            log_event(
                "СУДДЯ [ОЦІНЕНО]",
                f"[{judged_done}/{total_to_judge}] Замір #{rec['id']} ({rec['query_code']} | {rec['model_id']}) -> Рубрика={metrics['nist_composite_pct']}%, Q_det={metrics['integral_efficiency_pct']}%",
                level="EVAL",
            )
            await asyncio.sleep(1.0)
    finally:
        clear_active_prompt()
        conn.close()

    analytics = compute_run_analytics(run_id)
    conn2 = get_connection()
    conn2.execute(
        "UPDATE experiment_runs SET summary_json = ? WHERE id = ?",
        (json.dumps(analytics.get("overall", {}), ensure_ascii=False), run_id),
    )
    conn2.commit()
    conn2.close()
    reset_runtime_status(f"Дооцінювання серії #{run_id} завершено: оцінено {judged_done}/{total_to_judge} замірів")
    return analytics


def start_judge_experiment_task(
    run_id: int,
    judge_model_id: str,
    only_unjudged: bool = True,
    allow_self_judge: bool = False,
) -> Dict[str, Any]:
    _register_background_task(
        f"judge_run_{run_id}",
        evaluate_run_with_judge(
            run_id=run_id,
            judge_model_id=judge_model_id,
            only_unjudged=only_unjudged,
            allow_self_judge=allow_self_judge,
        ),
    )
    return {
        "status": "running",
        "background": True,
        "run_id": run_id,
        "judge_model_id": judge_model_id,
        "allow_self_judge": allow_self_judge,
    }
