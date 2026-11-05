import csv
import io
import json
import re
from typing import Any, Dict, List

from app.database import (
    add_kb_document,
    fetch_all_domains,
    fetch_all_queries,
    get_connection,
    upsert_domain,
    upsert_query_item,
)
from app.services.llm_runtime import execute_llm_request

FAQ_MAX_CHARS = 200_000
FAQ_MAX_ITEMS = 200
DOMAIN_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
NEUTRAL_ESCALATION_RULES = (
    "запит стосується безпеки облікового запису або конфіденційних даних, містить юридичну претензію, "
    "вимагає дій, яких немає в базі знань, або потрібної відповіді в базі знань немає"
)
_STOP_WORDS = frozenset("який яка яке які можна потрібно також тому якщо після перед через щоб коли тільки лише більше менше".split())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_faq(content: str, fmt: str) -> List[Dict[str, str]]:
    """Розбирає завантажений FAQ у пари питання-відповідь. Формат: json, csv або text."""
    if len(content) > FAQ_MAX_CHARS:
        raise ValueError(f"Файл завеликий: допустимо до {FAQ_MAX_CHARS} символів.")
    if fmt == "json":
        data = json.loads(content)
        raw = data.get("items", []) if isinstance(data, dict) else data
        pairs = [(i.get("question"), i.get("answer")) for i in raw if isinstance(i, dict)]
    elif fmt == "csv":
        rows = list(csv.reader(io.StringIO(content)))
        if rows and rows[0] and rows[0][0].strip().lower() in ("question", "питання"):
            rows = rows[1:]
        pairs = [(r[0], r[1]) for r in rows if len(r) >= 2]
    elif fmt == "text":
        pairs = []
        for block in re.split(r"\n\s*\n", content.strip()):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            if len(lines) >= 2:
                question = re.sub(r"^(Q|П|Питання)\s*[:.]\s*", "", lines[0], flags=re.I)
                answer = re.sub(r"^(A|В|Відповідь)\s*[:.]\s*", "", " ".join(lines[1:]), flags=re.I)
                pairs.append((question, answer))
    else:
        raise ValueError("Невідомий формат файлу: очікується json, csv або text.")

    items = [{"question": _clean(q), "answer": _clean(a)} for q, a in pairs]
    items = [i for i in items if len(i["question"]) >= 5 and len(i["answer"]) >= 10]
    if not items:
        raise ValueError("У файлі не знайдено пар «питання, відповідь».")
    if len(items) > FAQ_MAX_ITEMS:
        raise ValueError(f"Забагато записів: допустимо до {FAQ_MAX_ITEMS}.")
    return items


def derive_keywords(answer: str, limit: int = 4) -> List[str]:
    """Чернетка ключових фактів: числа та довгі слова з відповіді. Людина перевіряє їх на етапі затвердження."""
    numbers = re.findall(r"\d+(?:[.,]\d+)?%?", answer)
    words = [w for w in re.findall(r"[А-Яа-яІіЇїЄєҐґA-Za-z'-]{6,}", answer) if w.lower() not in _STOP_WORDS]
    seen: List[str] = []
    for token in numbers + sorted(words, key=len, reverse=True):
        if token.lower() not in (s.lower() for s in seen):
            seen.append(token)
    return seen[:limit]


