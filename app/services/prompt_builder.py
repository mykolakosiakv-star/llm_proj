"""
Модуль конструювання промптів (Prompt Engineering Engine).
Формує промпти для стратегій P1..P5 з підстановкою параметрів предметної сфери
({company}, {domain_name}, {escalation_rules}) та відбором few-shot прикладів без витоку еталону.
"""

import hashlib
import json
from typing import Dict, Any, List, Optional, Tuple
from app.database import (
    fetch_prompt_by_code,
    fetch_all_queries,
    compute_template_hash,
    fetch_domain_by_code,
)
from app.domain_datasets import DOMAIN_PROFILES
from app.services.classifier import tokenize_uk, jaccard_stem_similarity


def resolve_domain_template_vars(domain: Optional[str] = "fintech") -> Dict[str, str]:
    dom_code = str(domain or "fintech").strip()
    db_dom = fetch_domain_by_code(dom_code)
    if db_dom:
        return {
            "domain": dom_code,
            "company": str(db_dom.get("company") or dom_code),
            "domain_name": str(db_dom.get("domain_name") or dom_code),
            "escalation_rules": str(
                db_dom.get("escalation_rules")
                or "підозра на шахрайство, компрометація облікового запису, несанкціоноване списання коштів або відсутність потрібних даних у контексті"
            ),
        }

    prof = DOMAIN_PROFILES.get(dom_code) or DOMAIN_PROFILES.get("fintech") or {}
    return {
        "domain": dom_code,
        "company": str(prof.get("company") or "FinCloud"),
        "domain_name": str(prof.get("domain_name") or prof.get("name_uk") or "Фінтех та Інтернет-банкінг"),
        "escalation_rules": str(
            prof.get("escalation_rules")
            or "підозра на шахрайство, компрометація картки або рахунку, арешт, блокування безпеки, подвійне списання коштів або відсутність потрібних даних у контексті"
        ),
    }


def select_few_shot_examples(
    category: str,
    exclude_query_code: Optional[str] = None,
    current_user_query: Optional[str] = None,
    limit: int = 2,
    domain: Optional[str] = "fintech",
) -> Tuple[str, List[str]]:
    """
    Детерміновано підбирає few-shot приклади для стратегії P4 у межах предметної сфери:
    виключає поточний запит за кодом та за текстом і мінімізує лексичний перетин із поточним запитом.
    """
    candidates = fetch_all_queries(category=category, domain=domain)
    if not candidates:
        candidates = fetch_all_queries(domain=domain)
    if not candidates:
        candidates = fetch_all_queries(category=category)

    cur_tokens = tokenize_uk(current_user_query or "")
    cur_norm = (current_user_query or "").strip().lower()

    filtered: List[Tuple[float, str, Dict[str, Any]]] = []
    seed_key = exclude_query_code or cur_norm or category

    for item in candidates:
        if exclude_query_code and item["code"] == exclude_query_code:
            continue
        if cur_norm and item["user_query"].strip().lower() == cur_norm:
            continue

        cand_tokens = tokenize_uk(item["user_query"] + " " + item["context_fact"])
        sim = jaccard_stem_similarity(cur_tokens, cand_tokens) if cur_tokens else 0.0

        h = hashlib.sha256(f"{seed_key}:{item['code']}".encode("utf-8")).hexdigest()[:8]
        overlap_bucket = 0 if sim < 0.30 else 1
        filtered.append((overlap_bucket + round(sim, 2), h, item))

    filtered.sort(key=lambda x: (x[0], x[1]))
    selected = [x[2] for x in filtered[:limit]]

    if not selected:
        return "", []

    blocks: List[str] = []
    used_codes: List[str] = []
    for idx, ex in enumerate(selected, start=1):
        used_codes.append(ex["code"])
        esc_bool = bool(ex.get("expected_escalation", False))
        reason_str = (
            "Запит стосується критичної ситуації безпеки або фінансового спору і потребує перевірки оператором."
            if esc_bool
            else "Стандартний запит, усі необхідні факти наявні в офіційному контексті."
        )
        json_example = json.dumps(
            {
                "answer": ex["reference_answer"],
                "escalate": esc_bool,
                "reason": reason_str,
            },
            ensure_ascii=False,
        )
        blocks.append(
            f"Приклад {idx} ({ex['code']}):\n"
            f"- Запит клієнта: «{ex['user_query']}»\n"
            f"- Факт із бази знань: {ex['context_fact']}\n"
            f"- Еталонний JSON: {json_example}"
        )
    return "\n\n".join(blocks), used_codes


