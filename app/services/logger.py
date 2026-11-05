"""
Модуль журналювання подій у реальному часі (Live Telemetry & Execution Logger).
Забезпечує прозоре відображення того, на якому саме етапі зараз працює програма
та велика мовна модель (LLM):
1. Веде потоковий журнал подій у пам'яті (для Web UI) та у файлі data/system.log;
2. Зберігає поточний стан виконання (активний етап, активна модель, прогрес експерименту);
3. Підтримує безпечну зупинку тривалого пакетного експерименту за запитом користувача.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
from app.config import DATA_DIR

LOG_FILE_PATH = DATA_DIR / "system.log"

# Кільцевий буфер останніх 500 записів журналу для миттєвої передачі в інтерфейс
_LOG_BUFFER: List[Dict[str, Any]] = []
_LOG_COUNTER: int = 0

# Поточний стан активного процесу та LLM
_RUNTIME_STATUS: Dict[str, Any] = {
    "is_busy": False,
    "operation_type": "idle",  # 'idle' | 'single_query' | 'arena' | 'batch_experiment'
    "current_stage": "Очікування команди користувача",
    "stage_number": 0,         # 0..5 (кроки конвеєра)
    "active_model": "-",
    "active_provider": "-",
    "active_prompt": "-",
    "active_query_code": "-",
    "active_query_text": "-",
    "progress_current": 0,
    "progress_total": 0,
    "progress_pct": 0.0,
    "cancel_requested": False,
    "updated_at": "",
}


def log_event(
    stage: str,
    message: str,
    level: str = "INFO",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Додає новий запис до журналу подій (у пам'ять, у файл data/system.log та у консоль).
    Рівні (level): INFO, STEP, LLM_REQ, LLM_RES, EVAL, WARN, ERROR.
    """
    global _LOG_COUNTER
    _LOG_COUNTER += 1
    now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    entry = {
        "id": _LOG_COUNTER,
        "timestamp": now_str,
        "level": level,
        "stage": stage,
        "message": message,
        "details": details or {},
    }
    _LOG_BUFFER.append(entry)
    if len(_LOG_BUFFER) > 500:
        del _LOG_BUFFER[:-500]

    # Запис у файл та вивід у термінал сервера
    line = f"[{now_str}] [{level}] [{stage}] {message}\n"
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

    try:
        print(line.strip(), flush=True)
    except Exception:
        pass

    return entry


def update_runtime_status(**kwargs: Any) -> Dict[str, Any]:
    """Оновлює поточний стан виконання програми та LLM для індикатора етапів в інтерфейсі."""
    _RUNTIME_STATUS.update(kwargs)
    _RUNTIME_STATUS["updated_at"] = datetime.now().strftime("%H:%M:%S")
    cur = _RUNTIME_STATUS.get("progress_current", 0)
    tot = _RUNTIME_STATUS.get("progress_total", 0)
    if tot > 0:
        _RUNTIME_STATUS["progress_pct"] = round((cur / tot) * 100.0, 1)
    else:
        _RUNTIME_STATUS["progress_pct"] = 0.0
    return _RUNTIME_STATUS.copy()


def reset_runtime_status(message: str = "Готово, очікування команд") -> None:
    """Скидає індикатор стану після завершення операції."""
    update_runtime_status(
        is_busy=False,
        operation_type="idle",
        current_stage=message,
        stage_number=0,
        active_model="-",
        active_provider="-",
        active_prompt="-",
        active_query_code="-",
        active_query_text="-",
        progress_current=0,
        progress_total=0,
        cancel_requested=False,
    )


def request_cancel_experiment() -> None:
    """Встановлює прапорець зупинки поточного пакетного експерименту."""
    _RUNTIME_STATUS["cancel_requested"] = True
    log_event("КОНТРОЛЬ", "Користувач ініціював зупинку поточного експерименту...", level="WARN")


def is_cancel_requested() -> bool:
    """Перевіряє, чи натиснув користувач кнопку зупинки."""
    return bool(_RUNTIME_STATUS.get("cancel_requested", False))


def get_logs_and_status(since_id: int = 0) -> Dict[str, Any]:
    """Повертає всі нові записи логів (після since_id) та поточний стан роботи програми і LLM."""
    new_logs = [item for item in _LOG_BUFFER if item["id"] > since_id]
    return {
        "status": _RUNTIME_STATUS.copy(),
        "logs": new_logs,
        "last_log_id": _LOG_COUNTER,
    }


def clear_logs() -> None:
    """Очищує буфер логів за запитом користувача."""
    _LOG_BUFFER.clear()
    log_event("СИСТЕМА", "Журнал подій очищено користувачем.", level="INFO")
