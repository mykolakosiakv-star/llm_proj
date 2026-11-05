"""
Модуль вхідного аналізу, нормалізації тексту/чисел, класифікації користувацьких запитів та контекстуалізації.
Реалізує:
1. Канонічну нормалізацію чисел, сум, відсотків та діапазонів (normalize_numbers);
2. Українську токенізацію зі збереженням числових токенів і знака % (tokenize_uk);
3. Справжню міру Жаккара (Jaccard similarity) у діапазоні [0, 1] без подвійного рахування токенів;
4. Визначення категорії запиту (informational, navigational, procedural, troubleshooting, clarification або unknown).
"""

import re
from typing import Dict, Any, List, Optional, Set, Union
from app.config import QUERY_CATEGORIES
from app.database import fetch_all_queries, fetch_query_by_id

# Словник характерних маркерів для автоматичної класифікації вільних запитів користувача
CATEGORY_PATTERNS: Dict[str, List[str]] = {
    "troubleshooting": [
        "не можу", "помилка", "заблоковано", "не приходить", "списалися", "відхилено",
        "збій", "не працює", "зависло", "шахра", "вкрали", "несанкціонован", "двічі",
        "код 4", "код 5", "не вдається", "проблема", "втратив доступ", "арешт", "не видав",
    ],
    "procedural": [
        "як змінити", "як підключити", "як відкрити", "як закрити", "як оформити",
        "як перевипустити", "як активувати", "як налаштувати", "як скасувати",
        "покроково", "інструкція", "яким чином", "як подати", "як відновити", "як додати",
        "як перевести", "як згенерувати", "як вимкнути", "як замовити", "як оновити",
    ],
    "navigational": [
        "де знайти", "у якому розділі", "де подивитися", "де розташован", "як завантажити виписку",
        "де в меню", "де зберігається", "куди натиснути", "де квитанція", "де архів",
        "де перевірити", "де в інтерфейсі", "у якому меню", "де в особистому кабінеті", "де можна згенерувати",
    ],
    "clarification": [
        "скільки часу", "коли надійдуть", "чому змінився", "який статус", "чи враховується",
        "у який термін", "до котрої години", "за яким курсом", "чи можна прискорити",
        "скільки триває", "коли зарахується", "що означає статус", "чому сума", "чи потрібно повідомляти",
        "чи нараховується", "чи зараховується",
    ],
    "informational": [
        "які умови", "яка комісія", "який ліміт", "які тарифи", "що таке", "які переваги",
        "чи є кешбек", "яка відсоткова", "які документи потрібні", "скільки коштує",
        "чи гарантуються", "скільки віртуальних карток",
    ],
}

# Маркери критичних ситуацій, які потребують обов'язкової ескалації на оператора безпеки/підтримки
CRITICAL_ESCALATION_MARKERS: List[str] = [
    "шахра", "вкрали", "несанкціонован", "списалися двічі", "двічі списалася", "арешт", "виконавч",
    "фінмоніторинг", "заблоковано рахунок", "рахунок заблоковано", "судов", "помер", "спадщин",
    "втратив телефон", "готівку не видав", "неправильним номером",
]


