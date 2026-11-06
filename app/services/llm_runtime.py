"""
Клієнтський шлюз та планувальник запитів до API мовних моделей (Google Gemini, Groq,
OpenAI, Anthropic, OpenRouter, локальний сервер Ollama та довільні OpenAI-сумісні API).
"""

import asyncio
import json
import random
import re
import time
from typing import Any, Dict, List, Optional
import httpx

from app.config import (
    DEFAULT_MODELS_CATALOG,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_SEED,
    ROUTE_NATIVE,
    ROUTE_OPENROUTER,
    can_route_via_openrouter,
    get_env_secret,
    get_model_config,
    load_runtime_settings,
)
from app.database import (
    get_daily_usage_count,
    increment_daily_usage_count,
    upsert_model_registry_entry,
    upsert_model_status,
)
from app.services.logger import (
    log_event,
    update_runtime_status,
)
from app.services.model_discovery import (
    classify_api_response,
    extract_daily_quota_limit,
    extract_limits_from_headers,
)


class DailyQuotaExceededError(RuntimeError):
    """Виняток при вичерпанні добового ліміту або балансу для моделі."""


class ProviderUnavailableError(DailyQuotaExceededError):
    """Обраний маршрут не може виконати запит."""

    def __init__(
        self,
        message: str,
        model_id: str,
        route: str,
        reason: str,
        alt_route: Optional[str] = None,
    ):
        super().__init__(message)
        self.model_id = model_id
        self.route = route
        self.reason = reason
        self.alt_route = alt_route


class RequestScheduler:
    """Планувальник запитів з контролем RPM/RPD, Retry-After та експоненціальним backoff з jitter."""

    def __init__(self) -> None:
        self._rpm_timestamps: Dict[str, List[float]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def acquire_rpm_slot(self, bucket_key: str, rpm_limit: Optional[int]) -> float:
        """Очікує вільний хвилинний слот лише якщо поточна інтенсивність перевищує RPM."""
        if not rpm_limit or rpm_limit <= 0:
            return 0.0
        lock = self._get_lock(bucket_key)
        waited = 0.0
        async with lock:
            now = time.monotonic()
            window = [t for t in self._rpm_timestamps.get(bucket_key, []) if now - t < 60.0]
            if len(window) >= rpm_limit:
                oldest = window[0]
                sleep_sec = max(0.05, 60.0 - (now - oldest) + random.uniform(0.05, 0.25))
                await asyncio.sleep(sleep_sec)
                waited = sleep_sec
                now = time.monotonic()
                window = [t for t in window if now - t < 60.0]
            window.append(now)
            self._rpm_timestamps[bucket_key] = window
        return round(waited, 2)

    def check_and_reserve_rpd(self, model_id: str, rpd_limit: Optional[int]) -> int:
        """Перевіряє денну квоту в SQLite; при вичерпанні піднімає помилку, а серія пропускає лише цю модель."""
        if rpd_limit and rpd_limit > 0:
            used = get_daily_usage_count(model_id)
            if used >= rpd_limit:
                msg = f"Денний ліміт вичерпано для {model_id} ({used}/{rpd_limit} RPD)."
                self.handle_daily_quota_exhausted(model_id, msg, 429)
                raise DailyQuotaExceededError(msg)
        return increment_daily_usage_count(model_id)

    def compute_backoff_delay(
        self,
        attempt: int,
        headers: Optional[Dict[str, Any]] = None,
        raw_text: str = "",
        base_delay: float = 2.0,
    ) -> float:
        """Обчислює затримку: Retry-After або експоненціальний backoff з випадковим jitter."""
        hdr_info = extract_limits_from_headers(headers or {})
        retry_after = hdr_info.get("retry_after_sec")
        if retry_after is not None and retry_after > 0:
            return min(45.0, float(retry_after) + random.uniform(0.2, 0.8))

        m_wait = re.search(r"try again in\s+(\d+(?:\.\d+)?)s", raw_text or "", flags=re.IGNORECASE)
        if m_wait:
            try:
                return min(45.0, max(1.0, float(m_wait.group(1)) + random.uniform(0.3, 1.0)))
            except ValueError:
                pass

        exp = base_delay * (2 ** max(0, attempt - 1))
        jitter = random.uniform(0.15 * exp, 0.45 * exp)
        return round(min(35.0, exp + jitter), 2)

    def handle_daily_quota_exhausted(
        self,
        model_id: str,
        message: str,
        http_code: Optional[int] = 429,
    ) -> None:
        """Фіксує статус daily_quota. Серію не зупиняє: раннер сам пропускає цю модель і веде решту далі."""
        upsert_model_status(
            model_key=model_id,
            status="daily_quota",
            http_code=http_code,
            latency_ms=None,
            message=message[:240],
        )


REQUEST_SCHEDULER = RequestScheduler()

NATIVE_API_PROVIDERS = ("gemini", "anthropic", "ollama")


def estimate_tokens(text: str) -> int:
    """Резервна оцінка кількості токенів, якщо провайдер не повернув блок usage."""
    if not text:
        return 0
    return max(1, int(round(len(text) / 3.2)))


def is_model_price_known(model_id: str) -> bool:
    """Перевіряє, чи відома ціна для обраної моделі."""
    cfg = get_model_config(model_id)
    return bool(cfg.get("price_known", False))


def calculate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
) -> float:
    """Обчислює вартість запиту в USD (0.0 для SQLite, якщо ціна не задана)."""
    model_cfg = get_model_config(model_id)
    if not model_cfg.get("price_known", False):
        return 0.0
    in_price = model_cfg.get("cost_per_1m_input_usd")
    out_price = model_cfg.get("cost_per_1m_output_usd")
    if in_price is None or out_price is None:
        return 0.0
    in_cost = (input_tokens / 1_000_000.0) * float(in_price)
    billed_out = output_tokens + max(0, int(thinking_tokens or 0))
    out_cost = (billed_out / 1_000_000.0) * float(out_price)
    return round(in_cost + out_cost, 7)