def import_faq(domain_code: str, domain_name: str, company: str, items: List[Dict[str, str]]) -> Dict[str, Any]:
    """Створює сферу (якщо її ще немає) і чернетки запитів із FAQ. Вони не потрапляють у серії до перевірки людиною."""
    if not DOMAIN_CODE_RE.match(domain_code):
        raise ValueError("Код сфери: латинські літери, цифри й підкреслення, від 3 до 31 символа, починається з літери.")
    if domain_code not in {d["code"] for d in fetch_all_domains()}:
        upsert_domain({
            "code": domain_code, "domain_name": domain_name, "company": company or domain_name,
            "escalation_rules": NEUTRAL_ESCALATION_RULES,
        })
    start = len([q for q in fetch_all_queries(domain=domain_code) if q["source"] == "faq_upload"])
    for n, item in enumerate(items, start=start + 1):
        upsert_query_item({
            "code": f"{domain_code[:3].upper()}-FAQ-{n:03d}",
            "domain": domain_code,
            "category": "informational",
            "user_query": item["question"],
            "context_fact": item["answer"],
            "reference_answer": item["answer"],
            "required_keywords": derive_keywords(item["answer"]),
            "expected_escalation": False,
            "complexity": 2,
            "source": "faq_upload",
            "review_status": "draft",
        })
    return {"domain": domain_code, "imported": len(items)}


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Модель не повернула JSON.")
    return json.loads(cleaned[start:end + 1])


async def _ask_model(model_id: str, system: str, user: str) -> Dict[str, Any]:
    out = await execute_llm_request(
        model_id=model_id, prompt_code="KB", system_prompt=system, user_prompt=user,
        temperature=0.7, max_output_tokens=4000, json_mode=True,
    )
    return _extract_json(str(out.get("generated_answer") or out.get("response_text") or ""))


async def generate_distractors(domain: str, count: int, model_id: str) -> int:
    """Просить модель написати правдоподібні фрагменти бази знань, які не відповідають жодному з тестових запитів."""
    facts = [q["context_fact"] for q in fetch_all_queries(domain=domain, reviewed_only=True)][:15]
    system = "Ти допомагаєш наповнювати базу знань служби підтримки. Відповідай лише JSON."
    user = (
        f"Ось приклади фрагментів бази знань сфери «{domain}»:\n- " + "\n- ".join(facts)
        + f"\n\nНапиши {count} нових фрагментів на суміжні теми того самого сервісу (політики, умови, винятки, порядок дій), "
        "кожен 1-3 речення, з конкретними деталями. Вони не повинні повторювати наведені фрагменти і не повинні "
        'бути їх перефразом. Формат: {"items": [{"title": "...", "text": "..."}]}'
    )
    data = await _ask_model(model_id, system, user)
    created = 0
    for item in data.get("items", [])[:count]:
        text = _clean(item.get("text"))
        if len(text) >= 20:
            add_kb_document(domain, text, title=_clean(item.get("title")), source="llm_draft", is_distractor=True, review_status="draft")
            created += 1
    return created


async def generate_paraphrases(domain: str, limit: int, model_id: str) -> int:
    """Просить модель переформулювати запити розмовною мовою. Фрагмент-відповідь лишається спільним з оригіналом."""
    existing = {q["variant_of"] for q in fetch_all_queries(domain=domain) if q.get("variant_of")}
    originals = [q for q in fetch_all_queries(domain=domain, reviewed_only=True) if not q.get("variant_of") and q["code"] not in existing][:limit]
    if not originals:
        return 0
    system = "Ти допомагаєш створювати тестові запити для служби підтримки. Відповідай лише JSON."
    listing = "\n".join(f'{q["code"]}: {q["user_query"]}' for q in originals)
    user = (
        "Переформулюй кожен запит так, як його написав би клієнт у чаті: розмовно, можливо з неточностями, але з тим самим змістом "
        f'і без нових фактів.\n{listing}\n\nФормат: {{"items": [{{"code": "...", "paraphrase": "..."}}]}}'
    )
    data = await _ask_model(model_id, system, user)
    by_code = {q["code"]: q for q in originals}
    created = 0
    for item in data.get("items", []):
        base = by_code.get(item.get("code"))
        text = _clean(item.get("paraphrase"))
        if base and len(text) >= 5 and text.lower() != base["user_query"].lower():
            upsert_query_item({
                **base, "code": f'{base["code"]}-V1', "user_query": text, "variant_of": base["code"],
                "variant_type": "paraphrase", "source": "llm_draft", "review_status": "draft",
            })
            created += 1
    return created


