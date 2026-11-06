"""
Динамічне виявлення моделей, класифікація статусів провайдерів і двоступенева перевірка доступності.
"""

import asyncio
from datetime import datetime
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import (
    DEFAULT_MODELS_CATALOG,
    OPENROUTER_FREE_RPM_DOCS,
    PROVIDERS_CATALOG,
    ROUTE_NATIVE,
    ROUTE_OPENROUTER,
    get_env_secret,
    load_models_pricing,
    load_runtime_settings,
    sync_model_registry_from_env,
)
from app.database import (
    fetch_all_model_registry,
    fetch_all_model_statuses,
    fetch_all_provider_statuses,
    get_daily_usage_count,
    increment_daily_usage_count,
    upsert_model_registry_entry,
    upsert_model_status,
    upsert_provider_status,
)
from app.services.logger import get_logs_and_status, log_event

DISCOVERY_TTL_SECONDS = 6 * 3600
STALE_STATUS_SECONDS = 24 * 3600
PING_TIMEOUT_SECONDS = 20.0
PING_CONCURRENCY = 4
MAX_OPENROUTER_FREE_MODELS = 35
_ERR_MAX = 240
_ROUTE_ELIGIBLE_PROVIDERS = {"groq", "openai", "anthropic"}

STATUS_LABELS_UK: Dict[str, str] = {
    "ok": "Працює",
    "offline": "Немає зв'язку",
    "rate_limited": "Ліміт запитів",
    "daily_quota": "Денний ліміт вичерпано",
    "no_credit": "Нульовий баланс",
    "invalid_key": "Невірний ключ",
    "no_key": "Ключ не задано",
    "model_missing": "Модель недоступна",
    "unchecked": "Не перевірено",
}

_NON_TEXT_KEYWORDS = (
    "embedding",
    "embed",
    "tts",
    "whisper",
    "transcribe",
    "audio",
    "realtime",
    "image",
    "imagen",
    "dall-e",
    "veo",
    "moderation",
    "guard",
    "safeguard",
    "orpheus",
    "playai",
    "allam",
    "bisect",
    "robotics",
    "computer-use",
    "aqa",
    "rerank",
)

_ACTIVE_PROBE_TASK: Optional[asyncio.Task] = None
_SINGLE_PROBE_TASKS: set = set()
_PROBE_JOB: Dict[str, Any] = {"running": False, "phase": "", "done": 0, "total": 0}
_PROVIDER_SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}


def get_status_label_uk(status_code: Optional[str]) -> str:
    if not status_code:
        return STATUS_LABELS_UK["unchecked"]
    return STATUS_LABELS_UK.get(status_code, STATUS_LABELS_UK["unchecked"])


