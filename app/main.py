"""
Головний серверний модуль програмного комплексу «LLM-AutoBench» (FastAPI).
Включає:
- маршрути живого моніторингу етапів роботи та логів (/api/logs);
- пряме читання та збереження API-ключів у файл .env (/api/settings);
- виконання конвеєра автоматизації та серійних експериментів.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import (
    PROVIDERS_CATALOG,
    QUERY_CATEGORIES,
    ENV_FILE,
    DEFAULT_EVALUATION_WEIGHTS,
    AUTOMATION_FC_THRESHOLD_PCT,
    HALLUCINATION_PENALTY_PCT,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_SEED,
    DEFAULT_TOP_K,
    CONTEXT_MODES,
    load_runtime_settings,
    save_runtime_settings,
    load_models_pricing,
    get_model_config,
    get_env_secret,
    set_env_secret,
    sync_model_registry_from_env,
)
from app.database import (
    init_db,
    get_connection,
    fetch_all_queries,
    upsert_query_item,
    fetch_all_prompts,
    update_prompt_template,
    sync_run_counters,
    fetch_run_errors,
    compute_code_version_hash,
    compute_dataset_hash,
    fetch_all_domains,
    upsert_domain,
    fetch_model_registry_by_id,
    upsert_model_registry_entry,
    delete_custom_model_from_registry,
    upsert_model_status,
    get_daily_usage_count,
)
from app.models import (
    DomainCreateRequest,
    QueryItemCreate,
    PromptTemplateUpdate,
    SingleAutomationRequest,
    AssistantRequest,
    FaqImportRequest,
    KbGenerateRequest,
    KbReviewRequest,
    CascadeRequest,
    BatchExperimentRequest,
    FlexibleAnalyticsRequest,
    ManualScoreOverride,
    SettingsUpdateRequest,
    ResolvePromptRequest,
    CustomModelCreateRequest,
    CustomModelTestRequest,
    ModelRegistryUpdateRequest,
    RefreshStatusRequest,
)
from app.seed_data import seed_database_if_needed, DATASET_VERSION
from app.domain_datasets import DOMAIN_PROFILES, DEPLOYMENT_PRESETS
from app.services.evaluator import compute_composite_quality_index
from app.services.judge import load_rubric_metadata
from app.services.run_prompts import get_active_prompt, resolve_prompt
from app.services.analytics import (
    export_manual_validation_sample_csv,
    import_manual_validation_csv,
)
from app.services.analytics import compute_hypotheses_overview
from app.services.assistant import answer_customer_query
from app.services import kb_drafts
from app.services.cascade import simulate_cascade_for_runs
from app.services.experiment_runner import (
    run_single_automation_pipeline,
    execute_batch_experiment,
    start_batch_experiment_task,
    resume_batch_experiment,
    start_resume_experiment_task,
    evaluate_run_with_judge,
    start_judge_experiment_task,
    compute_run_analytics,
    compute_flexible_analytics,
    get_evaluation_item_detail,
    export_run_tables_for_thesis,
)
from app.services.logger import (
    log_event,
    get_logs_and_status,
    clear_logs,
    request_cancel_experiment,
)
from app.services.model_discovery import (
    start_probe_task,
    get_models_status_snapshot,
    get_status_label_uk,
    test_custom_model_connection,
)


def _mask_key(val: str) -> str:
    """Маскує API-ключ для безпечного показу в інтерфейсі (напр., gsk_...8a9f)."""
    if not val or len(val) < 8:
        return ""
    return f"{val[:5]}...{val[-4:]}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ініціалізує таблиці SQLite, реєстр моделей та довідник типових запитів і промптів."""
    init_db()
    await asyncio.to_thread(seed_database_if_needed)
    sync_model_registry_from_env()
    sync_run_counters()
    settings = load_runtime_settings()
    configured = [
        name
        for name, key in [
            ("GROQ", settings.get("groq_api_key")),
            ("OPENAI", settings.get("openai_api_key")),
            ("GEMINI", settings.get("gemini_api_key")),
            ("ANTHROPIC", settings.get("anthropic_api_key")),
            ("OPENROUTER", settings.get("openrouter_api_key")),
        ]
        if key
    ]
    if configured:
        log_event("КОНФІГУРАЦІЯ .env", f"Знайдено активні ключі у .env: {', '.join(configured)}", level="INFO")
    else:
        log_event(
            "КОНФІГУРАЦІЯ .env",
            f"У файлі {ENV_FILE} ще не заповнено API-ключі. Додайте ключі у файл .env або у вкладці «Провайдери і моделі».",
            level="WARN",
        )
    if settings.get("probe_on_startup", True):
        start_probe_task(only_enabled=True, force=False)
    yield