def list_drafts(domain: str = "all") -> Dict[str, List[Dict[str, Any]]]:
    queries = [q for q in fetch_all_queries(domain=domain) if q["review_status"] == "draft"]
    conn = get_connection()
    try:
        sql = ("SELECT d.doc_code, d.domain, d.title, d.source, c.text FROM kb_documents d JOIN kb_chunks c ON c.document_id = d.id "
               "WHERE d.review_status = 'draft' AND d.is_distractor = 1")
        params: List[Any] = []
        if domain != "all":
            sql += " AND d.domain = ?"
            params.append(domain)
        documents = [dict(r) for r in conn.execute(sql + " ORDER BY d.id", params)]
    finally:
        conn.close()
    return {"queries": queries, "documents": documents}


def review_draft(kind: str, key: str, action: str, text: str = "") -> Dict[str, Any]:
    """Затверджує, виправляє або відхиляє чернетку. Відхилена чернетка видаляється разом із пов'язаним фрагментом."""
    if action not in ("approve", "reject"):
        raise ValueError("Дія має бути approve або reject.")
    conn = get_connection()
    try:
        if kind == "document":
            if action == "reject":
                conn.execute("DELETE FROM kb_documents WHERE doc_code = ? AND review_status = 'draft'", (key,))
            else:
                doc = conn.execute("SELECT id FROM kb_documents WHERE doc_code = ?", (key,)).fetchone()
                if doc and text:
                    conn.execute("UPDATE kb_chunks SET text = ? WHERE document_id = ?", (_clean(text), doc["id"]))
                conn.execute("UPDATE kb_documents SET review_status = 'reviewed' WHERE doc_code = ?", (key,))
            conn.commit()
            return {"status": action}
        if kind != "query":
            raise ValueError("Тип має бути query або document.")
        if action == "reject":
            conn.execute("DELETE FROM kb_documents WHERE doc_code = ? AND review_status = 'draft'", (key,))
            conn.execute("DELETE FROM queries WHERE code = ? AND review_status = 'draft'", (key,))
            conn.commit()
            return {"status": "reject"}
    finally:
        conn.close()
    item = next((q for q in fetch_all_queries() if q["code"] == key), None)
    if item is None:
        raise ValueError("Запит не знайдено.")
    updated = {**item, "review_status": "reviewed"}
    if text:
        updated["user_query"] = _clean(text)
    upsert_query_item(updated)
    return {"status": "approve"}


def kb_summary() -> List[Dict[str, Any]]:
    """Скільки запитів і фрагментів у кожній сфері, і скільки з них чекає на перевірку."""
    conn = get_connection()
    try:
        out = []
        for d in conn.execute("SELECT code, domain_name FROM domains ORDER BY code").fetchall():
            q = conn.execute(
                "SELECT SUM(review_status = 'reviewed') AS ok, SUM(review_status = 'draft') AS draft, "
                "SUM(variant_of IS NOT NULL AND review_status = 'reviewed') AS variants FROM queries WHERE domain = ?",
                (d["code"],),
            ).fetchone()
            k = conn.execute(
                "SELECT SUM(review_status = 'reviewed') AS ok, SUM(review_status = 'draft') AS draft, "
                "SUM(is_distractor = 1 AND review_status = 'reviewed') AS distractors FROM kb_documents WHERE domain = ?",
                (d["code"],),
            ).fetchone()
            out.append({
                "domain": d["code"], "name_uk": d["domain_name"],
                "queries": int(q["ok"] or 0), "queries_draft": int(q["draft"] or 0), "paraphrases": int(q["variants"] or 0),
                "documents": int(k["ok"] or 0), "documents_draft": int(k["draft"] or 0), "distractors": int(k["distractors"] or 0),
            })
        return out
    finally:
        conn.close()
