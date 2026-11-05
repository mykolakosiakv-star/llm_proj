"""Модульні та інтеграційні тести ядра LLM-AutoBench."""

import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# Каталог даних підміняється до імпорту застосунку: тести працюють на чистій базі, а не на робочій
os.environ["LLM_AUTOBENCH_DATA"] = tempfile.mkdtemp(prefix="autobench_tests_")

from app.main import app
import app.database as db_module
from app.database import (
    init_db,
    get_connection,
    fetch_all_queries,
    fetch_all_prompts,
    fetch_all_domains,
    ensure_sandbox_run_id,
    log_run_error,
    fetch_run_errors,
)
from app.seed_data import seed_database_if_needed
from app.services.classifier import (
    normalize_numbers,
    tokenize_uk,
    jaccard_stem_similarity,
    classify_and_ground_query,
)
from app.services.retrieval import (
    retrieve_knowledge_chunks,
    resolve_experiment_context,
)
from app.services.prompt_builder import select_few_shot_examples, build_prompt_messages
from app.services.evaluator import (
    parse_contract_output,
    compute_fact_coverage,
    detect_hallucinations,
    compute_lexical_similarity_metrics,
    evaluate_format_checklist,
    detect_escalation_from_answer,
    evaluate_response_comprehensive,
)
from app.services.experiment_runner import _generate_with_contract_retry
from app.services.judge import (
    load_rubric_metadata,
    verify_judge_policy,
    evaluate_with_rubric_judge,
)
from app.services.stats import (
    describe_sample,
    compare_paired_conditions,
    compute_escalation_confusion_matrix,
    compute_repeat_stability,
    compute_weight_sensitivity_analysis,
    compute_pareto_fronts,
)
from scripts.audit_dataset import run_dataset_audit


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    init_db()
    seed_database_if_needed()
    # Модель знайдена колись автопошуком, у коді каталогу її немає: тести судді потребують її родини
    from app.database import upsert_model_registry_entry
    upsert_model_registry_entry({"id": "gpt-5.4-nano", "provider": "openai", "model_id": "gpt-5.4-nano", "display_name": "gpt-5.4-nano", "source": "discovered"})


def test_normalize_numbers_and_single_digit_tokens():
    assert "100000" in normalize_numbers("Ліміт становить 100 000 грн")
    assert "150000" in normalize_numbers("Сума до 150 тис. грн")
    assert "14.5%" in normalize_numbers("Ставка 14,5 % річних")
    assert "0 грн" in normalize_numbers("Вартість 0,00 грн")
    assert "0%" in normalize_numbers("Комісія нуль відсотків")
    assert "1-3" in normalize_numbers("Термін 1–3 робочі дні")

    tokens = tokenize_uk("Комісія 0% на 3 групи та 5% податку")
    assert "0%" in tokens
    assert "3" in tokens
    assert "5%" in tokens


def test_classifier_jaccard_bounds_and_unknown_category():
    s1 = ["картка", "ліміт", "зняття"]
    s2 = ["картка", "ліміт", "готівка"]
    sim = jaccard_stem_similarity(s1, s2)
    assert 0.0 <= sim <= 1.0
    assert round(sim, 2) == 0.50

    res = classify_and_ground_query("Яка сьогодні погода на Марсі біля кратера Єзеро?")
    assert 0.0 <= res["category_confidence"] <= 1.0
    assert res["detected_category"] == "unknown"


def test_dataset_v2_passes_100_percent_audit():
    exit_code = run_dataset_audit()
    assert exit_code == 0


def test_expanded_dataset_draft_v3_passes_audit():
    draft_path = Path(__file__).resolve().parent / "data" / "proposed" / "expanded_queries_draft.json"
    assert draft_path.exists()
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert payload["total_queries"] >= 100
    assert payload["escalation_share_pct"] >= 25.0
    assert payload["audit_100pct_passed"] is True
    esc_by_cat = payload["escalation_by_category"]
    assert esc_by_cat.get("informational", 0) >= 2
    assert esc_by_cat.get("procedural", 0) >= 2
    assert esc_by_cat.get("troubleshooting", 0) >= 6
    assert esc_by_cat.get("clarification", 0) >= 2


def test_few_shot_never_leaks_current_query():
    queries = fetch_all_queries(category="informational", domain="fintech")

    for target_q in queries[:4]:
        fs_text, selected_codes = select_few_shot_examples(
            category="informational",
            exclude_query_code=target_q["code"],
            current_user_query=target_q["user_query"],
            limit=2,
            domain="fintech",
        )
        assert len(selected_codes) == 2
        assert target_q["code"] not in selected_codes
        assert target_q["reference_answer"] not in fs_text

        built = build_prompt_messages(
            prompt_code="P4",
            user_query=target_q["user_query"],
            context_fact=target_q["context_fact"],
            category_code="informational",
            category_name_uk="Інформаційні запити",
            exclude_query_code=target_q["code"],
            domain="fintech",
        )
        assert target_q["code"] not in built["few_shot_used"]
        assert len(built["few_shot_used"]) == 2
        assert target_q["reference_answer"] not in built["full_rendered_prompt"]


def test_synonym_group_fact_coverage():
    req_kw = [
        "100 000 грн",
        {"id": "увімкнути", "any_of": ["увімкн", "актив", "ввімкн"]},
        "0%",
    ]
    ans = "Ви можете активувати послугу безкоштовно (комісія нуль відсотків) з лімітом до 100000 грн."
    fc, matched, missing = compute_fact_coverage(ans, req_kw)
    assert fc == 100.0
    assert len(missing) == 0
    assert "увімкнути" in matched


def test_hallucination_detector():
    ctx = "Тариф Business коштує 690 грн/міс. Комісія за SWIFT — 0,5% (мінімум 15 USD). Меню «Налаштування» -> «Безпека»."
    clean_ans = "1) Перейдіть у меню «Налаштування» -> «Безпека». 2) Тариф коштує 690 грн, а комісія SWIFT становить 0.5% (мінімум 15 USD)."
    res_clean = detect_hallucinations(clean_ans, ctx)
    assert res_clean["has_hallucination"] is False
    assert res_clean["hallucination_count"] == 0

    halluc_ans = "У меню «Секретний розділ» ви можете підключити тариф за 999 грн під 7.5% річних."
    res_halluc = detect_hallucinations(halluc_ans, ctx)
    assert res_halluc["has_hallucination"] is True
    assert res_halluc["hallucination_count"] >= 2


def test_semantic_similarity_scale():
    ref = "Відкриття віртуальної картки є безкоштовним (0 грн), ліміт переказів становить 100 000 грн."
    identical = compute_lexical_similarity_metrics(ref, ref)
    assert identical["semantic_similarity_pct"] == 100.0

    partial = "Відкриття віртуальної картки безкоштовне, але деталі лімітів дивіться в умовах."
    sim_partial = compute_lexical_similarity_metrics(partial, ref)
    expected_pure = round(0.55 * sim_partial["stem_cosine_pct"] + 0.45 * sim_partial["rouge_l_f1_pct"], 1)
    assert abs(sim_partial["semantic_similarity_pct"] - expected_pure) <= 0.1
    assert 0.0 < sim_partial["semantic_similarity_pct"] < 90.0


