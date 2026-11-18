"""Рисунки розділу 3: графіки за даними основної серії, серії для H2 та симуляції каскаду (схема архітектури: draw_arch.py).
Запуск: py draw_ch3.py <каталог з matplotlib> <вихідний каталог>
Числа взято з програми (сторінка «Результати», /api/cascade/simulate, вибірки з evaluation_results).
Оформлення узгоджено з рисунками розділів 1-2: Times New Roman, відтінки сірого.
"""
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

out = sys.argv[2]
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 12, "axes.spines.top": False,
                     "axes.spines.right": False, "hatch.linewidth": 0.6})
DARK, MID, LIGHT, INK = "#4d4d4d", "#9a9a9a", "#dcdcdc", "#000000"


def num(v, d=1):
    return f"{v:.{d}f}".replace(".", ",")


# Q за моделями з довірчими інтервалами
models = ["GPT-5.6 Terra", "DeepSeek V4 Pro", "DeepSeek V4.1 Flash", "GPT-5.6 Luna", "Gemma 4 26B",
          "Qwen 3.8 27B", "Gemini 3.1 Flash-Lite", "GPT-OSS 120B"]
q = [69.2, 68.5, 67.8, 67.4, 67.3, 67.1, 65.9, 63.3]
lo = [67.1, 66.4, 66.0, 65.3, 65.2, 65.0, 63.9, 61.4]
hi = [71.2, 70.5, 69.6, 69.4, 69.2, 69.1, 67.8, 65.1]
fig, ax = plt.subplots(figsize=(7.2, 3.5), dpi=200)
y = list(range(len(models)))[::-1]
ax.barh(y, q, color=LIGHT, edgecolor=INK, linewidth=0.8, height=0.62)
ax.errorbar(q, y, xerr=[[a - b for a, b in zip(q, lo)], [b - a for a, b in zip(q, hi)]], fmt="none",
            ecolor=INK, capsize=3, lw=1)
for yy, v in zip(y, q):
    ax.text(50.4, yy, num(v), va="center", fontsize=11)
ax.set_yticks(y)
ax.set_yticklabels(models)
ax.set_xlim(50, 74)
ax.set_xlabel("Інтегральна оцінка якості, % (відрізок – 95 % довірчий інтервал)")
fig.tight_layout()
fig.savefig(f"{out}/fig_3_q_models.png")
plt.close(fig)

# Q за сферами та промптами
fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.1), dpi=200)
dom = ["Фінансові\nсервіси", "ІТ-\nпідтримка", "Електронна\nкомерція", "Телеком"]
dv = [76.7, 65.0, 63.7, 62.9]
a1.bar(dom, dv, color=LIGHT, edgecolor=INK, linewidth=0.8, width=0.62)
for i, v in enumerate(dv):
    a1.text(i, v + 0.7, num(v), ha="center", fontsize=11)
a1.set_ylim(50, 82)
a1.set_ylabel("Якість, %")
a1.set_title("а) за предметними сферами", fontsize=12)
pr = ["P1", "P2", "P3", "P4", "P5"]
pv = [67.0, 67.0, 65.4, 69.9, 66.0]
a2.bar(pr, pv, color=LIGHT, edgecolor=INK, linewidth=0.8, width=0.62)
for i, v in enumerate(pv):
    a2.text(i, v + 0.35, num(v), ha="center", fontsize=11)
a2.set_ylim(50, 74)
a2.set_title("б) за стратегіями промпту", fontsize=12)
fig.tight_layout()
fig.savefig(f"{out}/fig_3_q_domain_prompt.png")
plt.close(fig)

# H2: контекст для трьох спільних моделей
names = ["Gemini 3.1\nFlash-Lite", "Gemma 4\n26B", "GPT-OSS\n120B"]
series = [([75.7, 77.9, 72.8], "Еталонний контекст", DARK, None),
          ([65.9, 67.3, 63.3], "Знайдений контекст (BM25)", MID, None),
          ([24.7, 22.9, 24.7], "Без контексту", "white", "///")]
fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=200)
w = 0.26
xs = list(range(3))
for k, (vals, lab, col, hatch) in enumerate(series):
    pos = [x + (k - 1) * w for x in xs]
    ax.bar(pos, vals, w, label=lab, color=col, edgecolor=INK, linewidth=0.8, hatch=hatch)
    for p_, v in zip(pos, vals):
        ax.text(p_, v + 1, num(v), ha="center", fontsize=10)
ax.set_xticks(xs)
ax.set_xticklabels(names)
ax.set_ylim(0, 95)
ax.set_ylabel("Якість, %")
ax.legend(frameon=False, fontsize=10.5, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.14))
fig.tight_layout()
fig.savefig(f"{out}/fig_3_h2_context.png")
plt.close(fig)

# Каскад: вартість і пропуск обов'язкових передач
cheap = ["Gemma 4 26B", "DeepSeek V4.1\nFlash", "Gemini 3.1\nFlash-Lite"]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.3), dpi=200)
x = list(range(3))
cost_cheap, cost_c, strong = [0.10, 0.55, 0.44], [0.80, 1.31, 1.23], 3.22
a1.bar([i - 0.27 for i in x], cost_cheap, 0.27, color="white", edgecolor=INK, linewidth=0.8, hatch="///", label="Лише дешева")
a1.bar(x, cost_c, 0.27, color=MID, edgecolor=INK, linewidth=0.8, label="Каскад")
a1.bar([i + 0.27 for i in x], [strong] * 3, 0.27, color=DARK, edgecolor=INK, linewidth=0.8, label="Лише сильна")
for i in x:
    a1.text(i - 0.27, cost_cheap[i] + 0.06, num(cost_cheap[i], 2), ha="center", fontsize=9)
    a1.text(i, cost_c[i] + 0.06, num(cost_c[i], 2), ha="center", fontsize=9)
    a1.text(i + 0.27, strong + 0.06, num(strong, 2), ha="center", fontsize=9)
a1.set_xticks(x)
a1.set_xticklabels(cheap, fontsize=10.5)
a1.set_ylabel("Вартість, $ за 1000 запитів")
a1.set_ylim(0, 4.6)
a1.set_title("а) вартість", fontsize=12)
a1.legend(frameon=False, fontsize=10, loc="upper left", ncol=3, columnspacing=0.8, handlelength=1.2,
          bbox_to_anchor=(-0.02, 1.0))
a2.bar([i - 0.2 for i in x], [4.3] * 3, 0.4, color="white", edgecolor=INK, linewidth=0.8, hatch="///", label="Лише дешева")
a2.bar([i + 0.2 for i in x], [8.7] * 3, 0.4, color=MID, edgecolor=INK, linewidth=0.8, label="Каскад і лише сильна")
for i in x:
    a2.text(i - 0.2, 4.45, "4,3", ha="center", fontsize=10)
    a2.text(i + 0.2, 8.85, "8,7", ha="center", fontsize=10)
a2.set_xticks(x)
a2.set_xticklabels(cheap, fontsize=10.5)
a2.set_ylabel("Пропуск передач оператору, %")
a2.set_ylim(0, 12.5)
a2.set_title("б) безпека", fontsize=12)
a2.legend(frameon=False, fontsize=10, loc="upper left")
fig.tight_layout()
fig.savefig(f"{out}/fig_3_cascade.png")
plt.close(fig)

print("ok")