app = FastAPI(
    title="LLM-AutoBench: дослідження ефективності великих мовних моделей",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Повертає головний веб-інтерфейс дослідницького комплексу."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Інтерфейс завантажується...</h1>")
    return FileResponse(
        str(index_file),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


@app.get("/api/logs")
async def api_get_logs(since_id: int = Query(default=0)):
    """Повертає нові записи логів та поточний етап роботи програми і LLM."""
    return get_logs_and_status(since_id=since_id)


@app.post("/api/logs/clear")
async def api_clear_logs():
    """Очищує консоль логів."""
    clear_logs()
    return {"status": "cleared"}


@app.post("/api/experiments/cancel")
async def api_cancel_experiment():
    """Надсилає сигнал зупинки активного серійного експерименту."""
    request_cancel_experiment()
    return {"status": "cancel_requested"}


@app.get("/api/experiments/active-prompt")
async def api_get_active_prompt():
    """Повертає поточний активний діалоговий запит до користувача під час експерименту."""
    return get_active_prompt() or {}


@app.post("/api/experiments/resolve-prompt")
async def api_resolve_prompt(payload: ResolvePromptRequest):
    """Фіксує рішення користувача (зміна маршруту, перемикання судді тощо) та розблоковує експеримент."""
    ok = resolve_prompt(payload.prompt_id, payload.action, payload.data)
    if not ok:
        raise HTTPException(status_code=404, detail="Активний запит не знайдено або термін його очікування вичерпано")
    return {"status": "ok", "action": payload.action}


@app.get("/api/metadata")
async def get_system_metadata():
    queries = fetch_all_queries()
    prompts = fetch_all_prompts()
    settings = load_runtime_settings()
    enabled_models = settings.get("enabled_models", [])
    model_routes = settings.get("model_routes", {})

    key_status = {
        "groq_configured": bool(settings.get("groq_api_key")),
        "groq_masked": _mask_key(settings.get("groq_api_key", "")),
        "openai_configured": bool(settings.get("openai_api_key")),
        "openai_masked": _mask_key(settings.get("openai_api_key", "")),
        "gemini_configured": bool(settings.get("gemini_api_key")),
        "gemini_masked": _mask_key(settings.get("gemini_api_key", "")),
        "anthropic_configured": bool(settings.get("anthropic_api_key")),
        "anthropic_masked": _mask_key(settings.get("anthropic_api_key", "")),
        "openrouter_configured": bool(settings.get("openrouter_api_key")),
        "openrouter_masked": _mask_key(settings.get("openrouter_api_key", "")),
    }

    cat_counts = {}
    for q in queries:
        cat_counts[q["category"]] = cat_counts.get(q["category"], 0) + 1

    snapshot = get_models_status_snapshot()
    models_list = list((snapshot.get("models") or {}).values())
    snapshot_providers = snapshot.get("providers") or {}

    providers_list = []
    for p_id, p_cfg in PROVIDERS_CATALOG.items():
        p_copy = dict(p_cfg)
        raw_val = settings.get(p_cfg["env_key"].lower(), "") or ""
        p_copy["configured"] = bool(raw_val)
        p_copy["key_masked"] = raw_val if p_cfg.get("is_local") else _mask_key(raw_val)
        p_snap = snapshot_providers.get(p_id, {})
        p_copy["models_count"] = p_snap.get("models_count", 0)
        p_copy["enabled_count"] = p_snap.get("enabled_count", 0)
        p_copy["status"] = p_snap.get("status", "unchecked" if p_copy["configured"] else "no_key")
        p_copy["status_label"] = p_snap.get("status_label", get_status_label_uk(p_copy["status"]))
        p_copy["checked_at"] = p_snap.get("checked_at")
        p_copy["checked_ago_uk"] = p_snap.get("checked_ago_uk", "Не перевірено")
        p_copy["can_openrouter_route"] = p_id in ("groq", "openai", "anthropic") and bool(settings.get("openrouter_api_key"))
        providers_list.append(p_copy)

    custom_snap = snapshot_providers.get("custom")
    if custom_snap and custom_snap.get("models_count", 0) > 0:
        providers_list.append(custom_snap)

    pricing_meta = load_models_pricing()
    db_domains = fetch_all_domains()
    domains_map = dict(DOMAIN_PROFILES)
    for d in db_domains:
        domains_map[d["code"]] = {
            **domains_map.get(d["code"], {}),
            **d,
            "id": d["code"],
            "name_uk": d["domain_name"],
        }

    return {
        "thesis_title_uk": "Дослідження ефективності застосування великих мовних моделей для автоматизації відповідей на типові запити користувачів",
        "domain_uk": "Багатодоменна система підтримки користувачів (Фінтех, ІТ-підтримка, E-Commerce, Телеком)",
        "domains": domains_map,
        "context_modes": list(CONTEXT_MODES),
        "default_top_k": DEFAULT_TOP_K,
        "deployment_presets": DEPLOYMENT_PRESETS,
        "dataset_version": DATASET_VERSION,
        "dataset_hash": compute_dataset_hash(),
        "code_version": compute_code_version_hash(),
        "pricing_metadata": pricing_meta,
        "categories": QUERY_CATEGORIES,
        "category_counts": cat_counts,
        "total_queries": len(queries),
        "providers": providers_list,
        "probe": snapshot,
        "models": models_list,
        "prompts": prompts,
        "settings": {
            "env_file_path": settings.get("env_file_path", str(ENV_FILE)),
            "confidence_threshold": settings.get("confidence_threshold", 0.75),
            "temperature": settings.get("temperature", DEFAULT_TEMPERATURE),
            "max_output_tokens": settings.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS),
            "seed": settings.get("seed", DEFAULT_SEED),
            "ollama_base_url": settings.get("ollama_base_url", "http://localhost:11434"),
            "ollama_model_name": settings.get("ollama_model_name", "llama3.2"),
            "probe_on_startup": settings.get("probe_on_startup", True),
            "enabled_models": enabled_models,
            "model_routes": model_routes,
            "weights": settings.get("weights", DEFAULT_EVALUATION_WEIGHTS),
            "key_status": key_status,
        },
    }


@app.get("/api/domains")
async def api_list_domains():
    """Повертає список усіх зареєстрованих предметних сфер із бази даних."""
    items = fetch_all_domains()
    if not items:
        return {"items": list(DOMAIN_PROFILES.values())}
    return {"items": items}


@app.post("/api/domains")
async def api_upsert_domain(payload: DomainCreateRequest):
    """Додає нову або оновлює існуючу предметну сферу без зміни коду проєкту."""
    saved = upsert_domain(payload.model_dump())
    log_event("ДОМЕНИ", f"Збережено предметну сферу '{saved['code']}' ({saved['domain_name']}, компанія: {saved['company']})", level="INFO")
    return {"status": "ok", "item": saved}