def test_unified_format_reliability_checklist():
    score_empty, _ = evaluate_format_checklist("", "P1", contract_ok=False)
    assert score_empty == 0.0

    clean_uk = "Максимальний добовий ліміт зняття готівки в Україні становить 100 000 грн."
    score_ok, checks_ok = evaluate_format_checklist(clean_uk, "P5", contract_ok=True)
    assert score_ok == 100.0
    assert checks_ok["contract_ok"] is True
    assert checks_ok["ukrainian_language"] is True
    assert checks_ok["length_in_bounds"] is True
    assert checks_ok["no_service_tag_leak"] is True

    leaked = "Максимальний добовий ліміт становить 100 000 грн. ```json {\"escalate\": false} ```"
    score_leak, checks_leak = evaluate_format_checklist(leaked, "P4", contract_ok=False)
    assert checks_leak["contract_ok"] is False
    assert checks_leak["no_service_tag_leak"] is False
    assert score_leak == 50.0


def test_escalation_parser_markdown_and_negation_resilience():
    assert detect_escalation_from_answer("**Ескалація**: **ТАК** — звернення передано оператору.") is True
    assert detect_escalation_from_answer("`Ескалація: НІ` Це типовий запит, який не потребує ескалації на оператора.") is False
    assert detect_escalation_from_answer("Цей інформаційний запит не потребує ескалації фахівцю.") is False
    assert detect_escalation_from_answer("Ситуація критична і потребує ескалації на профільного оператора безпеки.") is True


def test_orthogonal_composite_quality_and_non_circular_automation():
    json_ans = json.dumps({
        "answer": "Відкриття картки безкоштовне (0 грн), комісія 0%, ліміт 100 000 грн на місяць.",
        "escalate": False,
        "reason": "Типовий інформаційний запит",
    }, ensure_ascii=False)
    ev_fast = evaluate_response_comprehensive(
        generated_answer=json_ans,
        reference_answer="Відкриття картки безкоштовне (0 грн), комісія 0%, ліміт 100 000 грн на місяць.",
        required_keywords=["0 грн", "безкоштовн", "100 000 грн", "0%"],
        expected_escalation=False,
        prompt_code="P1",
        latency_ms=100.0,
        context_fact="Відкриття картки безкоштовне (0 грн), комісія 0%, ліміт 100 000 грн на місяць.",
    )
    ev_slow = evaluate_response_comprehensive(
        generated_answer=json_ans,
        reference_answer="Відкриття картки безкоштовне (0 грн), комісія 0%, ліміт 100 000 грн на місяць.",
        required_keywords=["0 грн", "безкоштовн", "100 000 грн", "0%"],
        expected_escalation=False,
        prompt_code="P1",
        latency_ms=9500.0,
        context_fact="Відкриття картки безкоштовне (0 грн), комісія 0%, ліміт 100 000 грн на місяць.",
    )
    assert ev_fast["integral_efficiency_pct"] == ev_slow["integral_efficiency_pct"]
    assert ev_fast["automation_success"] is True
    assert ev_fast["contract_ok"] == 1
    assert ev_fast["parse_method"] == "json"
    assert ev_fast["rubric_evaluated"] is False


def test_contract_parser_and_single_retry():
    # 1. Valid direct JSON
    valid_raw = '{"answer": "Тариф становить 0 грн.", "escalate": false, "reason": "Довідковий запит"}'
    p1 = parse_contract_output(valid_raw)
    assert p1["contract_ok"] is True
    assert p1["parse_method"] == "json"
    assert p1["answer"] == "Тариф становить 0 грн."
    assert p1["escalate"] is False
    assert p1["reason"] == "Довідковий запит"

    # 2. JSON inside markdown fence
    fenced_raw = 'Ось відповідь:\n```json\n{"answer": "Передаю оператору безпеки.", "escalate": true, "reason": "Підозра на шахрайство"}\n```'
    p2 = parse_contract_output(fenced_raw)
    assert p2["contract_ok"] is True
    assert p2["parse_method"] == "json"
    assert p2["escalate"] is True

    # 3. Broken JSON falls back to regex
    broken_raw = "На жаль, це критична ситуація. Ескалація: ТАК. Потрібна ручна перевірка."
    p3 = parse_contract_output(broken_raw)
    assert p3["contract_ok"] is False
    assert p3["parse_method"] == "fallback_regex"
    assert p3["escalate"] is True

    # 4. _generate_with_contract_retry performs 1 retry when first response is not valid JSON
    first_call = {
        "generated_answer": "Просто текст без JSON формату.",
        "input_tokens": 50,
        "output_tokens": 10,
        "thinking_tokens": 0,
        "latency_ms": 100.0,
        "call_ms": 100.0,
        "wall_ms": 100.0,
        "cost_usd": 0.0001,
        "attempts": 1,
    }
    second_call = {
        "generated_answer": '{"answer": "Виправлена відповідь за контрактом.", "escalate": false, "reason": "Автоматично"}',
        "input_tokens": 70,
        "output_tokens": 20,
        "thinking_tokens": 0,
        "latency_ms": 110.0,
        "call_ms": 110.0,
        "wall_ms": 110.0,
        "cost_usd": 0.0002,
        "attempts": 1,
    }

    async def _run_retry():
        with patch(
            "app.services.experiment_runner.execute_llm_request",
            new=AsyncMock(side_effect=[first_call, second_call]),
        ) as mocked_llm:
            gen, parsed = await _generate_with_contract_retry(
                model_id="gemini-3.1-flash-lite",
                prompt_code="P1",
                system_prompt="Система",
                user_prompt="Тест",
                temperature=0.0,
                max_output_tokens=256,
                seed=42,
            )
            assert mocked_llm.call_count == 2
            assert parsed["contract_ok"] is True
            assert parsed["parse_method"] == "retry_json"
            assert parsed["answer"] == "Виправлена відповідь за контрактом."
            assert gen["input_tokens"] == 120
            assert gen["output_tokens"] == 30

    asyncio.run(_run_retry())


def test_bm25_retrieval_hit_at_1_and_3_and_no_reference_leak():
    queries = fetch_all_queries(domain="fintech")
    assert len(queries) > 0
    sample_q = queries[0]

    ret = retrieve_knowledge_chunks(
        user_query=sample_q["user_query"],
        domain="fintech",
        top_k=3,
        gold_query_code=sample_q["code"],
    )
    assert len(ret["retrieved_chunks"]) == 3
    assert len(ret["retrieved_ids"]) == 3
    assert ret["hit_at_1"] in (0, 1)
    assert ret["hit_at_3"] == 1
    assert sample_q["code"] in ret["retrieved_ids"]
    for chunk in ret["retrieved_chunks"]:
        assert "reference_answer" not in chunk
        assert chunk["context_fact"]