def _record_response_headers_and_status(
    model_id: str,
    status: str,
    http_code: Optional[int],
    latency_ms: Optional[float],
    message: str,
    headers: Optional[Dict[str, Any]] = None,
) -> None:
    if model_id:
        upsert_model_status(
            model_key=model_id,
            status=status,
            http_code=http_code,
            latency_ms=latency_ms,
            message=message[:240],
        )
        if headers:
            lim = extract_limits_from_headers(get_model_config(model_id).get("provider", ""), headers)
            if lim.get("rpm") or lim.get("rpd"):
                sources = {k: "header" for k in ("rpm", "rpd", "tpm") if lim.get(k) is not None}
                upsert_model_registry_entry({"id": model_id, "limits": {**lim, "limits_source": sources}})


def _handle_http_failure(
    provider: str,
    provider_label: str,
    model_id: str,
    model_name: str,
    resp: httpx.Response,
    call_ms: float,
    attempt: int,
    max_attempts: int = 3,
) -> Optional[float]:
    """Повертає час очікування (сек) для retry або піднімає відповідний виняток."""
    try:
        payload = resp.json() if resp.content else None
    except Exception:
        payload = None
    raw_text = resp.text or ""
    status_code, msg = classify_api_response(provider, resp.status_code, payload, raw_text)
    _record_response_headers_and_status(
        model_id=model_id,
        status=status_code,
        http_code=resp.status_code,
        latency_ms=call_ms,
        message=msg,
        headers=dict(resp.headers),
    )

    if status_code == "daily_quota":
        learned_rpd = extract_daily_quota_limit(payload)
        if learned_rpd and model_id:
            upsert_model_registry_entry({"id": model_id, "limits": {"rpd": learned_rpd, "limits_source": {"rpd": "header"}}})
        full_msg = f"Денний ліміт вичерпано ({provider_label} / {model_name}): {msg}"
        log_event(stage=f"LLM <- {provider_label}", message=full_msg, level="ERROR")
        REQUEST_SCHEDULER.handle_daily_quota_exhausted(model_id or model_name, full_msg, resp.status_code)
        raise DailyQuotaExceededError(full_msg)

    if status_code == "no_credit":
        full_msg = f"Нульовий баланс ({provider_label} / {model_name}): {msg}"
        log_event(stage=f"LLM <- {provider_label}", message=full_msg, level="ERROR")
        raise DailyQuotaExceededError(full_msg)

    if resp.status_code in (429, 503, 529) and attempt < max_attempts:
        wait_sec = REQUEST_SCHEDULER.compute_backoff_delay(attempt, dict(resp.headers), raw_text)
        log_event(
            stage=f"LLM <- {provider_label}",
            message=f"Тимчасове обмеження ({resp.status_code}) для {model_name}. Пауза {wait_sec} с (спроба {attempt + 1}/{max_attempts})...",
            level="WARN",
        )
        return wait_sec

    err_short = raw_text[:260].replace("\n", " ")
    log_event(
        stage=f"LLM <- {provider_label}",
        message=f"Помилка HTTP {resp.status_code} для {model_name}: {err_short}",
        level="ERROR",
    )
    raise RuntimeError(f"Помилка {provider_label} ({resp.status_code}) для {model_name}: {msg}")


