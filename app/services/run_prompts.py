"""
Керування інтерактивними запитами до користувача під час виконання експериментів
(зміна маршруту моделі на OpenRouter, вибір судді або дострокове чесне завершення).
"""

import asyncio
import time
from typing import Any, Dict, List, Optional


_ACTIVE_PROMPT: Optional[Dict[str, Any]] = None
_PROMPT_EVENT: Optional[asyncio.Event] = None
_USER_DECISION: Optional[Dict[str, Any]] = None


def get_active_prompt() -> Optional[Dict[str, Any]]:
    """Повертає поточний активний запит до користувача (або None)."""
    return _ACTIVE_PROMPT


def clear_active_prompt() -> None:
    """Очищає стан інтерактивного запиту."""
    global _ACTIVE_PROMPT, _PROMPT_EVENT, _USER_DECISION
    _ACTIVE_PROMPT = None
    _PROMPT_EVENT = None
    _USER_DECISION = None


async def wait_for_user_decision(
    prompt_id: str,
    prompt_type: str,
    title: str,
    message: str,
    options: List[Dict[str, Any]],
    extra_context: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = 90.0,
) -> Optional[Dict[str, Any]]:
    """
    Призупиняє виконання циклу експерименту та очікує на вибір користувача у спливаючому модальному вікні.
    Якщо користувач не відповів протягом timeout_seconds, повертає None.
    """
    global _ACTIVE_PROMPT, _PROMPT_EVENT, _USER_DECISION
    _PROMPT_EVENT = asyncio.Event()
    _USER_DECISION = None
    _ACTIVE_PROMPT = {
        "prompt_id": prompt_id,
        "type": prompt_type,
        "title": title,
        "message": message,
        "options": options,
        "context": extra_context or {},
        "created_at": time.time(),
        "timeout_seconds": timeout_seconds,
    }

    try:
        await asyncio.wait_for(_PROMPT_EVENT.wait(), timeout=timeout_seconds)
        return _USER_DECISION
    except asyncio.TimeoutError:
        return None
    finally:
        _ACTIVE_PROMPT = None
        _PROMPT_EVENT = None


def resolve_prompt(prompt_id: str, action: str, data: Optional[Dict[str, Any]] = None) -> bool:
    """
    Фіксує рішення користувача та розблоковує призупинений експеримент.
    """
    global _ACTIVE_PROMPT, _PROMPT_EVENT, _USER_DECISION
    if not _ACTIVE_PROMPT or _ACTIVE_PROMPT.get("prompt_id") != prompt_id:
        return False
    _USER_DECISION = {
        "prompt_id": prompt_id,
        "action": action,
        "data": data or {},
    }
    if _PROMPT_EVENT:
        _PROMPT_EVENT.set()
    return True