def test_three_context_modes_resolution():
    queries = fetch_all_queries(domain="fintech")
    sample_q = queries[0]

    c_none = resolve_experiment_context(
        query_item=sample_q,
        context_mode="no_context",
        domain="fintech",
        top_k=3,
    )
    assert c_none["context_mode"] == "no_context"
    assert c_none["gold_context_fact"] == sample_q["context_fact"]
    assert c_none["retrieved_ids"] == []
    assert c_none["retrieval_hit_at_1"] is None
    assert c_none["retrieval_hit_at_3"] is None

    c_gold = resolve_experiment_context(
        query_item=sample_q,
        context_mode="gold_context",
        domain="fintech",
        top_k=3,
    )
    assert c_gold["context_mode"] == "gold_context"
    assert c_gold["prompt_context"] == sample_q["context_fact"]
    assert c_gold["retrieval_hit_at_1"] == 1
    assert c_gold["retrieval_hit_at_3"] == 1

    c_ret = resolve_experiment_context(
        query_item=sample_q,
        context_mode="retrieved_context",
        domain="fintech",
        top_k=3,
    )
    assert c_ret["context_mode"] == "retrieved_context"
    assert len(c_ret["retrieved_ids"]) == 3
    assert c_ret["retrieval_hit_at_3"] == 1
    assert c_ret["prompt_context"] != ""


def test_additive_db_migration_on_temp_sqlite(tmp_path):
    temp_db = tmp_path / "legacy_bench.db"
    conn = sqlite3.connect(str(temp_db))
    conn.executescript(
        """
        CREATE TABLE experiment_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            completed_items INTEGER NOT NULL DEFAULT 1,
            total_items INTEGER NOT NULL DEFAULT 1,
            execution_mode TEXT NOT NULL DEFAULT 'batch'
        );
        INSERT INTO experiment_runs (run_name, status, completed_items, total_items, execution_mode)
        VALUES ('Legacy Run 1', 'completed', 1, 1, 'batch');

        CREATE TABLE evaluation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            query_id INTEGER NOT NULL,
            category TEXT NOT NULL DEFAULT 'informational',
            model_id TEXT NOT NULL,
            prompt_code TEXT NOT NULL,
            generated_answer TEXT NOT NULL
        );
        INSERT INTO evaluation_results (run_id, query_id, category, model_id, prompt_code, generated_answer)
        VALUES (1, 1, 'informational', 'legacy-model', 'P1', 'Стара відповідь');
        """
    )
    conn.commit()
    conn.close()

    with patch.object(db_module, "DB_PATH", temp_db):
        db_module.init_db()
        verify_conn = sqlite3.connect(str(temp_db))
        verify_conn.row_factory = sqlite3.Row
        row = verify_conn.execute("SELECT * FROM evaluation_results WHERE id = 1").fetchone()
        assert row is not None
        assert row["generated_answer"] == "Стара відповідь"
        assert row["context_mode"] == "gold_context"
        assert row["top_k"] == 3
        assert row["contract_ok"] == 1
        assert row["parse_method"] == "json"

        run_row = verify_conn.execute("SELECT * FROM experiment_runs WHERE id = 1").fetchone()
        assert run_row["run_name"] == "Legacy Run 1"
        assert run_row["context_mode"] == "gold_context"
        assert run_row["top_k"] == 3

        domains_tbl = verify_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='domains'"
        ).fetchone()
        assert domains_tbl is not None
        verify_conn.close()


def test_domain_parameterization_and_no_hardcoded_fincloud():
    root_dir = Path(__file__).resolve().parent
    rubric_text = (root_dir / "rubric_v1.yaml").read_text(encoding="utf-8")
    judge_text = (root_dir / "app" / "services" / "judge.py").read_text(encoding="utf-8")
    assert "FinCloud" not in rubric_text
    assert "FinCloud" not in judge_text

    expected_companies = {
        "fintech": "FinCloud",
        "it_helpdesk": "CloudOps",
        "ecommerce": "MarketHub",
        "telecom": "TelecomNet",
    }
    for domain_code, company_name in expected_companies.items():
        built = build_prompt_messages(
            prompt_code="P2",
            user_query="Як перевірити статус заявки?",
            context_fact="Статус перевіряється в особистому кабінеті.",
            category_code="informational",
            category_name_uk="Інформаційні запити",
            domain=domain_code,
        )
        assert company_name in built["system_prompt"]
        assert "{company}" not in built["system_prompt"]
        assert "{domain_name}" not in built["system_prompt"]
        assert "{escalation_rules}" not in built["system_prompt"]

    client = TestClient(app)
    r_dom = client.get("/api/domains")
    assert r_dom.status_code == 200
    dom_codes = {d["code"] for d in r_dom.json()["items"]}
    assert {"fintech", "it_helpdesk", "ecommerce", "telecom"}.issubset(dom_codes)


def test_blind_judge_cross_family_policy_and_mock_cache():
    same_fam = verify_judge_policy("gemini-2.5-flash", "gemini-3.1-flash-lite")
    assert same_fam["is_independent"] is False

    cross_fam = verify_judge_policy("llama-3.3-70b-versatile", "gemini-3.1-flash-lite")
    assert cross_fam["is_independent"] is True

    mock_llm_return = {
        "generated_answer": json.dumps({
            "scores": {
                "relevance": 5,
                "factuality": 5,
                "completeness": 4,
                "clarity": 5,
                "policy_adherence": 5,
            },
            "rationale": {
                "relevance": "Повністю відповідає запиту",
                "factuality": "Точні факти з контексту",
                "completeness": "Всі основні деталі наведено",
                "clarity": "Чітка українська мова",
                "policy_adherence": "Дотримано правил",
            },
            "escalation_decision": False,
        }, ensure_ascii=False),
        "latency_ms": 120.0,
        "call_ms": 120.0,
        "wall_ms": 120.0,
    }

    unique_ans = f"Картка є безкоштовною (0 грн). Тест {uuid4().hex[:8]}"

    async def _run():
        with patch("app.services.judge.execute_llm_request", new=AsyncMock(return_value=mock_llm_return)) as mocked_call:
            res1 = await evaluate_with_rubric_judge(
                user_query="Яка вартість картки?",
                context_fact="Картка безкоштовна (0 грн).",
                reference_answer="Картка безкоштовна (0 грн).",
                expected_escalation=False,
                generated_answer=unique_ans,
                evaluated_model_id="gemini-3.1-flash-lite",
                judge_model_id="llama-3.3-70b-versatile",
            )
            assert res1["evaluated"] is True
            assert res1["rubric_composite_pct"] == 95.0
            assert res1["from_cache"] is False

            res2 = await evaluate_with_rubric_judge(
                user_query="Яка вартість картки?",
                context_fact="Картка безкоштовна (0 грн).",
                reference_answer="Картка безкоштовна (0 грн).",
                expected_escalation=False,
                generated_answer=unique_ans,
                evaluated_model_id="gemini-3.1-flash-lite",
                judge_model_id="llama-3.3-70b-versatile",
            )
            assert res2["from_cache"] is True
            assert mocked_call.call_count == 1

    asyncio.run(_run())


def test_sample_sd_bessel_correction_and_bootstrap_ci():
    vals = [70.0, 80.0, 90.0]
    desc = describe_sample(vals)
    assert desc["n"] == 3
    assert desc["mean"] == 80.0
    assert desc["median"] == 80.0
    assert desc["sd"] == 10.0
    assert desc["insufficient_n"] is True
    assert desc["ci95_low"] <= 80.0 <= desc["ci95_high"]


