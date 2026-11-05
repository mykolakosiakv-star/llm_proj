"""
Модуль пошуку знань (Knowledge Retrieval Engine) на основі BM25 з українською морфологічною основою.
Індексує виключно фрагменти бази знань (context_fact) запитів предметної сфери,
без використання reference_answer чи user_query як тексту фрагмента знань.
"""

from collections import Counter
import math
from typing import Dict, Any, List, Optional
from app.config import DEFAULT_TOP_K
from app.database import fetch_all_queries
from app.services.classifier import tokenize_uk, stem_uk_token


STOP_WORDS = frozenset(
    "як що це чи на у в і й та а але або не ні до за при від по з із зі для про між через над під без як-небудь "
    "який яка яке які його її їх ми ви вони він вона воно мій ваш наш є був була було бути можна треба".split()
)


def _stem_tokens(text: str) -> List[str]:
    # Без цього службові слова на кшталт «як» мають високу вагу IDF і виводять нерелевантні фрагменти на перше місце
    return [stem_uk_token(t) for t in tokenize_uk(text) if t and t not in STOP_WORDS]


class BM25KnowledgeIndex:
    """
    Пошуковий індекс BM25 над фрагментами бази знань (context_fact) заданої предметної сфери.
    """

    def __init__(self, chunks: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: List[Dict[str, Any]] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_freqs: List[Counter] = []
        self.doc_lens: List[int] = []
        self.df: Counter = Counter()

        for item in chunks:
            code = str(item.get("code") or item.get("id") or "")
            context_fact = str(item.get("context_fact") or "").strip()
            if not context_fact:
                continue
            tokens = _stem_tokens(context_fact)
            tf = Counter(tokens)
            self.chunks.append(
                {
                    "code": code,
                    "category": str(item.get("category") or ""),
                    "domain": str(item.get("domain") or "fintech"),
                    "context_fact": context_fact,
                }
            )
            self.doc_tokens.append(tokens)
            self.doc_freqs.append(tf)
            self.doc_lens.append(len(tokens))
            for term in tf.keys():
                self.df[term] += 1

        self.num_docs = len(self.chunks)
        self.avg_dl = (sum(self.doc_lens) / self.num_docs) if self.num_docs > 0 else 1.0

    def search(self, query_text: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        if self.num_docs == 0:
            return []
        q_terms = _stem_tokens(query_text)
        if not q_terms:
            return [
                {**self.chunks[i], "score": 0.0, "rank": i + 1}
                for i in range(min(top_k, self.num_docs))
            ]

        scored: List[tuple] = []
        for idx in range(self.num_docs):
            tf_map = self.doc_freqs[idx]
            dl = self.doc_lens[idx] or 1
            score = 0.0
            for term in q_terms:
                f = tf_map.get(term, 0)
                if f == 0:
                    continue
                n_q = self.df.get(term, 0)
                idf = math.log(1.0 + (self.num_docs - n_q + 0.5) / (n_q + 0.5))
                denom = f + self.k1 * (1.0 - self.b + self.b * (dl / self.avg_dl))
                score += idf * ((f * (self.k1 + 1.0)) / denom)
            scored.append((score, self.chunks[idx]["code"], idx))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top_items: List[Dict[str, Any]] = []
        for rank_idx, (sc, _, doc_idx) in enumerate(scored[: max(1, top_k)], start=1):
            entry = dict(self.chunks[doc_idx])
            entry["score"] = round(float(sc), 4)
            entry["rank"] = rank_idx
            top_items.append(entry)
        return top_items


def retrieve_knowledge_chunks(
    user_query: str,
    domain: str = "fintech",
    top_k: int = DEFAULT_TOP_K,
    gold_query_code: Optional[str] = None,
    corpus: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Виконує BM25-пошук top-k фрагментів знань у межах предметної сфери та обчислює hit@1 і hit@3.
    """
    domain_items = corpus if corpus is not None else fetch_all_queries(domain=domain)
    if not domain_items and corpus is None:
        domain_items = fetch_all_queries()

    index = BM25KnowledgeIndex(domain_items)
    effective_k = max(1, int(top_k or DEFAULT_TOP_K))
    search_k = max(effective_k, 3)
    top_results = index.search(user_query, top_k=search_k)
    selected = top_results[:effective_k]

    retrieved_ids = [item["code"] for item in selected]
    top3_ids = [item["code"] for item in top_results[:3]]

    hit_at_1: Optional[int] = None
    hit_at_3: Optional[int] = None
    if gold_query_code:
        hit_at_1 = 1 if (top_results and top_results[0]["code"] == gold_query_code) else 0
        hit_at_3 = 1 if (gold_query_code in top3_ids) else 0

    assembled_lines = [
        f"[{idx}] ({item['code']}) {item['context_fact']}"
        for idx, item in enumerate(selected, start=1)
    ]
    assembled_context = "\n".join(assembled_lines)

    return {
        "retrieved_chunks": selected,
        "retrieved_ids": retrieved_ids,
        "assembled_context": assembled_context,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "top_k": effective_k,
    }


def resolve_experiment_context(
    query_item: Dict[str, Any],
    context_mode: str = "gold_context",
    domain: str = "fintech",
    top_k: int = DEFAULT_TOP_K,
    corpus: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Формує контекст, який подається у промпт моделі залежно від режиму context_mode:
    - no_context: модель отримує лише запит користувача без фрагментів бази знань;
    - gold_context: модель отримує еталонний context_fact поточного запиту;
    - retrieved_context: модель отримує top-k знайдених фрагментів із бази знань домену.
    Оцінювання якості (FC, HR, Judge) у всіх режимах виконується відносно еталонного gold_context_fact.
    """
    mode = str(context_mode or "gold_context").strip().lower()
    if mode not in ("no_context", "gold_context", "retrieved_context"):
        mode = "gold_context"

    gold_code = str(query_item.get("code") or "")
    gold_context_fact = str(query_item.get("context_fact") or "")
    effective_k = max(1, int(top_k or DEFAULT_TOP_K))

    if mode == "no_context":
        return {
            "context_mode": "no_context",
            "top_k": effective_k,
            "prompt_context": "Довідковий контекст із бази знань не надано. Якщо для точної відповіді потрібні специфічні тарифи або регламент, яких ти не знаєш напевно, ініціюй ескалацію на оператора.",
            "gold_context_fact": gold_context_fact,
            "retrieved_ids": [],
            "retrieval_hit_at_1": None,
            "retrieval_hit_at_3": None,
            "retrieved_chunks": [],
        }

    if mode == "retrieved_context":
        ret = retrieve_knowledge_chunks(
            user_query=str(query_item.get("user_query") or ""),
            domain=domain,
            top_k=effective_k,
            gold_query_code=gold_code or None,
            corpus=corpus,
        )
        return {
            "context_mode": "retrieved_context",
            "top_k": effective_k,
            "prompt_context": ret["assembled_context"] or gold_context_fact,
            "gold_context_fact": gold_context_fact,
            "retrieved_ids": ret["retrieved_ids"],
            "retrieval_hit_at_1": ret["hit_at_1"],
            "retrieval_hit_at_3": ret["hit_at_3"],
            "retrieved_chunks": ret["retrieved_chunks"],
        }

    return {
        "context_mode": "gold_context",
        "top_k": effective_k,
        "prompt_context": gold_context_fact,
        "gold_context_fact": gold_context_fact,
        "retrieved_ids": [gold_code] if gold_code else [],
        "retrieval_hit_at_1": 1 if gold_code else None,
        "retrieval_hit_at_3": 1 if gold_code else None,
        "retrieved_chunks": [],
    }
