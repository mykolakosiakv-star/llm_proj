from typing import Any, Dict

from app.config import get_model_config
from app.services.classifier import classify_and_ground_query
from app.services.experiment_runner import _generate_with_contract_retry
from app.services.logger import log_event
from app.services.prompt_builder import build_prompt_messages
from app.services.retrieval import retrieve_knowledge_chunks

ASSISTANT_PROMPT_CODE = "P3"
ASSISTANT_TOP_K = 3


async def answer_customer_query(user_query: str, model_id: str, domain: str) -> Dict[str, Any]:
    """Робочий конвеєр без оцінювання: пошук знань за текстом користувача, відповідь моделі, рішення про оператора.

    Еталонні відповіді датасету сюди не потрапляють: категорія береться лише для підказки промпту,
    а контекст завжди знаходить BM25, як у реальному впровадженні.
    """
    model_info = get_model_config(model_id)
    category = classify_and_ground_query(user_query=user_query)
    retrieval = retrieve_knowledge_chunks(user_query=user_query, domain=domain, top_k=ASSISTANT_TOP_K)

    prompt_pack = build_prompt_messages(
        prompt_code=ASSISTANT_PROMPT_CODE,
        user_query=user_query,
        context_fact=retrieval["assembled_context"],
        category_code=category["detected_category"],
        category_name_uk=category["category_name_uk"],
        exclude_query_code=None,
        domain=domain,
    )
    llm_out, contract = await _generate_with_contract_retry(
        model_id=model_id,
        prompt_code=ASSISTANT_PROMPT_CODE,
        system_prompt=prompt_pack["system_prompt"],
        user_prompt=prompt_pack["user_prompt"],
        temperature=0.0,
    )

    # Відповідь без розбірного формату не можна вважати надійним рішенням, тому такий випадок іде оператору
    escalate = bool(contract["escalate"]) or not contract["contract_ok"]
    reason = contract["reason"] if contract["contract_ok"] else "Відповідь моделі не відповідає очікуваному формату, запит передано оператору."

    log_event("ПОМІЧНИК", f"{model_info.get('name', model_id)}: ескалація {'так' if escalate else 'ні'}, {llm_out['latency_ms']} мс", level="STEP")
    return {
        "answer": contract["answer"],
        "escalate": escalate,
        "reason": reason,
        "category_name_uk": category["category_name_uk"],
        "model_name": model_info.get("name", model_id),
        "latency_ms": llm_out["latency_ms"],
        "sources": [
            {"code": c["code"], "text": c["context_fact"], "score": c["score"]}
            for c in retrieval["retrieved_chunks"]
        ],
    }