def test_wilcoxon_friedman_holm_and_cliffs_delta():
    rows = []
    for i in range(1, 15):
        rows.append({"query_code": f"Q-{i}", "model_id": "Model_A", "fact_coverage_pct": 90.0 + (i % 3)})
        rows.append({"query_code": f"Q-{i}", "model_id": "Model_B", "fact_coverage_pct": 60.0 + (i % 3)})
        rows.append({"query_code": f"Q-{i}", "model_id": "Model_C", "fact_coverage_pct": 75.0 + (i % 3)})

    comp = compare_paired_conditions(rows, group_key="model_id", metric_key="fact_coverage_pct")
    assert comp["omnibus_test"] is not None
    assert comp["omnibus_test"]["p_value"] < 0.05
    assert len(comp["pairwise"]) == 3
    pair_ab = next(p for p in comp["pairwise"] if p["group_a"] == "Model_A" and p["group_b"] == "Model_B")
    assert pair_ab["cliffs_delta"] == 1.0
    assert pair_ab["p_holm"] < 0.05


def test_escalation_confusion_matrix_and_repeat_stability():
    records = [
        {"query_code": "TRB-03", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 1, "expected_escalation": True, "requires_escalation": True, "fact_coverage_pct": 90.0, "integral_efficiency_pct": 90.0},
        {"query_code": "TRB-04", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 1, "expected_escalation": True, "requires_escalation": False, "fact_coverage_pct": 60.0, "integral_efficiency_pct": 60.0},
        {"query_code": "INF-01", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 1, "expected_escalation": False, "requires_escalation": False, "fact_coverage_pct": 95.0, "integral_efficiency_pct": 95.0},
        {"query_code": "INF-02", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 1, "expected_escalation": False, "requires_escalation": True, "fact_coverage_pct": 70.0, "integral_efficiency_pct": 70.0},
    ]
    mat = compute_escalation_confusion_matrix(records)
    assert mat["tp"] == 1
    assert mat["fn"] == 1
    assert mat["tn"] == 1
    assert mat["fp"] == 1
    assert mat["critical_fn_rate_pct"] == 50.0

    stab_single = compute_repeat_stability(records)
    assert stab_single["available"] is False

    records_multi = records + [
        {"query_code": "TRB-03", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 2, "expected_escalation": True, "requires_escalation": True, "fact_coverage_pct": 88.0, "integral_efficiency_pct": 88.0},
        {"query_code": "INF-01", "model_id": "m1", "prompt_code": "P5", "context_mode": "gold_context", "repeat_index": 2, "expected_escalation": False, "requires_escalation": False, "fact_coverage_pct": 94.0, "integral_efficiency_pct": 94.0},
    ]
    stab_multi = compute_repeat_stability(records_multi)
    assert stab_multi["available"] is True
    assert stab_multi["mean_within_sd"] is not None


def test_fastapi_endpoints_and_methodology_single_source_of_truth():
    client = TestClient(app)

    r_meth = client.get("/api/methodology")
    assert r_meth.status_code == 200
    meth_data = r_meth.json()
    assert meth_data["methodology_version"] == "2.1"
    assert len(meth_data["metrics_v2"]) >= 6

    r_exp = client.get("/api/experiments")
    assert r_exp.status_code == 200
    runs = r_exp.json()["runs"]
    assert all(r.get("execution_mode") != "sandbox" for r in runs)

    if runs:
        run_id = runs[0]["id"]
        r_flex = client.post(
            "/api/analytics/flexible",
            json={"run_ids": [run_id], "balanced_only": True},
        )
        assert r_flex.status_code == 200
        flex_data = r_flex.json()
        assert "overall" in flex_data
        assert "statistical_tests" in flex_data
        assert "pareto_fronts" in flex_data
        assert "weight_sensitivity" in flex_data
        assert "by_context_mode" in flex_data
        assert "context_mode_comparison" in flex_data

    r_prompt = client.get("/api/experiments/active-prompt")
    assert r_prompt.status_code == 200

    r_set = client.put("/api/settings", json={"model_routes": {"groq:qwen-3.8-27b": "native"}})
    assert r_set.status_code == 200
    assert "model_routes" in r_set.json()


def test_stage2_classify_all_9_model_statuses_and_text_filter():
    from app.services.model_discovery import (
        STATUS_LABELS_UK,
        classify_api_response,
        is_text_generation_model,
    )

    assert len(STATUS_LABELS_UK) == 9
    assert STATUS_LABELS_UK["ok"] == "Працює"
    assert STATUS_LABELS_UK["offline"] == "Немає зв'язку"
    assert STATUS_LABELS_UK["rate_limited"] == "Ліміт запитів"
    assert STATUS_LABELS_UK["daily_quota"] == "Денний ліміт вичерпано"
    assert STATUS_LABELS_UK["no_credit"] == "Нульовий баланс"
    assert STATUS_LABELS_UK["invalid_key"] == "Невірний ключ"
    assert STATUS_LABELS_UK["no_key"] == "Ключ не задано"
    assert STATUS_LABELS_UK["model_missing"] == "Модель недоступна"
    assert STATUS_LABELS_UK["unchecked"] == "Не перевірено"

    assert classify_api_response("openai", 200, {"choices": []})[0] == "ok"
    assert classify_api_response("ollama", None, is_network_error=True)[0] == "offline"
    assert classify_api_response(
        "openai",
        429,
        {"error": {"code": "insufficient_quota", "type": "insufficient_quota", "message": "You exceeded your current quota, check billing"}},
    )[0] == "no_credit"
    assert classify_api_response(
        "anthropic",
        400,
        {"error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}},
    )[0] == "no_credit"
    assert classify_api_response(
        "openrouter",
        402,
        {"error": {"code": 402, "message": "Insufficient credits"}},
    )[0] == "no_credit"
    assert classify_api_response(
        "gemini",
        429,
        {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel"}},
    )[0] == "daily_quota"
    assert classify_api_response(
        "groq",
        429,
        {"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached for requests per minute (RPM). Please try again in 4.2s"}},
    )[0] == "rate_limited"
    assert classify_api_response(
        "openai",
        401,
        {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}},
    )[0] == "invalid_key"
    assert classify_api_response(
        "openai",
        404,
        {"error": {"code": "model_not_found", "message": "The model `gpt-99` does not exist"}},
    )[0] == "model_missing"

    assert is_text_generation_model("gpt-4o-mini") is True
    assert is_text_generation_model("text-embedding-3-small") is False
    assert is_text_generation_model("whisper-1") is False
    assert is_text_generation_model("tts-1-hd") is False
    assert is_text_generation_model("omni-moderation-latest") is False