def normalize_numbers(text: str) -> str:
    """
    Канонічно нормалізує числові вирази, грошові суми, відсотки та діапазони в українському тексті:
    - '100 000' -> '100000', '12 500' -> '12500', '1 000' -> '1000';
    - '150 тис.' / '150 тисяч' -> '150000';
    - десяткова кома у числах '14,5%' -> '14.5%', '0,5%' -> '0.5%';
    - '0,00' / '0.00' -> '0';
    - 'нуль відсотків' -> '0%', 'нуль гривень' -> '0 грн';
    - тире в числових діапазонах '1–3' / '1—3' -> '1-3'.
    """
    if not text:
        return ""
    s = text

    # Нормалізація словесних нулів
    s = re.sub(r"\bнуль\s+відсотк[а-яіїєґ]*\b", "0%", s, flags=re.IGNORECASE)
    s = re.sub(r"\bнульов[а-яіїєґ]+\s+комісі[а-яіїєґ]*\b", "0%", s, flags=re.IGNORECASE)
    s = re.sub(r"\bнуль\s+(?:грн|грив[а-яіїєґ]*)\b", "0 грн", s, flags=re.IGNORECASE)

    # Прибираємо пробіли/нерозривні пробіли всередині багатозначних чисел (напр. 100 000 -> 100000)
    # Виконуємо двічі для чисел типу 1 000 000
    for _ in range(2):
        s = re.sub(r"(?<=\d)[\s\u00a0\u202f]+(?=\d{3}\b)", "", s)

    # Конвертація конструкцій "150 тис." / "150 тисяч" -> "150000"
    def _replace_thousands(match: re.Match) -> str:
        raw_num = match.group(1).replace(",", ".")
        try:
            val = float(raw_num) * 1000.0
            if val.is_integer():
                return str(int(val))
            return str(val)
        except ValueError:
            return match.group(0)

    s = re.sub(
        r"\b(\d+(?:[.,]\d+)?)\s*(?:тис\.?|тисяч[а-яіїєґ]*)(?=\s|$|[.,;:!?%)])",
        _replace_thousands,
        s,
        flags=re.IGNORECASE,
    )

    # Уніфікація десяткової коми всередині чисел: 14,5 -> 14.5, 11,165% -> 11.165%
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)

    # Спрощення нульових копійок: 0.00 -> 0, 100.00 -> 100
    s = re.sub(r"\b(\d+)\.00\b", r"\1", s)

    # Уніфікація тире між числами: 1–3 -> 1-3
    s = re.sub(r"(?<=\d)\s*[–—−-]\s*(?=\d)", "-", s)

    # Прибираємо пробіл перед знаком %: "0 %" -> "0%"
    s = re.sub(r"(?<=\d)\s+%", "%", s)

    return s


def stem_uk_token(tok: str) -> str:
    """Повертає морфологічну основу українського токена (числові токени залишає без обрізання)."""
    if not tok:
        return ""
    if tok[0].isdigit():
        return tok
    if len(tok) >= 6:
        return tok[: max(4, len(tok) - 2)]
    if len(tok) >= 5:
        return tok[:4]
    return tok


