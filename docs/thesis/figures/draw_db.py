"""Рисунок Е.1: схема бази даних у нотації «воронячої лапки» (IE). Запуск: py draw_db.py <каталог з matplotlib> <вихідний каталог>"""
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

out = sys.argv[2]
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
INK, GREY = "#000000", "#e6e6e6"
NL = chr(10)
HEAD, ROW = 3.8, 2.7

fig, ax = plt.subplots(figsize=(7.6, 7.1), dpi=200)
ax.set_xlim(0, 104)
ax.set_ylim(-2, 91)
ax.set_aspect("equal")
ax.axis("off")

boxes = {}


def table(name, x, y, w, fields):
    """Поле задається парою (мітка, ім'я); мітка PK або FK виводиться в окремій колонці."""
    h = HEAD + ROW * len(fields) + 0.8
    ax.add_patch(Rectangle((x, y - h), w, h, fc="white", ec=INK, lw=1.1))
    ax.add_patch(Rectangle((x, y - HEAD), w, HEAD, fc=GREY, ec=INK, lw=1.1))
    ax.text(x + w / 2, y - HEAD / 2, name, ha="center", va="center", fontweight="bold")
    for i, (tag, f) in enumerate(fields):
        yy = y - HEAD - 0.4 - ROW * (i + 0.5)
        if tag:
            ax.text(x + 1.2, yy, tag, va="center", fontsize=8.5, fontweight="bold")
        ax.text(x + 5.2, yy, f, va="center", fontsize=9.5)
    boxes[name] = (x, y - h, w, h)


def one(p, d):
    """Кінець зв'язку «один і лише один»: дві поперечні риски. d - одиничний вектор усередину таблиці."""
    (x, y), (dx, dy) = p, d
    for k in (1.6, 3.0):
        cx, cy = x + dx * k, y + dy * k
        ax.plot([cx - dy * 1.1, cx + dy * 1.1], [cy + dx * 1.1, cy - dx * 1.1], color=INK, lw=1.1)


def many(p, d):
    """Кінець зв'язку «багато» (одна чи більше): лапка з трьох ліній і риска перед нею."""
    (x, y), (dx, dy) = p, d
    bx, by = x - dx * 3.2, y - dy * 3.2
    for s in (-1.4, 0, 1.4):
        tip = (x - dy * s, y + dx * s)
        ax.plot([bx, tip[0]], [by, tip[1]], color=INK, lw=1.1)
    cx, cy = x - dx * 4.4, y - dy * 4.4
    ax.plot([cx - dy * 1.1, cx + dy * 1.1], [cy + dx * 1.1, cy - dx * 1.1], color=INK, lw=1.1)


def link(points, one_end, many_end):
    """Ламана з ортогональних відрізків: перша точка на межі таблиці «один», остання на межі таблиці «багато»."""
    xs, ys = zip(*points)
    ax.plot(xs, ys, color=INK, lw=1.1, solid_joinstyle="miter")

    def unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = (dx * dx + dy * dy) ** 0.5
        return dx / n, dy / n

    one(points[0], unit(points[0], points[1]))
    many(points[-1], unit(points[-2], points[-1]))


PK, FK, N = "PK", "FK", ""
table("domains", 1, 90, 26, [(PK, "code"), (N, "domain_name"), (N, "company"), (N, "escalation_rules")])
table("queries", 36, 90, 32, [(PK, "id"), (N, "code"), (N, "category"), (N, "user_query"), (N, "reference_answer"),
                              (N, "expected_escalation"), (FK, "domain"), (N, "review_status")])
table("kb_documents", 77, 90, 26, [(PK, "id"), (N, "doc_code"), (N, "domain"), (N, "is_distractor"), (N, "review_status")])
table("kb_chunks", 77, 64, 26, [(PK, "id"), (FK, "document_id"), (N, "text"), (N, "domain")])
table("query_gold_chunks", 40, 52, 26, [(FK, "query_id"), (FK, "chunk_id")])
table("experiment_runs", 1, 62, 30, [(PK, "id"), (N, "title, status"), (N, "dataset_hash"), (N, "corpus_version"),
                                      (N, "settings_snapshot_json")])
table("evaluation_results", 1, 38, 32, [(PK, "id"), (FK, "run_id"), (FK, "query_id"), (N, "model_id, prompt_code"),
                                         (N, "fact_coverage_pct"), (N, "semantic_similarity_pct"),
                                         (N, "latency_ms, cost_usd"), (N, "escalation_correct")])
table("model_registry", 42, 34, 28, [(PK, "id"), (N, "provider, model_id"), (N, "input_price_per_1m"),
                                      (N, "output_price_per_1m"), (N, "limits_json")])
table("model_status", 77, 44, 26, [(PK, "model_key"), (N, "status, http_code"), (N, "check_kind"), (N, "checked_at")])
table("judge_cache", 73, 26, 30, [(PK, "cache_key"), (N, "answer_hash, rubric_hash"), (N, "judge_model, response_json")])
table("model_daily_usage", 42, 12, 26, [(N, "model_key"), (N, "usage_date"), (N, "requests_count")])

# один домен має багато запитів
link([(27, 84), (36, 84)], 0, 0)
# один документ має багато фрагментів
link([(90, 90 - boxes["kb_documents"][3]), (90, 64)], 0, 0)
# один запит має багато записів про еталонні фрагменти
link([(52, 90 - boxes["queries"][3]), (52, 52)], 0, 0)
# один фрагмент може бути еталоном багатьох запитів
link([(77, 58), (71, 58), (71, 47), (66, 47)], 0, 0)
# одна серія має багато результатів
link([(16, 62 - boxes["experiment_runs"][3]), (16, 38)], 0, 0)
# один запит має багато результатів
link([(38, 90 - boxes["queries"][3]), (38, 31), (33, 31)], 0, 0)
# одна модель реєстру має багато результатів
link([(42, 24), (33, 24)], 0, 0)

fig.tight_layout(pad=0.3)
fig.savefig(out + "/fig_E_db.png", bbox_inches="tight", pad_inches=0.05)
print("ok")