def test_stage2_scheduler_backoff_retry_after_and_paused_quota():
    from app.services.llm_runtime import REQUEST_SCHEDULER, DailyQuotaExceededError
    from app.database import get_connection, delete_custom_model_from_registry, upsert_model_registry_entry

    d_retry = REQUEST_SCHEDULER.compute_backoff_delay(1, headers={"Retry-After": "6"}, raw_text="")
    assert 6.0 <= d_retry <= 7.2

    d1 = REQUEST_SCHEDULER.compute_backoff_delay(1, headers={}, raw_text="", base_delay=2.0)
    d2 = REQUEST_SCHEDULER.compute_backoff_delay(2, headers={}, raw_text="", base_delay=2.0)
    assert 2.2 <= d1 <= 3.1
    assert 4.4 <= d2 <= 6.2

    test_model = "custom:quota-test-model"
    upsert_model_registry_entry(
        {
            "id": test_model,
            "provider": "custom",
            "model_id": "quota-test-model",
            "display_name": "Quota Test",
            "source": "custom",
            "enabled": False,
            "limits": {"rpd": 1},
        }
    )

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM model_daily_usage WHERE model_key = ?", (test_model,))
    cur.execute(
        """
        INSERT INTO experiment_runs (created_at, title, description, models_json, prompts_json, categories_json, total_items, completed_items, status, execution_mode, summary_json)
        VALUES ('2026-11-05 12:00:00', 'Тест паузи квоти', '', '[]', '[]', '[]', 10, 3, 'running', 'live_api', '{}')
        """
    )
    temp_run_id = cur.lastrowid
    conn.commit()
    conn.close()

    try:
        cnt = REQUEST_SCHEDULER.check_and_reserve_rpd(test_model, rpd_limit=1)
        assert cnt == 1
        try:
            REQUEST_SCHEDULER.check_and_reserve_rpd(test_model, rpd_limit=1)
            assert False, "Мав бути піднятий DailyQuotaExceededError"
        except DailyQuotaExceededError:
            pass

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status, summary_json FROM experiment_runs WHERE id = ?", (temp_run_id,))
        row = dict(cur.fetchone())
        conn.close()
        assert row["status"] == "paused_quota"
        summary = json.loads(row["summary_json"] or "{}")
        assert summary.get("quota_paused") is True
        assert summary.get("quota_model_id") == test_model
    finally:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM experiment_runs WHERE id = ?", (temp_run_id,))
        cur.execute("DELETE FROM model_daily_usage WHERE model_key = ?", (test_model,))
        conn.commit()
        conn.close()
        delete_custom_model_from_registry(test_model)


def test_stage2_custom_model_add_and_mock_connection():
    client = TestClient(app)

    import httpx

    # Підміняється лише мережевий рівень: сама перевірка підключення виконується по-справжньому
    http_stub = AsyncMock(return_value=httpx.Response(200, json={"choices": []}))
    with patch("httpx.AsyncClient.post", new=http_stub):
        r_test = client.post(
            "/api/models/custom/test",
            json={
                "base_url": "http://localhost:8080/v1",
                "model_id": "local-vllm-7b",
                "api_key": "sk-test-local",
            },
        )
        assert r_test.status_code == 200
        assert r_test.json()["status"] == "ok"
        sent_url = http_stub.call_args.args[0]
        assert sent_url == "http://localhost:8080/v1/chat/completions"
        assert http_stub.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test-local"

        r_add = client.post(
            "/api/models/custom",
            json={
                "provider": "custom",
                "base_url": "http://localhost:8080/v1",
                "model_id": "local-vllm-7b",
                "display_name": "Local vLLM 7B",
                "input_price_per_1m": None,
                "output_price_per_1m": None,
                "context_window": 32768,
                "rpm": 60,
                "rpd": 2000,
                "enabled": False,
            },
        )
        assert r_add.status_code == 200
        added = r_add.json()["model"]
        assert added["id"] == "custom:local-vllm-7b"
        assert added["price_source"] == "unknown"

        r_est = client.post(
            "/api/experiments/estimate",
            json={
                "domain": "fintech",
                "model_ids": ["custom:local-vllm-7b", "gemini-3.1-flash-lite"],
                "prompt_codes": ["P1", "P3"],
                "categories": ["informational"],
                "max_queries_per_category": 2,
                "repeats": 1,
                "use_judge": True,
                "judge_model_id": "gemini-3.1-flash-lite",
            },
        )
        assert r_est.status_code == 200
        est_data = r_est.json()
        assert est_data["total_generation_calls"] == 8
        assert est_data["total_judge_calls"] == 8
        assert est_data["has_unknown_price"] is True
        custom_est = next(m for m in est_data["models"] if m["model_id"] == "custom:local-vllm-7b")
        assert custom_est["cost_label"] == "ціна не задана"

        r_upd = client.put(
            "/api/models/custom:local-vllm-7b",
            json={"input_price_per_1m": 0.15, "output_price_per_1m": 0.45},
        )
        assert r_upd.status_code == 200
        assert r_upd.json()["model"]["price_source"] == "manual"

        r_del = client.delete("/api/models/custom:local-vllm-7b")
        assert r_del.status_code == 200


def test_stage3_judge_resilience_empty_truncated_and_valid_json():
    from app.services.judge import evaluate_with_judge

    valid_json_text = json.dumps(
        {
            "relevance": 5,
            "completeness": 4,
            "groundedness": 5,
            "clarity": 5,
            "actionability": 4,
            "rationale": "Коректна і повна відповідь.",
        },
        ensure_ascii=False,
    )

    async def _run():
        # 1. Empty response on attempt 1 -> retry with 4096 tokens -> valid JSON on attempt 2
        empty_then_valid = [
            {"response_text": "", "finish_reason": "STOP", "input_tokens": 120, "output_tokens": 0, "latency_ms": 150.0, "cost_usd": 0.0001},
            {"response_text": valid_json_text, "finish_reason": "STOP", "input_tokens": 120, "output_tokens": 80, "latency_ms": 210.0, "cost_usd": 0.0002},
        ]
        call_kwargs_log = []

        async def fake_exec_1(*args, **kwargs):
            call_kwargs_log.append(kwargs)
            return empty_then_valid[len(call_kwargs_log) - 1]

        with patch("app.services.judge.execute_llm_request", side_effect=fake_exec_1):
            res1 = await evaluate_with_judge(
                query_text="Як змінити ПІН?",
                reference_answer="У застосунку: Картка -> Налаштування -> Змінити ПІН.",
                expected_facts=["Картка", "Змінити ПІН"],
                retrieved_context="Картка -> Налаштування -> Змінити ПІН.",
                generated_answer=f"Перейдіть у Картка -> Налаштування -> Змінити ПІН. {uuid4().hex[:6]}",
                judge_model_id="gemini-3.1-flash-lite",
                evaluated_model_id="gpt-5.4-nano",
            )
        assert res1["evaluated"] is True
        assert res1["attempt_count"] == 2
        assert call_kwargs_log[1]["max_output_tokens"] == 4096
        assert call_kwargs_log[1]["json_mode"] is True

        # 2. Truncated response (finish_reason=MAX_TOKENS) -> retry -> valid JSON
        trunc_then_valid = [
            {"response_text": '{"relevance": 5, "completeness": 4, "ground', "finish_reason": "MAX_TOKENS", "input_tokens": 120, "output_tokens": 1000, "latency_ms": 300.0, "cost_usd": 0.0002},
            {"response_text": valid_json_text, "finish_reason": "STOP", "input_tokens": 120, "output_tokens": 85, "latency_ms": 220.0, "cost_usd": 0.0002},
        ]
        idx2 = 0

        async def fake_exec_2(*args, **kwargs):
            nonlocal idx2
            r = trunc_then_valid[idx2]
            idx2 += 1
            return r

        with patch("app.services.judge.execute_llm_request", side_effect=fake_exec_2):
            res2 = await evaluate_with_judge(
                query_text="Як змінити ПІН?",
                reference_answer="У застосунку.",
                expected_facts=["Картка"],
                retrieved_context="Контекст.",
                generated_answer=f"Відповідь {uuid4().hex[:6]}",
                judge_model_id="gemini-3.1-flash-lite",
                evaluated_model_id="gpt-5.4-nano",
            )
        assert res2["evaluated"] is True
        assert res2["weighted_score_pct"] > 80.0

        # 3. Both attempts return empty response -> non-empty error with finish_reason and len
        always_empty = [
            {"response_text": "", "finish_reason": "MAX_TOKENS", "input_tokens": 120, "output_tokens": 1000, "latency_ms": 300.0, "cost_usd": 0.0002},
            {"response_text": "   ", "finish_reason": "MAX_TOKENS", "input_tokens": 120, "output_tokens": 4096, "latency_ms": 500.0, "cost_usd": 0.0005},
        ]
        idx3 = 0

        async def fake_exec_3(*args, **kwargs):
            nonlocal idx3
            r = always_empty[idx3]
            idx3 += 1
            return r

        with patch("app.services.judge.execute_llm_request", side_effect=fake_exec_3):
            res3 = await evaluate_with_judge(
                query_text="Як змінити ПІН?",
                reference_answer="У застосунку.",
                expected_facts=["Картка"],
                retrieved_context="Контекст.",
                generated_answer=f"Відповідь {uuid4().hex[:6]}",
                judge_model_id="gemini-3.1-flash-lite",
                evaluated_model_id="gpt-5.4-nano",
            )
        assert res3["evaluated"] is False
        assert res3["error"] and len(res3["error"].strip()) > 10
        assert "MAX_TOKENS" in res3["error"]
        assert "len=" in res3["error"]

    asyncio.run(_run())