async def call_openai_compatible_api(
    provider_label: str,
    base_url: str,
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    seed: Optional[int] = DEFAULT_SEED,
    model_id: str = "",
    json_mode: bool = False,
    reasoning_effort: Optional[str] = None,
) -> Dict[str, Any]:
    """Виконує запит до OpenAI-сумісного API (Groq, OpenAI, OpenRouter або власний сервер)."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    effective_max_tokens = min(max_output_tokens, 4096) if provider_label.upper() == "GROQ" else max_output_tokens
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": effective_max_tokens,
    }
    if seed is not None:
        payload["seed"] = int(seed)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if reasoning_effort and ("qwen" in model_name.lower() or "deepseek" in model_name.lower()):
        payload["reasoning_effort"] = reasoning_effort

    provider_code = provider_label.lower()
    wall_t0 = time.perf_counter()
    for attempt in range(1, 4):
        log_event(
            stage=f"LLM -> {provider_label}",
            message=f"Запит до {provider_label} | Модель: {model_name} | temp={temperature} | max_tokens={effective_max_tokens}",
            level="LLM_REQ",
        )
        call_t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=65.0) as client:
                resp = await client.post(endpoint, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if attempt < 3:
                wait_s = REQUEST_SCHEDULER.compute_backoff_delay(attempt, base_delay=2.0)
                log_event(
                    stage=f"LLM <- {provider_label}",
                    message=f"Мережева затримка від {model_name}. Повторна спроба {attempt + 1}/3 через {wait_s} с...",
                    level="WARN",
                )
                await asyncio.sleep(wait_s)
                continue
            _record_response_headers_and_status(model_id, "offline", None, None, str(exc))
            raise RuntimeError(f"Немає зв'язку з {provider_label} ({model_name}): {exc}") from exc

        call_ms = round((time.perf_counter() - call_t0) * 1000.0, 1)
        if resp.status_code == 400 and ("response_format" in payload or "reasoning_effort" in payload):
            payload.pop("response_format", None)
            payload.pop("reasoning_effort", None)
            continue
        if resp.status_code >= 400:
            wait_sec = _handle_http_failure(
                provider_code, provider_label, model_id, model_name, resp, call_ms, attempt
            )
            if wait_sec is not None:
                await asyncio.sleep(wait_sec)
                continue

        wall_ms = round((time.perf_counter() - wall_t0) * 1000.0, 1)
        _record_response_headers_and_status(
            model_id, "ok", resp.status_code, call_ms, "Модель відповідає", dict(resp.headers)
        )
        data = resp.json()
        choice0 = (data.get("choices") or [{}])[0]
        answer_text = (choice0.get("message", {}).get("content") or "").strip()
        finish_reason = str(choice0.get("finish_reason") or "stop")
        truncated = finish_reason.lower() in ("length", "max_tokens")

        usage = data.get("usage") or {}
        raw_in = usage.get("prompt_tokens")
        raw_out = usage.get("completion_tokens")
        tokens_estimated = (raw_in is None) or (raw_out is None)
        in_tok = int(raw_in) if raw_in is not None else estimate_tokens(system_prompt + user_prompt)
        out_tok = int(raw_out) if raw_out is not None else estimate_tokens(answer_text)
        thinking_tok = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)

        log_event(
            stage=f"LLM <- {provider_label}",
            message=f"Відповідь від {model_name} за {call_ms} мс (спроба {attempt}) | Токени: {in_tok}/{out_tok} | finish={finish_reason}",
            level="LLM_RES",
        )
        return {
            "generated_answer": answer_text,
            "latency_ms": call_ms,
            "call_ms": call_ms,
            "wall_ms": wall_ms,
            "attempts": attempt,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "thinking_tokens": thinking_tok,
            "tokens_estimated": tokens_estimated,
            "raw_usage_json": json.dumps(usage, ensure_ascii=False),
            "finish_reason": finish_reason,
            "truncated": truncated,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "seed": seed,
        }

    raise RuntimeError(f"Перевищено кількість спроб звернення до {provider_label} ({model_name})")


async def call_gemini_api(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    seed: Optional[int] = DEFAULT_SEED,
    model_id: str = "",
    json_mode: bool = False,
    thinking_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Виконує запит до Google Gemini / Gemma API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    gen_cfg: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
    }
    if json_mode and "gemma" not in model_name.lower():
        gen_cfg["responseMimeType"] = "application/json"
    if thinking_budget is not None and "2.5" in model_name.lower():
        gen_cfg["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": gen_cfg,
    }

    wall_t0 = time.perf_counter()
    for attempt in range(1, 4):
        log_event(
            stage="LLM -> GEMINI",
            message=f"Запит до Google AI Studio | Модель: {model_name} | temp={temperature} | maxOutputTokens={max_output_tokens}",
            level="LLM_REQ",
        )
        call_t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if attempt < 3:
                wait_s = REQUEST_SCHEDULER.compute_backoff_delay(attempt, base_delay=2.0)
                log_event(
                    stage="LLM <- GEMINI",
                    message=f"Мережева помилка/таймаут для {model_name}. Спроба {attempt + 1}/3 через {wait_s} с...",
                    level="WARN",
                )
                await asyncio.sleep(wait_s)
                continue
            _record_response_headers_and_status(model_id, "offline", None, None, str(exc))
            raise RuntimeError(f"Мережева помилка Google API для моделі {model_name}: {exc}") from exc

        call_ms = round((time.perf_counter() - call_t0) * 1000.0, 1)
        if resp.status_code == 400 and ("thinkingConfig" in gen_cfg or "responseMimeType" in gen_cfg):
            gen_cfg.pop("thinkingConfig", None)
            gen_cfg.pop("responseMimeType", None)
            continue
        if resp.status_code >= 400:
            wait_sec = _handle_http_failure(
                "gemini", "GEMINI", model_id, model_name, resp, call_ms, attempt
            )
            if wait_sec is not None:
                await asyncio.sleep(wait_sec)
                continue

        wall_ms = round((time.perf_counter() - wall_t0) * 1000.0, 1)
        _record_response_headers_and_status(
            model_id, "ok", resp.status_code, call_ms, "Модель відповідає", dict(resp.headers)
        )
        data = resp.json()
        candidates = data.get("candidates", [])
        answer_text = ""
        finish_reason = "STOP"
        if candidates:
            finish_reason = str(candidates[0].get("finishReason") or "STOP")
            if "content" in candidates[0]:
                parts = candidates[0]["content"].get("parts", [])
                visible_parts = [p.get("text", "") for p in parts if not p.get("thought")]
                if not visible_parts and parts:
                    visible_parts = [parts[-1].get("text", "")]
                answer_text = "".join(visible_parts).strip()

        truncated = finish_reason.upper() in ("MAX_TOKENS", "LENGTH")
        usage = data.get("usageMetadata") or {}
        raw_in = usage.get("promptTokenCount")
        raw_out = usage.get("candidatesTokenCount")
        thinking_tok = int(usage.get("thoughtsTokenCount") or 0)
        tokens_estimated = (raw_in is None) or (raw_out is None)
        in_tok = int(raw_in) if raw_in is not None else estimate_tokens(system_prompt + user_prompt)
        out_tok = int(raw_out) if raw_out is not None else estimate_tokens(answer_text)

        log_event(
            stage="LLM <- GEMINI",
            message=f"Відповідь від {model_name} за {call_ms} мс (спроба {attempt}) | Токени: {in_tok}/{out_tok} | finish={finish_reason}",
            level="LLM_RES",
        )
        return {
            "generated_answer": answer_text,
            "latency_ms": call_ms,
            "call_ms": call_ms,
            "wall_ms": wall_ms,
            "attempts": attempt,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "thinking_tokens": thinking_tok,
            "tokens_estimated": tokens_estimated,
            "raw_usage_json": json.dumps(usage, ensure_ascii=False),
            "finish_reason": finish_reason,
            "truncated": truncated,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "seed": seed,
        }

    raise RuntimeError(f"Не вдалося отримати відповідь від {model_name} після повторних спроб.")


async def call_anthropic_api(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    model_id: str = "",
) -> Dict[str, Any]:
    """Виконує запит до Anthropic Messages API (Claude)."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model_name,
        "max_tokens": max_output_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system_prompt:
        payload["system"] = system_prompt

    wall_t0 = time.perf_counter()
    for attempt in range(1, 4):
        call_t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if attempt < 3:
                await asyncio.sleep(REQUEST_SCHEDULER.compute_backoff_delay(attempt))
                continue
            _record_response_headers_and_status(model_id, "offline", None, None, str(exc))
            raise RuntimeError(f"Мережева помилка Anthropic API ({model_name}): {exc}") from exc

        call_ms = round((time.perf_counter() - call_t0) * 1000.0, 1)
        if resp.status_code >= 400:
            wait_sec = _handle_http_failure(
                "anthropic", "ANTHROPIC", model_id, model_name, resp, call_ms, attempt
            )
            if wait_sec is not None:
                await asyncio.sleep(wait_sec)
                continue

        wall_ms = round((time.perf_counter() - wall_t0) * 1000.0, 1)
        _record_response_headers_and_status(
            model_id, "ok", resp.status_code, call_ms, "Модель відповідає", dict(resp.headers)
        )
        data = resp.json()
        text = "".join(
            c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"
        )
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        stop_reason = str(data.get("stop_reason", "end_turn"))

        return {
            "generated_answer": text,
            "latency_ms": call_ms,
            "call_ms": call_ms,
            "wall_ms": wall_ms,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "thinking_tokens": 0,
            "total_tokens": in_tok + out_tok,
            "tokens_estimated": in_tok == 0 and out_tok == 0,
            "raw_usage_json": json.dumps(usage, ensure_ascii=False),
            "finish_reason": stop_reason,
            "truncated": stop_reason == "max_tokens",
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "seed": None,
            "attempts": attempt,
        }

    raise RuntimeError(f"Не вдалося отримати відповідь від Anthropic ({model_name}).")