@app.get("/api/methodology")
async def api_get_methodology():
    """
    Єдине джерело правди про методику оцінювання:
    повертає детерміновані формули, єдиний JSON-контракт для P1..P5,
    рубрику оцінювання та статистичні критерії значущості.
    """
    settings = load_runtime_settings()
    weights = settings.get("weights", DEFAULT_EVALUATION_WEIGHTS)
    rubric_meta = load_rubric_metadata()
    rubric = rubric_meta.get("data", {})
    w_fc = float(weights.get("fact_coverage", DEFAULT_EVALUATION_WEIGHTS["fact_coverage"]))
    w_ss = float(weights.get("semantic_similarity", DEFAULT_EVALUATION_WEIGHTS["semantic_similarity"]))
    return {
        "methodology_version": "2.1",
        "code_version": compute_code_version_hash(),
        "dataset_version": DATASET_VERSION,
        "dataset_hash": compute_dataset_hash(),
        "rubric_version": rubric_meta.get("rubric_version", "1.0"),
        "rubric_hash": rubric_meta.get("rubric_hash", "none"),
        "rubric_basis": rubric.get(
            "basis",
            "Рубрика на основі характеристик надійності з NIST AI RMF 1.0 / NIST AI 600-1",
        ),
        "weights": weights,
        "automation_fc_threshold_pct": AUTOMATION_FC_THRESHOLD_PCT,
        "hallucination_penalty_pct": HALLUCINATION_PENALTY_PCT,
        "generation_defaults": {
            "temperature": DEFAULT_TEMPERATURE,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "seed": DEFAULT_SEED,
            "top_k": DEFAULT_TOP_K,
        },
        "metrics_v2": [
            {
                "id": "FC",
                "symbol": "FC",
                "name_uk": "Фактична повнота (Fact Coverage, %)",
                "formula_latex": "FC = (|K_matched| / |K_req|) * 100%",
                "range": "[0.0%; 100.0%]",
                "description_uk": (
                    "Частка обов'язкових фактів та груп синонімів із контексту бази знань (required_keywords), "
                    "знайдених у відповіді з урахуванням канонічної нормалізації числових значень "
                    "та морфологічної стемізації українських лексем."
                ),
            },
            {
                "id": "HAL",
                "symbol": "Hallucination",
                "name_uk": "Детектор фактичних галюцинацій (Hallucination Rate)",
                "formula_latex": "has_hallucination = 1, якщо знайдено числа/суми/відсотки/меню поза context_fact",
                "range": "{0, 1}",
                "description_uk": (
                    "Виявляє у згенерованій відповіді грошові суми, відсотки, часові інтервали та власні назви розділів, "
                    "яких немає в офіційному контексті та запиті користувача."
                ),
            },
            {
                "id": "SS",
                "symbol": "SS",
                "name_uk": "Лексико-семантична близькість до еталону (Semantic Similarity, %)",
                "formula_latex": "SS = (0.55 * Cosine_stem + 0.45 * ROUGE_L_F1) * 100%",
                "range": "[0.0%; 100.0%]",
                "description_uk": (
                    "Комбінація косинусної схожості векторів українських стемів (Cosine_stem) та F1-міри найдовшої спільної "
                    "підпослідовності слів (ROUGE-L F1)."
                ),
            },
            {
                "id": "FMT",
                "symbol": "FMT",
                "name_uk": "Надійність вихідного контракту (Format Reliability, %)",
                "formula_latex": "FMT = (passed_checks / 4) * 100%",
                "range": "[0.0%; 100.0%]",
                "description_uk": (
                    "Універсальна перевірка однакова для всіх стратегій P1-P5: валідність JSON-контракту "
                    "{\"answer\", \"escalate\", \"reason\"}, українська мова, адекватна довжина тексту клієнта "
                    "та відсутність витоку службових тегів. Фіксується окремо і не входить у Q_det."
                ),
            },
            {
                "id": "ESC",
                "symbol": "Escalation",
                "name_uk": "Точність шлюзу ескалації (Escalation Gate: TP, FP, FN, TN)",
                "formula_latex": "F1_esc = 2 * Precision * Recall / (Precision + Recall); FN_rate = FN / (TP + FN)",
                "range": "[0.0%; 100.0%]",
                "description_uk": (
                    "Перевірка здатності моделі розпізнати критичні запити за правилами домену "
                    "та виставити поле escalate = true у JSON-відповіді. "
                    "Критичною помилкою безпеки вважається FN (пропуск необхідної ескалації)."
                ),
            },
            {
                "id": "AUTO",
                "symbol": "AutoSuccess",
                "name_uk": "Критерій успішності автоматизації (Automation Success)",
                "formula_latex": (
                    f"Якщо expected_esc=0: (requires_esc=0) AND (FC >= {AUTOMATION_FC_THRESHOLD_PCT:.0f}%) AND (has_hallucination=0); "
                    "якщо expected_esc=1: (requires_esc=1)"
                ),
                "range": "{0, 1}",
                "description_uk": (
                    f"Правило маршрутизації: для стандартних запитів відповідь вважається успішно автоматизованою, "
                    f"якщо немає хибної ескалації, повнота фактів не нижча {AUTOMATION_FC_THRESHOLD_PCT:.0f}% і відсутні галюцинації. "
                    "Для критичних запитів потрібна коректна ескалація."
                ),
            },
            {
                "id": "Q_DET",
                "symbol": "Q_det",
                "name_uk": "Композитний індекс якості (Composite Quality Index, %)",
                "formula_latex": (
                    f"Q_det = max(0, {w_fc:.2f} * FC + {w_ss:.2f} * SS - {HALLUCINATION_PENALTY_PCT:.0f} * has_hallucination)"
                ),
                "range": "[0.0%; 100.0%]",
                "description_uk": (
                    "Зведена метрика змістовної якості відповіді на основі повноти фактів (FC) та близькості до еталону (SS) "
                    "зі штрафом за фактичні галюцинації. Стійкість ранжування до вибору ваг перевіряється коефіцієнтом Кендалла."
                ),
            },
        ],
        "rubric": {
            "version": rubric.get("version", "1.0"),
            "scale": rubric.get("scale", {"min": 1, "max": 5, "normalization_formula": "((mean_score - 1.0) / 4.0) * 100.0"}),
            "criteria": rubric.get("criteria", {}),
        },
        "statistical_methods": [
            {
                "name_uk": "Одиниця аналізу та 95% довірчі інтервали",
                "details_uk": "Одиницею статистичного аналізу є запит (query_code): повтори (repeats) спочатку усереднюються всередині запиту. Для кожної вибірки розраховуються n запитів, Mean, Median, Sample SD, IQR, P95 та 95% бутстреп-довірчий інтервал.",
            },
            {
                "name_uk": "Непараметричні парні критерії значущості (n >= 6)",
                "details_uk": "Парний критерій знакових рангів Вілкоксона з поправкою Холма застосовується лише при n >= 6 спільних запитів (при n < 6 p-значення не обчислюються). Критерій Фрідмана для k >= 3 умов видає попередження при кількості блоків < 10. Завжди наводяться Cliff's delta та 95% ДІ різниці.",
            },
            {
                "name_uk": "Аналіз чутливості, кореляції Спірмена та валідація судді",
                "details_uk": "Перебір сітки ваг (w_FC, w_SS) і штрафів за галюцинації (5..30 в.п.) з оцінкою стійкості рангів Кендалла. Матриця рангових кореляцій Спірмена між FC, SS, Q_det, рубрикою, затримкою та вартістю. Перевірка LLM-судді на ручній розмітці через зважену каппу Коена та Спірмена.",
            },
        ],
    }


@app.get("/api/queries")
async def api_list_queries(
    category: Optional[str] = Query(default=None),
    domain: Optional[str] = Query(default="all"),
):
    """Повертає список типових користувацьких запитів із еталонними відповідями."""
    return {"items": fetch_all_queries(category=category, domain=domain)}


@app.post("/api/queries")
async def api_upsert_query(payload: QueryItemCreate):
    """Додає або оновлює типовий запит користувача в еталонній базі."""
    saved = upsert_query_item(payload.model_dump())
    log_event("БАЗА ЗАПИТІВ", f"Збережено запит {saved['code']} ({saved.get('domain', 'fintech')} / {saved['category']})", level="INFO")
    return {"status": "ok", "item": saved}