def test_stage3_judge_independence_and_ceiling_effect():
    from app.services.judge import evaluate_with_judge
    from app.services.stats import compute_rubric_diagnostics

    async def _run():
        # Strict mode: same family (gemini judge evaluating gemini model) -> skipped without self-evaluation
        res_skip = await evaluate_with_judge(
            query_text="Тест",
            reference_answer="Еталон",
            expected_facts=["факт"],
            retrieved_context="Контекст",
            generated_answer="Відповідь",
            judge_model_id="gemini-3.1-flash-lite",
            evaluated_model_id="gemini-2.5-flash",
            allow_self_judge=False,
        )
        assert res_skip["evaluated"] is False
        assert res_skip["judge_status"] == "skipped_no_independent_judge"
        assert "не оцінено: немає незалежного судді" in res_skip["status_label"]

    asyncio.run(_run())

    # Ceiling effect & multi-judge dominance
    rows = [
        {"rubric_evaluated": 1, "rubric_composite_pct": 95.0, "judge_model": "judge-A", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 96.0, "judge_model": "judge-A", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 92.0, "judge_model": "judge-A", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 98.0, "judge_model": "judge-A", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 94.0, "judge_model": "judge-A", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 50.0, "judge_model": "judge-B", "self_judged": 0},
        {"rubric_evaluated": 0, "rubric_composite_pct": None, "judge_model": None, "judge_status": "skipped_no_independent_judge", "self_judged": 0},
        {"rubric_evaluated": 1, "rubric_composite_pct": 100.0, "judge_model": "judge-A", "self_judged": 1},
    ]
    diag = compute_rubric_diagnostics(rows, include_self_judged=False)
    assert diag["multiple_judges_detected"] is True
    assert diag["dominant_judge_model"] == "judge-A"
    assert diag["eligible_scored_count"] == 5
    assert diag["self_judged_count"] == 1
    assert diag["skipped_no_independent_judge_count"] == 1
    assert diag["ceiling_effect_detected"] is True


def test_stage3_honest_stats_ci_width_and_wilcoxon_min_pairs():
    from app.services.stats import (
        describe_sample,
        compare_paired_conditions,
        compute_weight_sensitivity_analysis,
        compute_metrics_correlation_matrix,
    )
    from app.services.analytics import _calc_group_metrics

    # Non-zero CI width containing the mean on a sample with spread
    sample = [62.0, 70.0, 75.0, 81.0, 84.0, 88.0, 91.0, 95.0]
    desc = describe_sample(sample)
    assert desc["ci95_low"] < desc["mean"] < desc["ci95_high"]
    assert (desc["ci95_high"] - desc["ci95_low"]) > 5.0

    # Group metrics repeats aggregation per query & non-zero CI in stats dict
    group_rows = [
        {"query_code": f"Q{i}", "repeat_index": r, "integral_efficiency_pct": 60.0 + i * 4.0, "fact_coverage_pct": 55.0 + i * 5.0, "semantic_similarity_pct": 70.0 + i * 3.0, "conciseness_pct": 85.0, "hallucination_flag": 0, "escalation_Triggered": 0, "expected_escalation": 0, "latency_ms": 400.0 + i * 20.0, "cost_usd": 0.0005, "model_id": "gemini-3.1-flash-lite"}
        for i in range(1, 9)
        for r in (1, 2)
    ]
    gm = _calc_group_metrics(group_rows)
    assert gm["query_units_count"] == 8
    assert gm["count"] == 8
    assert gm["raw_measurements_count"] == 16
    ci_low, ci_high = gm["efficiency_ci95"]
    assert ci_low < gm["integral_efficiency_pct"] < ci_high
    assert gm["stats"]["integral_efficiency_pct"]["ci95_low"] == ci_low
    assert gm["stats"]["integral_efficiency_pct"]["ci95_high"] == ci_high

    # Wilcoxon n < 6 -> insufficient_data, p_raw = None, p_holm = None
    small_rows = [
        {"query_code": f"Q{i}", "model_id": m, "prompt_code": "P1", "category": "informational", "integral_efficiency_pct": 80.0 + (5.0 if m == "M1" else 0.0)}
        for i in range(1, 5)
        for m in ("M1", "M2")
    ]
    comp_small = compare_paired_conditions(small_rows, group_field="model_id")
    assert comp_small["pairwise_comparisons"][0]["status"] == "insufficient_data"
    assert comp_small["pairwise_comparisons"][0]["p_raw"] is None
    assert comp_small["pairwise_comparisons"][0]["p_holm"] is None
    assert "недостатньо даних для критерію" in comp_small["pairwise_comparisons"][0]["status_label"]

    # Sensitivity analysis varies both (w_FC, w_SS) and hallucination penalty
    sens = compute_weight_sensitivity_analysis([
        {"model_id": "M1", "model_name": "Model 1", "fact_coverage_pct": 88.0, "semantic_similarity_pct": 82.0, "hallucination_rate_pct": 4.0},
        {"model_id": "M2", "model_name": "Model 2", "fact_coverage_pct": 75.0, "semantic_similarity_pct": 79.0, "hallucination_rate_pct": 12.0},
    ])
    assert sens["available"] is True
    assert sens["grid_points_tested"] == 8 * 6
    assert "penalty_grid" in sens

    # Spearman correlation matrix between metrics
    corr = compute_metrics_correlation_matrix(group_rows)
    assert corr["n_units"] == 8
    assert "matrix" in corr and corr["matrix"]["FC"]["SS"]["rho"] is not None