def tokenize_uk(text: str) -> List[str]:
    """
    Нормалізує числа та розбиває український текст на токени.
    Зберігає числові значення (включно з одноцифровими '0', '3' та відсотками '0%', '14.5%')
    і слова довжиною >= 2 символи.
    """
    if not text:
        return []
    norm = normalize_numbers(text.lower())
    # Виділяємо числа з відсотками/діапазонами/дрібними частинами або буквені слова
    raw_tokens = re.findall(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?%?|[а-яіїєґa-z0-9]+%?", norm)
    tokens: List[str] = []
    for t in raw_tokens:
        t_clean = t.strip()
        if not t_clean:
            continue
        if t_clean[0].isdigit() or len(t_clean) >= 2:
            tokens.append(t_clean)
    return tokens


def flatten_keywords_to_strings(required_keywords: List[Union[str, Dict[str, Any]]]) -> List[str]:
    """Перетворює список ключових фактів (рядків або груп синонімів) у плоский список рядків."""
    flat: List[str] = []
    for item in required_keywords or []:
        if isinstance(item, str):
            flat.append(item)
        elif isinstance(item, dict):
            any_of = item.get("any_of") or []
            if any_of:
                flat.extend(str(x) for x in any_of)
            elif item.get("id"):
                flat.append(str(item["id"]))
    return flat


def jaccard_stem_similarity(tokens_a: List[str], tokens_b: List[str]) -> float:
    """
    Обчислює справжній коефіцієнт Жаккара на множинах морфологічних основ:
    J(A, B) = |A ∩ B| / |A ∪ B| у строгому діапазоні [0.0, 1.0] без подвійного врахування токенів.
    """
    if not tokens_a or not tokens_b:
        return 0.0
    set_a: Set[str] = {stem_uk_token(t) for t in tokens_a if t}
    set_b: Set[str] = {stem_uk_token(t) for t in tokens_b if t}
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def classify_and_ground_query(
    user_query: str,
    query_id: Optional[int] = None,
    custom_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Виконує автоматичну класифікацію запиту користувача та знаходить релевантний
    контекст у базі знань сервісу.
    Якщо запит не відповідає жодній відомій категорії і не має лексичного перетину з базою,
    повертає detected_category='unknown'.
    """
    # 1. Якщо передано точний ID еталонного запиту з датасету
    if query_id is not None:
        exact_item = fetch_query_by_id(query_id)
        if exact_item:
            cat_code = exact_item["category"]
            cat_info = QUERY_CATEGORIES.get(cat_code, QUERY_CATEGORIES["informational"])
            return {
                "matched_query": exact_item,
                "detected_category": cat_code,
                "category_name_uk": cat_info["name_uk"],
                "category_confidence": 1.0,
                "context_fact": custom_context or exact_item["context_fact"],
                "reference_answer": exact_item["reference_answer"],
                "required_keywords": exact_item["required_keywords"],
                "expected_escalation": exact_item["expected_escalation"],
                "complexity": exact_item["complexity"],
                "matching_method": "exact_dataset_id",
            }

    # 2. Пошук найближчого запиту в еталонній базі за коефіцієнтом Жаккара у [0, 1]
    all_queries = fetch_all_queries()
    q_tokens = tokenize_uk(user_query)
    best_match: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for item in all_queries:
        if item["user_query"].strip().lower() == user_query.strip().lower():
            best_match = item
            best_score = 1.0
            break
        kw_strings = flatten_keywords_to_strings(item["required_keywords"])
        cand_tokens = tokenize_uk(item["user_query"] + " " + " ".join(kw_strings))
        if not q_tokens or not cand_tokens:
            continue
        score = jaccard_stem_similarity(q_tokens, cand_tokens)
        if score > best_score:
            best_score = score
            best_match = item

    # 3. Визначення категорії за правилами маркерів
    q_lower = normalize_numbers(user_query.lower())
    cat_hits: Dict[str, int] = {k: 0 for k in QUERY_CATEGORIES.keys()}
    for cat_code, markers in CATEGORY_PATTERNS.items():
        for m in markers:
            if m in q_lower:
                cat_hits[cat_code] += 1

    max_hits = max(cat_hits.values()) if cat_hits else 0
    rule_escalation = any(m in q_lower for m in CRITICAL_ESCALATION_MARKERS)

    # Якщо знайдено достатньо близький запит у датасеті (Jaccard >= 0.25)
    if best_match and best_score >= 0.25:
        detected_cat = best_match["category"]
        cat_info = QUERY_CATEGORIES.get(detected_cat, QUERY_CATEGORIES["informational"])
        cat_confidence = round(min(1.0, max(0.5, best_score)), 2)
        return {
            "matched_query": best_match,
            "detected_category": detected_cat,
            "category_name_uk": cat_info["name_uk"],
            "category_confidence": cat_confidence,
            "context_fact": custom_context or best_match["context_fact"],
            "reference_answer": best_match["reference_answer"],
            "required_keywords": best_match["required_keywords"],
            "expected_escalation": bool(best_match["expected_escalation"] or rule_escalation),
            "complexity": best_match["complexity"],
            "matching_method": f"jaccard_lookup (sim={round(best_score, 3)})",
        }

    # Якщо спрацювали явні маркери категорії
    if max_hits > 0:
        detected_cat = max(cat_hits.items(), key=lambda x: x[1])[0]
        cat_info = QUERY_CATEGORIES[detected_cat]
        cat_confidence = round(min(0.85, 0.45 + 0.15 * max_hits), 2)
    else:
        # Низька впевненість — чесно повертаємо unknown замість фіктивного informational
        detected_cat = "unknown"
        cat_info = {"name_uk": "Невизначена категорія (поза базою)"}
        cat_confidence = round(best_score, 2)

    fallback_context = (
        custom_context
        or "Служба підтримки користувачів забезпечує цілодобове обслуговування, консультації за чинними тарифами та регламентами, керування параметрами безпеки облікового запису та автоматичну перевірку статусу операцій. У разі підозрілих операцій, блокування доступу або інцидентів безпеки звернення передається профільному спеціалісту."
    )
    return {
        "matched_query": None,
        "detected_category": detected_cat,
        "category_name_uk": cat_info["name_uk"],
        "category_confidence": cat_confidence,
        "context_fact": fallback_context,
        "reference_answer": fallback_context,
        "required_keywords": tokenize_uk(user_query)[:4],
        "expected_escalation": rule_escalation,
        "complexity": 2,
        "matching_method": "rule_intent_classifier" if max_hits > 0 else "unknown_fallback",
    }
