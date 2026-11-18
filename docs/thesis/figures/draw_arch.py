"""Рисунок 3.1: архітектура програми. Запуск: py draw_arch.py <каталог з matplotlib> <вихідний каталог>"""
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

out = sys.argv[2]
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 12})
INK = "#000000"
NL = chr(10)

fig, ax = plt.subplots(figsize=(7.2, 5.3), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(0, 76)
ax.axis("off")


def box(x, y, w, h, title, sub=None, fill="#eeeeee", dashed=False, fs=12, sfs=10.5):
    ax.add_patch(Rectangle((x, y), w, h, fc=fill, ec=INK, lw=1.3, ls="--" if dashed else "-"))
    if sub:
        ax.text(x + w / 2, y + h * 0.68, title, ha="center", va="center", fontsize=fs)
        ax.text(x + w / 2, y + h * 0.30, sub, ha="center", va="center", fontsize=sfs, linespacing=1.15)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, linespacing=1.15)


def arrow(x, y1, y2):
    ax.annotate("", xy=(x, y2), xytext=(x, y1),
                arrowprops=dict(arrowstyle="<|-|>", color=INK, lw=1.2, mutation_scale=11, shrinkA=0, shrinkB=0))


box(4, 66, 92, 9.5, "Веб-інтерфейс", "Початок · Помічник · Експерименти · Результати · Дані · Моделі · Журнал")
arrow(50, 66, 61.5)
box(4, 52, 92, 9.5, "Серверна частина (FastAPI)", "маршрути API, фонові задачі, журнал виконання")
arrow(25, 52, 47.5)
arrow(73, 52, 47.5)

ax.add_patch(Rectangle((4, 24), 42, 23.5, fc="white", ec=INK, lw=1.0, ls="--"))
ax.text(25, 44.6, "Автоматизація відповіді", ha="center", va="center", fontsize=11, style="italic")
box(6.5, 26, 18, 15.5, "Помічник", "відповідь" + NL + "і рішення", fs=11.5, sfs=10)
box(25.5, 26, 18, 15.5, "Пошук знань", "BM25, hit@k", fs=11.5, sfs=10)

ax.add_patch(Rectangle((50, 24), 46, 23.5, fc="white", ec=INK, lw=1.0, ls="--"))
ax.text(73, 44.6, "Дослідження ефективності", ha="center", va="center", fontsize=11, style="italic")
box(52, 34.2, 20.5, 7.3, "Серії" + NL + "експериментів", fs=10.5)
box(73.5, 34.2, 20.5, 7.3, "Оцінювання," + NL + "LLM-суддя", fs=10.5)
box(52, 26, 20.5, 7.3, "Статистика," + NL + "гіпотези", fs=10.5)
box(73.5, 26, 20.5, 7.3, "Каскад, вибір" + NL + "моделі", fs=10.5)

arrow(25, 24, 19.5)
arrow(73, 24, 19.5)
box(4, 10, 42, 9.5, "База даних SQLite", "запити, знання, моделі, серії", sfs=10.5)
box(50, 10, 46, 9.5, "Клієнти моделей", "виклики, повтори, ліміти, вартість", sfs=10.5)
arrow(73, 10, 5.5)
box(50, 0.3, 46, 5.2, "OpenRouter, Gemini, Groq, Ollama", fill="white", dashed=True, fs=10.5)
fig.savefig(out + "/fig_3_arch.png", bbox_inches="tight", pad_inches=0.08)
plt.close(fig)
print("ok")