def test_stage3_manual_validation_dataset_import_and_stratified_sampling():
    from app.services.experiment_runner import select_stratified_queries
    from app.services.stats import weighted_cohens_kappa
    from scripts.audit_dataset import audit_domain_category_balance

    # Weighted Cohen's kappa check
    k_val = weighted_cohens_kappa([5, 4, 3, 5, 2, 4, 5], [5, 4, 3, 4, 2, 4, 5])
    assert k_val is not None and k_val > 0.8

    # Stratified sampling across domains
    mock_queries = [
        {"query_code": f"FIN_{i}", "domain": "fintech", "category": "informational" if i % 2 == 0 else "complaint"}
        for i in range(10)
    ] + [
        {"query_code": f"EC_{i}", "domain": "ecommerce", "category": "informational" if i % 2 == 0 else "complaint"}
        for i in range(20)
    ]
    sampled = select_stratified_queries(mock_queries, equal_domain_sample=True, seed=42)
    fin_c = sum(1 for q in sampled if q["domain"] == "fintech")
    ec_c = sum(1 for q in sampled if q["domain"] == "ecommerce")
    assert fin_c == ec_c == 10

    # Audit warning when domain < 30 queries
    balance = audit_domain_category_balance(mock_queries)
    assert len(balance["warnings"]) >= 2

    # Dataset import validation (duplicate query_code rejection)
    client = TestClient(app)
    r_dup = client.post(
        "/api/queries/import",
        json={
            "items": [
                {
                    "query_code": "DUP_TEST_01",
                    "domain": "fintech",
                    "category": "informational",
                    "user_query": "Як відкрити картку?",
                    "reference_answer": "Через застосунок.",
                    "expected_facts": ["застосунок"],
                    "source": "manual",
                    "review_status": "reviewed",
                },
                {
                    "query_code": "DUP_TEST_01",
                    "domain": "fintech",
                    "category": "informational",
                    "user_query": "Дубль",
                    "reference_answer": "Через застосунок.",
                    "expected_facts": ["застосунок"],
                },
            ]
        },
    )
    assert r_dup.status_code in (400, 422)




def _conclusion_model(model_id, q, fn, cost, price_known=True):
    return {
        "model_id": model_id, "model_name": model_id, "count": 40,
        "integral_efficiency_pct": q, "hallucination_rate_pct": 0.0,
        "escalation_matrix": {"fn_rate_pct": fn},
        "cost_per_1m_usd": cost, "price_known": price_known,
    }


def test_conclusion_prefers_cheaper_model_without_safety_loss():
    from app.services.decision_support import build_conclusion
    models = [_conclusion_model("strong", 80.0, 2.0, 10.0), _conclusion_model("cheap", 77.0, 2.0, 0.5)]
    res = build_conclusion(models, {}, n_queries=40)
    assert res["recommended_model_id"] == "cheap"


def test_conclusion_keeps_best_when_cheaper_misses_escalations():
    from app.services.decision_support import build_conclusion
    models = [_conclusion_model("strong", 80.0, 2.0, 10.0), _conclusion_model("cheap", 79.0, 9.0, 0.5)]
    res = build_conclusion(models, {}, n_queries=40)
    assert res["recommended_model_id"] == "strong"


def test_conclusion_does_not_treat_unknown_price_as_free_and_warns_on_small_sample():
    from app.services.decision_support import build_conclusion
    models = [_conclusion_model("strong", 80.0, 2.0, 10.0), _conclusion_model("noprice", 79.0, 2.0, None, price_known=False)]
    res = build_conclusion(models, {}, n_queries=12)
    assert res["recommended_model_id"] == "strong"
    assert any("12" in c for c in res["caveats_uk"])
    assert any("noprice" in c for c in res["caveats_uk"])


def test_gemini_usage_day_follows_pacific_time():
    from datetime import datetime
    from app.database import _pacific_now
    # 2026-11-05 01:00 UTC: уже зимовий час (UTC-8), у Каліфорнії ще 4 листопада
    assert _pacific_now(datetime(2026, 11, 5, 1)).strftime("%Y-%m-%d") == "2026-11-04"
    # 2026-07-05 06:30 UTC: літній час (UTC-7), 4 липня 23:30
    assert _pacific_now(datetime(2026, 7, 5, 6, 30)).strftime("%Y-%m-%d") == "2026-07-04"
    # перехід на зимовий час 2026-11-01 о 09:00 UTC
    assert _pacific_now(datetime(2026, 11, 1, 8, 30)).hour == 1
    assert _pacific_now(datetime(2026, 11, 1, 9, 30)).hour == 1


def _assistant_run(contract):
    from app.services.assistant import answer_customer_query
    llm_out = {"latency_ms": 5}
    stub = AsyncMock(return_value=(llm_out, contract))
    with patch("app.services.assistant._generate_with_contract_retry", stub):
        return asyncio.run(answer_customer_query("Як відкрити картку?", "gemini-3.1-flash-lite", "fintech")), stub


def test_assistant_returns_sources_and_model_decision():
    res, stub = _assistant_run({"answer": "Через застосунок.", "escalate": False, "reason": "є в базі", "contract_ok": True})
    assert res["escalate"] is False and res["answer"] == "Через застосунок."
    assert res["sources"] and {"code", "text", "score"} <= set(res["sources"][0])
    # у промпт має потрапити текст знайденого джерела
    sent = stub.call_args.kwargs["system_prompt"] + stub.call_args.kwargs["user_prompt"]
    assert res["sources"][0]["text"] in sent


def test_assistant_escalates_when_contract_is_broken():
    res, _ = _assistant_run({"answer": "???", "escalate": False, "reason": "", "contract_ok": False})
    assert res["escalate"] is True
    assert "оператор" in res["reason"]


def test_assistant_endpoint_validates_and_answers():
    client = TestClient(app)
    assert client.post("/api/assistant/ask", json={"user_query": "ab", "model_id": "x"}).status_code == 422
    stub = AsyncMock(return_value=({"latency_ms": 5}, {"answer": "Так.", "escalate": True, "reason": "ризик", "contract_ok": True}))
    with patch("app.services.assistant._generate_with_contract_retry", stub):
        r = client.post("/api/assistant/ask", json={"user_query": "Заблокуйте мою картку", "model_id": "gemini-3.1-flash-lite", "domain": "fintech"})
    assert r.status_code == 200 and r.json()["escalate"] is True


