"""
Схеми даних та валідації запитів (Pydantic).
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class DomainCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, description="Унікальний код предметної сфери, напр. fintech")
    domain_name: str = Field(..., min_length=2, description="Назва предметної сфери українською")
    company: str = Field(..., min_length=2, description="Назва компанії або сервісу для підстановки у промпти")
    short_uk: Optional[str] = Field(default="")
    description_uk: Optional[str] = Field(default="")
    escalation_rules: Optional[str] = Field(
        default="підозра на шахрайство, компрометація облікового запису, несанкціоноване списання коштів або відсутність потрібних даних у контексті"
    )
    risk_profile_uk: Optional[str] = Field(default="")
    default_preset: Optional[str] = Field(default="balanced")


class QueryItemCreate(BaseModel):
    code: str = Field(..., description="Унікальний код запиту, напр. INF-01")
    domain: Optional[str] = Field(default="fintech", description="Предметна сфера запиту")
    category: str = Field(..., description="Категорія запиту")
    user_query: str = Field(..., description="Текст запиту користувача")
    context_fact: str = Field(..., description="Фрагмент бази знань")
    reference_answer: str = Field(..., description="Еталонна відповідь оператора")
    required_keywords: List[Union[str, Dict[str, Any]]] = Field(..., description="Ключові факти або синонімічні групи")
    expected_escalation: bool = Field(default=False, description="Чи вимагає запит передачі оператору")
    complexity: int = Field(default=2, ge=1, le=3)
    source: Optional[str] = Field(default="manual", description="Джерело запиту: manual або llm_draft")
    review_status: Optional[str] = Field(default="reviewed", description="Статус перевірки: draft або reviewed")


class PromptTemplateUpdate(BaseModel):
    code: str
    name_uk: str
    strategy_type: Optional[str] = "Custom"
    description_uk: str
    system_template: str
    user_template: str


class SingleAutomationRequest(BaseModel):
    user_query: str = Field(..., description="Вхідний запит користувача")
    model_id: str = Field(default="gemini-3.1-flash-lite", description="Ідентифікатор обраної моделі")
    prompt_code: str = Field(default="P3", description="Код стратегії промпту (P1..P5)")
    query_id: Optional[int] = Field(default=None, description="ID еталонного запиту")
    custom_context: Optional[str] = Field(default=None)
    context_mode: Optional[str] = Field(default="gold_context")
    top_k: int = Field(default=3, ge=1, le=10)
    domain: Optional[str] = Field(default=None)
    temperature: Optional[float] = Field(default=0.0, ge=0.0, le=1.5)
    save_to_db: bool = Field(default=True)
    judge_model_id: Optional[str] = Field(default=None)


class AssistantRequest(BaseModel):
    user_query: str = Field(..., min_length=3, description="Запит користувача")
    model_id: str
    domain: str = Field(default="fintech")


class CascadeRequest(BaseModel):
    run_ids: List[int] = Field(..., min_length=1)
    cheap_model_id: str
    strong_model_id: str
    strong_first_categories: List[str] = Field(default_factory=list)
    prompt_code: Optional[str] = None
    context_mode: Optional[str] = None


class BatchExperimentRequest(BaseModel):
    title: str = Field(default="Порівняльний тест моделей та промптів")
    description: str = Field(default="Оцінювання якості автоматизації за категоріями запитів")
    domain: Optional[str] = Field(default="fintech")
    domains: Optional[List[str]] = Field(default=None)
    equal_domain_sample: bool = Field(default=False)
    context_mode: Optional[str] = Field(default="gold_context")
    context_modes: Optional[List[str]] = Field(default=None)
    top_k: int = Field(default=3, ge=1, le=10)
    model_ids: List[str]
    prompt_codes: List[str]
    categories: List[str]
    max_queries_per_category: int = Field(default=12, ge=1, le=50)
    repeats: int = Field(default=1, ge=1, le=5)
    use_judge: bool = Field(default=False)
    judge_model_id: Optional[str] = Field(default=None)
    allow_self_judge: bool = Field(default=False)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: Optional[int] = Field(default=None, ge=100, le=8000)
    seed: Optional[int] = Field(default=None)
    wait_for_completion: bool = Field(default=False)


class ManualScoreOverride(BaseModel):
    relevance_score: float = Field(..., ge=1.0, le=5.0)
    factuality_score: float = Field(..., ge=1.0, le=5.0)
    completeness_score: float = Field(..., ge=1.0, le=5.0)
    clarity_score: float = Field(..., ge=1.0, le=5.0)
    format_score: float = Field(..., ge=1.0, le=5.0)
    policy_adherence_score: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    manual_annotator: Optional[str] = Field(default="expert_1")
    manual_notes: Optional[str] = Field(default="")


class SettingsUpdateRequest(BaseModel):
    groq_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    ollama_base_url: Optional[str] = None
    ollama_model_name: Optional[str] = None
    confidence_threshold: Optional[float] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    enabled_models: Optional[List[str]] = None
    judge_enabled: Optional[bool] = None
    judge_model: Optional[str] = None
    probe_on_startup: Optional[bool] = None
    model_routes: Optional[Dict[str, str]] = None
    weights: Optional[Dict[str, float]] = None


class CustomModelCreateRequest(BaseModel):
    provider: str = Field(default="custom", description="Провайдер або 'custom' для OpenAI-сумісного API")
    model_id: str = Field(..., min_length=1, description="Ідентифікатор моделі в API")
    display_name: Optional[str] = Field(default=None, description="Назва моделі для інтерфейсу")
    base_url: Optional[str] = Field(default="", description="Базовий URL сервера OpenAI-сумісного API")
    api_key_env: Optional[str] = Field(default="", description="Назва змінної в .env або сам ключ")
    api_key: Optional[str] = Field(default=None, description="Значення ключа API для збереження в .env")
    api_key_value: Optional[str] = Field(default=None, description="Аліас ключа API для збереження в .env")
    context_window: Optional[int] = Field(default=128000, ge=512)
    input_price_per_1m: Optional[float] = Field(default=None, ge=0.0)
    output_price_per_1m: Optional[float] = Field(default=None, ge=0.0)
    rpm: Optional[int] = Field(default=None, ge=1)
    rpd: Optional[int] = Field(default=None, ge=1)
    enabled: bool = Field(default=True)


class CustomModelTestRequest(BaseModel):
    provider: str = Field(default="custom")
    model_id: str = Field(..., min_length=1)
    base_url: Optional[str] = Field(default="")
    api_key_env: Optional[str] = Field(default="")
    api_key: Optional[str] = Field(default=None)
    api_key_value: Optional[str] = Field(default=None)


class ModelRegistryUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    enabled: Optional[bool] = None
    route: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    context_window: Optional[int] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    price_source: Optional[str] = None
    rpm: Optional[int] = None
    rpd: Optional[int] = None


class RefreshStatusRequest(BaseModel):
    model_key: Optional[str] = Field(default=None, description="Опціональний ID моделі для точкової перевірки")
    only_enabled: bool = Field(default=True)
    force: bool = Field(default=True)


class ResolvePromptRequest(BaseModel):
    prompt_id: str
    action: str
    data: Optional[Dict[str, Any]] = Field(default=None)


class FlexibleAnalyticsRequest(BaseModel):
    run_ids: List[int]
    model_ids: Optional[List[str]] = Field(default=None)
    prompt_codes: Optional[List[str]] = Field(default=None)
    categories: Optional[List[str]] = Field(default=None)
    balanced_only: bool = Field(default=True)
    include_self_judged: bool = Field(default=False)
