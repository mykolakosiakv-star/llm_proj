"""
Модуль роботи з локальною реляційною базою даних SQLite.
Забезпечує створення таблиць та адитивну міграцію схеми даних без втрати накопичених результатів.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from functools import lru_cache
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.config import DB_PATH, BASE_DIR


# Версія схеми зберігається в PRAGMA user_version. Міграції лишаються адитивними (_ensure_column, CREATE IF NOT EXISTS)
SCHEMA_VERSION = 2


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Зовнішні ключі в SQLite вмикаються окремо для кожного з'єднання
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def compute_template_hash(system_template: str, user_template: str) -> str:
    payload = f"SYS:{system_template.strip()}\nUSR:{user_template.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def compute_code_version_hash() -> str:
    app_dir = BASE_DIR / "app"
    hasher = hashlib.sha256()
    files_to_hash: List[Path] = []
    if app_dir.exists():
        files_to_hash.extend(sorted(app_dir.rglob("*.py")))
        static_dir = app_dir / "static"
        if static_dir.exists():
            for p in sorted(static_dir.rglob("*")):
                if p.is_file():
                    files_to_hash.append(p)

    for file_path in files_to_hash:
        try:
            rel = file_path.relative_to(BASE_DIR).as_posix()
            hasher.update(rel.encode("utf-8"))
            hasher.update(file_path.read_bytes())
        except Exception:
            continue
    return hasher.hexdigest()[:16]


_DATASET_HASH_CACHE: Optional[str] = None


def compute_dataset_hash() -> str:
    global _DATASET_HASH_CACHE
    if _DATASET_HASH_CACHE is not None:
        return _DATASET_HASH_CACHE
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT code, domain, category, user_query, context_fact, reference_answer, required_keywords_json, expected_escalation, complexity FROM queries ORDER BY code ASC"
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    _DATASET_HASH_CACHE = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return _DATASET_HASH_CACHE


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, col_def: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row["name"] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS domains (
            code TEXT PRIMARY KEY,
            domain_name TEXT NOT NULL,
            company TEXT NOT NULL,
            short_uk TEXT NOT NULL DEFAULT '',
            description_uk TEXT NOT NULL DEFAULT '',
            escalation_rules TEXT NOT NULL DEFAULT '',
            risk_profile_uk TEXT NOT NULL DEFAULT '',
            default_preset TEXT NOT NULL DEFAULT 'balanced',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            domain TEXT NOT NULL DEFAULT 'fintech',
            category TEXT NOT NULL,
            user_query TEXT NOT NULL,
            context_fact TEXT NOT NULL,
            reference_answer TEXT NOT NULL,
            required_keywords_json TEXT NOT NULL,
            expected_escalation INTEGER NOT NULL DEFAULT 0,
            complexity INTEGER NOT NULL DEFAULT 2,
            source TEXT NOT NULL DEFAULT 'manual',
            review_status TEXT NOT NULL DEFAULT 'reviewed'
        )
        """
    )
    _ensure_column(cur, "queries", "domain", "TEXT NOT NULL DEFAULT 'fintech'")
    _ensure_column(cur, "queries", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _ensure_column(cur, "queries", "review_status", "TEXT NOT NULL DEFAULT 'reviewed'")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name_uk TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            description_uk TEXT NOT NULL,
            system_template TEXT NOT NULL,
            user_template TEXT NOT NULL,
            order_index INTEGER NOT NULL DEFAULT 1,
            current_version INTEGER NOT NULL DEFAULT 1,
            template_hash TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _ensure_column(cur, "prompts", "current_version", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cur, "prompts", "template_hash", "TEXT NOT NULL DEFAULT ''")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            version INTEGER NOT NULL,
            name_uk TEXT NOT NULL DEFAULT '',
            strategy_type TEXT NOT NULL DEFAULT '',
            description_uk TEXT NOT NULL DEFAULT '',
            system_template TEXT NOT NULL,
            user_template TEXT NOT NULL,
            template_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(code, version)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            models_json TEXT NOT NULL,
            prompts_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            repeats INTEGER NOT NULL DEFAULT 1,
            total_items INTEGER NOT NULL DEFAULT 0,
            completed_items INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            summary_json TEXT NOT NULL DEFAULT '{}',
            is_legacy INTEGER NOT NULL DEFAULT 0,
            dataset_version TEXT NOT NULL DEFAULT '2.0',
            dataset_hash TEXT NOT NULL DEFAULT '',
            code_version TEXT NOT NULL DEFAULT '',
            settings_snapshot_json TEXT NOT NULL DEFAULT '{}',
            context_mode TEXT NOT NULL DEFAULT 'gold_context',
            top_k INTEGER NOT NULL DEFAULT 3
        )
        """
    )
    _ensure_column(cur, "experiment_runs", "is_legacy", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, "experiment_runs", "dataset_version", "TEXT NOT NULL DEFAULT '2.0'")
    _ensure_column(cur, "experiment_runs", "dataset_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(cur, "experiment_runs", "code_version", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(cur, "experiment_runs", "settings_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(cur, "experiment_runs", "context_mode", "TEXT NOT NULL DEFAULT 'gold_context'")
    _ensure_column(cur, "experiment_runs", "top_k", "INTEGER NOT NULL DEFAULT 3")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            query_id INTEGER NOT NULL,
            query_code TEXT NOT NULL,
            category TEXT NOT NULL,
            prompt_code TEXT NOT NULL,
            model_id TEXT NOT NULL,
            repeat_index INTEGER NOT NULL DEFAULT 1,
            generated_answer TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            fact_coverage_pct REAL NOT NULL,
            semantic_similarity_pct REAL NOT NULL,
            format_compliance_pct REAL NOT NULL,
            relevance_score REAL NOT NULL,
            factuality_score REAL NOT NULL,
            completeness_score REAL NOT NULL,
            clarity_score REAL NOT NULL,
            format_score REAL NOT NULL,
            nist_composite_pct REAL NOT NULL,
            confidence_score REAL NOT NULL,
            requires_escalation INTEGER NOT NULL DEFAULT 0,
            escalation_correct INTEGER NOT NULL DEFAULT 1,
            automation_success INTEGER NOT NULL DEFAULT 1,
            integral_efficiency_pct REAL NOT NULL,
            matched_keywords_json TEXT NOT NULL DEFAULT '[]',
            missing_keywords_json TEXT NOT NULL DEFAULT '[]',
            provider_used TEXT NOT NULL DEFAULT 'live_api',
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES experiment_runs(id) ON DELETE CASCADE
        )
        """
    )

    eval_new_columns = [
        ("prompt_version_id", "INTEGER DEFAULT NULL"),
        ("prompt_version", "INTEGER NOT NULL DEFAULT 1"),
        ("rendered_system_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("rendered_user_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("template_hash", "TEXT NOT NULL DEFAULT ''"),
        ("few_shot_used_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("temperature", "REAL NOT NULL DEFAULT 0.0"),
        ("max_output_tokens", "INTEGER NOT NULL DEFAULT 2000"),
        ("seed", "INTEGER DEFAULT NULL"),
        ("finish_reason", "TEXT NOT NULL DEFAULT 'stop'"),
        ("truncated", "INTEGER NOT NULL DEFAULT 0"),
        ("attempts", "INTEGER NOT NULL DEFAULT 1"),
        ("raw_usage_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("thinking_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("tokens_estimated", "INTEGER NOT NULL DEFAULT 0"),
        ("call_ms", "REAL NOT NULL DEFAULT 0.0"),
        ("wall_ms", "REAL NOT NULL DEFAULT 0.0"),
        ("source", "TEXT NOT NULL DEFAULT 'batch'"),
        ("query_code", "TEXT NOT NULL DEFAULT ''"),
        ("rouge_l_f1_pct", "REAL NOT NULL DEFAULT 0.0"),
        ("stem_cosine_pct", "REAL NOT NULL DEFAULT 0.0"),
        ("embedding_available", "INTEGER NOT NULL DEFAULT 0"),
        ("format_checks_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("hallucinated_items_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("hallucination_count", "INTEGER NOT NULL DEFAULT 0"),
        ("has_hallucination", "INTEGER NOT NULL DEFAULT 0"),
        ("escalation_regex", "INTEGER NOT NULL DEFAULT 0"),
        ("escalation_judge", "INTEGER DEFAULT NULL"),
        ("escalation_disagreement", "INTEGER NOT NULL DEFAULT 0"),
        ("escalation_method", "TEXT NOT NULL DEFAULT 'regex'"),
        ("judge_scores_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("judge_rationale_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("judge_model", "TEXT NOT NULL DEFAULT ''"),
        ("judge_prompt_hash", "TEXT NOT NULL DEFAULT ''"),
        ("rubric_version", "TEXT NOT NULL DEFAULT ''"),
        ("rubric_hash", "TEXT NOT NULL DEFAULT ''"),
        ("policy_adherence_score", "REAL NOT NULL DEFAULT 0.0"),
        ("rubric_composite_pct", "REAL DEFAULT NULL"),
        ("rubric_evaluated", "INTEGER NOT NULL DEFAULT 0"),
        ("manual_scored", "INTEGER NOT NULL DEFAULT 0"),
        ("manual_annotator", "TEXT NOT NULL DEFAULT ''"),
        ("manual_notes", "TEXT NOT NULL DEFAULT ''"),
        ("legacy_heuristic_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("context_mode", "TEXT NOT NULL DEFAULT 'gold_context'"),
        ("top_k", "INTEGER NOT NULL DEFAULT 3"),
        ("retrieved_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("retrieval_hit_at_1", "INTEGER DEFAULT NULL"),
        ("retrieval_hit_at_3", "INTEGER DEFAULT NULL"),
        ("contract_ok", "INTEGER NOT NULL DEFAULT 1"),
        ("parse_method", "TEXT NOT NULL DEFAULT 'json'"),
        ("parsed_reason", "TEXT NOT NULL DEFAULT ''"),
        ("self_judged", "INTEGER NOT NULL DEFAULT 0"),
        ("judge_status", "TEXT NOT NULL DEFAULT 'ok'"),
        ("human_scores_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for col_name, col_def in eval_new_columns:
        _ensure_column(cur, "evaluation_results", col_name, col_def)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS run_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            query_code TEXT NOT NULL,
            model_id TEXT NOT NULL,
            prompt_code TEXT NOT NULL,
            repeat_index INTEGER NOT NULL DEFAULT 1,
            error_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES experiment_runs(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS judge_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            answer_hash TEXT NOT NULL,
            rubric_hash TEXT NOT NULL,
            judge_model TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            annotators_count INTEGER NOT NULL DEFAULT 1,
            metrics_json TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT ''
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS model_registry (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'discovered',
            enabled INTEGER NOT NULL DEFAULT 0,
            route TEXT NOT NULL DEFAULT 'direct',
            base_url TEXT NOT NULL DEFAULT '',
            api_key_env TEXT NOT NULL DEFAULT '',
            context_window INTEGER DEFAULT NULL,
            input_price_per_1m REAL DEFAULT NULL,
            output_price_per_1m REAL DEFAULT NULL,
            price_source TEXT NOT NULL DEFAULT 'unknown',
            limits_json TEXT NOT NULL DEFAULT '{}',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS model_status (
            model_key TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'unchecked',
            http_code INTEGER DEFAULT NULL,
            latency_ms INTEGER DEFAULT NULL,
            message TEXT NOT NULL DEFAULT '',
            checked_at TEXT DEFAULT NULL
        )
        """
    )

    _ensure_column(cur, "model_status", "check_kind", "TEXT NOT NULL DEFAULT 'ping'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_status (
            provider TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'unchecked',
            http_status INTEGER DEFAULT NULL,
            latency_ms INTEGER DEFAULT NULL,
            models_count INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL DEFAULT '{}',
            listed_at TEXT DEFAULT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS model_daily_usage (
            model_key TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            requests_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (model_key, usage_date)
        )
        """
    )

    # Складені індекси під запити аналітики й перегляду замірів; поодинокі індекси за model, prompt, category
    # мали низьку вибірковість і більше не потрібні
    for obsolete in ("idx_eval_model", "idx_eval_prompt", "idx_eval_category", "idx_eval_context_mode"):
        cur.execute(f"DROP INDEX IF EXISTS {obsolete}")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_eval_run ON evaluation_results(run_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_eval_run_cell ON evaluation_results(run_id, model_id, prompt_code, query_code)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_eval_run_source ON evaluation_results(run_id, source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_eval_query ON evaluation_results(query_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_mode_status ON experiment_runs(execution_mode, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_queries_domain_category ON queries(domain, category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_run_errors_run ON run_errors(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_model_reg_provider ON model_registry(provider)")

    cur.execute(
        """
        UPDATE experiment_runs
        SET status = 'incomplete'
        WHERE completed_items < total_items AND status = 'completed' AND execution_mode != 'sandbox'
        """
    )

    cur.execute("DELETE FROM evaluation_results WHERE run_id NOT IN (SELECT id FROM experiment_runs)")
    cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    # WAL дозволяє читати інтерфейсу, поки фонова серія пише результати; режим зберігається у файлі бази
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.close()


def fetch_all_domains() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM domains ORDER BY code ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["id"] = r["code"]
        r["name_uk"] = r["domain_name"]
    return rows


def fetch_domain_by_code(code: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM domains WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["id"] = item["code"]
    item["name_uk"] = item["domain_name"]
    return item


def upsert_domain(data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    code = str(data.get("code") or data.get("id") or "custom").strip()
    domain_name = str(data.get("domain_name") or data.get("name_uk") or code).strip()
    company = str(data.get("company") or domain_name).strip()
    short_uk = str(data.get("short_uk") or domain_name).strip()
    description_uk = str(data.get("description_uk") or "").strip()
    escalation_rules = str(
        data.get("escalation_rules")
        or "підозра на шахрайство, компрометація облікового запису, несанкціоноване списання коштів, юридична претензія або відсутність потрібних даних у базі знань"
    ).strip()
    risk_profile_uk = str(data.get("risk_profile_uk") or "").strip()
    default_preset = str(data.get("default_preset") or "balanced").strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        INSERT INTO domains (
            code, domain_name, company, short_uk, description_uk,
            escalation_rules, risk_profile_uk, default_preset, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            domain_name = excluded.domain_name,
            company = excluded.company,
            short_uk = excluded.short_uk,
            description_uk = excluded.description_uk,
            escalation_rules = excluded.escalation_rules,
            risk_profile_uk = excluded.risk_profile_uk,
            default_preset = excluded.default_preset
        """,
        (
            code,
            domain_name,
            company,
            short_uk,
            description_uk,
            escalation_rules,
            risk_profile_uk,
            default_preset,
            now_str,
        ),
    )
    conn.commit()
    conn.close()
    return fetch_domain_by_code(code) or data


def fetch_all_queries(category: Optional[str] = None, domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Повертає список типових запитів з бази даних (опціонально фільтруючи за категорією та доменом)."""
    conn = get_connection()
    cur = conn.cursor()
    sql = "SELECT * FROM queries WHERE 1=1"
    params: List[Any] = []
    if category and category != "all":
        sql += " AND category = ?"
        params.append(category)
    if domain and domain != "all":
        sql += " AND domain = ?"
        params.append(domain)
    sql += " ORDER BY id ASC"
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        item = dict(r)
        item["domain"] = item.get("domain") or "fintech"
        item["source"] = item.get("source") or "manual"
        item["review_status"] = item.get("review_status") or "reviewed"
        item["required_keywords"] = json.loads(item.pop("required_keywords_json", "[]"))
        item["expected_escalation"] = bool(item["expected_escalation"])
        result.append(item)
    return result


def fetch_query_by_id(query_id: int) -> Optional[Dict[str, Any]]:
    """Повертає типовий запит за його числовим ідентифікатором."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM queries WHERE id = ?", (query_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["domain"] = item.get("domain") or "fintech"
    item["source"] = item.get("source") or "manual"
    item["review_status"] = item.get("review_status") or "reviewed"
    item["required_keywords"] = json.loads(item.pop("required_keywords_json", "[]"))
    item["expected_escalation"] = bool(item["expected_escalation"])
    return item


def upsert_query_item(data: Dict[str, Any]) -> Dict[str, Any]:
    global _DATASET_HASH_CACHE
    _DATASET_HASH_CACHE = None
    """Додає новий або оновлює існуючий типовий запит за його кодом."""
    conn = get_connection()
    cur = conn.cursor()
    kw_json = json.dumps(data.get("required_keywords", []), ensure_ascii=False)
    esc = 1 if data.get("expected_escalation") else 0
    comp = int(data.get("complexity", 2))
    dom = str(data.get("domain") or "fintech")
    src = str(data.get("source") or "manual")
    if src not in ("manual", "llm_draft"):
        src = "manual"
    rev = str(data.get("review_status") or ("draft" if src == "llm_draft" else "reviewed"))
    if rev not in ("draft", "reviewed"):
        rev = "reviewed"

    cur.execute(
        """
        INSERT INTO queries (
            code, domain, category, user_query, context_fact, reference_answer,
            required_keywords_json, expected_escalation, complexity, source, review_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            domain = excluded.domain,
            category = excluded.category,
            user_query = excluded.user_query,
            context_fact = excluded.context_fact,
            reference_answer = excluded.reference_answer,
            required_keywords_json = excluded.required_keywords_json,
            expected_escalation = excluded.expected_escalation,
            complexity = excluded.complexity,
            source = excluded.source,
            review_status = excluded.review_status
        """,
        (
            data["code"],
            dom,
            data["category"],
            data["user_query"],
            data["context_fact"],
            data["reference_answer"],
            kw_json,
            esc,
            comp,
            src,
            rev,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM queries WHERE code = ?", (data["code"],))
    row = dict(cur.fetchone())
    conn.close()
    row["domain"] = row.get("domain") or "fintech"
    row["source"] = row.get("source") or "manual"
    row["review_status"] = row.get("review_status") or "reviewed"
    row["required_keywords"] = json.loads(row.pop("required_keywords_json", "[]"))
    row["expected_escalation"] = bool(row["expected_escalation"])
    return row


def fetch_all_prompts() -> List[Dict[str, Any]]:
    """Повертає всі стратегії формування промптів (P1..P5) разом з поточною версією та хешем."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM prompts ORDER BY order_index ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def fetch_prompt_by_code(code: str) -> Optional[Dict[str, Any]]:
    """Повертає актуальний шаблон промпту за його кодом (напр. P1, P2, P3, P4, P5)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM prompts WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    prompt_dict = dict(row)
    cur.execute(
        "SELECT id FROM prompt_versions WHERE code = ? AND version = ?",
        (code, prompt_dict.get("current_version", 1)),
    )
    ver_row = cur.fetchone()
    conn.close()
    prompt_dict["prompt_version_id"] = ver_row["id"] if ver_row else None
    return prompt_dict


def fetch_prompt_version_by_id(version_id: int) -> Optional[Dict[str, Any]]:
    """Повертає конкретну історичну версію промпту за її ID у таблиці prompt_versions."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_prompt_template(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Оновлює шаблон промпту у базі даних та створює новий запис у prompt_versions,
    якщо змінився system_template або user_template.
    """
    conn = get_connection()
    cur = conn.cursor()
    code = data["code"]
    sys_tmpl = data["system_template"]
    usr_tmpl = data["user_template"]
    new_hash = compute_template_hash(sys_tmpl, usr_tmpl)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("SELECT * FROM prompts WHERE code = ?", (code,))
    existing = cur.fetchone()
    if existing:
        ex_dict = dict(existing)
        old_hash = ex_dict.get("template_hash") or compute_template_hash(
            ex_dict["system_template"], ex_dict["user_template"]
        )
        cur_ver = int(ex_dict.get("current_version") or 1)
        if new_hash != old_hash:
            cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM prompt_versions WHERE code = ?", (code,))
            max_v = int(cur.fetchone()["max_v"])
            next_ver = max(cur_ver, max_v) + 1
            cur.execute(
                """
                INSERT INTO prompt_versions (
                    code, version, name_uk, strategy_type, description_uk,
                    system_template, user_template, template_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    next_ver,
                    data["name_uk"],
                    data["strategy_type"],
                    data["description_uk"],
                    sys_tmpl,
                    usr_tmpl,
                    new_hash,
                    now_str,
                ),
            )
            cur_ver = next_ver

        cur.execute(
            """
            UPDATE prompts
            SET name_uk = ?, strategy_type = ?, description_uk = ?,
                system_template = ?, user_template = ?,
                current_version = ?, template_hash = ?
            WHERE code = ?
            """,
            (
                data["name_uk"],
                data["strategy_type"],
                data["description_uk"],
                sys_tmpl,
                usr_tmpl,
                cur_ver,
                new_hash,
                code,
            ),
        )
    conn.commit()
    conn.close()
    return fetch_prompt_by_code(code) or data


def log_run_error(
    run_id: int,
    query_code: str,
    model_id: str,
    prompt_code: str,
    repeat_index: int,
    error_type: str,
    message: str,
) -> None:
    """Записує помилку окремого виклику LLM під час серійного експерименту до таблиці run_errors."""
    conn = get_connection()
    cur = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO run_errors (
            run_id, query_code, model_id, prompt_code, repeat_index, error_type, message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, query_code, model_id, prompt_code, repeat_index, error_type, message[:1000], now_str),
    )
    conn.commit()
    conn.close()


def fetch_run_errors(run_id: int) -> List[Dict[str, Any]]:
    """Повертає список усіх зафіксованих помилок для заданого запуску експерименту."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM run_errors WHERE run_id = ? ORDER BY id ASC", (run_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def ensure_sandbox_run_id() -> int:
    """
    Повертає ID спеціального контейнера experiment_runs для інтерактивних запусків
    пісочниці та Арени (source='sandbox'), щоб вони зберігалися в БД, але не змішувалися
    із серійними експериментами.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM experiment_runs WHERE execution_mode = 'sandbox' LIMIT 1")
    row = cur.fetchone()
    if row:
        run_id = int(row["id"])
        conn.close()
        return run_id

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO experiment_runs (
            title, description, created_at, execution_mode,
            models_json, prompts_json, categories_json,
            repeats, total_items, completed_items, status,
            is_legacy, dataset_version, dataset_hash, code_version
        ) VALUES (?, ?, ?, 'sandbox', '[]', '[]', '[]', 1, 0, 0, 'completed', 0, '2.0', ?, ?)
        """,
        (
            "Інтерактивна пісочниця та Арена (Sandbox)",
            "Журнал одиничних та порівняльних запусків із вкладки «Інтерактивний конвеєр»",
            now_str,
            compute_dataset_hash(),
            compute_code_version_hash(),
        ),
    )
    run_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return run_id


def pause_active_runs_for_quota(model_id: str, message: str, resume_after: Optional[str] = None) -> List[int]:
    """Переводить активні серії експериментів у статус paused_quota при вичерпанні денного ліміту."""
    conn = get_connection()
    cur = conn.cursor()
    paused_ids: List[int] = []
    try:
        cur.execute("SELECT id, summary_json FROM experiment_runs WHERE status = 'running' AND execution_mode != 'sandbox'")
        rows = cur.fetchall()
        for r in rows:
            run_id = int(r["id"])
            paused_ids.append(run_id)
            try:
                summary = json.loads(r["summary_json"] or "{}")
            except Exception:
                summary = {}
            summary["quota_paused"] = True
            summary["quota_model_id"] = model_id
            summary["quota_message"] = message
            summary["resume_after"] = resume_after or "після оновлення добової квоти (Gemini: опівночі за тихоокеанським часом, інші: 00:00 UTC)"
            cur.execute(
                "UPDATE experiment_runs SET status = 'paused_quota', summary_json = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False), run_id),
            )
        conn.commit()
    finally:
        conn.close()
    return paused_ids


KNOWN_RUN_STATUSES = ("running", "completed", "incomplete", "failed", "cancelled", "paused_quota")


def sync_run_counters() -> int:
    """Приводить лічильники й статуси серій у відповідність до фактично збережених замірів. Повертає кількість серій."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE experiment_runs SET completed_items = "
            "(SELECT COUNT(*) FROM evaluation_results WHERE run_id = experiment_runs.id)"
        )
        conn.execute(
            "UPDATE experiment_runs SET status = 'incomplete' WHERE execution_mode != 'sandbox' "
            "AND status = 'completed' AND total_items > 0 AND completed_items < total_items"
        )
        conn.execute(
            "UPDATE experiment_runs SET status = 'completed' WHERE execution_mode != 'sandbox' "
            "AND status = 'paused_quota' AND total_items > 0 AND completed_items >= total_items"
        )
        placeholders = ",".join("?" for _ in KNOWN_RUN_STATUSES)
        conn.execute(
            "UPDATE experiment_runs SET status = CASE "
            "WHEN total_items > 0 AND completed_items = total_items THEN 'completed' "
            "WHEN completed_items > 0 THEN 'incomplete' ELSE 'failed' END "
            f"WHERE execution_mode != 'sandbox' AND status NOT IN ({placeholders})",
            KNOWN_RUN_STATUSES,
        )
        conn.commit()
        return int(conn.execute("SELECT COUNT(*) FROM experiment_runs WHERE execution_mode != 'sandbox'").fetchone()[0])
    finally:
        conn.close()


def _hydrate_registry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out.get("enabled"))
    try:
        out["limits"] = json.loads(out.get("limits_json") or "{}")
    except Exception:
        out["limits"] = {}
    return out


def fetch_all_model_registry() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM model_registry ORDER BY provider ASC, display_name ASC")
        rows = [_hydrate_registry_row(dict(r)) for r in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def fetch_model_registry_by_id(reg_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM model_registry WHERE id = ? OR model_id = ? LIMIT 1", (reg_id, reg_id))
        row = cur.fetchone()
        return _hydrate_registry_row(dict(row)) if row else None
    except Exception:
        return None
    finally:
        conn.close()


LIMIT_SOURCE_RANK = {"catalog": 1, "header": 2, "manual": 3}


def _merge_limits(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Ліміти мають пріоритет за джерелом: вручну, потім із заголовків відповіді, потім припущення каталогу.

    Автопошук моделей надсилає припущення каталогу й не повинен затирати значення, які вже уточнені точніше.
    """
    merged = {**existing, **{k: v for k, v in new.items() if v is not None and k != "limits_source"}}
    ex_src = dict(existing.get("limits_source") or {})
    new_src = new.get("limits_source") or {}
    for key in ("rpm", "rpd", "tpm"):
        if new.get(key) is None:
            continue
        incoming = new_src.get(key, "catalog")
        if LIMIT_SOURCE_RANK.get(incoming, 1) >= LIMIT_SOURCE_RANK.get(ex_src.get(key, "catalog"), 1):
            ex_src[key] = incoming
        elif existing.get(key) is not None:
            merged[key] = existing[key]
    merged["limits_source"] = ex_src
    return merged


def upsert_model_registry_entry(entry: Dict[str, Any], preserve_user_fields: bool = True) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reg_id = str(entry["id"]).strip()
    try:
        cur.execute("SELECT * FROM model_registry WHERE id = ?", (reg_id,))
        existing = cur.fetchone()
        limits_obj = entry.get("limits")
        if limits_obj is not None:
            limits_json = json.dumps(limits_obj, ensure_ascii=False)
        else:
            limits_json = str(entry.get("limits_json") or "{}")

        if existing is None:
            first_seen = str(entry.get("first_seen") or now_str)
            last_seen = str(entry.get("last_seen") or now_str)
            cur.execute(
                """
                INSERT INTO model_registry (
                    id, provider, model_id, display_name, source, enabled, route,
                    base_url, api_key_env, context_window, input_price_per_1m,
                    output_price_per_1m, price_source, limits_json, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reg_id,
                    str(entry.get("provider") or "custom"),
                    str(entry.get("model_id") or reg_id),
                    str(entry.get("display_name") or entry.get("model_id") or reg_id),
                    str(entry.get("source") or "discovered"),
                    1 if entry.get("enabled") else 0,
                    str(entry.get("route") or "direct"),
                    str(entry.get("base_url") or ""),
                    str(entry.get("api_key_env") or ""),
                    entry.get("context_window"),
                    entry.get("input_price_per_1m"),
                    entry.get("output_price_per_1m"),
                    str(entry.get("price_source") or "unknown"),
                    limits_json,
                    first_seen,
                    last_seen,
                ),
            )
        else:
            ex = dict(existing)
            enabled_val = int(ex["enabled"]) if (preserve_user_fields and "enabled" not in entry) else (1 if entry.get("enabled") else 0)
            route_val = str(ex["route"]) if (preserve_user_fields and "route" not in entry) else str(entry.get("route") or ex["route"] or "direct")
            source_val = str(ex["source"]) if preserve_user_fields else str(entry.get("source") or ex["source"])

            # Якщо ціна вже була задана вручну (manual), не перезаписуємо її при автоматичному опитуванні
            if preserve_user_fields and ex.get("price_source") == "manual" and entry.get("price_source") != "manual":
                in_p = ex.get("input_price_per_1m")
                out_p = ex.get("output_price_per_1m")
                p_src = "manual"
            else:
                in_p = entry.get("input_price_per_1m", ex.get("input_price_per_1m"))
                out_p = entry.get("output_price_per_1m", ex.get("output_price_per_1m"))
                p_src = str(entry.get("price_source") or ex.get("price_source") or "unknown")

            try:
                ex_lim = json.loads(ex.get("limits_json") or "{}")
            except Exception:
                ex_lim = {}
            try:
                new_lim = json.loads(limits_json or "{}")
            except Exception:
                new_lim = {}
            merged_lim = _merge_limits(ex_lim, new_lim)

            cur.execute(
                """
                UPDATE model_registry
                SET provider = ?, model_id = ?, display_name = ?, source = ?,
                    enabled = ?, route = ?, base_url = ?, api_key_env = ?,
                    context_window = ?, input_price_per_1m = ?, output_price_per_1m = ?,
                    price_source = ?, limits_json = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    str(entry.get("provider") or ex["provider"]),
                    str(entry.get("model_id") or ex["model_id"]),
                    str(entry.get("display_name") or ex["display_name"]),
                    source_val,
                    enabled_val,
                    route_val,
                    str(entry.get("base_url") if entry.get("base_url") is not None else ex["base_url"]),
                    str(entry.get("api_key_env") if entry.get("api_key_env") is not None else ex["api_key_env"]),
                    entry.get("context_window") if entry.get("context_window") is not None else ex.get("context_window"),
                    in_p,
                    out_p,
                    p_src,
                    json.dumps(merged_lim, ensure_ascii=False),
                    str(entry.get("last_seen") or now_str),
                    reg_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return fetch_model_registry_by_id(reg_id) or entry


def update_model_registry_flags(
    enabled_ids: Optional[List[str]] = None,
    routes_map: Optional[Dict[str, str]] = None,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        if enabled_ids is not None:
            enabled_set = {str(x).strip() for x in enabled_ids if str(x).strip()}
            cur.execute("UPDATE model_registry SET enabled = 0")
            for mid in enabled_set:
                cur.execute("UPDATE model_registry SET enabled = 1 WHERE id = ? OR model_id = ?", (mid, mid))
        if routes_map:
            for mid, r_val in routes_map.items():
                norm_route = "openrouter" if r_val == "openrouter" else "direct"
                cur.execute(
                    "UPDATE model_registry SET route = ? WHERE id = ? OR model_id = ?",
                    (norm_route, mid, mid),
                )
        conn.commit()
    finally:
        conn.close()


def delete_custom_model_from_registry(reg_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM model_registry WHERE id = ? AND source = 'custom'", (reg_id,))
        deleted = cur.rowcount > 0
        if deleted:
            cur.execute("DELETE FROM model_status WHERE model_key = ?", (reg_id,))
        conn.commit()
        return deleted
    finally:
        conn.close()


def fetch_judge_cache(cache_key: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT response_json FROM judge_cache WHERE cache_key = ?", (cache_key,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["response_json"]) if row else None


def store_judge_cache(cache_key: str, answer_hash: str, rubric_hash: str, judge_model: str, result: Dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO judge_cache (cache_key, answer_hash, rubric_hash, judge_model, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cache_key, answer_hash, rubric_hash, judge_model,
             json.dumps(result, ensure_ascii=False, default=str), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_provider_status(provider: str, report: Dict[str, Any], listed_at: str) -> None:
    extra = {k: v for k, v in report.items() if k in ("account", "configured")}
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO provider_status (provider, status, http_status, latency_ms, models_count, error, extra_json, listed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                status = excluded.status, http_status = excluded.http_status, latency_ms = excluded.latency_ms,
                models_count = excluded.models_count, error = excluded.error,
                extra_json = excluded.extra_json, listed_at = excluded.listed_at
            """,
            (
                provider, str(report.get("status") or "unchecked"), report.get("http_status"), report.get("latency_ms"),
                int(report.get("models_total") or 0), str(report.get("error") or "")[:400],
                json.dumps(extra, ensure_ascii=False, default=str), listed_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_all_provider_statuses() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM provider_status").fetchall()
    finally:
        conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        d["checked_at"] = d.pop("listed_at")
        d.update(json.loads(d.pop("extra_json") or "{}"))
        out[d["provider"]] = d
    return out


def fetch_all_model_statuses() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        cur.execute("SELECT * FROM model_status")
        for r in cur.fetchall():
            d = dict(r)
            out[d["model_key"]] = d
    except Exception:
        pass
    finally:
        conn.close()
    return out


def upsert_model_status(
    model_key: str,
    status: str,
    http_code: Optional[int] = None,
    latency_ms: Optional[int] = None,
    message: str = "",
    checked_at: Optional[str] = None,
    check_kind: str = "ping",
) -> None:
    """check_kind: ping (перевірено запитом), list (за списком провайдера), provider (успадковано від провайдера)."""
    conn = get_connection()
    cur = conn.cursor()
    ts = checked_at if checked_at is not None else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur.execute(
            """
            INSERT INTO model_status (model_key, status, http_code, latency_ms, message, checked_at, check_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_key) DO UPDATE SET
                status = excluded.status,
                http_code = excluded.http_code,
                latency_ms = excluded.latency_ms,
                message = excluded.message,
                checked_at = excluded.checked_at,
                check_kind = excluded.check_kind
            """,
            (str(model_key), str(status), http_code, latency_ms, str(message or "")[:400], ts, check_kind),
        )
        conn.commit()
    finally:
        conn.close()


def _nth_sunday(year: int, month: int, n: int) -> int:
    first_weekday = datetime(year, month, 1).weekday()
    return 1 + (6 - first_weekday) % 7 + 7 * (n - 1)


def _pacific_now(utc_now: datetime) -> datetime:
    """Тихоокеанський час без tzdata (на Windows його може не бути): літній час США діє з 2-ї неділі березня до 1-ї неділі листопада."""
    year = utc_now.year
    dst_start = datetime(year, 3, _nth_sunday(year, 3, 2), 10)
    dst_end = datetime(year, 11, _nth_sunday(year, 11, 1), 9)
    offset = 7 if dst_start <= utc_now < dst_end else 8
    return utc_now - timedelta(hours=offset)


def current_usage_day(model_key: str) -> str:
    """Gemini скидає добовий ліміт опівночі за тихоокеанським часом, решта провайдерів за UTC."""
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    moment = _pacific_now(utc_now) if "gemini" in model_key.lower() else utc_now
    return moment.strftime("%Y-%m-%d")


def get_daily_usage_count(model_key: str, usage_date: Optional[str] = None) -> int:
    day_str = usage_date or current_usage_day(model_key)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT requests_count FROM model_daily_usage WHERE model_key = ? AND usage_date = ?",
            (str(model_key), day_str),
        )
        row = cur.fetchone()
        return int(row["requests_count"]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def increment_daily_usage_count(model_key: str, usage_date: Optional[str] = None, delta: int = 1) -> int:
    day_str = usage_date or current_usage_day(model_key)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO model_daily_usage (model_key, usage_date, requests_count)
            VALUES (?, ?, ?)
            ON CONFLICT(model_key, usage_date) DO UPDATE SET
                requests_count = model_daily_usage.requests_count + excluded.requests_count
            """,
            (str(model_key), day_str, max(1, int(delta))),
        )
        conn.commit()
        cur.execute(
            "SELECT requests_count FROM model_daily_usage WHERE model_key = ? AND usage_date = ?",
            (str(model_key), day_str),
        )
        row = cur.fetchone()
        return int(row["requests_count"]) if row else delta
    except Exception:
        return 0
    finally:
        conn.close()