def test_gemini_model_with_catalog_base_url_uses_native_client():
    from app.services import llm_runtime
    cfg = {"provider": "gemini", "name": "G", "api_model_name": "gemini-2.5-flash",
           "base_url": "https://generativelanguage.googleapis.com/v1beta/models"}
    native, compat = AsyncMock(return_value={}), AsyncMock(return_value={})
    settings = {"gemini_api_key": "test-key"}
    with patch.object(llm_runtime, "call_gemini_api", native), patch.object(llm_runtime, "call_openai_compatible_api", compat):
        asyncio.run(llm_runtime._call_native("m", cfg, settings, "s", "u", 0.0, 100, 1))
    assert native.await_count == 1 and compat.await_count == 0


def test_retrieval_ignores_stop_words_in_ranking():
    from app.services.retrieval import retrieve_knowledge_chunks
    corpus = [
        {"code": "TAX", "context_fact": "Податок на депозит утримується як податковий агент банком."},
        {"code": "CARD", "context_fact": "Віртуальну картку можна відкрити в застосунку миттєво."},
        {"code": "OTHER", "context_fact": "Переказ між рахунками виконується за кілька секунд."},
    ]
    res = retrieve_knowledge_chunks("Як відкрити картку?", domain="fintech", top_k=3, corpus=corpus)
    assert res["retrieved_ids"][0] == "CARD"


def _cascade_row(query, model, escalate, expected, quality, cost, category="informational", halluc=False):
    return {"query_code": query, "model_id": model, "category": category, "requires_escalation": escalate,
            "expected_escalation": expected, "integral_efficiency_pct": quality, "cost_usd": cost,
            "has_hallucination": halluc, "price_known": True}


def test_cascade_saves_cost_when_cheap_model_handles_most_queries():
    from app.services.cascade import simulate_cascade
    rows = []
    for i in range(10):
        # дешева модель відповідає сама, окрім Q9, який вона віддає далі
        rows.append(_cascade_row(f"Q{i}", "cheap", i == 9, False, 80, 0.001))
        rows.append(_cascade_row(f"Q{i}", "strong", False, False, 90, 0.01))
    res = simulate_cascade(rows, "cheap", "strong")
    p = res["policies"]
    assert p["strong_only"]["cost_per_1000_usd"] == 10.0
    assert p["cascade"]["cost_per_1000_usd"] == round((9 * 0.001 + 0.001 + 0.01) / 10 * 1000, 4)
    assert res["cost_saving_vs_strong_pct"] > 0
    assert p["cascade"]["auto_pct"] == 100.0 and p["cheap_only"]["operator_pct"] == 10.0
    assert any("10" in c for c in res["caveats_uk"])


def test_cascade_counts_missed_escalations_and_risky_categories():
    from app.services.cascade import simulate_cascade
    rows = [
        _cascade_row("A", "cheap", False, True, 70, 0.001, category="troubleshooting"),
        _cascade_row("A", "strong", True, True, 90, 0.01, category="troubleshooting"),
    ]
    res = simulate_cascade(rows, "cheap", "strong")
    assert res["policies"]["cheap_only"]["missed_escalation_pct"] == 100.0
    assert res["policies"]["cascade"]["missed_escalation_pct"] == 100.0
    # ризикову категорію одразу бачить сильна модель і передає оператору
    risky = simulate_cascade(rows, "cheap", "strong", strong_first_categories=["troubleshooting"])
    assert risky["policies"]["cascade"]["missed_escalation_pct"] == 0.0


def test_cascade_requires_known_prices_for_cost_and_distinct_models():
    from app.services.cascade import simulate_cascade
    rows = [_cascade_row("A", "cheap", False, False, 80, 0.001), _cascade_row("A", "strong", False, False, 90, 0.01)]
    rows[0]["price_known"] = False
    res = simulate_cascade(rows, "cheap", "strong")
    assert res["policies"]["cascade"]["cost_per_1000_usd"] is None and res["cost_saving_vs_strong_pct"] is None
    assert simulate_cascade(rows, "cheap", "cheap")["available"] is False


def _gemini_429(quota_id):
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota, please check your plan and billing details.",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests", "quotaId": quota_id}]},
                                  {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "36s"}]}}


def test_status_classifier_on_realistic_provider_error_bodies():
    from app.services.model_discovery import classify_api_response as classify
    assert classify("gemini", 429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))[0] == "daily_quota"
    assert classify("gemini", 429, _gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))[0] == "rate_limited"
    invalid = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key.",
                         "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}
    assert classify("gemini", 400, invalid)[0] == "invalid_key"
    tpd = {"error": {"type": "tokens", "code": "rate_limit_exceeded",
                     "message": "Rate limit reached for model `llama-3.3-70b-versatile` on tokens per day (TPD): Limit 100000, Used 99800."}}
    tpm = {"error": {"type": "tokens", "code": "rate_limit_exceeded",
                     "message": "Rate limit reached for model `llama-3.3-70b-versatile` on tokens per minute (TPM): Limit 6000, Used 5900."}}
    assert classify("groq", 429, tpd)[0] == "daily_quota"
    assert classify("groq", 429, tpm)[0] == "rate_limited"
    assert classify("openrouter", 429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})[0] == "daily_quota"
    assert classify("gemini", 404, None, raw_text="")[0] == "model_missing"


def test_classifier_does_not_call_unknown_errors_a_missing_model():
    from app.services.model_discovery import classify_api_response as classify
    # 400 з невідомим текстом не доводить, що моделі немає
    assert classify("openai", 400, {"error": {"message": "Unsupported parameter: max_tokens"}})[0] == "unchecked"
    assert classify("groq", 422, None, raw_text="validation error")[0] == "unchecked"
    # перевантаження сервера не є лімітом користувача
    assert classify("anthropic", 529, {"error": {"type": "overloaded_error", "message": "Overloaded"}})[0] == "offline"
    assert classify("openai", 503, None, raw_text="Service Unavailable")[0] == "offline"
    assert classify("gemini", 400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key expired. Please renew the API key."}})[0] == "invalid_key"
    # справжні випадки лишаються як були
    assert classify("openai", 404, {"error": {"code": "model_not_found"}})[0] == "model_missing"
    assert classify("openai", 429, {"error": {"message": "Rate limit reached"}})[0] == "rate_limited"


def test_openai_ping_uses_max_completion_tokens():
    import httpx
    from app.services.model_discovery import _ping_single_model
    stub = AsyncMock(return_value=httpx.Response(200, json={"choices": []}))

    async def run(provider):
        with patch("httpx.AsyncClient.post", new=stub):
            async with httpx.AsyncClient() as client:
                await _ping_single_model(client=client, provider=provider, api_model_name="m", api_key="k", base_url="")

    asyncio.run(run("openai"))
    assert "max_completion_tokens" in stub.call_args.kwargs["json"] and "max_tokens" not in stub.call_args.kwargs["json"]
    asyncio.run(run("groq"))
    assert "max_tokens" in stub.call_args.kwargs["json"]


def test_models_snapshot_uses_field_names_the_interface_reads():
    from app.services.model_discovery import get_models_status_snapshot
    snap = get_models_status_snapshot()
    provider = next(iter(snap["providers"].values()))
    assert "checked_ago_uk" in provider and "models_count" in provider
    model = next(iter(snap["models"].values()))
    assert "checked_ago_uk" in model