@app.post("/api/queries/import")
async def api_import_queries(payload: dict = Body(...)):
    """Імпортує масив типових запитів із JSON з обов'язковою перевіркою полів та дублікатів."""
    items = payload.get("items") or []
    target_domain = str(payload.get("domain") or "fintech").strip()
    allow_overwrite = bool(payload.get("allow_overwrite", False))
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Передайте непорожній список запитів у полі 'items'.")

    existing_qs = fetch_all_queries(domain="all")
    existing_codes = {str(q["code"]).strip().upper() for q in existing_qs}
    existing_texts = {str(q["user_query"]).strip().lower() for q in existing_qs}

    seen_codes: set = set()
    seen_texts: set = set()
    validated_items: List[Dict[str, Any]] = []

    required_fields = ("code", "category", "user_query", "context_fact", "reference_answer", "required_keywords")
    for idx, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"Елемент #{idx} не є JSON-об'єктом.")
        missing = [f for f in required_fields if raw.get(f) in (None, "", [])]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Запит #{idx} ({raw.get('code', 'без коду')}): відсутні обов'язкові поля: {', '.join(missing)}.",
            )
        code_val = str(raw["code"]).strip()
        query_text = str(raw["user_query"]).strip()
        code_norm = code_val.upper()
        text_norm = query_text.lower()

        if code_norm in seen_codes:
            raise HTTPException(status_code=400, detail=f"Дублікат коду запиту '{code_val}' усередині JSON-пакета.")
        if text_norm in seen_texts:
            raise HTTPException(status_code=400, detail=f"Дублікат тексту запиту '{query_text[:60]}' усередині JSON-пакета.")
        if not allow_overwrite and (code_norm in existing_codes or text_norm in existing_texts):
            raise HTTPException(
                status_code=400,
                detail=f"Запит '{code_val}' уже існує в базі даних (дублікат коду або тексту).",
            )
        seen_codes.add(code_norm)
        seen_texts.add(text_norm)

        src = str(raw.get("source") or payload.get("source") or "manual").strip()
        if src not in ("manual", "llm_draft"):
            src = "manual"
        rev = str(raw.get("review_status") or payload.get("review_status") or ("draft" if src == "llm_draft" else "reviewed")).strip()
        if rev not in ("draft", "reviewed"):
            rev = "reviewed"

        validated_items.append({
            "code": code_val,
            "domain": str(raw.get("domain") or target_domain).strip(),
            "category": str(raw["category"]).strip(),
            "user_query": query_text,
            "context_fact": str(raw["context_fact"]).strip(),
            "reference_answer": str(raw["reference_answer"]).strip(),
            "required_keywords": raw["required_keywords"],
            "expected_escalation": bool(raw.get("expected_escalation", False)),
            "complexity": int(raw.get("complexity", 2)),
            "source": src,
            "review_status": rev,
        })

    for item_data in validated_items:
        upsert_query_item(item_data)

    imported_count = len(validated_items)
    log_event("БАЗА ЗАПИТІВ [ІМПОРТ]", f"Імпортовано {imported_count} запитів у сферу '{target_domain}'", level="INFO")
    return {"status": "ok", "imported_count": imported_count, "domain": target_domain}


@app.get("/api/prompts")
async def api_list_prompts():
    """Повертає список усіх стратегій формування промптів (P1..P5)."""
    return {"items": fetch_all_prompts()}


@app.put("/api/prompts")
async def api_update_prompt(payload: PromptTemplateUpdate):
    """Оновлює системний та користувацький шаблон обраної стратегії промпту."""
    updated = update_prompt_template(payload.model_dump())
    log_event("ШАБЛОНИ ПРОМПТІВ", f"Оновлено шаблон промпту {payload.code}", level="INFO")
    return {"status": "ok", "item": updated}