def build_prompt_messages(
    prompt_code: str,
    user_query: str,
    context_fact: str,
    category_code: str,
    category_name_uk: str,
    exclude_query_code: Optional[str] = None,
    domain: Optional[str] = "fintech",
) -> Dict[str, Any]:
    dom_vars = resolve_domain_template_vars(domain)
    template = fetch_prompt_by_code(prompt_code)
    if not template:
        system_text = (
            f"Ти автоматизований асистент підтримки користувачів сервісу {dom_vars['company']} ({dom_vars['domain_name']}). "
            f"Відповідай українською мовою точно за наданим контекстом. Правила ескалації: {dom_vars['escalation_rules']}.\n\n"
            'Поверни відповідь ВИКЛЮЧНО у форматі JSON: {"answer": "...", "escalate": true/false, "reason": "..."}'
        )
        user_text = f"Контекст: {context_fact}\nЗапит користувача: {user_query}"
        t_hash = compute_template_hash(system_text, user_text)
        return {
            "prompt_code": prompt_code,
            "prompt_name_uk": "Базовий промпт",
            "strategy_type": "Zero-Shot",
            "prompt_version_id": None,
            "prompt_version": 1,
            "template_hash": t_hash,
            "few_shot_used": [],
            "system_prompt": system_text,
            "user_prompt": user_text,
            "full_rendered_prompt": f"[SYSTEM]\n{system_text}\n\n[USER]\n{user_text}",
        }

    few_shot_block = ""
    few_shot_codes: List[str] = []
    needs_few_shot = (
        prompt_code == "P4"
        or "{few_shot_examples}" in template["user_template"]
        or "{few_shot_examples}" in template["system_template"]
        or "{few_shot}" in template["user_template"]
        or "{few_shot}" in template["system_template"]
    )
    if needs_few_shot:
        few_shot_block, few_shot_codes = select_few_shot_examples(
            category=category_code,
            exclude_query_code=exclude_query_code,
            current_user_query=user_query,
            limit=2,
            domain=dom_vars["domain"],
        )

    format_vars = {
        "user_query": user_query,
        "context_fact": context_fact,
        "category_code": category_code,
        "category_name_uk": category_name_uk,
        "few_shot_examples": few_shot_block,
        "few_shot": few_shot_block,
        "company": dom_vars["company"],
        "domain_name": dom_vars["domain_name"],
        "escalation_rules": dom_vars["escalation_rules"],
    }

    system_rendered = template["system_template"]
    user_rendered = template["user_template"]
    for k, v in format_vars.items():
        system_rendered = system_rendered.replace("{" + k + "}", str(v))
        user_rendered = user_rendered.replace("{" + k + "}", str(v))

    t_hash = template.get("template_hash") or compute_template_hash(
        template["system_template"], template["user_template"]
    )

    return {
        "prompt_code": template["code"],
        "prompt_name_uk": template["name_uk"],
        "strategy_type": template["strategy_type"],
        "prompt_version_id": template.get("prompt_version_id"),
        "prompt_version": int(template.get("current_version") or 1),
        "template_hash": t_hash,
        "few_shot_used": few_shot_codes,
        "system_prompt": system_rendered.strip(),
        "user_prompt": user_rendered.strip(),
        "full_rendered_prompt": (
            f"[SYSTEM PROMPT ({template['code']} v{template.get('current_version', 1)} - {template['strategy_type']})]\n"
            f"{system_rendered.strip()}\n\n"
            f"[USER PROMPT]\n{user_rendered.strip()}"
        ),
    }