def format_checked_ago_uk(checked_at: Optional[str]) -> str:
    if not checked_at:
        return "не перевірено"
    try:
        dt = datetime.strptime(checked_at, "%Y-%m-%d %H:%M:%S")
        diff_sec = max(0, int((datetime.now() - dt).total_seconds()))
        if diff_sec < 60:
            return "перевірено щойно"
        mins = diff_sec // 60
        if mins < 60:
            return f"перевірено {mins} хв тому"
        hours = mins // 60
        if hours < 24:
            return f"перевірено {hours} год тому"
        days = hours // 24
        return f"перевірено {days} дн тому"
    except Exception:
        return "не перевірено"


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _per_million(v: Any) -> Optional[float]:
    try:
        val = float(v)
        if val < 0:
            return None
        return round(val * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def _parse_error_payload(payload: Any, raw_text: str) -> Dict[str, Any]:
    data = payload
    if data is None and raw_text:
        try:
            data = json.loads(raw_text)
        except Exception:
            data = None

    err_obj = data.get("error", data) if isinstance(data, dict) else {}
    if not isinstance(err_obj, dict):
        err_obj = {"message": str(err_obj or raw_text or "")}

    code = str(err_obj.get("code") or "").strip()
    err_type = str(err_obj.get("type") or "").strip()
    status_str = str(err_obj.get("status") or "").strip()
    message = str(err_obj.get("message") or raw_text or "").strip()
    details = err_obj.get("details") if isinstance(err_obj.get("details"), list) else []

    return {
        "code": code,
        "type": err_type,
        "status": status_str,
        "message": " ".join(message.split())[:_ERR_MAX],
        "details": details,
    }


def classify_api_response(
    provider: str,
    http_code: Optional[int],
    payload: Any = None,
    raw_text: str = "",
    is_network_error: bool = False,
) -> Tuple[str, str]:
    """
    Класифікує стан моделі або провайдера за HTTP-кодом і структурованим полем помилки API.
    Повертає кортеж (status_code, clean_message).
    """
    if is_network_error or http_code is None:
        msg = " ".join(str(raw_text or "Сервер недоступний").split())[:_ERR_MAX]
        return "offline", msg

    if http_code == 200:
        return "ok", ""

    parsed = _parse_error_payload(payload, raw_text)
    code_low = parsed["code"].lower()
    type_low = parsed["type"].lower()
    status_low = parsed["status"].lower()
    msg = parsed["message"]
    msg_low = msg.lower()
    details = parsed["details"]
    prov = (provider or "").lower()

    details_str = json.dumps(details, ensure_ascii=False) if details else ""
    details_low = details_str.lower()

    if http_code == 404 or code_low == "model_not_found" or type_low == "not_found_error" or status_low == "not_found":
        return "model_missing", msg

    if prov == "openai":
        if http_code in (401, 403) or code_low == "invalid_api_key":
            return "invalid_key", msg
        if http_code == 429:
            if code_low == "insufficient_quota" or type_low == "insufficient_quota":
                return "no_credit", msg
            if "requests per day" in msg_low or "(rpd)" in msg_low:
                return "daily_quota", msg
            return "rate_limited", msg

    if prov == "anthropic":
        if http_code == 400 and ("credit balance is too low" in msg_low or "billing" in type_low):
            return "no_credit", msg
        if http_code in (401, 403) or type_low in ("authentication_error", "permission_error"):
            return "invalid_key", msg
        if http_code == 429 or type_low == "rate_limit_error":
            return "rate_limited", msg
        if http_code == 529 or type_low == "overloaded_error":
            return "offline", msg

    if prov == "gemini":
        if "api_key_invalid" in details_low or "api key not valid" in msg_low or "api key expired" in msg_low or http_code in (401, 403):
            return "invalid_key", msg
        if http_code == 429 or status_low == "resource_exhausted":
            has_per_day = (
                "perday" in details_low
                or "permodelperday" in details_low
                or "perday" in msg_low
                or ("limit: 20" in msg_low and "perminute" not in msg_low)
            )
            return ("daily_quota" if has_per_day else "rate_limited"), msg

    if prov == "groq":
        if http_code in (401, 403) or code_low == "invalid_api_key":
            return "invalid_key", msg
        if http_code == 429:
            if "per day" in msg_low or "(rpd)" in msg_low or "(tpd)" in msg_low:
                return "daily_quota", msg
            return "rate_limited", msg

    if prov == "openrouter":
        if http_code in (401, 403):
            return "invalid_key", msg
        if http_code == 402 or code_low == "402":
            return "no_credit", msg
        if http_code == 429:
            if "per-day" in msg_low or "daily" in msg_low or "free-models-per-day" in msg_low:
                return "daily_quota", msg
            return "rate_limited", msg

    # Загальні правила для сумісних OpenAI-серверів або нестандартних відповідей
    if http_code in (401, 403) or code_low == "invalid_api_key":
        return "invalid_key", msg
    if http_code == 402 or code_low == "insufficient_quota":
        return "no_credit", msg
    if http_code == 429:
        if "per day" in msg_low or "(rpd)" in msg_low or "perday" in details_low:
            return "daily_quota", msg
        return "rate_limited", msg

    # Перевантаження сервера (503, 529) не є лімітом користувача. Невідома помилка 4xx
    # не доводить, що моделі немає, тому статус лишається непевним, а текст помилки видно в інтерфейсі.
    return ("offline" if http_code >= 500 else "unchecked"), msg


def is_text_generation_model(
    provider_or_api_id: str,
    api_id: Optional[str] = None,
    methods: Optional[List[str]] = None,
) -> bool:
    if api_id is None:
        provider = ""
        target_id = provider_or_api_id
    else:
        provider = provider_or_api_id
        target_id = api_id

    low = (target_id or "").strip().lower()
    if not low:
        return False
    if any(k in low for k in _NON_TEXT_KEYWORDS):
        return False

    if provider == "gemini":
        if methods is not None and "generateContent" not in methods:
            return False
        if "thinking-exp" in low or "learnlm" in low:
            return False
        return low.startswith(("gemini-", "gemma-"))

    if provider == "openai":
        if not low.startswith(("gpt-4", "gpt-5", "o1", "o3", "o4", "chatgpt-")):
            return False
        if "instruct" in low or "vision-preview" in low or "search" in low:
            return False
        if re.search(r"-\d{4}-\d{2}-\d{2}$", low) or re.search(r"-\d{4}$", low):
            return False
        return True

    return True


def extract_daily_quota_limit(payload: Any) -> Optional[int]:
    """Реальний добовий ліміт запитів, який Gemini повідомляє в деталях помилки 429 (quotaValue).

    Він різний для різних моделей і тарифів, а за каталогом його вгадати не можна: бувають значення в десятки запитів.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    for detail in (error or {}).get("details") or []:
        for violation in detail.get("violations") or []:
            if "perday" in str(violation.get("quotaId", "")).lower().replace("_", ""):
                try:
                    return int(violation.get("quotaValue"))
                except (TypeError, ValueError):
                    continue
    return None


def extract_limits_from_headers(
    provider_or_headers: Any,
    headers: Optional[Any] = None,
) -> Dict[str, Any]:
    if headers is None and isinstance(provider_or_headers, (dict, httpx.Headers)):
        provider = ""
        hdrs = provider_or_headers
    else:
        provider = str(provider_or_headers or "")
        hdrs = headers or {}

    low_map = {str(k).lower(): v for k, v in (hdrs.items() if hasattr(hdrs, "items") else [])}
    g = lambda k: _int(low_map.get(k.lower()))  # noqa: E731
    retry_after = _int(low_map.get("retry-after"))
    lim: Dict[str, Any] = {}

    if provider == "groq":
        # Заголовки Groq описують добовий ліміт запитів; ліміт на хвилину в них не передається
        lim = {
            "rpd": g("x-ratelimit-limit-requests"),
            "tpm": g("x-ratelimit-limit-tokens"),
            "rpd_left": g("x-ratelimit-remaining-requests"),
            "tpm_left": g("x-ratelimit-remaining-tokens"),
        }
    elif provider == "anthropic":
        lim = {
            "rpm": g("anthropic-ratelimit-requests-limit"),
            "tpm": g("anthropic-ratelimit-input-tokens-limit"),
            "rpm_left": g("anthropic-ratelimit-requests-remaining"),
            "tpm_left": g("anthropic-ratelimit-input-tokens-remaining"),
        }
    else:
        lim = {
            "rpm": g("x-ratelimit-limit-requests"),
            "tpm": g("x-ratelimit-limit-tokens"),
            "rpm_left": g("x-ratelimit-remaining-requests"),
            "tpm_left": g("x-ratelimit-remaining-tokens"),
        }

    if retry_after is not None:
        lim["retry_after_sec"] = retry_after

    return {k: v for k, v in lim.items() if v is not None}


async def _timed_get(client: httpx.AsyncClient, url: str, **kw: Any) -> Tuple[httpx.Response, int]:
    t0 = time.perf_counter()
    resp = await client.get(url, **kw)
    return resp, int((time.perf_counter() - t0) * 1000)


async def _list_gemini(client: httpx.AsyncClient, key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not key:
        return {"status": "no_key", "configured": False}, {}
    try:
        resp, lat = await _timed_get(
            client,
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key, "pageSize": 1000},
        )
    except Exception as exc:
        st, msg = classify_api_response("gemini", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("gemini", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    models: Dict[str, Any] = {}
    for m in resp.json().get("models", []):
        api_id = m.get("name", "").removeprefix("models/")
        methods = m.get("supportedGenerationMethods", [])
        if not is_text_generation_model("gemini", api_id, methods):
            continue
        is_gemma = api_id.lower().startswith("gemma")
        models[api_id] = {
            "display_name": m.get("displayName") or api_id,
            "context_in": m.get("inputTokenLimit") or 1048576,
            "context_out": m.get("outputTokenLimit") or 65536,
            "price_in": 0.0 if is_gemma else None,
            "price_out": 0.0 if is_gemma else None,
            "price_source": "api" if is_gemma else None,
            "rpm": 30 if is_gemma else 15,
            "rpd": 14400 if is_gemma else 1500,
            "tpm": 1000000,
        }
    return {"status": "ok", "configured": True, "http_status": 200, "latency_ms": lat, "models_total": len(models)}, models


async def _list_groq(client: httpx.AsyncClient, key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not key:
        return {"status": "no_key", "configured": False}, {}
    try:
        resp, lat = await _timed_get(
            client,
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
    except Exception as exc:
        st, msg = classify_api_response("groq", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("groq", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    models: Dict[str, Any] = {}
    for m in resp.json().get("data", []):
        if m.get("active") is False:
            continue
        api_id = m.get("id", "")
        if not is_text_generation_model("groq", api_id):
            continue
        pricing = m.get("pricing") or {}
        p_in = _per_million(pricing.get("prompt"))
        p_out = _per_million(pricing.get("completion"))
        models[api_id] = {
            "display_name": api_id,
            "context_in": m.get("context_window") or m.get("context_length") or 131072,
            "context_out": m.get("max_completion_tokens") or m.get("max_output_length") or 8192,
            "price_in": p_in,
            "price_out": p_out,
            "price_source": "api" if (p_in is not None and p_out is not None) else None,
            "rpm": 30,
            "rpd": 1000,
            "tpm": 12000,
        }
    return {"status": "ok", "configured": True, "http_status": 200, "latency_ms": lat, "models_total": len(models)}, models


async def _list_openai(client: httpx.AsyncClient, key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not key:
        return {"status": "no_key", "configured": False}, {}
    try:
        resp, lat = await _timed_get(
            client,
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
    except Exception as exc:
        st, msg = classify_api_response("openai", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("openai", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    models: Dict[str, Any] = {}
    for m in resp.json().get("data", []):
        api_id = m.get("id", "")
        if not is_text_generation_model("openai", api_id):
            continue
        models[api_id] = {
            "display_name": f"OpenAI {api_id}",
            "context_in": 128000,
            "context_out": 16384,
            "price_in": None,
            "price_out": None,
            "price_source": None,
            "rpm": 500,
            "rpd": 10000,
        }
    return {"status": "ok", "configured": True, "http_status": 200, "latency_ms": lat, "models_total": len(models)}, models


async def _list_anthropic(client: httpx.AsyncClient, key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not key:
        return {"status": "no_key", "configured": False}, {}
    try:
        resp, lat = await _timed_get(
            client,
            "https://api.anthropic.com/v1/models",
            params={"limit": 1000},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    except Exception as exc:
        st, msg = classify_api_response("anthropic", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("anthropic", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    models: Dict[str, Any] = {}
    for m in resp.json().get("data", []):
        api_id = m.get("id", "")
        if not is_text_generation_model("anthropic", api_id):
            continue
        models[api_id] = {
            "display_name": m.get("display_name") or api_id,
            "context_in": m.get("max_input_tokens") or 200000,
            "context_out": m.get("max_tokens") or 8192,
            "price_in": None,
            "price_out": None,
            "price_source": None,
            "rpm": 50,
            "rpd": 1000,
        }
    return {"status": "ok", "configured": True, "http_status": 200, "latency_ms": lat, "models_total": len(models)}, models


async def _list_openrouter(client: httpx.AsyncClient, key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not key:
        return {"status": "no_key", "configured": False}, {}
    auth = {"Authorization": f"Bearer {key}"}
    try:
        resp, lat = await _timed_get(client, "https://openrouter.ai/api/v1/key", headers=auth)
    except Exception as exc:
        st, msg = classify_api_response("openrouter", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("openrouter", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    d = resp.json().get("data", {})
    credits: Dict[str, Any] = {}
    try:
        cr = await client.get("https://openrouter.ai/api/v1/credits", headers=auth)
        if cr.status_code == 200:
            credits = cr.json().get("data", {}) or {}
    except Exception:
        credits = {}

    total = credits.get("total_credits")
    spent = credits.get("total_usage", d.get("usage"))
    balance = round(max(0.0, float(total) - float(spent)), 4) if (total is not None and spent is not None) else None
    key_left = d.get("limit_remaining")
    no_credit = (balance <= 0) if balance is not None else (key_left is not None and key_left <= 0)
    status = "no_credit" if no_credit else "ok"

    account = {
        "balance_usd": balance,
        "credits_total_usd": total,
        "spent_usd": spent,
        "key_limit_remaining_usd": key_left,
        "free_rpm": OPENROUTER_FREE_RPM_DOCS,
        "is_free_tier": d.get("is_free_tier"),
    }

    models: Dict[str, Any] = {}
    try:
        models_resp = await client.get("https://openrouter.ai/api/v1/models")
        if models_resp.status_code == 200:
            for m in models_resp.json().get("data", []):
                mid = m.get("id", "")
                if not is_text_generation_model("openrouter", mid):
                    continue
                pricing = m.get("pricing") or {}
                top = m.get("top_provider") or {}
                p_in = _per_million(pricing.get("prompt"))
                p_out = _per_million(pricing.get("completion"))
                models[mid] = {
                    "display_name": m.get("name") or mid,
                    "context_in": m.get("context_length") or 128000,
                    "context_out": top.get("max_completion_tokens") or 8192,
                    "price_in": p_in,
                    "price_out": p_out,
                    "price_source": "api" if (p_in is not None and p_out is not None) else None,
                }
    except Exception:
        pass

    return {
        "status": status,
        "configured": True,
        "http_status": 200,
        "latency_ms": lat,
        "models_total": len(models),
        "account": account,
    }, models


async def _list_ollama(client: httpx.AsyncClient, base_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"status": "no_key", "configured": False}, {}
    try:
        resp, lat = await _timed_get(client, f"{base}/api/tags", timeout=3.0)
    except Exception as exc:
        st, msg = classify_api_response("ollama", None, raw_text=str(exc), is_network_error=True)
        return {"status": st, "configured": True, "error": msg}, {}

    if resp.status_code != 200:
        st, msg = classify_api_response("ollama", resp.status_code, raw_text=resp.text)
        return {"status": st, "configured": True, "http_status": resp.status_code, "latency_ms": lat, "error": msg}, {}

    models: Dict[str, Any] = {}
    raw_models = resp.json().get("models", [])
    for m in raw_models:
        name = m.get("name", "")
        if not name or not is_text_generation_model("ollama", name):
            continue
        details = m.get("details") or {}
        info = {
            "display_name": f"Ollama {name}",
            "params_size": details.get("parameter_size", ""),
            "family": details.get("family", "local_ollama"),
            "context_in": 128000,
            "context_out": 8192,
            "price_in": 0.0,
            "price_out": 0.0,
            "price_source": "api",
            "rpm": 9999,
            "rpd": 999999,
        }
        models[name] = info
        if name.endswith(":latest"):
            models[name.removesuffix(":latest")] = info
    return {"status": "ok", "configured": True, "http_status": 200, "latency_ms": lat, "models_total": len(raw_models)}, models


def _match_openrouter_equivalent(provider: str, api_name: str, or_list: Dict[str, Any]) -> Optional[str]:
    if provider not in _ROUTE_ELIGIBLE_PROVIDERS or not or_list:
        return None
    candidates = [
        api_name,
        f"{api_name}:free",
        f"{provider}/{api_name}",
        f"{provider}/{api_name}:free",
    ]
    if provider == "groq" and api_name.startswith("llama-"):
        candidates.extend([
            f"meta-llama/{api_name}",
            f"meta-llama/{api_name}:free",
            f"meta-llama/{api_name.replace('-versatile', '-instruct').replace('-instant', '-instruct')}",
            f"meta-llama/{api_name.replace('-versatile', '-instruct').replace('-instant', '-instruct')}:free",
        ])
    if provider == "anthropic":
        short = api_name.removesuffix("-latest")
        candidates.extend([f"anthropic/{short}", f"anthropic/{api_name}"])
    for cand in candidates:
        if cand in or_list:
            return cand
    return None


async def _ping_single_model(
    client: httpx.AsyncClient,
    provider: str,
    api_model_name: str,
    api_key: str,
    base_url: str = "",
) -> Tuple[str, Optional[int], int, str, Dict[str, Any]]:
    """
    Виконує мінімальний запит (max_tokens=1) до конкретної моделі для перевірки її фактичної доступності,
    балансу та заголовків лімітів.
    """
    t0 = time.perf_counter()
    try:
        if provider == "gemini":
            if not api_key:
                return "no_key", None, 0, "Ключ GEMINI_API_KEY не задано", {}
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{api_model_name}:generateContent?key={api_key}",
                json={
                    "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                    "generationConfig": {"maxOutputTokens": 1},
                },
                timeout=8.0,
            )
        elif provider == "anthropic":
            if not api_key:
                return "no_key", None, 0, "Ключ ANTHROPIC_API_KEY не задано", {}
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"model": api_model_name, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
                timeout=8.0,
            )
        elif provider == "ollama":
            url_base = (base_url or "http://localhost:11434").rstrip("/")
            if url_base.endswith("/v1"):
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.post(
                    f"{url_base}/chat/completions",
                    headers=headers,
                    json={"model": api_model_name, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    timeout=5.0,
                )
            else:
                resp = await client.post(
                    f"{url_base}/api/chat",
                    json={
                        "model": api_model_name,
                        "messages": [{"role": "user", "content": "ping"}],
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                    timeout=5.0,
                )
        else:
            default_bases = {
                "groq": "https://api.groq.com/openai/v1",
                "openai": "https://api.openai.com/v1",
                "openrouter": "https://openrouter.ai/api/v1",
            }
            url_base = (base_url or default_bases.get(provider, "")).rstrip("/")
            if not url_base:
                return "offline", None, 0, "Не задано base_url для провайдера", {}
            is_local_url = "localhost" in url_base or "127.0.0.1" in url_base
            if not api_key and not is_local_url:
                return "no_key", None, 0, "Ключ API не задано", {}
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            # Моделі gpt-5 і o-серії відхиляють max_tokens, тому для OpenAI потрібен max_completion_tokens
            token_field = "max_completion_tokens" if provider == "openai" else "max_tokens"
            resp = await client.post(
                f"{url_base}/chat/completions",
                headers=headers,
                json={"model": api_model_name, "messages": [{"role": "user", "content": "ping"}], token_field: 1},
                timeout=8.0,
            )
    except Exception as exc:
        lat = int((time.perf_counter() - t0) * 1000)
        reason = "Час очікування відповіді вичерпано" if isinstance(exc, httpx.TimeoutException) else (str(exc) or type(exc).__name__)
        st, msg = classify_api_response(provider, None, raw_text=reason, is_network_error=True)
        return st, None, lat, msg, {}

    lat = int((time.perf_counter() - t0) * 1000)
    lim = extract_limits_from_headers(provider, resp.headers)
    st, msg = classify_api_response(provider, resp.status_code, raw_text=resp.text)
    if st == "daily_quota":
        try:
            learned = extract_daily_quota_limit(resp.json())
        except ValueError:
            learned = None
        if learned:
            lim = {**lim, "rpd": learned}
    return st, resp.status_code, lat, msg, lim


async def test_custom_model_connection(
    provider: str,
    model_id: str,
    base_url: str = "",
    api_key_env: str = "",
    api_key_value: Optional[str] = None,
) -> Dict[str, Any]:
    """Перевіряє підключення до користувацької OpenAI-сумісної моделі мінімальним запитом."""
    prov = (provider or "custom").strip().lower()
    prov_cfg = PROVIDERS_CATALOG.get(prov, {})
    eff_base = (base_url or prov_cfg.get("base_url") or "").strip()
    eff_key = (api_key_value or "").strip()
    if not eff_key and api_key_env:
        cand = api_key_env.strip()
        if cand.isupper() or "_" in cand:
            eff_key = get_env_secret(cand)
        else:
            eff_key = cand
    if not eff_key and prov in PROVIDERS_CATALOG:
        eff_key = get_env_secret(prov_cfg.get("env_key", ""))

    async with httpx.AsyncClient(timeout=10.0) as client:
        st, code, lat, msg, lim = await _ping_single_model(
            client=client,
            provider=prov,
            api_model_name=model_id.strip(),
            api_key=eff_key,
            base_url=eff_base,
        )
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "ok": st == "ok",
        "status": st,
        "status_label": get_status_label_uk(st),
        "http_code": code,
        "latency_ms": lat,
        "message": msg,
        "limits": lim,
        "checked_at": now_str,
        "checked_ago_uk": format_checked_ago_uk(now_str),
    }


def _is_recently_seen(first_seen: Optional[str]) -> bool:
    if not first_seen:
        return False
    try:
        dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - dt).total_seconds() <= 86400
    except Exception:
        return False


def get_models_status_snapshot() -> Dict[str, Any]:
    """
    Формує повний знімок реєстру моделей, їхніх індивідуальних статусів і зведеного стану провайдерів
    із бази даних SQLite без блокування мережевими запитами.
    """
    sync_model_registry_from_env()
    settings = load_runtime_settings()
    reg_rows = fetch_all_model_registry()
    statuses_map = fetch_all_model_statuses()
    stored_providers = fetch_all_provider_statuses()

    keys_present = {
        "gemini": bool(settings.get("gemini_api_key")),
        "groq": bool(settings.get("groq_api_key")),
        "openai": bool(settings.get("openai_api_key")),
        "anthropic": bool(settings.get("anthropic_api_key")),
        "openrouter": bool(settings.get("openrouter_api_key")),
        "ollama": bool(settings.get("ollama_base_url")),
    }

    models_out: Dict[str, Dict[str, Any]] = {}
    for r in reg_rows:
        mid = r["id"]
        prov = r["provider"]
        lim = r.get("limits") or {}
        st_row = statuses_map.get(mid) or {}

        has_key = keys_present.get(prov, True)
        if r.get("api_key_env"):
            has_key = bool(get_env_secret(r["api_key_env"])) or ("localhost" in (r.get("base_url") or ""))

        raw_status = st_row.get("status") or ("unchecked" if has_key else "no_key")
        if not has_key and raw_status == "unchecked":
            raw_status = "no_key"

        checked_at = st_row.get("checked_at")
        age = _age_seconds(checked_at)
        is_stale = raw_status == "ok" and age is not None and age > STALE_STATUS_SECONDS
        p_src = r.get("price_source") or "unknown"
        in_p = r.get("input_price_per_1m")
        out_p = r.get("output_price_per_1m")
        price_known = (p_src != "unknown") and (in_p is not None) and (out_p is not None)

        or_equiv = lim.get("openrouter_model")
        can_or = bool(
            r.get("source") != "custom"
            and prov in _ROUTE_ELIGIBLE_PROVIDERS
            and or_equiv
            and keys_present["openrouter"]
        )
        route_val = "openrouter" if (r.get("route") == "openrouter" and can_or) else "direct"
        preferred_val = ROUTE_OPENROUTER if route_val == "openrouter" else ROUTE_NATIVE

        used_today = get_daily_usage_count(mid)
        rpd_limit = lim.get("rpd")
        rpd_left = max(0, int(rpd_limit) - used_today) if rpd_limit is not None else lim.get("rpd_left")

        eff_limits = {
            **lim,
            "used_today": used_today,
            "rpd_left": rpd_left,
        }

        models_out[mid] = {
            "id": mid,
            "name": r["display_name"],
            "display_name": r["display_name"],
            "provider": prov,
            "family": lim.get("family") or prov,
            "api_model_name": r["model_id"],
            "model_id": r["model_id"],
            "openrouter_model": or_equiv,
            "source": r.get("source", "discovered"),
            "is_custom": r.get("source") == "custom",
            "is_dynamic": mid not in DEFAULT_MODELS_CATALOG,
            "is_new": (mid not in DEFAULT_MODELS_CATALOG) and _is_recently_seen(r.get("first_seen")),
            "enabled": bool(r.get("enabled")),
            "route": route_val,
            "preferred": preferred_val,
            "can_openrouter": can_or,
            "base_url": r.get("base_url") or "",
            "api_key_env": r.get("api_key_env") or "",
            "class_uk": lim.get("class_uk") or ("Власна модель" if r.get("source") == "custom" else ""),
            "description_uk": lim.get("description_uk") or "",
            "status_code": raw_status,
            "status_label": get_status_label_uk(raw_status),
            "http_code": st_row.get("http_code"),
            "latency_ms": st_row.get("latency_ms"),
            "error": st_row.get("message") or "",
            "checked_at": checked_at,
            "checked_ago_uk": format_checked_ago_uk(checked_at),
            "check_kind": st_row.get("check_kind") or ("ping" if checked_at else "none"),
            "is_stale": is_stale,
            "context_in": r.get("context_window") or 128000,
            "context_out": lim.get("output_limit") or 8192,
            "price_in": float(in_p) if price_known else None,
            "price_out": float(out_p) if price_known else None,
            "cost_per_1m_input_usd": float(in_p) if price_known else None,
            "cost_per_1m_output_usd": float(out_p) if price_known else None,
            "price_source": p_src,
            "price_known": price_known,
            "price_label_uk": f"${float(in_p):.2f} / ${float(out_p):.2f}" if price_known else "ціна не задана",
            "rpm": lim.get("rpm"),
            "rpd": rpd_limit,
            "tpm": lim.get("tpm"),
            "limits": eff_limits,
            "first_seen": r.get("first_seen"),
            "last_seen": r.get("last_seen"),
        }

    providers_out: Dict[str, Dict[str, Any]] = {}
    all_provider_ids = list(PROVIDERS_CATALOG.keys())
    if any(m["provider"] == "custom" for m in models_out.values()):
        all_provider_ids.append("custom")

    for pid in all_provider_ids:
        p_models = [m for m in models_out.values() if m["provider"] == pid]
        enabled_models = [m for m in p_models if m["enabled"]]
        cached_prov = _PROVIDER_SUMMARY_CACHE.get(pid) or stored_providers.get(pid) or {}

        checked_times = [m["checked_at"] for m in p_models if m.get("checked_at")]
        latest_check = max(checked_times) if checked_times else cached_prov.get("checked_at")

        if pid != "custom" and not keys_present.get(pid, False):
            p_status = "no_key"
        elif cached_prov.get("status") in ("invalid_key", "no_credit", "offline"):
            p_status = cached_prov["status"]
        elif enabled_models:
            statuses_set = {m["status_code"] for m in enabled_models}
            if "ok" in statuses_set:
                p_status = "ok"
            elif "daily_quota" in statuses_set:
                p_status = "daily_quota"
            elif "rate_limited" in statuses_set:
                p_status = "rate_limited"
            elif "no_credit" in statuses_set:
                p_status = "no_credit"
            elif "invalid_key" in statuses_set:
                p_status = "invalid_key"
            elif "offline" in statuses_set:
                p_status = "offline"
            else:
                p_status = next(iter(statuses_set))
        else:
            p_status = cached_prov.get("status") or ("unchecked" if keys_present.get(pid, True) else "no_key")

        providers_out[pid] = {
            "id": pid,
            "status": p_status,
            "status_label": get_status_label_uk(p_status),
            "configured": keys_present.get(pid, True),
            "models_count": len(p_models),
            "enabled_count": len(enabled_models),
            "checked_at": latest_check,
            "checked_ago_uk": format_checked_ago_uk(latest_check),
            "error": cached_prov.get("error") or "",
            "account": cached_prov.get("account"),
            "latency_ms": cached_prov.get("latency_ms"),
        }

    is_probing = _PROBE_JOB["running"] or any(not t.done() for t in _SINGLE_PROBE_TASKS)
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_probing": is_probing,
        "probe": {
            "running": _PROBE_JOB["running"],
            "phase": _PROBE_JOB.get("phase", ""),
            "done": _PROBE_JOB.get("done", 0),
            "total": _PROBE_JOB.get("total", 0),
            "queued": bool(_PROBE_JOB.get("queued")),
        },
        "providers": providers_out,
        "models": models_out,
    }


def _age_seconds(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        return (datetime.now() - datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None


def _listing_is_fresh() -> bool:
    """Список моделей перепитується не частіше за TTL, час останнього опитування зберігається в базі."""
    stored = fetch_all_provider_statuses()
    ages = [_age_seconds(p.get("checked_at")) for p in stored.values()]
    return bool(ages) and all(a is not None and a < DISCOVERY_TTL_SECONDS for a in ages)


def _provider_keys(settings: Dict[str, Any]) -> Dict[str, str]:
    return {
        "gemini": settings.get("gemini_api_key", ""),
        "groq": settings.get("groq_api_key", ""),
        "openai": settings.get("openai_api_key", ""),
        "anthropic": settings.get("anthropic_api_key", ""),
        "openrouter": settings.get("openrouter_api_key", ""),
        "ollama": settings.get("ollama_base_url", "http://localhost:11434"),
    }


def _remember_provider_report(pid: str, report: Dict[str, Any], now_str: str) -> None:
    report["checked_at"] = now_str
    _PROVIDER_SUMMARY_CACHE[pid] = report
    upsert_provider_status(pid, report, now_str)


def _merge_discovered_models(lists: Dict[str, Dict[str, Any]], now_str: str) -> None:
    pricing_data = load_models_pricing()
    pricing_map = pricing_data.get("models", pricing_data) if isinstance(pricing_data, dict) else {}
    or_list = lists.get("openrouter", {})
    by_prov_api = {(r["provider"], r["model_id"]): r["id"] for r in fetch_all_model_registry()}
    catalog_source = {"rpm": "catalog", "rpd": "catalog", "tpm": "catalog"}

    for pid in ("gemini", "groq", "openai", "anthropic", "ollama"):
        prov_cfg = PROVIDERS_CATALOG.get(pid, {})
        for api_name, facts in lists.get(pid, {}).items():
            reg_id = by_prov_api.get((pid, api_name)) or (
                api_name if api_name in DEFAULT_MODELS_CATALOG else f"{pid}:{api_name}"
            )
            seed_price = pricing_map.get(reg_id) or pricing_map.get(api_name) or {}
            if facts.get("price_in") is not None and facts.get("price_out") is not None:
                in_p, out_p, p_src = float(facts["price_in"]), float(facts["price_out"]), "api"
            elif seed_price.get("input_per_1m") is not None:
                in_p, out_p, p_src = float(seed_price["input_per_1m"]), float(seed_price["output_per_1m"]), "manual"
            else:
                in_p, out_p, p_src = None, None, "unknown"
            upsert_model_registry_entry(
                {
                    "id": reg_id,
                    "provider": pid,
                    "model_id": api_name,
                    "display_name": facts.get("display_name") or api_name,
                    "source": "discovered",
                    "base_url": prov_cfg.get("base_url", ""),
                    "api_key_env": prov_cfg.get("env_key", ""),
                    "context_window": facts.get("context_in") or 128000,
                    "input_price_per_1m": in_p,
                    "output_price_per_1m": out_p,
                    "price_source": p_src,
                    "limits": {
                        "rpm": facts.get("rpm"),
                        "rpd": facts.get("rpd"),
                        "tpm": facts.get("tpm"),
                        "limits_source": catalog_source,
                        "output_limit": facts.get("context_out") or 8192,
                        "openrouter_model": _match_openrouter_equivalent(pid, api_name, or_list),
                        "family": facts.get("family") or pid,
                    },
                    "last_seen": now_str,
                },
                preserve_user_fields=True,
            )
    _merge_openrouter_free_models(or_list, now_str)


def _merge_openrouter_free_models(or_list: Dict[str, Any], now_str: str) -> None:
    added = 0
    for or_api_name, facts in or_list.items():
        if added >= MAX_OPENROUTER_FREE_MODELS:
            break
        if not (or_api_name.endswith(":free") or (facts.get("price_in") == 0.0 and facts.get("price_out") == 0.0)):
            continue
        added += 1
        upsert_model_registry_entry(
            {
                "id": f"openrouter:{or_api_name}",
                "provider": "openrouter",
                "model_id": or_api_name,
                "display_name": facts.get("display_name") or or_api_name,
                "source": "discovered",
                "base_url": PROVIDERS_CATALOG["openrouter"]["base_url"],
                "api_key_env": "OPENROUTER_API_KEY",
                "context_window": facts.get("context_in") or 128000,
                "input_price_per_1m": 0.0,
                "output_price_per_1m": 0.0,
                "price_source": "api",
                "limits": {
                    "rpm": OPENROUTER_FREE_RPM_DOCS,
                    "rpd": 50,
                    "limits_source": {"rpm": "catalog", "rpd": "catalog"},
                    "output_limit": facts.get("context_out") or 8192,
                    "openrouter_model": or_api_name,
                },
                "last_seen": now_str,
            },
            preserve_user_fields=True,
        )


async def _discover_models(client: httpx.AsyncClient, keys: Dict[str, str], now_str: str) -> Tuple[Dict[str, Dict[str, Any]], set]:
    """Опитує списки моделей усіх провайдерів незалежно й оновлює реєстр.

    Повертає також множину провайдерів, чий список отримано успішно: лише їм можна вірити,
    коли модель у списку не знайдено. Збій списку не має змінювати статуси моделей.
    """
    listers = {
        "gemini": _list_gemini,
        "groq": _list_groq,
        "openai": _list_openai,
        "anthropic": _list_anthropic,
        "openrouter": _list_openrouter,
        "ollama": _list_ollama,
    }
    lists: Dict[str, Dict[str, Any]] = {}
    listed_ok: set = set()
    if not any(r["provider"] == "ollama" and r.get("enabled") for r in fetch_all_model_registry()):
        # Локальний сервер опитується лише тоді, коли ввімкнено хоча б одну локальну модель, інакше журнал засмічують помилки з'єднання
        listers.pop("ollama")
        _remember_provider_report("ollama", {"status": "unchecked", "configured": bool(keys["ollama"]),
                                             "error": "Не використовується: жодну локальну модель не ввімкнено."}, now_str)
    pids = list(listers)
    results = await asyncio.gather(*(listers[p](client, keys[p]) for p in pids), return_exceptions=True)
    for pid, res in zip(pids, results):
        if isinstance(res, Exception):
            status, message = classify_api_response(pid, None, raw_text=str(res), is_network_error=True)
            report, models = {"status": status, "configured": bool(keys[pid]), "error": message}, {}
        else:
            report, models = res
        _remember_provider_report(pid, report, now_str)
        lists[pid] = models
        if report.get("status") == "ok" and models:
            listed_ok.add(pid)
    _merge_discovered_models(lists, now_str)
    return lists, listed_ok


def _mark_inherited_statuses(lists: Dict[str, Dict[str, Any]], listed_ok: set, now_str: str) -> None:
    """Для вимкнених моделей запит не витрачається: статус береться від провайдера або зі списку, з відповідною позначкою."""
    for r in fetch_all_model_registry():
        if r.get("enabled"):
            continue
        pid = r["provider"]
        prov = _PROVIDER_SUMMARY_CACHE.get(pid) or {}
        if prov.get("status") in ("no_key", "invalid_key", "no_credit", "offline"):
            upsert_model_status(
                r["id"], prov["status"], prov.get("http_status"), prov.get("latency_ms"),
                prov.get("error", ""), now_str, check_kind="provider",
            )
        elif pid in listed_ok and r.get("source") != "custom" and pid != "ollama" and r["model_id"] not in lists[pid]:
            upsert_model_status(
                r["id"], "model_missing", 404, None, "Модель відсутня у списку API провайдера", now_str, check_kind="list",
            )


def _resolve_ping_target(r: Dict[str, Any], keys: Dict[str, str], settings: Dict[str, Any]) -> Dict[str, Any]:
    pid = r["provider"]
    lim = r.get("limits") or {}
    via_openrouter = bool(
        r.get("route") == "openrouter" and pid in _ROUTE_ELIGIBLE_PROVIDERS
        and lim.get("openrouter_model") and keys.get("openrouter")
    )
    if via_openrouter:
        model, base, key = lim["openrouter_model"], PROVIDERS_CATALOG["openrouter"]["base_url"], keys["openrouter"]
    else:
        model = settings.get("ollama_model_name", "llama3.2") if r["id"] == "ollama-local" else r["model_id"]
        base = r.get("base_url") or PROVIDERS_CATALOG.get(pid, {}).get("base_url", "")
        if pid == "ollama" and not r.get("base_url"):
            base = keys["ollama"]
        if pid in keys and pid != "ollama":
            key = keys[pid]
        elif r.get("api_key_env"):
            env_name = r["api_key_env"].strip()
            key = get_env_secret(env_name) if (env_name.isupper() or "_" in env_name) else env_name
        else:
            key = ""
    return {"provider": "openrouter" if via_openrouter else pid, "model": model, "key": key, "base": base, "via_openrouter": via_openrouter}


async def _check_one_model(
    client: httpx.AsyncClient,
    r: Dict[str, Any],
    keys: Dict[str, str],
    settings: Dict[str, Any],
    lists: Dict[str, Dict[str, Any]],
    listed_ok: set,
    now_str: str,
) -> None:
    target = _resolve_ping_target(r, keys, settings)
    pid = r["provider"]
    if (
        not target["via_openrouter"] and pid in listed_ok and r.get("source") != "custom"
        and pid != "ollama" and target["model"] not in lists[pid]
    ):
        upsert_model_status(
            r["id"], "model_missing", 404, None, f"Модель {target['model']} не знайдено у списку провайдера",
            now_str, check_kind="list",
        )
        return
    status, code, latency, message, header_limits = await _ping_single_model(
        client=client, provider=target["provider"], api_model_name=target["model"],
        api_key=target["key"], base_url=target["base"],
    )
    upsert_model_status(r["id"], status, code, latency, message, now_str)
    if code is not None:
        increment_daily_usage_count(r["id"])
    if header_limits:
        sources = {k: "header" for k in ("rpm", "rpd", "tpm") if header_limits.get(k) is not None}
        upsert_model_registry_entry({**r, "limits": {**header_limits, "limits_source": sources}}, preserve_user_fields=True)


async def _check_models(
    client: httpx.AsyncClient,
    targets: List[Dict[str, Any]],
    keys: Dict[str, str],
    settings: Dict[str, Any],
    lists: Dict[str, Dict[str, Any]],
    listed_ok: set,
    now_str: str,
    progress: Dict[str, Any],
) -> None:
    """Перевіряє кожну модель окремо: таймаут і збій однієї моделі не зупиняють інші."""
    progress.update(phase="Перевірка моделей", done=0, total=len(targets))
    semaphore = asyncio.Semaphore(PING_CONCURRENCY)

    async def worker(r: Dict[str, Any]) -> None:
        try:
            async with semaphore:
                await asyncio.wait_for(
                    _check_one_model(client, r, keys, settings, lists, listed_ok, now_str), timeout=PING_TIMEOUT_SECONDS
                )
        except asyncio.TimeoutError:
            upsert_model_status(r["id"], "offline", None, None, "Час очікування відповіді вичерпано", now_str)
        except Exception as exc:
            log_event("ДІАГНОСТИКА API", f"Не вдалося перевірити {r['id']}: {exc}", level="WARN")
        finally:
            progress["done"] += 1

    await asyncio.gather(*(worker(t) for t in targets))


def _experiment_is_running() -> bool:
    return bool((get_logs_and_status().get("status") or {}).get("is_busy"))


async def probe_all_providers(
    model_key: Optional[str] = None,
    only_enabled: bool = True,
    force: bool = True,
    progress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Поновлює списки моделей (не частіше за TTL, якщо не force) і перевіряє увімкнені моделі окремо.

    Під час експерименту запити-перевірки не надсилаються, щоб не витрачати квоту й не заважати серії.
    """
    progress = progress if progress is not None else {}
    sync_model_registry_from_env()
    settings = load_runtime_settings()
    keys = _provider_keys(settings)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with httpx.AsyncClient(timeout=12.0) as client:
        lists: Dict[str, Dict[str, Any]] = {}
        listed_ok: set = set()
        if model_key is None and (force or not _listing_is_fresh()):
            progress.update(phase="Пошук моделей", done=0, total=0)
            lists, listed_ok = await _discover_models(client, keys, now_str)
            _mark_inherited_statuses(lists, listed_ok, now_str)

        all_reg = fetch_all_model_registry()
        if model_key:
            targets = [r for r in all_reg if model_key in (r["id"], r["model_id"])]
        else:
            targets = [r for r in all_reg if r.get("enabled")] if only_enabled else all_reg
        if targets and model_key is None and _experiment_is_running():
            log_event("ДІАГНОСТИКА API", "Перевірку моделей запитами пропущено: триває експеримент.", level="INFO")
            targets = []
        await _check_models(client, targets, keys, settings, lists, listed_ok, now_str, progress)
    return get_models_status_snapshot()


def _launch_probe(params: Dict[str, Any]) -> asyncio.Task:
    global _ACTIVE_PROBE_TASK

    async def runner() -> None:
        global _ACTIVE_PROBE_TASK
        single = params.get("model_key") is not None
        progress = {"phase": "", "done": 0, "total": 0} if single else _PROBE_JOB
        try:
            await probe_all_providers(**params, progress=progress)
        except Exception as exc:
            log_event("ДІАГНОСТИКА API", f"Помилка фонової перевірки моделей: {exc}", level="ERROR")
        finally:
            if not single:
                _PROBE_JOB.update(running=False, phase="")
                queued = _PROBE_JOB.pop("queued", None)
                if queued:
                    _ACTIVE_PROBE_TASK = _launch_probe(queued)

    if params.get("model_key") is None:
        _PROBE_JOB.update(running=True, phase="Пошук моделей", done=0, total=0)
    task = asyncio.create_task(runner())
    if params.get("model_key") is None:
        _ACTIVE_PROBE_TASK = task
    else:
        _SINGLE_PROBE_TASKS.add(task)
        task.add_done_callback(_SINGLE_PROBE_TASKS.discard)
    return task


def start_probe_task(
    model_key: Optional[str] = None,
    only_enabled: bool = True,
    force: bool = True,
) -> Dict[str, Any]:
    """Запускає перевірку у фоні. Якщо загальна перевірка вже триває, новий запит ставиться в чергу, а не губиться."""
    params = {"model_key": model_key, "only_enabled": only_enabled, "force": force}
    if model_key is None and _PROBE_JOB["running"]:
        _PROBE_JOB["queued"] = params
        return {"started": False, "already_running": True, "queued": True, "snapshot": get_models_status_snapshot()}
    _launch_probe(params)
    return {"started": True, "model_key": model_key, "snapshot": get_models_status_snapshot()}


def load_cached_probe() -> Dict[str, Any]:
    return get_models_status_snapshot()