@app.post("/api/automate/single")
async def api_automate_single(payload: SingleAutomationRequest):
    try:
        result = await run_single_automation_pipeline(
            user_query=payload.user_query,
            model_id=payload.model_id,
            prompt_code=payload.prompt_code,
            query_id=payload.query_id,
            custom_context=payload.custom_context,
            temperature=payload.temperature,
            judge_model_id=payload.judge_model_id,
            context_mode=payload.context_mode or "gold_context",
            top_k=payload.top_k,
            domain=payload.domain,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/kb/import-faq")
async def api_kb_import_faq(payload: FaqImportRequest):
    try:
        items = kb_drafts.parse_faq(payload.content, payload.format)
        return kb_drafts.import_faq(payload.domain_code, payload.domain_name, payload.company, items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/kb/generate")
async def api_kb_generate(payload: KbGenerateRequest):
    try:
        if payload.kind == "distractors":
            return {"created": await kb_drafts.generate_distractors(payload.domain, payload.count, payload.model_id)}
        if payload.kind == "paraphrases":
            return {"created": await kb_drafts.generate_paraphrases(payload.domain, payload.count, payload.model_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="kind має бути distractors або paraphrases.")


@app.get("/api/kb/drafts")
async def api_kb_drafts(domain: str = Query(default="all")):
    return kb_drafts.list_drafts(domain)


@app.post("/api/kb/review")
async def api_kb_review(payload: KbReviewRequest):
    try:
        return kb_drafts.review_draft(payload.kind, payload.key, payload.action, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/kb/summary")
async def api_kb_summary():
    return {"items": kb_drafts.kb_summary()}


@app.post("/api/assistant/ask")
async def api_assistant_ask(payload: AssistantRequest):
    try:
        return await answer_customer_query(payload.user_query.strip(), payload.model_id, payload.domain)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/cascade/simulate")
async def api_cascade_simulate(payload: CascadeRequest):
    return simulate_cascade_for_runs(
        run_ids=payload.run_ids,
        cheap_model_id=payload.cheap_model_id,
        strong_model_id=payload.strong_model_id,
        strong_first_categories=payload.strong_first_categories,
        prompt_code=payload.prompt_code,
        context_mode=payload.context_mode,
    )


@app.get("/api/experiments")
async def api_list_experiments():
    conn = get_connection()
    cur = conn.cursor()
    if not (get_logs_and_status().get("status") or {}).get("is_busy"):
        cur.execute(
            """
            UPDATE experiment_runs
            SET status = CASE WHEN completed_items >= total_items AND total_items > 0 THEN 'completed' ELSE 'incomplete' END
            WHERE status = 'running'
            """
        )
        conn.commit()
    cur.execute(
        """
        SELECT r.*,
               (SELECT COUNT(*) FROM run_errors re WHERE re.run_id = r.id) AS errors_count,
               (SELECT COUNT(*) FROM evaluation_results er WHERE er.run_id = r.id) AS actual_items_count,
               (SELECT COUNT(*) FROM evaluation_results er WHERE er.run_id = r.id AND COALESCE(er.rubric_evaluated, 0) = 1) AS judged_items_count
        FROM experiment_runs r
        WHERE r.execution_mode != 'sandbox'
        ORDER BY r.id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    filtered_rows = []
    for r in rows:
        r["models"] = json.loads(r.pop("models_json", "[]") or "[]")
        r["prompts"] = json.loads(r.pop("prompts_json", "[]") or "[]")
        r["categories"] = json.loads(r.pop("categories_json", "[]") or "[]")
        r["summary"] = json.loads(r.pop("summary_json", "{}") or "{}")
        r["settings_snapshot"] = json.loads(r.pop("settings_snapshot_json", "{}") or "{}")
        filtered_rows.append(r)
    return {"runs": filtered_rows}


@app.post("/api/experiments/run")
async def api_run_batch_experiment(payload: BatchExperimentRequest):
    """Запускає новий серійний експеримент у фоновому режимі (або синхронно за прапорцем wait_for_completion)."""
    if not payload.model_ids or not payload.prompt_codes or not payload.categories:
        raise HTTPException(
            status_code=400,
            detail="Оберіть щонайменше одну модель, один промпт та одну категорію запитів.",
        )
    try:
        if payload.wait_for_completion:
            return await execute_batch_experiment(
                title=payload.title,
                description=payload.description,
                model_ids=payload.model_ids,
                prompt_codes=payload.prompt_codes,
                categories=payload.categories,
                max_queries_per_category=payload.max_queries_per_category,
                repeats=payload.repeats,
                use_judge=payload.use_judge,
                judge_model_id=payload.judge_model_id,
                allow_self_judge=payload.allow_self_judge,
                temperature=payload.temperature,
                max_output_tokens=payload.max_output_tokens,
                seed=payload.seed,
                domain=payload.domain or "fintech",
                domains=payload.domains,
                equal_domain_sample=payload.equal_domain_sample,
                context_mode=payload.context_mode,
                context_modes=payload.context_modes,
                top_k=payload.top_k,
            )
        return start_batch_experiment_task(
            title=payload.title,
            description=payload.description,
            model_ids=payload.model_ids,
            prompt_codes=payload.prompt_codes,
            categories=payload.categories,
            max_queries_per_category=payload.max_queries_per_category,
            repeats=payload.repeats,
            use_judge=payload.use_judge,
            judge_model_id=payload.judge_model_id,
            allow_self_judge=payload.allow_self_judge,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
            seed=payload.seed,
            domain=payload.domain or "fintech",
            domains=payload.domains,
            equal_domain_sample=payload.equal_domain_sample,
            context_mode=payload.context_mode,
            context_modes=payload.context_modes,
            top_k=payload.top_k,
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        log_event("ЕКСПЕРИМЕНТ [ПОМИЛКА ЗАПУСКУ]", f"Не вдалося запустити серію: {exc}", level="ERROR")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/experiments/{run_id}/resume")
async def api_resume_experiment(
    run_id: int,
    payload: Optional[dict] = Body(default=None),
):
    """Довиконує перервану або неповну серію експериментів з місця зупинки у фоновому режимі."""
    judge_model_id = (payload or {}).get("judge_model_id")
    wait_sync = bool((payload or {}).get("wait_for_completion", False))
    try:
        if wait_sync:
            return await resume_batch_experiment(run_id=run_id, judge_model_id=judge_model_id)
        return start_resume_experiment_task(run_id=run_id, judge_model_id=judge_model_id)
    except Exception as exc:
        log_event("ЕКСПЕРИМЕНТ [ПОМИЛКА ДОВИКОНАННЯ]", f"Не вдалося довиконати серію #{run_id}: {exc}", level="ERROR")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/experiments/{run_id}/judge")
async def api_judge_experiment_run(
    run_id: int,
    payload: dict = Body(...),
):
    """Дооцінює збережені відповіді серії за допомогою обраного LLM-судді у фоновому режимі."""
    judge_model_id = (payload or {}).get("judge_model_id")
    only_unjudged = bool((payload or {}).get("only_unjudged", True))
    allow_self_judge = bool((payload or {}).get("allow_self_judge", False))
    wait_sync = bool((payload or {}).get("wait_for_completion", False))
    if not judge_model_id:
        raise HTTPException(status_code=400, detail="Оберіть модель для LLM-судді.")
    try:
        if wait_sync:
            return await evaluate_run_with_judge(
                run_id=run_id,
                judge_model_id=str(judge_model_id),
                only_unjudged=only_unjudged,
                allow_self_judge=allow_self_judge,
            )
        return start_judge_experiment_task(
            run_id=run_id,
            judge_model_id=str(judge_model_id),
            only_unjudged=only_unjudged,
            allow_self_judge=allow_self_judge,
        )
    except Exception as exc:
        log_event("СУДДЯ [ПОМИЛКА ДООЦІНЮВАННЯ]", f"Не вдалося дооцінити серію #{run_id}: {exc}", level="ERROR")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/experiments/{run_id}/manual-sample/export")
async def api_export_manual_validation_sample(
    run_id: int,
    max_items: int = Query(default=60, ge=1, le=200),
):
    """Вивантажує стратифіковану вибірку (до 60 відповідей) у CSV для ручної експертної оцінки."""
    csv_text = export_manual_validation_sample_csv(run_id=run_id, max_items=max_items)
    if not csv_text:
        raise HTTPException(status_code=404, detail="Дані серії для ручної вибірки не знайдено.")
    content = "\ufeff" + csv_text
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="manual_validation_sample_run_{run_id}.csv"'},
    )


@app.post("/api/experiments/{run_id}/manual-sample/import")
async def api_import_manual_validation_sample(
    run_id: int,
    payload: dict = Body(...),
):
    """Імпортує заповнений CSV ручної розмітки та обчислює узгодженість із суддею (каппа Коена та Спірмен)."""
    csv_text = str((payload or {}).get("csv_text") or "")
    annotator = str((payload or {}).get("annotator") or "expert").strip() or "expert"
    try:
        res = import_manual_validation_csv(run_id=run_id, csv_text=csv_text, annotator=annotator)
        log_event(
            "ВАЛІДАЦІЯ СУДДІ",
            f"Імпортовано {res['imported_count']} ручних оцінок для серії #{run_id} (автор: {annotator})",
            level="EVAL",
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/experiments/{run_id}")
async def api_get_experiment_analytics(
    run_id: int,
    balanced_only: bool = Query(default=True),
    include_self_judged: bool = Query(default=False),
):
    """Повертає повну аналітику, таблиці, перехресні матриці, статтести та висновки для обраного експерименту."""
    analytics = compute_run_analytics(run_id, balanced_only=balanced_only, include_self_judged=include_self_judged)
    if not analytics:
        raise HTTPException(status_code=404, detail="Серію експериментів не знайдено")
    return analytics


@app.get("/api/experiments/{run_id}/errors")
async def api_get_experiment_errors(run_id: int):
    """Повертає журнал помилок (run_errors), що виникли під час виконання серії експериментів."""
    return {"run_id": run_id, "errors": fetch_run_errors(run_id)}


@app.get("/api/hypotheses")
async def api_hypotheses_overview():
    return compute_hypotheses_overview()


@app.post("/api/analytics/flexible")
async def api_get_flexible_analytics(payload: FlexibleAnalyticsRequest):
    """Повертає зведену та порівняльну аналітику для однієї або кількох серій із фільтрацією за моделями, промптами та категоріями."""
    if not payload.run_ids:
        raise HTTPException(status_code=400, detail="Оберіть щонайменше одну серію експериментів.")
    analytics = compute_flexible_analytics(
        run_ids=payload.run_ids,
        model_ids=payload.model_ids,
        prompt_codes=payload.prompt_codes,
        categories=payload.categories,
        domains=payload.domains,
        balanced_only=payload.balanced_only,
        include_self_judged=payload.include_self_judged,
    )
    return analytics


@app.get("/api/experiments/{run_id}/items")
async def api_get_experiment_items(
    run_id: int,
    model_id: Optional[str] = Query(default=None),
    prompt_code: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    """Повертає детальні записи окремих замірів у межах серії експериментів."""
    conn = get_connection()
    cur = conn.cursor()

    sql = """
        SELECT e.*, q.user_query, q.reference_answer, q.expected_escalation
        FROM evaluation_results e
        LEFT JOIN queries q ON e.query_id = q.id
        WHERE e.run_id = ? AND e.source != 'sandbox'
    """
    params = [run_id]
    if model_id and model_id != "all":
        sql += " AND e.model_id = ?"
        params.append(model_id)
    if prompt_code and prompt_code != "all":
        sql += " AND e.prompt_code = ?"
        params.append(prompt_code)
    if category and category != "all":
        sql += " AND e.category = ?"
        params.append(category)

    sql += " ORDER BY e.id ASC LIMIT ?"
    params.append(limit)

    cur.execute(sql, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    from app.services.llm_runtime import calculate_cost_usd, is_model_price_known
    for r in rows:
        r["matched_keywords"] = json.loads(r.pop("matched_keywords_json", "[]") or "[]")
        r["missing_keywords"] = json.loads(r.pop("missing_keywords_json", "[]") or "[]")
        r["hallucinated_items"] = json.loads(r.pop("hallucinated_items_json", "[]") or "[]")
        r["format_checks"] = json.loads(r.pop("format_checks_json", "{}") or "{}")
        r["requires_escalation"] = bool(r["requires_escalation"])
        r["escalation_correct"] = bool(r["escalation_correct"])
        r["automation_success"] = bool(r["automation_success"])
        r["expected_escalation"] = bool(r["expected_escalation"])
        r["has_hallucination"] = bool(r.get("has_hallucination", 0))
        r["rubric_evaluated"] = bool(r.get("rubric_evaluated", 0))
        r["manual_scored"] = bool(r.get("manual_scored", 0))
        r["truncated"] = bool(r.get("truncated", 0))
        raw_usage = {}
        try:
            raw_usage = json.loads(r.get("raw_usage_json") or "{}")
        except Exception:
            raw_usage = {}
        price_known = is_model_price_known(r["model_id"]) and not bool(raw_usage.get("cost_unknown"))
        r["cost_unknown"] = not price_known
        cost_val = (
            (r["cost_usd"] if r.get("cost_usd") and r["cost_usd"] > 0 else calculate_cost_usd(
                r["model_id"], r.get("input_tokens", 0), r.get("output_tokens", 0)
            ))
            if price_known
            else 0.0
        )
        r["cost_usd"] = round(cost_val, 6)
        r["cost_per_1k_usd"] = round(cost_val * 1000.0, 4)
        tot_tok = max(1, int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0) + int(r.get("thinking_tokens") or 0))
        r["cost_per_1m_usd"] = round((cost_val / tot_tok) * 1_000_000.0, 4)

    return {"items": rows}


@app.get("/api/evaluations/{item_id}/detail")
async def api_get_evaluation_detail(item_id: int):
    """Повертає повний паспорт окремого заміру: запит, еталон, рендерений промпт, відповідь LLM та розбір усіх формул оцінки."""
    detail = get_evaluation_item_detail(item_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Запис заміру не знайдено")
    return detail


@app.put("/api/evaluations/{item_id}/manual-score")
async def api_override_evaluation_score(item_id: int, payload: ManualScoreOverride):
    """
    Дозволяє досліднику (експерту) зберегти ручну оцінку (1–5) за рубрикою для конкретного результату.
    Не змішує рубрику з затримкою мережі (speed_score_pct) і позначає manual_scored=1, rubric_evaluated=1.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM evaluation_results WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Запис оцінювання не знайдено")

    rec = dict(row)
    policy_val = (
        payload.policy_adherence_score
        if payload.policy_adherence_score is not None
        else payload.format_score
    )
    avg_5 = (
        payload.relevance_score
        + payload.factuality_score
        + payload.completeness_score
        + payload.clarity_score
        + policy_val
    ) / 5.0
    rubric_pct = round(((avg_5 - 1.0) / 4.0) * 100.0, 1)

    settings = load_runtime_settings()
    weights = settings.get("weights", DEFAULT_EVALUATION_WEIGHTS)
    integral_pct = compute_composite_quality_index(
        fact_coverage_pct=float(rec.get("fact_coverage_pct") or 0.0),
        semantic_similarity_pct=float(rec.get("semantic_similarity_pct") or 0.0),
        format_compliance_pct=float(rec.get("format_compliance_pct") or 0.0),
        weights=weights,
    )

    cur.execute(
        """
        UPDATE evaluation_results
        SET relevance_score = ?, factuality_score = ?, completeness_score = ?,
            clarity_score = ?, format_score = ?, policy_adherence_score = ?,
            nist_composite_pct = ?, rubric_composite_pct = ?,
            rubric_evaluated = 1, manual_scored = 1,
            manual_annotator = ?, manual_notes = ?,
            integral_efficiency_pct = ?
        WHERE id = ?
        """,
        (
            payload.relevance_score,
            payload.factuality_score,
            payload.completeness_score,
            payload.clarity_score,
            payload.format_score,
            policy_val,
            rubric_pct,
            rubric_pct,
            payload.manual_annotator,
            payload.manual_notes,
            integral_pct,
            item_id,
        ),
    )
    conn.commit()
    conn.close()
    log_event(
        "ЕКСПЕРТНА ОЦІНКА",
        f"Збережено ручну оцінку рубрики для заміру #{item_id} (автор: {payload.manual_annotator}) -> Rubric={rubric_pct}%, Q_det={integral_pct}%",
        level="EVAL",
    )
    return {
        "status": "ok",
        "nist_composite_pct": rubric_pct,
        "rubric_composite_pct": rubric_pct,
        "integral_efficiency_pct": integral_pct,
        "manual_scored": True,
        "rubric_evaluated": True,
    }


@app.get("/api/experiments/{run_id}/export")
async def api_export_experiment_tables(run_id: int):
    """Генерує та повертає таблиці у форматах Markdown, CSV та LaTeX."""
    exported = export_run_tables_for_thesis(run_id)
    if not exported.get("markdown"):
        raise HTTPException(status_code=404, detail="Дані для експорту не знайдено")
    log_event("ЕКСПОРТ", f"Згенеровано таблиці звіту (Серія #{run_id})", level="INFO")
    return exported


@app.get("/api/experiments/{run_id}/download/{fmt}")
async def api_download_experiment_export(run_id: int, fmt: str):
    """Завантажує файл результатів експерименту (.csv для Excel, .md або .tex) одним кліком."""
    exported = export_run_tables_for_thesis(run_id)
    if not exported.get("markdown"):
        raise HTTPException(status_code=404, detail="Дані для експорту не знайдено")
    fmt_low = fmt.lower().strip()
    if fmt_low == "csv":
        content = "\ufeff" + (exported.get("csv_models") or "")
        return Response(
            content=content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="llm_autobench_run_{run_id}.csv"'},
        )
    elif fmt_low in ("md", "markdown"):
        content = exported.get("markdown") or ""
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="llm_autobench_report_run_{run_id}.md"'},
        )
    elif fmt_low in ("tex", "latex"):
        content = exported.get("latex") or ""
        return Response(
            content=content.encode("utf-8"),
            media_type="application/x-tex; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="llm_autobench_table_run_{run_id}.tex"'},
        )
    raise HTTPException(status_code=400, detail=f"Невідомий формат експорту: {fmt}")


@app.delete("/api/experiments/{run_id}")
async def api_delete_experiment(run_id: int):
    """Видаляє серію експериментів разом із її записами та перенумеровує решту серій без пропусків (1..N)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM evaluation_results WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM experiment_runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()
    remaining = sync_run_counters()
    log_event(
        "ЖУРНАЛ",
        f"Видалено серію експериментів №{run_id}. Нумерацію серій впорядковано (всього активних серій: {remaining}).",
        level="WARN",
    )
    return {"status": "deleted", "run_id": run_id, "remaining_runs": remaining}


@app.put("/api/settings")
async def api_update_settings(payload: SettingsUpdateRequest):
    """Записує оновлені API-ключі та параметри у .env, а вибір моделей та маршрути — у БД."""
    raw = {k: v for k, v in payload.model_dump().items() if v is not None}
    key_fields = [k for k in ("groq_api_key", "openai_api_key", "gemini_api_key", "anthropic_api_key", "openrouter_api_key") if raw.get(k)]
    updated = save_runtime_settings(raw)
    enabled_count = len(updated.get("enabled_models", []))
    keys_desc = f", оновлено ключі: {', '.join(key_fields)}" if key_fields else ""
    log_event(
        "КОНФІГУРАЦІЯ",
        f"Збережено конфігурацію{keys_desc} | Активних моделей: {enabled_count} | Temp: {updated.get('temperature')} | Conf: {updated.get('confidence_threshold')}",
        level="INFO",
    )
    if key_fields:
        start_probe_task(only_enabled=True, force=True)
    return {
        "status": "ok",
        "env_file_path": updated.get("env_file_path"),
        "confidence_threshold": updated.get("confidence_threshold"),
        "temperature": updated.get("temperature"),
        "probe_on_startup": updated.get("probe_on_startup", True),
        "enabled_models": updated.get("enabled_models", []),
        "model_routes": updated.get("model_routes", {}),
    }


@app.get("/api/models/status")
async def api_get_models_status():
    """Повертає актуальний знімок реєстру моделей та індивідуальних статусів із SQLite."""
    return get_models_status_snapshot()


@app.post("/api/models/status/refresh")
async def api_refresh_models_status(payload: Optional[RefreshStatusRequest] = Body(default=None)):
    """Неблокуюче запускає перевірку статусів моделей (усіх увімкнених або однієї конкретної)."""
    req = payload or RefreshStatusRequest()
    return start_probe_task(
        model_key=req.model_key,
        only_enabled=req.only_enabled,
        force=req.force,
    )


@app.post("/api/models/custom/test")
async def api_test_custom_model(payload: CustomModelTestRequest):
    """Перевіряє з'єднання для власної OpenAI-сумісної моделі перед додаванням."""
    return await test_custom_model_connection(
        provider=payload.provider,
        model_id=payload.model_id,
        base_url=payload.base_url or "",
        api_key_env=payload.api_key_env or "",
        api_key_value=payload.api_key or payload.api_key_value,
    )


@app.post("/api/models/custom")
async def api_add_custom_model(payload: CustomModelCreateRequest):
    """Додає нову модель у реєстр SQLite та (за потреби) зберігає ключ у .env."""
    raw_model_id = payload.model_id.strip()
    if not raw_model_id:
        raise HTTPException(status_code=400, detail="Вкажіть ідентифікатор моделі (model_id).")
    provider = (payload.provider or "custom").strip().lower()
    env_var = (payload.api_key_env or "").strip().upper()
    if payload.api_key and not env_var:
        safe_suffix = "".join(ch if ch.isalnum() else "_" for ch in raw_model_id.upper())[:24]
        env_var = f"CUSTOM_{safe_suffix}_API_KEY"
    if payload.api_key and env_var:
        set_env_secret(env_var, payload.api_key.strip())

    has_manual_price = payload.input_price_per_1m is not None or payload.output_price_per_1m is not None
    price_source = "manual" if has_manual_price else "unknown"
    registry_id = raw_model_id if provider != "custom" else f"custom:{raw_model_id}"
    limits: dict = {}
    if payload.rpm is not None:
        limits["rpm"] = payload.rpm
    if payload.rpd is not None:
        limits["rpd"] = payload.rpd
    if limits:
        limits["limits_source"] = {k: "manual" for k in limits}

    entry = upsert_model_registry_entry(
        {
            "id": registry_id,
            "provider": provider,
            "model_id": raw_model_id,
            "display_name": (payload.display_name or raw_model_id).strip(),
            "source": "custom",
            "enabled": bool(payload.enabled),
            "route": "direct",
            "base_url": (payload.base_url or "https://api.openai.com/v1").strip(),
            "api_key_env": env_var or f"{provider.upper()}_API_KEY",
            "context_window": payload.context_window,
            "input_price_per_1m": payload.input_price_per_1m,
            "output_price_per_1m": payload.output_price_per_1m,
            "price_source": price_source,
            "limits": limits,
        }
    )

    test_res = await test_custom_model_connection(
        provider=provider,
        model_id=raw_model_id,
        base_url=entry.get("base_url") or "https://api.openai.com/v1",
        api_key_env=entry.get("api_key_env") or "",
        api_key_value=payload.api_key,
    )
    upsert_model_status(
        model_key=registry_id,
        status=test_res["status"],
        http_code=test_res.get("http_code"),
        latency_ms=test_res.get("latency_ms"),
        message=test_res.get("message", ""),
    )
    log_event(
        "РЕЄСТР МОДЕЛЕЙ",
        f"Додано модель '{entry['display_name']}' ({registry_id}) -> {test_res['status_label']}",
        level="INFO",
    )
    return {"status": "ok", "model": entry, "connection": test_res}


@app.put("/api/models/{model_key:path}")
async def api_update_model_registry(model_key: str, payload: ModelRegistryUpdateRequest):
    """Оновлює прапорець увімкнення, маршрут, ручні ціни та ліміти RPM/RPD моделі у SQLite."""
    existing = fetch_model_registry_by_id(model_key)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Модель '{model_key}' не знайдено у реєстрі.")
    limits_patch = {}
    if payload.rpm is not None:
        limits_patch["rpm"] = payload.rpm
    if payload.rpd is not None:
        limits_patch["rpd"] = payload.rpd
    if limits_patch:
        limits_patch["limits_source"] = {k: "manual" for k in limits_patch}
    has_price_edit = payload.input_price_per_1m is not None or payload.output_price_per_1m is not None
    patch_entry = {"id": model_key}
    if payload.enabled is not None:
        patch_entry["enabled"] = payload.enabled
    if payload.route is not None:
        patch_entry["route"] = payload.route
    if payload.display_name is not None:
        patch_entry["display_name"] = payload.display_name
    if payload.base_url is not None:
        patch_entry["base_url"] = payload.base_url
    if payload.api_key_env is not None:
        patch_entry["api_key_env"] = payload.api_key_env
    if payload.context_window is not None:
        patch_entry["context_window"] = payload.context_window
    if payload.input_price_per_1m is not None:
        patch_entry["input_price_per_1m"] = payload.input_price_per_1m
    if payload.output_price_per_1m is not None:
        patch_entry["output_price_per_1m"] = payload.output_price_per_1m
    if has_price_edit:
        patch_entry["price_source"] = "manual"
    if limits_patch:
        patch_entry["limits"] = limits_patch
    updated = upsert_model_registry_entry(patch_entry)
    return {"status": "ok", "model": updated}


@app.delete("/api/models/{model_key:path}")
async def api_delete_custom_model(model_key: str):
    """Видаляє користувацьку модель із реєстру SQLite."""
    deleted = delete_custom_model_from_registry(model_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Модель не знайдено або вона не є користувацькою.")
    return {"status": "deleted", "model_key": model_key}


@app.post("/api/experiments/estimate")
async def api_estimate_batch_experiment(payload: dict = Body(...)):
    """Обчислює попередній прогноз вартості, токенів, часу та денної квоти перед запуском серії."""
    model_ids = payload.get("model_ids") or []
    prompt_codes = payload.get("prompt_codes") or []
    categories = payload.get("categories") or []
    max_per_cat = int(payload.get("max_queries_per_category") or 20)
    repeats = max(1, int(payload.get("repeats") or 1))
    use_judge = bool(payload.get("use_judge", False))
    judge_model_id = payload.get("judge_model_id")
    domain = payload.get("domain") or "fintech"
    modes = payload.get("context_modes") or ([payload.get("context_mode")] if payload.get("context_mode") else ["gold_context"])
    modes_count = max(1, len(modes))

    all_queries = fetch_all_queries(domain=domain, reviewed_only=True)
    selected_queries_count = 0
    for cat in categories:
        cat_qs = [q for q in all_queries if q.get("category") == cat]
        selected_queries_count += min(len(cat_qs), max_per_cat)

    calls_per_model = selected_queries_count * max(1, len(prompt_codes)) * modes_count * repeats
    total_generation_calls = calls_per_model * len(model_ids)
    total_judge_calls = total_generation_calls if (use_judge and judge_model_id) else 0

    avg_in_tokens = 420
    avg_out_tokens = 180
    judge_in_tokens = 650
    judge_out_tokens = 160

    models_breakdown = []
    total_known_cost_usd = 0.0
    has_unknown_price = False
    quota_warnings = []
    est_seconds_total = 0.0

    for m_id in model_ids:
        cfg = get_model_config(m_id)
        price_known = bool(cfg.get("price_known", False))
        in_tok = calls_per_model * avg_in_tokens
        out_tok = calls_per_model * avg_out_tokens
        if price_known and cfg.get("cost_per_1m_input_usd") is not None and cfg.get("cost_per_1m_output_usd") is not None:
            m_cost = (in_tok / 1_000_000.0) * float(cfg["cost_per_1m_input_usd"]) + (out_tok / 1_000_000.0) * float(cfg["cost_per_1m_output_usd"])
            total_known_cost_usd += m_cost
            cost_label = f"${m_cost:.4f}"
        else:
            m_cost = None
            has_unknown_price = True
            cost_label = "ціна не задана"

        rpm = cfg.get("rpm")
        rpd = cfg.get("rpd")
        used_today = get_daily_usage_count(m_id)
        remaining_rpd = max(0, int(rpd) - used_today) if rpd else None
        quota_exceeded = bool(remaining_rpd is not None and calls_per_model > remaining_rpd)
        if quota_exceeded:
            quota_warnings.append(
                f"{cfg.get('name', m_id)}: потрібно {calls_per_model} зап., залишок RPD {remaining_rpd}/{rpd}"
            )

        sec_by_latency = calls_per_model * 2.2
        sec_by_rpm = (calls_per_model / float(rpm)) * 60.0 if (rpm and rpm > 0) else 0.0
        est_seconds_total += max(sec_by_latency, sec_by_rpm)

        models_breakdown.append(
            {
                "model_id": m_id,
                "name": cfg.get("name", m_id),
                "calls": calls_per_model,
                "est_input_tokens": in_tok,
                "est_output_tokens": out_tok,
                "price_known": price_known,
                "est_cost_usd": round(m_cost, 6) if m_cost is not None else None,
                "cost_label": cost_label,
                "rpm": rpm,
                "rpd": rpd,
                "used_today": used_today,
                "remaining_rpd": remaining_rpd,
                "quota_exceeded": quota_exceeded,
            }
        )

    judge_breakdown = None
    if total_judge_calls > 0 and judge_model_id:
        j_cfg = get_model_config(str(judge_model_id))
        j_price_known = bool(j_cfg.get("price_known", False))
        j_in = total_judge_calls * judge_in_tokens
        j_out = total_judge_calls * judge_out_tokens
        if j_price_known and j_cfg.get("cost_per_1m_input_usd") is not None and j_cfg.get("cost_per_1m_output_usd") is not None:
            j_cost = (j_in / 1_000_000.0) * float(j_cfg["cost_per_1m_input_usd"]) + (j_out / 1_000_000.0) * float(j_cfg["cost_per_1m_output_usd"])
            total_known_cost_usd += j_cost
            j_cost_label = f"${j_cost:.4f}"
        else:
            j_cost = None
            has_unknown_price = True
            j_cost_label = "ціна не задана"
        j_rpd = j_cfg.get("rpd")
        j_used = get_daily_usage_count(str(judge_model_id))
        j_rem = max(0, int(j_rpd) - j_used) if j_rpd else None
        if j_rem is not None and total_judge_calls > j_rem:
            quota_warnings.append(
                f"Суддя {j_cfg.get('name', judge_model_id)}: потрібно {total_judge_calls} зап., залишок RPD {j_rem}/{j_rpd}"
            )
        j_rpm = j_cfg.get("rpm")
        est_seconds_total += max(total_judge_calls * 2.0, (total_judge_calls / float(j_rpm)) * 60.0 if (j_rpm and j_rpm > 0) else 0.0)
        judge_breakdown = {
            "model_id": str(judge_model_id),
            "name": j_cfg.get("name", str(judge_model_id)),
            "calls": total_judge_calls,
            "est_input_tokens": j_in,
            "est_output_tokens": j_out,
            "price_known": j_price_known,
            "est_cost_usd": round(j_cost, 6) if j_cost is not None else None,
            "cost_label": j_cost_label,
        }

    return {
        "queries_count": selected_queries_count,
        "calls_per_model": calls_per_model,
        "total_generation_calls": total_generation_calls,
        "total_judge_calls": total_judge_calls,
        "total_api_calls": total_generation_calls + total_judge_calls,
        "est_total_cost_usd": round(total_known_cost_usd, 5),
        "has_unknown_price": has_unknown_price,
        "est_duration_sec": int(round(est_seconds_total)),
        "quota_sufficient": len(quota_warnings) == 0,
        "quota_warnings": quota_warnings,
        "models": models_breakdown,
        "judge": judge_breakdown,
    }