async def call_ollama_api(
    base_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    seed: Optional[int] = DEFAULT_SEED,
    model_id: str = "",
) -> Dict[str, Any]:
    """Виконує запит до локального сервера Ollama."""
    url = f"{base_url.rstrip('/')}/api/chat"
    log_event(
        stage="LLM -> OLLAMA",
        message=f"Запит до локального сервера Ollama ({url}) | Модель: {model_name}",
        level="LLM_REQ",
    )
    options: Dict[str, Any] = {
        "temperature": temperature,
        "num_predict": max_output_tokens,
    }
    if seed is not None:
        options["seed"] = int(seed)

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": options,
    }
    call_t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)
        call_ms = round((time.perf_counter() - call_t0) * 1000.0, 1)
        if resp.status_code >= 400:
            _handle_http_failure("ollama", "OLLAMA", model_id, model_name, resp, call_ms, 3, 3)
        data = resp.json()

    _record_response_headers_and_status(model_id, "ok", 200, call_ms, "Модель відповідає")
    answer_text = (data.get("message", {}).get("content") or "").strip()
    finish_reason = str(data.get("done_reason") or "stop")
    truncated = finish_reason.lower() in ("length", "max_tokens")

    raw_in = data.get("prompt_eval_count")
    raw_out = data.get("eval_count")
    tokens_estimated = (raw_in is None) or (raw_out is None)
    in_tok = int(raw_in) if raw_in is not None else estimate_tokens(system_prompt + user_prompt)
    out_tok = int(raw_out) if raw_out is not None else estimate_tokens(answer_text)
    usage = {
        "prompt_eval_count": raw_in,
        "eval_count": raw_out,
        "total_duration": data.get("total_duration"),
        "eval_duration": data.get("eval_duration"),
    }

    log_event(
        stage="LLM <- OLLAMA",
        message=f"Відповідь від Ollama ({model_name}) за {call_ms} мс | Токени: {in_tok}/{out_tok}",
        level="LLM_RES",
    )
    return {
        "generated_answer": answer_text,
        "latency_ms": call_ms,
        "call_ms": call_ms,
        "wall_ms": call_ms,
        "attempts": 1,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "thinking_tokens": 0,
        "tokens_estimated": tokens_estimated,
        "raw_usage_json": json.dumps(usage, ensure_ascii=False),
        "finish_reason": finish_reason,
        "truncated": truncated,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "seed": seed,
    }


