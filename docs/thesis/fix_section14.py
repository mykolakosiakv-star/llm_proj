"""Разова правка п. 1.4: таблиця 1.7, абзаци про аналоги та нові джерела (за перевіркою першоджерел 08.11.2026)."""
import re
import shutil
import sys
import zipfile
from xml.sax.saxutils import escape

path = sys.argv[1]
zin = zipfile.ZipFile(path)
files = {n: zin.read(n) for n in zin.namelist()}
zin.close()
xml = files["word/document.xml"].decode("utf-8")


def plain(p):
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))


def run(text, size=None):
    rpr = f'<w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>' if size else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph_span(startswith):
    for m in re.finditer(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        t = plain(m.group(0)).replace("&amp;", "&")
        if t.lstrip().startswith(startswith):
            return m
    raise SystemExit("абзац не знайдено: " + startswith)


def rewrite(startswith, new_text):
    global xml
    m = paragraph_span(startswith)
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", m.group(0), flags=re.S)
    new = f"<w:p>{ppr.group(0) if ppr else ''}{run(new_text)}</w:p>"
    xml = xml[:m.start()] + new + xml[m.end():]


# ---------- вступний абзац ----------
rewrite(
    "Для порівняння з розроблюваним продуктом",
    "Для порівняння з розроблюваним продуктом розглянуто відкриті інструменти оцінювання LLM: фреймворк OpenAI Evals [34], "
    "набір lm-evaluation-harness [8], інструмент тестування промптів promptfoo [35], бібліотеку DeepEval [36], метрики RAGAS [32], "
    "а також фреймворк і рейтинги HELM [7] та платформу Arena [33], відому раніше як Chatbot Arena. Порівняння виконано за "
    "відкритою документацією цих засобів станом на листопад 2026 року; у таблиці 1.7 наведено лише ознаки, суттєві для поставленої задачі.",
)

# ---------- таблиця 1.7 ----------
i = xml.find("Порівняння аналогів з розроблюваним")
j = xml.find("<w:tbl>", i)
k = xml.find("</w:tbl>", j) + len("</w:tbl>")
tbl = xml[j:k]
rows = re.findall(r"<w:tr[ >].*?</w:tr>", tbl, flags=re.S)
assert len(rows) == 7, len(rows)
widths = [1984, 1984, 1488, 1488, 1488, 1491]
CELL_PPR = '<w:pPr><w:spacing w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:jc w:val="left"/></w:pPr>'


def make_row(cells):
    out = "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
    for w, text in zip(widths, cells):
        out += f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/></w:tcPr><w:p>{CELL_PPR}{run(text, 24)}</w:p></w:tc>'
    return out + "</w:tr>"


new_rows = [
    ["OpenAI Evals", "Реєстр тестів і запуск оцінювання", "Створюються користувачем", "Окремо не передбачено",
     "Не передбачено", "Так, через власні функції генерації"],
    ["lm-evaluation-harness", "Академічні тести здібностей", "Власні задачі через YAML, готових сфер підтримки немає",
     "Ні", "Ні", "Так"],
    ["promptfoo", "Порівняння промптів і моделей з перевірками", "Створюються користувачем", "Через власні перевірки",
     "Перевірки вартості й часу окремого виклику", "Так"],
    ["DeepEval", "Метрики якості для додатків із LLM", "Створюються користувачем", "Через власні метрики",
     "Не описано", "Так"],
    ["HELM", "Фреймворк і рейтинги оцінювання моделей (з 06.2026 лише підтримка)", "Ні", "Ні",
     "Метрика ефективності без вартості в $", "Так, через конфігурацію"],
    ["Arena (Chatbot Arena)", "Рейтинг моделей за голосами людей", "Ні", "Ні", "Не описано",
     "Ні, лише через організаторів"],
]
new_tbl = tbl.replace(rows[1], "".join(make_row(r) for r in new_rows), 1)
# видаляємо старі рядки 2..6
for old in rows[2:6]:
    new_tbl = new_tbl.replace(old, "", 1)
xml = xml[:j] + new_tbl + xml[k:]

# ---------- аналіз після таблиці ----------
m = paragraph_span("З таблиці видно, що наявні засоби")
ppr = re.search(r"<w:pPr>.*?</w:pPr>", m.group(0), flags=re.S)
ppr = ppr.group(0) if ppr else ""
analysis = (
    "З таблиці видно, що наявні засоби дозволяють оцінювати моделі й промпти, але від користувача потрібно самостійно "
    "будувати тестовий набір, корпус знань і метрику рішення про передачу оператору, а вартісні показники, де вони є, "
    "стосуються окремого виклику, а не маршрутизації запитів. Для керівника служби підтримки це перешкода, бо йому потрібна "
    "не лише таблиця балів, а відповідь на практичне запитання про вибір моделі й про умови передачі запиту оператору."
)
extra = (
    "Ландшафт таких засобів швидко змінюється. Розробники promptfoo у березні 2026 року оголосили про приєднання до OpenAI, "
    "зберігши відкритий код [41], а розвиток HELM з червня 2026 року обмежено підтримкою без нових оцінювань [42]. "
    "Окремо слід згадати τ-bench [39]: він перевіряє агентів підтримки в кількох сферах (роздрібна торгівля й авіаперевезення) "
    "за дотриманням правил і підсумковим станом бази даних, проте не порівнює моделі за вартістю й не пропонує схеми "
    "маршрутизації між ними."
)
new = f"<w:p>{ppr}{run(analysis)}</w:p><w:p>{ppr}{run(extra)}</w:p>"
xml = xml[:m.start()] + new + xml[m.end():]

# ---------- нерозв'язані аспекти ----------
rewrite(
    "відсутнє спільне оцінювання",
    "у розглянутих засобах немає окремої вбудованої метрики, що оцінює разом якість відповіді й правильність рішення про "
    "передачу оператору, хоча саме воно визначає безпеку автоматизації;",
)
rewrite(
    "невідомо, чи залежить вибір",
    "залежність вибору найкращої моделі від предметної сфери в прикладній постановці служби підтримки лишається недостатньо "
    "дослідженою: загальні набори тестів [7, 8] оцінюють здібності моделей, а не відповідність базі знань конкретної сфери, "
    "а наявні тести агентів підтримки [39] охоплюють лише кілька сфер;",
)
rewrite(
    "вартість часто лишається поза оцінкою",
    "вартість зазвичай оцінюється для окремого виклику, а можливість зекономити, спрямовуючи прості запити слабшій моделі, "
    "а складні – сильнішій [6, 40], перевіряється переважно на загальних тестах, а не на даних конкретної служби підтримки "
    "з урахуванням передачі запиту оператору;",
)

# ---------- 1.3.2: метрики RAGAS ----------
m = paragraph_span("Для оцінювання систем із пошуком запропоновано")
t = plain(m.group(0))
assert "Вони корисні, коли еталонної відповіді немає, але вимагають додаткових викликів моделі." in t
rewrite("Для оцінювання систем із пошуком запропоновано",
        t.replace("Вони корисні, коли еталонної відповіді немає, але вимагають додаткових викликів моделі.",
                  "Частина з них не потребує еталонної відповіді, але вимагає додаткових викликів моделі."))

# ---------- дати звернення ----------
def fix_date(match):
    r = match.group(0)
    r = re.sub(r"<w:highlight[^>]*/>", "", r)
    return r.replace("__.11.2026", "08.11.2026")


xml, n_dates = re.subn(r"<w:r[ >](?:(?!</w:r>).)*?__\.11\.2026(?:(?!</w:r>).)*?</w:r>", fix_date, xml, flags=re.S)

# ---------- нові джерела ----------
paras = list(re.finditer(r"<w:p[ >].*?</w:p>", xml, flags=re.S))
head_idx = next(i for i, p in enumerate(paras) if 'w:pStyle w:val="1"' in p.group(0) and plain(p.group(0)).strip() == "ПЕРЕЛІК ПОСИЛАНЬ" and "fldChar" not in p.group(0))
last_ref = None
for p in paras[head_idx + 1:]:
    if re.match(r"\s*\d+\.\s", plain(p.group(0))):
        last_ref = p
n = int(re.match(r"\s*(\d+)\.", plain(last_ref.group(0))).group(1))
assert n == 38, n
ppr = re.search(r"<w:pPr>.*?</w:pPr>", last_ref.group(0), flags=re.S)
ppr = ppr.group(0) if ppr else "<w:pPr/>"
srcs = [
    "Yao S., Shinn N., Razavi P., Narasimhan K. τ-bench: a benchmark for tool-agent-user interaction in real-world domains. "
    "International Conference on Learning Representations (ICLR 2025). 2025. arXiv:2406.12045.",
    "Ong I. et al. RouteLLM: learning to route LLMs with preference data. International Conference on Learning "
    "Representations (ICLR 2025). 2025. arXiv:2406.18665.",
    "Webster I., D’Angelo M. Promptfoo is joining OpenAI. Promptfoo Blog. 9 March 2026. "
    "URL: https://www.promptfoo.dev/blog/promptfoo-joining-openai/ (дата звернення: 08.11.2026).",
    "HELM maintenance mode policy. Stanford CRFM. URL: https://crfm-helm.readthedocs.io/en/latest/maintenance_mode/ "
    "(дата звернення: 08.11.2026).",
]
add = "".join(f"<w:p>{ppr}{run(f'{n + k}. {s}')}</w:p>" for k, s in enumerate(srcs, 1))
xml = xml[:last_ref.end()] + add + xml[last_ref.end():]

files["word/document.xml"] = xml.encode("utf-8")
tmp = path + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for name, data in files.items():
        z.writestr(name, data)
shutil.move(tmp, path)
print("ok; дат виправлено:", n_dates)
