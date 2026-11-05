"""
Конфігурація додатку, каталог провайдерів і базових моделей, робота з .env.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
# Тести підміняють каталог даних, щоб не чіпати робочу базу
DATA_DIR = Path(os.environ.get("LLM_AUTOBENCH_DATA") or BASE_DIR / "data")
EXPORTS_DIR = BASE_DIR / "exports"
ENV_FILE = BASE_DIR / ".env"
PRICING_FILE = BASE_DIR / "models_pricing.json"
RUBRIC_FILE = BASE_DIR / "rubric_v1.yaml"

DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "llm_research.db"

DEFAULT_TEMPERATURE: float = 0.0
DEFAULT_MAX_OUTPUT_TOKENS: int = 2000
DEFAULT_SEED: int = 42

PROVIDERS_CATALOG: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "id": "gemini",
        "name": "Google AI Studio",
        "short_name": "Google Gemini",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "description_uk": "Моделі сімейств Gemini та Gemma через прямий ключ Google AI Studio.",
        "color": "blue",
    },
    "groq": {
        "id": "groq",
        "name": "Groq Cloud",
        "short_name": "Groq LPU",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "description_uk": "Швидкісна інференс-платформа Groq для відкритих моделей (Llama, Qwen, GPT-OSS).",
        "color": "amber",
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "short_name": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "description_uk": "Агрегатор моделей для резервної маршрутизації хмарних провайдерів.",
        "color": "purple",
        "is_router": True,
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI Platform",
        "short_name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "description_uk": "Комерційні моделі сімейства GPT. Підтримується прямий ключ або запуск через OpenRouter.",
        "color": "emerald",
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "short_name": "Anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1",
        "description_uk": "Моделі сімейства Claude. Підтримується прямий ключ або запуск через OpenRouter.",
        "color": "rose",
    },
    "ollama": {
        "id": "ollama",
        "name": "Ollama Local",
        "short_name": "Ollama",
        "env_key": "OLLAMA_BASE_URL",
        "base_url": "http://localhost:11434",
        "description_uk": "Локальний сервер моделей без лімітів API та без передачі даних у хмару.",
        "color": "slate",
        "is_local": True,
    },
}

DEFAULT_MODELS_CATALOG: Dict[str, Dict[str, Any]] = {
    "gemini-3.1-flash-lite": {
        "id": "gemini-3.1-flash-lite",
        "name": "Google Gemini 3.1 Flash-Lite",
        "provider": "gemini",
        "family": "google",
        "api_model_name": "gemini-3.1-flash-lite-preview",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Хмарна (Google AI)",
        "params_size": "Flash-Lite",
        "cost_per_1m_input_usd": 0.075,
        "cost_per_1m_output_usd": 0.30,
        "input_limit": 1048576,
        "output_limit": 65536,
        "rpm": 15,
        "rpd": 1500,
        "tpm": 1000000,
        "price_verified": False,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Швидка модель Google AI з високим добовим лімітом (1 500 запитів/день).",
    },
    "gemini-3-flash": {
        "id": "gemini-3-flash",
        "name": "Google Gemini 3.0 Flash",
        "provider": "gemini",
        "family": "google",
        "api_model_name": "gemini-3-flash-preview",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Хмарна (Google AI)",
        "params_size": "Flash 3.0",
        "cost_per_1m_input_usd": 0.15,
        "cost_per_1m_output_usd": 0.60,
        "input_limit": 1048576,
        "output_limit": 65536,
        "rpm": 15,
        "rpd": 1500,
        "tpm": 1000000,
        "price_verified": False,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Збалансована модель Gemini 3.0 для завдань класифікації та генерації відповідей.",
    },
    "gemma-4-26b-a4b-it": {
        "id": "gemma-4-26b-a4b-it",
        "name": "Google Gemma 4 26B MoE (A4B)",
        "provider": "gemini",
        "family": "google_gemma",
        "api_model_name": "gemma-4-26b-a4b-it",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Відкрита MoE (Google AI)",
        "params_size": "26B (4B active)",
        "cost_per_1m_input_usd": 0.0,
        "cost_per_1m_output_usd": 0.0,
        "input_limit": 262144,
        "output_limit": 32768,
        "rpm": 30,
        "rpd": 14400,
        "tpm": 1000000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Відкрита модель архітектури Mixture-of-Experts у хмарі Google AI Studio.",
    },
    "gemma-4-31b-it": {
        "id": "gemma-4-31b-it",
        "name": "Google Gemma 4 31B Dense",
        "provider": "gemini",
        "family": "google_gemma",
        "api_model_name": "gemma-4-31b-it",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Відкрита Dense (Google AI)",
        "params_size": "31B",
        "cost_per_1m_input_usd": 0.0,
        "cost_per_1m_output_usd": 0.0,
        "input_limit": 262144,
        "output_limit": 32768,
        "rpm": 30,
        "rpd": 14400,
        "tpm": 1000000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Повнорозмірна відкрита модель Gemma 4 на 31 млрд параметрів.",
    },
    "gemini-2.5-flash": {
        "id": "gemini-2.5-flash",
        "name": "Google Gemini 2.5 Flash",
        "provider": "gemini",
        "family": "google",
        "api_model_name": "gemini-2.5-flash",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Хмарна (Google AI)",
        "params_size": "Flash 2.5",
        "cost_per_1m_input_usd": 0.15,
        "cost_per_1m_output_usd": 0.60,
        "input_limit": 1048576,
        "output_limit": 65536,
        "rpm": 15,
        "rpd": 1500,
        "tpm": 1000000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Стабільна версія Gemini 2.5 Flash, рекомендована для ролі моделі-судді.",
    },
    "gemini-2.5-flash-lite": {
        "id": "gemini-2.5-flash-lite",
        "name": "Google Gemini 2.5 Flash-Lite",
        "provider": "gemini",
        "family": "google",
        "api_model_name": "gemini-2.5-flash-lite",
        "env_key": "GEMINI_API_KEY",
        "class_uk": "Хмарна (Google AI)",
        "params_size": "Flash-Lite 2.5",
        "cost_per_1m_input_usd": 0.075,
        "cost_per_1m_output_usd": 0.30,
        "input_limit": 1048576,
        "output_limit": 65536,
        "rpm": 15,
        "rpd": 1500,
        "tpm": 1000000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": False,
        "description_uk": "Полегшена модель лінійки Gemini 2.5 для швидких відповідей.",
    },
    "llama-3.3-70b-versatile": {
        "id": "llama-3.3-70b-versatile",
        "name": "Meta Llama 3.3 70B (Groq)",
        "provider": "groq",
        "family": "meta_llama",
        "api_model_name": "llama-3.3-70b-versatile",
        "openrouter_model": "meta-llama/llama-3.3-70b-instruct:free",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита (Groq LPU)",
        "params_size": "70B",
        "cost_per_1m_input_usd": 0.59,
        "cost_per_1m_output_usd": 0.79,
        "input_limit": 131072,
        "output_limit": 32768,
        "rpm": 30,
        "rpd": 1000,
        "tpm": 12000,
        "price_verified": True,
        "is_free_tier": False,
        "default_enabled": True,
        "description_uk": "Модель Meta 70B на інференс-платформі Groq (до 1 000 запитів/добу).",
    },
    "llama-3.1-8b-instant": {
        "id": "llama-3.1-8b-instant",
        "name": "Meta Llama 3.1 8B Instant (Groq)",
        "provider": "groq",
        "family": "meta_llama",
        "api_model_name": "llama-3.1-8b-instant",
        "openrouter_model": "meta-llama/llama-3.1-8b-instruct",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Компактна (Groq LPU)",
        "params_size": "8B",
        "cost_per_1m_input_usd": 0.05,
        "cost_per_1m_output_usd": 0.08,
        "input_limit": 131072,
        "output_limit": 131072,
        "rpm": 30,
        "rpd": 14400,
        "tpm": 6000,
        "price_verified": True,
        "is_free_tier": False,
        "default_enabled": True,
        "description_uk": "Компактна модель 8B із мінімальною затримкою та лімітом 14 400 RPD.",
    },
    "gpt-oss-120b": {
        "id": "gpt-oss-120b",
        "name": "OpenAI GPT-OSS 120B (Groq)",
        "provider": "groq",
        "family": "openai_oss",
        "api_model_name": "openai/gpt-oss-120b",
        "openrouter_model": "openai/gpt-oss-120b:free",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита 120B (Groq LPU)",
        "params_size": "120B",
        "cost_per_1m_input_usd": 0.15,
        "cost_per_1m_output_usd": 0.60,
        "input_limit": 131072,
        "output_limit": 65536,
        "rpm": 30,
        "rpd": 1000,
        "tpm": 8000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Потужна відкрита модель 120B на Groq Cloud (1 000 запитів/добу).",
    },
    "gpt-oss-20b": {
        "id": "gpt-oss-20b",
        "name": "OpenAI GPT-OSS 20B (Groq)",
        "provider": "groq",
        "family": "openai_oss",
        "api_model_name": "openai/gpt-oss-20b",
        "openrouter_model": "openai/gpt-oss-20b:free",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита 20B (Groq LPU)",
        "params_size": "20B",
        "cost_per_1m_input_usd": 0.075,
        "cost_per_1m_output_usd": 0.30,
        "input_limit": 131072,
        "output_limit": 65536,
        "rpm": 30,
        "rpd": 1000,
        "tpm": 8000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Швидка відкрита модель 20B на платформі Groq Cloud.",
    },
    "qwen-3.8-27b": {
        "id": "qwen-3.8-27b",
        "name": "Alibaba Qwen 3.8 27B (Groq)",
        "provider": "groq",
        "family": "qwen",
        "api_model_name": "qwen/qwen3.8-27b",
        "openrouter_model": "qwen/qwen3.8-27b",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита 27B (Groq LPU)",
        "params_size": "27B",
        "cost_per_1m_input_usd": 0.25,
        "cost_per_1m_output_usd": 0.50,
        "input_limit": 131072,
        "output_limit": 40960,
        "rpm": 30,
        "rpd": 1000,
        "tpm": 8000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Актуальна модель Qwen 3.8 27B на інференс-платформі Groq LPU.",
    },
    "qwen3-32b": {
        "id": "qwen3-32b",
        "name": "Alibaba Qwen 3 32B (Groq)",
        "provider": "groq",
        "family": "qwen",
        "api_model_name": "qwen/qwen3-32b",
        "openrouter_model": "qwen/qwen3-32b",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита 32B (Groq LPU)",
        "params_size": "32B",
        "cost_per_1m_input_usd": 0.29,
        "cost_per_1m_output_usd": 0.59,
        "input_limit": 131072,
        "output_limit": 40960,
        "rpm": 60,
        "rpd": 1000,
        "tpm": 6000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Модель Qwen 3 32B на Groq LPU з високою швидкістю обробки.",
    },
    "llama-4-scout-17b": {
        "id": "llama-4-scout-17b",
        "name": "Meta Llama 4 Scout 17B MoE (Groq)",
        "provider": "groq",
        "family": "meta_llama",
        "api_model_name": "meta-llama/llama-4-scout-17b-16e-instruct",
        "openrouter_model": "meta-llama/llama-4-scout",
        "env_key": "GROQ_API_KEY",
        "class_uk": "Відкрита MoE (Groq LPU)",
        "params_size": "17B×16E",
        "cost_per_1m_input_usd": 0.11,
        "cost_per_1m_output_usd": 0.34,
        "input_limit": 131072,
        "output_limit": 8192,
        "rpm": 30,
        "rpd": 1000,
        "tpm": 30000,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": True,
        "description_uk": "Модель Meta Llama 4 Scout (16 експертів) із високим лімітом 30 000 TPM.",
    },
    "gpt-4o-mini": {
        "id": "gpt-4o-mini",
        "name": "OpenAI GPT-4o mini",
        "provider": "openai",
        "family": "openai",
        "api_model_name": "gpt-4o-mini",
        "openrouter_model": "openai/gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "class_uk": "Комерційна (OpenAI)",
        "params_size": "Компактна",
        "cost_per_1m_input_usd": 0.15,
        "cost_per_1m_output_usd": 0.60,
        "input_limit": 128000,
        "output_limit": 16384,
        "rpm": 500,
        "rpd": 10000,
        "tpm": 200000,
        "price_verified": True,
        "is_free_tier": False,
        "default_enabled": False,
        "description_uk": "Комерційна модель OpenAI для корпоративних сценаріїв підтримки.",
    },
    "claude-3-5-haiku": {
        "id": "claude-3-5-haiku",
        "name": "Anthropic Claude 3.5 Haiku",
        "provider": "anthropic",
        "family": "anthropic",
        "api_model_name": "claude-3-5-haiku-latest",
        "openrouter_model": "anthropic/claude-3.5-haiku",
        "env_key": "ANTHROPIC_API_KEY",
        "class_uk": "Комерційна (Anthropic)",
        "params_size": "Haiku 3.5",
        "cost_per_1m_input_usd": 0.80,
        "cost_per_1m_output_usd": 4.00,
        "input_limit": 200000,
        "output_limit": 8192,
        "rpm": 50,
        "rpd": 1000,
        "tpm": 50000,
        "price_verified": True,
        "is_free_tier": False,
        "default_enabled": False,
        "description_uk": "Швидка модель сімейства Anthropic Claude 3.5.",
    },
    "ollama-local": {
        "id": "ollama-local",
        "name": "Ollama Local Model",
        "provider": "ollama",
        "family": "local_ollama",
        "api_model_name": "llama3.2",
        "env_key": "OLLAMA_BASE_URL",
        "class_uk": "Локальна (On-Premise)",
        "params_size": "3B–8B",
        "cost_per_1m_input_usd": 0.0,
        "cost_per_1m_output_usd": 0.0,
        "input_limit": 128000,
        "output_limit": 8192,
        "rpm": 9999,
        "rpd": 999999,
        "tpm": 999999,
        "price_verified": True,
        "is_free_tier": True,
        "default_enabled": False,
        "description_uk": "Локальний запуск через сервер Ollama (http://localhost:11434).",
    },
}

QUERY_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "informational": {
        "code": "informational",
        "name_uk": "Інформаційні запити",
        "short_uk": "Інформаційні",
        "description_uk": "Запити щодо тарифів, лімітів, комісій, графіків роботи та умов надання послуг.",
        "keywords": ["тариф", "комісія", "вартість", "ліміт", "відсоток", "умови", "скільки", "термін", "курс", "кешбек"],
    },
    "navigational": {
        "code": "navigational",
        "name_uk": "Навігаційні запити",
        "short_uk": "Навігаційні",
        "description_uk": "Пошук потрібного розділу кабінету, меню налаштувань, документів або контактів.",
        "keywords": ["де знайти", "розділ", "меню", "вкладка", "завантажити", "кнопка", "посилання", "кабінет", "договір", "виписка"],
    },
    "procedural": {
        "code": "procedural",
        "name_uk": "Процедурні (інструктивні) запити",
        "short_uk": "Процедурні",
        "description_uk": "Покрокові інструкції з підключення послуг, зміни реквізитів, верифікації або налаштування безпеки.",
        "keywords": ["як підключити", "як змінити", "інструкція", "кроки", "оформити", "активувати", "відкрити", "налаштувати", "2fa", "пароль"],
    },
    "troubleshooting": {
        "code": "troubleshooting",
        "name_uk": "Проблемні запити (усунення збоїв)",
        "short_uk": "Проблемні",
        "description_uk": "Діагностика помилок оплати, блокування карток, затримок транзакцій, дублювання списань та збоїв входу.",
        "keywords": ["не працює", "помилка", "заблоковано", "відхилено", "списалося двічі", "не приходить", "збій", "шахраї", "втратив", "код"],
    },
    "clarification": {
        "code": "clarification",
        "name_uk": "Неоднозначні / уточнювальні запити",
        "short_uk": "Уточнювальні",
        "description_uk": "Неповні, нечіткі або багатозначні звернення користувачів, що потребують уточнювальних запитань.",
        "keywords": ["не можу", "допоможіть", "питання по картці", "проблема з рахунком", "переказ", "змінити дані", "чому так", "терміново"],
    },
}

DEFAULT_EVALUATION_WEIGHTS: Dict[str, float] = {
    "fact_coverage": 0.65,
    "semantic_similarity": 0.35,
}

HALLUCINATION_PENALTY_PCT: float = 15.0
HALLUCINATION_PENALTY_GRID: List[float] = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
AUTOMATION_FC_THRESHOLD_PCT: float = 80.0
FORMAT_MIN_CHARS: int = 20
FORMAT_MAX_CHARS: int = 1500
DEFAULT_TOP_K: int = 3
CONTEXT_MODES: tuple = ("no_context", "gold_context", "retrieved_context")

# Пороги застосовності статистичних критеріїв (при n < 6 для двостороннього тесту Вілкоксона p_min = 0.0625 > 0.05)
WILCOXON_MIN_PAIRS: int = 6
FRIEDMAN_MIN_BLOCKS_WARN: int = 10
JUDGE_DEFAULT_MAX_TOKENS: int = 1500
JUDGE_RETRY_MAX_TOKENS: int = 4096
CEILING_EFFECT_SHARE_PCT: float = 80.0
CEILING_EFFECT_THRESHOLD_PCT: float = 90.0
DOMAIN_MIN_QUERIES_WARN: int = 30
MANUAL_VALIDATION_SAMPLE_MAX: int = 60

OPENROUTER_FREE_RPM_DOCS: int = 20

ROUTE_NATIVE = "native"
ROUTE_DIRECT = "direct"
ROUTE_OPENROUTER = "openrouter"
_ROUTE_ELIGIBLE_PROVIDERS = {"groq", "openai", "anthropic"}


def load_models_pricing() -> Dict[str, Any]:
    """Завантажує початкові значення цін із models_pricing.json."""
    if not PRICING_FILE.exists():
        return {"verified": False, "last_updated": "2026-10-01", "models": {}}
    try:
        data = json.loads(PRICING_FILE.read_text(encoding="utf-8"))
        models_map = data.get("models", data) if isinstance(data, dict) else {}
        for m_id, p_info in models_map.items():
            if not isinstance(p_info, dict):
                continue
            if m_id in DEFAULT_MODELS_CATALOG:
                in_val = p_info.get("input_per_1m", p_info.get("input_per_1m_usd"))
                out_val = p_info.get("output_per_1m", p_info.get("output_per_1m_usd"))
                if in_val is not None:
                    DEFAULT_MODELS_CATALOG[m_id]["cost_per_1m_input_usd"] = float(in_val)
                if out_val is not None:
                    DEFAULT_MODELS_CATALOG[m_id]["cost_per_1m_output_usd"] = float(out_val)
                DEFAULT_MODELS_CATALOG[m_id]["price_verified"] = bool(p_info.get("verified", True))
                DEFAULT_MODELS_CATALOG[m_id]["is_free_tier"] = bool(
                    p_info.get("is_free_tier", p_info.get("free_tier_available", False))
                )
        return data
    except Exception:
        return {"verified": False, "last_updated": "2026-10-01", "models": {}}


load_models_pricing()


def parse_model_routes(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in (raw or "").split(","):
        if ":" not in item:
            continue
        mid, route = (p.strip() for p in item.rsplit(":", 1))
        if mid and route in (ROUTE_NATIVE, ROUTE_DIRECT, ROUTE_OPENROUTER):
            out[mid] = ROUTE_OPENROUTER if route == ROUTE_OPENROUTER else ROUTE_NATIVE
    return out


def _read_env_file(path: Path) -> Dict[str, str]:
    vals: Dict[str, str] = {}
    if not path.exists():
        return vals
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key:
                vals[key] = val
    except Exception:
        pass
    return vals


def get_env_secret(env_key: str, default: str = "") -> str:
    if not env_key:
        return default
    file_vals = _read_env_file(ENV_FILE)
    for k in (env_key, env_key.upper(), env_key.lower()):
        v = file_vals.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
        ev = os.getenv(k)
        if ev is not None and str(ev).strip() != "":
            return str(ev).strip()
    return default


def set_env_secret(env_key: str, secret_value: str) -> None:
    if not env_key or not secret_value or "..." in secret_value:
        return
    clean_key = env_key.strip().upper()
    file_vals = _read_env_file(ENV_FILE)
    file_vals[clean_key] = secret_value.strip()
    _write_clean_env_file(file_vals)


def _write_clean_env_file(vals: Dict[str, str]) -> None:
    standard_keys = {
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL_NAME",
        "CONFIDENCE_THRESHOLD",
        "DEFAULT_TEMPERATURE",
        "DEFAULT_MAX_OUTPUT_TOKENS",
        "DEFAULT_SEED",
        "JUDGE_ENABLED",
        "JUDGE_MODEL",
        "PROBE_ON_STARTUP",
        "ENABLED_MODELS",
        "MODEL_ROUTES",
    }
    extra_lines = []
    for k, v in sorted(vals.items()):
        if k not in standard_keys and v:
            extra_lines.append(f"{k}={v}")

    extra_block = ("\n# Ключі користувацьких провайдерів\n" + "\n".join(extra_lines) + "\n") if extra_lines else ""
    env_content = (
        "# Конфігурація ключів API та адрес серверів LLM-AutoBench\n"
        f"GEMINI_API_KEY={vals.get('GEMINI_API_KEY', '')}\n"
        f"GROQ_API_KEY={vals.get('GROQ_API_KEY', '')}\n"
        f"OPENROUTER_API_KEY={vals.get('OPENROUTER_API_KEY', '')}\n"
        f"OPENAI_API_KEY={vals.get('OPENAI_API_KEY', '')}\n"
        f"ANTHROPIC_API_KEY={vals.get('ANTHROPIC_API_KEY', '')}\n\n"
        "# Локальний сервер Ollama\n"
        f"OLLAMA_BASE_URL={vals.get('OLLAMA_BASE_URL', 'http://localhost:11434')}\n"
        f"OLLAMA_MODEL_NAME={vals.get('OLLAMA_MODEL_NAME', 'llama3.2')}\n"
        f"{extra_block}\n"
        "# Параметри генерації та оцінювання\n"
        f"CONFIDENCE_THRESHOLD={vals.get('CONFIDENCE_THRESHOLD', '0.75')}\n"
        f"DEFAULT_TEMPERATURE={vals.get('DEFAULT_TEMPERATURE', str(DEFAULT_TEMPERATURE))}\n"
        f"DEFAULT_MAX_OUTPUT_TOKENS={vals.get('DEFAULT_MAX_OUTPUT_TOKENS', str(DEFAULT_MAX_OUTPUT_TOKENS))}\n"
        f"DEFAULT_SEED={vals.get('DEFAULT_SEED', str(DEFAULT_SEED))}\n"
        f"JUDGE_ENABLED={vals.get('JUDGE_ENABLED', 'false')}\n"
        f"JUDGE_MODEL={vals.get('JUDGE_MODEL', 'gemini-2.5-flash')}\n"
        f"PROBE_ON_STARTUP={vals.get('PROBE_ON_STARTUP', 'true')}\n"
    )
    ENV_FILE.write_text(env_content, encoding="utf-8")


def sync_model_registry_from_env() -> None:
    """
    При першому запуску переносить поточний вибір моделей і маршрутів із .env у таблицю model_registry,
    а також заповнює початкові значення з DEFAULT_MODELS_CATALOG та models_pricing.json.
    """
    from app.database import fetch_all_model_registry, upsert_model_registry_entry

    existing = fetch_all_model_registry()
    existing_ids = {r["id"] for r in existing}
    file_vals = _read_env_file(ENV_FILE)

    default_enabled_ids = [
        m["id"] for m in DEFAULT_MODELS_CATALOG.values() if m.get("default_enabled", False)
    ]
    raw_enabled = file_vals.get("ENABLED_MODELS") or os.getenv("ENABLED_MODELS") or ""
    env_enabled = [x.strip() for x in raw_enabled.split(",") if x.strip()] or default_enabled_ids
    env_routes = parse_model_routes(file_vals.get("MODEL_ROUTES") or os.getenv("MODEL_ROUTES") or "")

    pricing_data = load_models_pricing()
    pricing_map = pricing_data.get("models", pricing_data) if isinstance(pricing_data, dict) else {}

    for mid, cfg in DEFAULT_MODELS_CATALOG.items():
        if mid in existing_ids:
            continue
        prov = cfg["provider"]
        prov_cfg = PROVIDERS_CATALOG.get(prov, {})
        has_initial_price = mid in pricing_map or cfg.get("cost_per_1m_input_usd") is not None
        in_price = float(cfg["cost_per_1m_input_usd"]) if cfg.get("cost_per_1m_input_usd") is not None else None
        out_price = float(cfg["cost_per_1m_output_usd"]) if cfg.get("cost_per_1m_output_usd") is not None else None
        p_source = "manual" if has_initial_price else "unknown"
        route_val = "openrouter" if env_routes.get(mid) == ROUTE_OPENROUTER else "direct"
        limits_obj = {
            "rpm": cfg.get("rpm"),
            "rpd": cfg.get("rpd"),
            "tpm": cfg.get("tpm"),
            "output_limit": cfg.get("output_limit", 8192),
            "openrouter_model": cfg.get("openrouter_model"),
            "family": cfg.get("family", prov),
            "class_uk": cfg.get("class_uk", ""),
            "description_uk": cfg.get("description_uk", ""),
        }
        upsert_model_registry_entry(
            {
                "id": mid,
                "provider": prov,
                "model_id": cfg["api_model_name"],
                "display_name": cfg["name"],
                "source": "discovered",
                "enabled": mid in env_enabled,
                "route": route_val,
                "base_url": prov_cfg.get("base_url", ""),
                "api_key_env": cfg.get("env_key") or prov_cfg.get("env_key", ""),
                "context_window": cfg.get("input_limit", 128000),
                "input_price_per_1m": in_price,
                "output_price_per_1m": out_price,
                "price_source": p_source,
                "limits": limits_obj,
            },
            preserve_user_fields=False,
        )

    for dyn_id in env_enabled:
        if dyn_id not in existing_ids and dyn_id not in DEFAULT_MODELS_CATALOG and ":" in dyn_id:
            prov, api_name = dyn_id.split(":", 1)
            prov_cfg = PROVIDERS_CATALOG.get(prov, {})
            upsert_model_registry_entry(
                {
                    "id": dyn_id,
                    "provider": prov,
                    "model_id": api_name,
                    "display_name": api_name,
                    "source": "discovered",
                    "enabled": True,
                    "route": "openrouter" if env_routes.get(dyn_id) == ROUTE_OPENROUTER else "direct",
                    "base_url": prov_cfg.get("base_url", ""),
                    "api_key_env": prov_cfg.get("env_key", f"{prov.upper()}_API_KEY"),
                    "context_window": 128000,
                    "input_price_per_1m": None,
                    "output_price_per_1m": None,
                    "price_source": "unknown",
                    "limits": {},
                },
                preserve_user_fields=False,
            )

    if "ENABLED_MODELS" in file_vals or "MODEL_ROUTES" in file_vals:
        _write_clean_env_file(file_vals)


def get_model_config(model_id: str) -> Dict[str, Any]:
    """Повертає повну конфігурацію моделі з реєстру БД або базового каталогу."""
    try:
        from app.database import fetch_model_registry_by_id

        reg = fetch_model_registry_by_id(model_id)
    except Exception:
        reg = None

    if reg:
        prov = reg["provider"]
        prov_cfg = PROVIDERS_CATALOG.get(prov, {})
        lim = reg.get("limits") or {}
        cat_fallback = DEFAULT_MODELS_CATALOG.get(reg["id"]) or DEFAULT_MODELS_CATALOG.get(model_id) or {}
        p_src = reg.get("price_source") or "unknown"
        in_p = reg.get("input_price_per_1m")
        out_p = reg.get("output_price_per_1m")
        has_positive_or_local_price = (
            (in_p is not None and out_p is not None)
            and (prov == "ollama" or float(in_p) > 0.0 or float(out_p) > 0.0)
        )
        price_known = (p_src != "unknown") and has_positive_or_local_price
        is_free_flag = bool(
            lim.get("is_free_tier")
            if lim.get("is_free_tier") is not None
            else cat_fallback.get("is_free_tier", False)
        ) or (in_p is not None and out_p is not None and float(in_p) == 0.0 and float(out_p) == 0.0)
        or_model = lim.get("openrouter_model") or cat_fallback.get("openrouter_model")
        return {
            "id": reg["id"],
            "name": reg["display_name"],
            "display_name": reg["display_name"],
            "provider": prov,
            "family": lim.get("family") or cat_fallback.get("family") or prov,
            "api_model_name": reg["model_id"],
            "model_id": reg["model_id"],
            "openrouter_model": or_model,
            "base_url": reg.get("base_url") or prov_cfg.get("base_url", ""),
            "env_key": reg.get("api_key_env") or prov_cfg.get("env_key", ""),
            "api_key_env": reg.get("api_key_env") or prov_cfg.get("env_key", ""),
            "source": reg.get("source", "discovered"),
            "enabled": bool(reg.get("enabled")),
            "route": "openrouter" if reg.get("route") == "openrouter" else "direct",
            "class_uk": lim.get("class_uk") or cat_fallback.get("class_uk") or ("Власна модель" if reg.get("source") == "custom" else f"Модель ({prov_cfg.get('short_name', prov)})"),
            "params_size": lim.get("params_size") or cat_fallback.get("params_size", ""),
            "cost_per_1m_input_usd": float(in_p) if price_known else None,
            "cost_per_1m_output_usd": float(out_p) if price_known else None,
            "input_price_per_1m": float(in_p) if price_known else None,
            "output_price_per_1m": float(out_p) if price_known else None,
            "price_source": p_src,
            "price_known": price_known,
            "price_verified": p_src in ("api", "manual") and price_known,
            "input_limit": int(reg.get("context_window") or cat_fallback.get("input_limit") or 128000),
            "output_limit": int(lim.get("output_limit") or cat_fallback.get("output_limit") or 8192),
            "context_window": int(reg.get("context_window") or cat_fallback.get("input_limit") or 128000),
            "rpm": lim.get("rpm") if lim.get("rpm") is not None else cat_fallback.get("rpm"),
            "rpd": lim.get("rpd") if lim.get("rpd") is not None else cat_fallback.get("rpd"),
            "tpm": lim.get("tpm") if lim.get("tpm") is not None else cat_fallback.get("tpm"),
            "limits": lim,
            "first_seen": reg.get("first_seen"),
            "last_seen": reg.get("last_seen"),
            "is_free_tier": is_free_flag,
            "default_enabled": bool(reg.get("enabled")),
            "description_uk": lim.get("description_uk") or cat_fallback.get("description_uk") or f"Модель {reg['model_id']} ({prov}).",
        }

    if model_id in DEFAULT_MODELS_CATALOG:
        c = dict(DEFAULT_MODELS_CATALOG[model_id])
        in_c = c.get("cost_per_1m_input_usd")
        out_c = c.get("cost_per_1m_output_usd")
        known_c = (
            in_c is not None
            and out_c is not None
            and (c.get("provider") == "ollama" or float(in_c) > 0.0 or float(out_c) > 0.0)
        )
        c["price_source"] = "manual" if known_c else "unknown"
        c["price_known"] = known_c
        c["input_price_per_1m"] = float(in_c) if known_c else None
        c["output_price_per_1m"] = float(out_c) if known_c else None
        return c

    if ":" in model_id:
        provider, api_name = model_id.split(":", 1)
        prov_cfg = PROVIDERS_CATALOG.get(provider, {})
        return {
            "id": model_id,
            "name": api_name,
            "display_name": api_name,
            "provider": provider,
            "family": provider,
            "api_model_name": api_name,
            "model_id": api_name,
            "openrouter_model": api_name if ("/" in api_name and provider in _ROUTE_ELIGIBLE_PROVIDERS) else None,
            "base_url": prov_cfg.get("base_url", ""),
            "env_key": prov_cfg.get("env_key", f"{provider.upper()}_API_KEY"),
            "api_key_env": prov_cfg.get("env_key", f"{provider.upper()}_API_KEY"),
            "source": "discovered",
            "route": "direct",
            "class_uk": f"Модель API ({prov_cfg.get('short_name', provider)})",
            "params_size": "",
            "cost_per_1m_input_usd": None,
            "cost_per_1m_output_usd": None,
            "input_price_per_1m": None,
            "output_price_per_1m": None,
            "price_source": "unknown",
            "price_known": False,
            "price_verified": False,
            "input_limit": 128000,
            "output_limit": 8192,
            "context_window": 128000,
            "rpm": None,
            "rpd": None,
            "tpm": None,
            "is_free_tier": False,
            "default_enabled": False,
            "description_uk": f"Модель {api_name} ({prov_cfg.get('name', provider)}).",
        }

    fallback = dict(DEFAULT_MODELS_CATALOG["gemini-2.5-flash-lite"])
    fallback["price_source"] = "manual"
    fallback["price_known"] = True
    return fallback


def can_route_via_openrouter(model_id: str) -> bool:
    """
    Маршрут через OpenRouter має сенс лише для хмарних моделей Groq, OpenAI, Anthropic,
    які мають еквівалент в каталозі OpenRouter. Для Ollama, Gemini та власних моделей — False.
    """
    cfg = get_model_config(model_id)
    provider = cfg.get("provider", "")
    if cfg.get("source") == "custom" or provider not in _ROUTE_ELIGIBLE_PROVIDERS:
        return False
    return bool(cfg.get("openrouter_model"))


def load_runtime_settings() -> Dict[str, Any]:
    """Читає ключі API та налаштування з .env, а вибір моделей і маршрутів — з таблиці model_registry."""
    file_vals = _read_env_file(ENV_FILE)

    def _get(key: str, default: str = "") -> str:
        val = file_vals.get(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
        env_val = os.getenv(key)
        if env_val is not None and str(env_val).strip() != "":
            return str(env_val).strip()
        return default

    enabled_models: List[str] = []
    model_routes: Dict[str, str] = {}
    try:
        from app.database import fetch_all_model_registry

        reg_rows = fetch_all_model_registry()
        if reg_rows:
            enabled_models = [r["id"] for r in reg_rows if r.get("enabled")]
            for r in reg_rows:
                model_routes[r["id"]] = ROUTE_OPENROUTER if r.get("route") == "openrouter" else ROUTE_NATIVE
    except Exception:
        reg_rows = []

    if not enabled_models:
        default_enabled_str = ",".join(
            m["id"] for m in DEFAULT_MODELS_CATALOG.values() if m.get("default_enabled", False)
        )
        enabled_raw = _get("ENABLED_MODELS", default_enabled_str)
        enabled_models = [m.strip() for m in enabled_raw.split(",") if m.strip()]
    if not model_routes:
        model_routes = parse_model_routes(_get("MODEL_ROUTES", ""))

    try:
        conf_threshold = float(_get("CONFIDENCE_THRESHOLD", "0.75"))
    except ValueError:
        conf_threshold = 0.75

    try:
        temperature = float(_get("DEFAULT_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
    except ValueError:
        temperature = DEFAULT_TEMPERATURE

    try:
        max_output_tokens = int(_get("DEFAULT_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)))
    except ValueError:
        max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS

    try:
        seed = int(_get("DEFAULT_SEED", str(DEFAULT_SEED)))
    except ValueError:
        seed = DEFAULT_SEED

    judge_enabled = _get("JUDGE_ENABLED", "false").lower() in ("1", "true", "yes", "on")
    judge_model = _get("JUDGE_MODEL", "gemini-2.5-flash")
    probe_on_startup = _get("PROBE_ON_STARTUP", "true").lower() in ("1", "true", "yes", "on")

    return {
        "groq_api_key": _get("GROQ_API_KEY", ""),
        "openai_api_key": _get("OPENAI_API_KEY", ""),
        "gemini_api_key": _get("GEMINI_API_KEY", ""),
        "anthropic_api_key": _get("ANTHROPIC_API_KEY", ""),
        "openrouter_api_key": _get("OPENROUTER_API_KEY", ""),
        "ollama_base_url": _get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model_name": _get("OLLAMA_MODEL_NAME", "llama3.2"),
        "confidence_threshold": conf_threshold,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "seed": seed,
        "enabled_models": enabled_models,
        "model_routes": model_routes,
        "judge_enabled": judge_enabled,
        "judge_model": judge_model,
        "probe_on_startup": probe_on_startup,
        "weights": dict(DEFAULT_EVALUATION_WEIGHTS),
        "env_file_path": str(ENV_FILE),
    }


def save_runtime_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Зберігає API-ключі та адреси серверів у .env, а вибір моделей і маршрутів — у БД."""
    file_vals = _read_env_file(ENV_FILE)
    current = load_runtime_settings()

    key_map = {
        "groq_api_key": "GROQ_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "ollama_base_url": "OLLAMA_BASE_URL",
        "ollama_model_name": "OLLAMA_MODEL_NAME",
        "confidence_threshold": "CONFIDENCE_THRESHOLD",
        "temperature": "DEFAULT_TEMPERATURE",
        "max_output_tokens": "DEFAULT_MAX_OUTPUT_TOKENS",
        "seed": "DEFAULT_SEED",
        "judge_model": "JUDGE_MODEL",
    }
    for field_name, env_name in key_map.items():
        if field_name in current and current[field_name] is not None:
            file_vals[env_name] = str(current[field_name])
        if field_name in new_settings and new_settings[field_name] is not None:
            val_str = str(new_settings[field_name]).strip()
            if field_name.endswith("_api_key") and ("..." in val_str or val_str == ""):
                continue
            file_vals[env_name] = val_str

    if "judge_enabled" in new_settings and new_settings["judge_enabled"] is not None:
        file_vals["JUDGE_ENABLED"] = "true" if new_settings["judge_enabled"] else "false"
    else:
        file_vals["JUDGE_ENABLED"] = "true" if current.get("judge_enabled") else "false"

    if "probe_on_startup" in new_settings and new_settings["probe_on_startup"] is not None:
        file_vals["PROBE_ON_STARTUP"] = "true" if new_settings["probe_on_startup"] else "false"
    else:
        file_vals["PROBE_ON_STARTUP"] = "true" if current.get("probe_on_startup", True) else "false"

    _write_clean_env_file(file_vals)

    enabled_arg: Optional[List[str]] = None
    if "enabled_models" in new_settings and isinstance(new_settings["enabled_models"], list):
        valid = [str(m).strip() for m in new_settings["enabled_models"] if str(m).strip()]
        if valid:
            enabled_arg = valid

    routes_arg: Optional[Dict[str, str]] = None
    if "model_routes" in new_settings and isinstance(new_settings["model_routes"], dict):
        routes_arg = {}
        for mid, route in new_settings["model_routes"].items():
            if route in (ROUTE_NATIVE, ROUTE_DIRECT, ROUTE_OPENROUTER):
                routes_arg[mid] = "openrouter" if route == ROUTE_OPENROUTER else "direct"

    if enabled_arg is not None or routes_arg is not None:
        try:
            from app.database import update_model_registry_flags

            update_model_registry_flags(enabled_ids=enabled_arg, routes_map=routes_arg)
        except Exception:
            pass

    return load_runtime_settings()