_SESSION_ROUTES: Dict[str, str] = {}


def set_session_route(model_id: str, route: str) -> None:
    if model_id not in DEFAULT_MODELS_CATALOG and ":" in model_id:
        cand = model_id.split(":", 1)[1]
        if cand in DEFAULT_MODELS_CATALOG:
            model_id = cand
    _SESSION_ROUTES[model_id] = route


def clear_session_routes() -> None:
    _SESSION_ROUTES.clear()


def classify_provider_error(text: str) -> Optional[str]:
    t = (text or "").lower()
    m = re.search(r"\((\d{3})\)|http (\d{3})", t)
    code = int(next(g for g in m.groups() if g)) if m else None
    if "не вказано" in t or "вкажіть" in t or "ключ не задано" in t:
        return "no_key"
    if "денний ліміт" in t or "добовий ліміт" in t or "perday" in t:
        return "daily_quota"
    if code == 402 or any(k in t for k in ("credit", "billing", "insufficient", "баланс")):
        return "no_credit"
    if code in (401, 403) or ("api key" in t and "invalid" in t) or "невірний ключ" in t:
        return "invalid_key"
    if code == 404 or "does not exist" in t or "model_not_found" in t or "недоступна" in t:
        return "model_missing"
    if code == 429 or any(k in t for k in ("quota", "rate limit", "resource_exhausted", "квот", "ліміт")):
        return "rate_limited"
    if "немає зв'язку" in t or ("ollama" in t and "недоступн" in t):
        return "offline"
    return None


def resolve_route(model_id: str, settings: Optional[Dict[str, Any]] = None) -> str:
    if settings is None:
        settings = load_runtime_settings()
    if model_id in _SESSION_ROUTES:
        return _SESSION_ROUTES[model_id]
    cfg = get_model_config(model_id)
    if cfg.get("route") == ROUTE_OPENROUTER and can_route_via_openrouter(model_id):
        return ROUTE_OPENROUTER
    if settings.get("model_routes", {}).get(model_id) == ROUTE_OPENROUTER and can_route_via_openrouter(model_id):
        return ROUTE_OPENROUTER
    return ROUTE_NATIVE


def alternate_route(model_id: str, current: str, settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if settings is None:
        settings = load_runtime_settings()
    cfg = get_model_config(model_id)
    provider = cfg.get("provider", "")
    if provider in ("ollama", "gemini", "openrouter", "custom"):
        return None
    if current == ROUTE_NATIVE:
        if can_route_via_openrouter(model_id) and settings.get("openrouter_api_key"):
            return ROUTE_OPENROUTER
        return None
    return ROUTE_NATIVE if settings.get(f"{provider}_api_key") else None


async def _call_openrouter(
    model_id, model_cfg, settings, system_prompt, user_prompt, temp, max_tok, seed_val,
    json_mode: bool = False, thinking_budget: Optional[int] = None, reasoning_effort: Optional[str] = None,
):
    key = settings.get("openrouter_api_key")
    or_model = model_cfg.get("openrouter_model") or model_cfg.get("api_model_name")
    if not key or not or_model:
        raise ProviderUnavailableError(
            f"Для моделі {model_cfg['name']} не вказано OPENROUTER_API_KEY у файлі .env",
            model_id,
            ROUTE_OPENROUTER,
            "no_key",
            alternate_route(model_id, ROUTE_OPENROUTER, settings),
        )
    res = await call_openai_compatible_api(
        provider_label="OPENROUTER",
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
        model_name=or_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temp,
        max_output_tokens=max_tok,
        seed=seed_val,
        model_id=model_id,
        json_mode=json_mode,
        reasoning_effort=reasoning_effort,
    )
    res["provider_used"] = "openrouter_api"
    return res


async def _call_native(
    model_id, model_cfg, settings, system_prompt, user_prompt, temp, max_tok, seed_val,
    json_mode: bool = False, thinking_budget: Optional[int] = None, reasoning_effort: Optional[str] = None,
):
    provider = model_cfg["provider"]
    if provider == "openrouter":
        return await _call_openrouter(
            model_id, model_cfg, settings, system_prompt, user_prompt, temp, max_tok, seed_val,
            json_mode=json_mode, thinking_budget=thinking_budget, reasoning_effort=reasoning_effort,
        )

    # У Gemini, Anthropic та Ollama власні формати API, тому base_url з каталогу їм не підходить
    if provider == "custom" or (model_cfg.get("base_url") and provider not in NATIVE_API_PROVIDERS):
        base_url = model_cfg.get("base_url") or "https://api.openai.com/v1"
        env_name = model_cfg.get("api_key_env") or ""
        custom_key = get_env_secret(env_name) if env_name else settings.get(f"{provider}_api_key", "")
        res = await call_openai_compatible_api(
            provider_label=provider.upper(),
            base_url=base_url,
            api_key=custom_key,
            model_name=model_cfg["api_model_name"],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
            max_output_tokens=max_tok,
            seed=seed_val,
            model_id=model_id,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
        )
        res["provider_used"] = f"{provider}_api"
        return res

    key = settings.get(f"{provider}_api_key", "")

    if provider == "ollama":
        ollama_url = settings.get("ollama_base_url", "http://localhost:11434")
        ollama_model = (
            model_cfg["api_model_name"]
            if model_id != "ollama-local"
            else (settings.get("ollama_model_name") or model_cfg["api_model_name"])
        )
        try:
            res = await call_ollama_api(
                base_url=ollama_url,
                model_name=ollama_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temp,
                max_output_tokens=max_tok,
                seed=seed_val,
                model_id=model_id,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"Локальний сервер Ollama ({ollama_url}) недоступний: {exc}",
                model_id,
                ROUTE_NATIVE,
                "offline",
                None,
            ) from exc
        res["provider_used"] = f"ollama ({ollama_model})"
        return res

    if not key:
        raise ProviderUnavailableError(
            f"Для моделі {model_cfg['name']} не вказано {provider.upper()}_API_KEY у файлі .env",
            model_id,
            ROUTE_NATIVE,
            "no_key",
            alternate_route(model_id, ROUTE_NATIVE, settings),
        )

    if provider == "gemini":
        res = await call_gemini_api(
            api_key=key,
            model_name=model_cfg["api_model_name"],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
            max_output_tokens=max_tok,
            seed=seed_val,
            model_id=model_id,
            json_mode=json_mode,
            thinking_budget=thinking_budget,
        )
    elif provider == "anthropic":
        res = await call_anthropic_api(
            api_key=key,
            model_name=model_cfg["api_model_name"],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
            max_output_tokens=max_tok,
            model_id=model_id,
        )
    elif provider in ("groq", "openai"):
        res = await call_openai_compatible_api(
            provider_label=provider.upper(),
            base_url={"groq": "https://api.groq.com/openai/v1", "openai": "https://api.openai.com/v1"}[provider],
            api_key=key,
            model_name=model_cfg["api_model_name"],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
            max_output_tokens=max_tok,
            seed=seed_val,
            model_id=model_id,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
        )
    else:
        raise RuntimeError(f"Невідомий провайдер моделі: {model_id}")
    res["provider_used"] = f"{provider}_api"
    return res


async def execute_llm_request(
    model_id: str,
    prompt_code: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    seed: Optional[int] = None,
    json_mode: bool = False,
    thinking_budget: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Виконує запит до обраної моделі через планувальник RPM/RPD за налаштованим маршрутом."""
    settings = load_runtime_settings()
    temp = float(temperature) if temperature is not None else float(settings.get("temperature", DEFAULT_TEMPERATURE))
    max_tok = int(max_output_tokens) if max_output_tokens is not None else int(settings.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS))
    seed_val = int(seed) if seed is not None else int(settings.get("seed", DEFAULT_SEED))

    model_cfg = get_model_config(model_id)
    route = resolve_route(model_id, settings)
    provider_label = "OPENROUTER" if route == ROUTE_OPENROUTER else model_cfg["provider"].upper()

    rpm_limit = model_cfg.get("rpm")
    rpd_limit = model_cfg.get("rpd")
    await REQUEST_SCHEDULER.acquire_rpm_slot(f"{provider_label}:{model_id}", rpm_limit)
    REQUEST_SCHEDULER.check_and_reserve_rpd(model_id, rpd_limit)

    update_runtime_status(
        stage_number=4,
        current_stage=f"Етап 4/5: Генерація відповіді моделлю {model_cfg['name']} ({provider_label})",
        active_model=model_cfg["name"],
        active_provider=provider_label,
        active_prompt=prompt_code,
    )

    caller = _call_openrouter if route == ROUTE_OPENROUTER else _call_native
    try:
        res = await caller(
            model_id, model_cfg, settings, system_prompt, user_prompt, temp, max_tok, seed_val,
            json_mode=json_mode, thinking_budget=thinking_budget, reasoning_effort=reasoning_effort,
        )
    except ProviderUnavailableError:
        raise
    except DailyQuotaExceededError:
        raise
    except Exception as exc:
        reason = classify_provider_error(str(exc))
        if reason is None:
            raise
        raise ProviderUnavailableError(
            str(exc), model_id, route, reason, alternate_route(model_id, route, settings)
        ) from exc

    res["route"] = route
    price_known = bool(model_cfg.get("price_known", False))
    res["cost_unknown"] = not price_known
    res["cost_usd"] = calculate_cost_usd(
        model_id, res["input_tokens"], res["output_tokens"], res.get("thinking_tokens", 0)
    )
    if not price_known:
        try:
            usage_obj = json.loads(res.get("raw_usage_json") or "{}")
        except Exception:
            usage_obj = {}
        usage_obj["cost_unknown"] = True
        res["raw_usage_json"] = json.dumps(usage_obj, ensure_ascii=False)
    return res
