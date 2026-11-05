"""Збирає пояснювальну записку у .docx за вимогами Положення та методичних вказівок.

Тіло роботи лежить у body.txt (розмітка описана нижче), довідник джерел у sources.py.
Запуск: py -I docs/thesis/build_thesis.py
"""
import io
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sources import SOURCES  # noqa: E402

OUT = os.path.join(HERE, "Пояснювальна_записка.docx")

# розміри в twips (1 мм = 56.7)
MM = 56.7
MARGIN = dict(left=int(25 * MM), right=int(10 * MM), top=int(20 * MM), bottom=int(20 * MM),
              header=int(15 * MM), footer=int(15 * MM))
TEXT_W = int(210 * MM) - MARGIN["left"] - MARGIN["right"]
FIRST_LINE = 709  # 1,25 см
FONT = "Times New Roman"

used_sources = []  # ключі в порядку першої згадки


def cite_num(key):
    if key not in SOURCES:
        raise KeyError("невідоме джерело: " + key)
    if key not in used_sources:
        used_sources.append(key)
    return used_sources.index(key) + 1


def run(text, hl=False, italic=False, size=None, caps=False):
    props = f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" w:eastAsia="{FONT}"/>'
    if italic:
        props += "<w:i/>"
    if caps:
        props += "<w:caps/>"
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if hl:
        props += '<w:highlight w:val="yellow"/>'
    parts = text.split("\t")
    xml = ""
    for i, p in enumerate(parts):
        if i:
            xml += f"<w:r><w:rPr>{props}</w:rPr><w:tab/></w:r>"
        if p:
            xml += f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(p)}</w:t></w:r>'
    return xml


def inline(text, size=None):
    """[[...]] жовте виділення (місце для уточнення), {ключ} номер джерела, _x_ курсив."""
    def cite_sub(m):
        keys = [k.strip() for k in m.group(1).split(",")]
        nums = sorted(cite_num(k) for k in keys)
        return "[" + ", ".join(str(n) for n in nums) + "]"
    text = re.sub(r"\{([a-z0-9_, ]+)\}", cite_sub, text)
    out = ""
    for part in re.split(r"(\[\[.*?\]\])", text):
        if part.startswith("[["):
            out += run(part[2:-2], hl=True, size=size)
        else:
            for sub in re.split(r"(_[^_]+_)", part):
                if len(sub) > 2 and sub.startswith("_") and sub.endswith("_"):
                    out += run(sub[1:-1], italic=True, size=size)
                else:
                    out += run(sub, size=size)
    return out


def para(text="", style=None, jc=None, ind=None, keep_next=False, page_break=False, size=None,
         raw=None, spacing=None, border=False, sect=None):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if keep_next:
        ppr += "<w:keepNext/>"
    if page_break:
        ppr += "<w:pageBreakBefore/>"
    if border:
        ppr += ('<w:pBdr><w:top w:val="single" w:sz="6" w:space="6" w:color="000000"/>'
                '<w:left w:val="single" w:sz="6" w:space="6" w:color="000000"/>'
                '<w:bottom w:val="single" w:sz="6" w:space="6" w:color="000000"/>'
                '<w:right w:val="single" w:sz="6" w:space="6" w:color="000000"/></w:pBdr>')
    if spacing:
        ppr += spacing
    if ind is not None:
        ppr += ind
    if jc:
        ppr += f'<w:jc w:val="{jc}"/>'
    if sect:
        ppr += sect
    body = raw if raw is not None else inline(text, size)
    return f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>"


NO_IND = '<w:ind w:firstLine="0" w:left="0"/>'
SINGLE = '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'


def sect_xml(header=False, last=False):
    hdr = '<w:headerReference w:type="default" r:id="rIdHdr"/>' if header else ""
    pg = (f'<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="{MARGIN["top"]}" w:right="{MARGIN["right"]}" '
          f'w:bottom="{MARGIN["bottom"]}" w:left="{MARGIN["left"]}" w:header="{MARGIN["header"]}" '
          f'w:footer="{MARGIN["footer"]}" w:gutter="0"/>')
    typ = "" if last else '<w:type w:val="nextPage"/>'
    return f"<w:sectPr>{hdr}{typ}{pg}</w:sectPr>"


# ---------- таблиці та рисунки ----------
def table(rows, widths=None, size=24):
    n = len(rows[0])
    if not widths:
        widths = [100 // n] * n
    tot = sum(widths)
    ws = [int(TEXT_W * w / tot) for w in widths]
    ws[-1] = TEXT_W - sum(ws[:-1])
    brd = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                  for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    xml = (f'<w:tbl><w:tblPr><w:tblW w:w="{TEXT_W}" w:type="dxa"/><w:tblBorders>{brd}</w:tblBorders>'
           '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="85" w:type="dxa"/>'
           '<w:right w:w="85" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
           + "".join(f'<w:gridCol w:w="{w}"/>' for w in ws) + "</w:tblGrid>")
    for ri, row in enumerate(rows):
        trpr = "<w:trPr><w:cantSplit/>" + ("<w:tblHeader/>" if ri == 0 else "") + "</w:trPr>"
        xml += f"<w:tr>{trpr}"
        for ci, cell in enumerate(row):
            xml += (f'<w:tc><w:tcPr><w:tcW w:w="{ws[ci]}" w:type="dxa"/></w:tcPr>'
                    + para(cell, jc="left", ind=NO_IND, spacing=SINGLE, size=size) + "</w:tc>")
        xml += "</w:tr>"
    return xml + "</w:tbl>"


# ---------- парсер тіла ----------
def parse_body(path):
    lines = io.open(path, encoding="utf-8").read().split("\n")
    out = []
    i = 0
    counters = {}
    list_style = "Dash"
    while i < len(lines):
        ln = lines[i].rstrip()
        i += 1
        if not ln.strip() or ln.startswith("//"):
            continue
        if ln.startswith("## "):
            out.append(para(ln[3:], style="Heading2"))
        elif ln.startswith("### "):
            out.append(para(ln[4:], style="Heading3"))
        elif ln.startswith("# "):
            out.append(para(ln[2:], style="Heading1"))
        elif ln.startswith("#nobreak "):
            out.append(para(ln[9:], style="Heading1", page_break=False))
        elif ln.startswith("!table "):
            cap = ln[7:]
            widths = None
            rows = []
            while i < len(lines) and (lines[i].startswith("|") or lines[i].startswith("!widths")):
                if lines[i].startswith("!widths"):
                    widths = [int(x) for x in lines[i].split()[1:]]
                else:
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(para(cap, style="TableCaption", keep_next=True))
            out.append(table(rows, widths))
            out.append(para("", spacing=SINGLE, ind=NO_IND))
        elif ln.startswith("!fig "):
            cap = ln[5:]
            note = lines[i].strip()[5:] if i < len(lines) and lines[i].startswith("!box ") else ""
            if note:
                i += 1
            out.append(para("[[Місце для рисунка: " + note + "]]", jc="center", ind=NO_IND,
                            keep_next=True, border=True, spacing=SINGLE))
            out.append(para(cap, style="FigCaption"))
        elif ln.startswith("- "):
            out.append(para(ln[2:], style="Dash"))
        elif ln.startswith("!pagebreak"):
            out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        else:
            out.append(para(ln))
    return "".join(out)


# ---------- службові частини ----------
def title_page():
    c = lambda t, **k: para(t, jc="center", ind=NO_IND, spacing=SINGLE, **k)
    gap = lambda n: "".join(para("", spacing=SINGLE, ind=NO_IND) for _ in range(n))
    x = c("МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ")
    x += c("НАЦІОНАЛЬНИЙ УНІВЕРСИТЕТ «ОДЕСЬКА ЮРИДИЧНА АКАДЕМІЯ»")
    x += c("ФАКУЛЬТЕТ КІБЕРБЕЗПЕКИ ТА ІНФОРМАЦІЙНИХ ТЕХНОЛОГІЙ")
    x += c("Кафедра [[програмної інженерії / інформаційних технологій: уточнити]]")
    x += gap(5)
    x += c("КВАЛІФІКАЦІЙНА РОБОТА")
    x += c("на тему:")
    x += c("«ДОСЛІДЖЕННЯ ЕФЕКТИВНОСТІ ЗАСТОСУВАННЯ ВЕЛИКИХ МОВНИХ МОДЕЛЕЙ ДЛЯ АВТОМАТИЗАЦІЇ "
           "ВІДПОВІДЕЙ НА ТИПОВІ ЗАПИТИ КОРИСТУВАЧІВ»")
    x += gap(4)
    left = '<w:ind w:left="4536" w:firstLine="0"/>'
    l = lambda t: para(t, jc="left", ind=left, spacing=SINGLE)
    x += l("Рівень «магістр»")
    x += l("Галузь знань [[F / 12]] «Інформаційні технології»,")
    x += l("спеціальність [[F3 «Комп’ютерні науки» або 124 «Системний аналіз»: уточнити]]")
    x += l("Виконав здобувач 2 курсу")
    x += l("Косяк Микола Владиславович")
    x += l("Керівник к.т.н., доцент")
    x += l("Трофименко Олена Григорівна")
    x += l("Рецензент [[посада, звання, ПІБ]]")
    x += gap(6)
    x += c("Одеса – 2026", sect=sect_xml(header=False))
    return x


def task_page():
    c = lambda t, **k: para(t, jc="center", ind=NO_IND, spacing=SINGLE, **k)
    l = lambda t, **k: para(t, jc="left", ind=NO_IND, spacing=SINGLE, **k)
    x = c("НАЦІОНАЛЬНИЙ УНІВЕРСИТЕТ «ОДЕСЬКА ЮРИДИЧНА АКАДЕМІЯ»")
    x += l("Факультет кібербезпеки та інформаційних технологій")
    x += l("Кафедра [[назва кафедри]]")
    x += l("Галузь знань: [[код]] «Інформаційні технології»")
    x += l("Спеціальність: [[код і назва]]")
    x += l("Назва освітньої програми: [[назва]]")
    x += para("", spacing=SINGLE, ind=NO_IND)
    x += para("ЗАТВЕРДЖУЮ", jc="right", ind=NO_IND, spacing=SINGLE)
    x += para("Завідувач кафедри [[назва]]", jc="right", ind=NO_IND, spacing=SINGLE)
    x += para("[[науковий ступінь, звання, ПІБ]] ___________", jc="right", ind=NO_IND, spacing=SINGLE)
    x += para("«____» ______________ 20__ року", jc="right", ind=NO_IND, spacing=SINGLE)
    x += para("", spacing=SINGLE, ind=NO_IND)
    x += c("З А В Д А Н Н Я")
    x += c("на кваліфікаційну (магістерську) роботу")
    x += l("здобувачеві Косяку Миколі Владиславовичу")
    x += l("1. Тема роботи «Дослідження ефективності застосування великих мовних моделей для "
           "автоматизації відповідей на типові запити користувачів» та керівник роботи: "
           "к.т.н., доцент Трофименко Олена Григорівна затверджені наказом НУ «ОЮА» "
           "від «__» __________ 20___ року № _____")
    x += l("2. Строк подання здобувачем роботи «__» __________ 20___ року.")
    x += l("3. Дата видачі завдання «__» __________ 20___ року.")
    x += para("", spacing=SINGLE, ind=NO_IND)
    x += c("КАЛЕНДАРНИЙ ПЛАН")
    rows = [["№ з/п", "Назва етапів магістерської роботи", "Строк виконання етапів роботи", "Примітка"],
            ["1", "Аналіз підходів до автоматизації відповідей та оцінювання LLM", "", ""],
            ["2", "Розроблення методики та математичної моделі оцінювання", "", ""],
            ["3", "Формування набору запитів і корпусу знань", "", ""],
            ["4", "Розроблення програмного продукту", "", ""],
            ["5", "Проведення експериментів, перевірка гіпотез", "", ""],
            ["6", "Аналіз результатів, рекомендації", "", ""],
            ["7", "Оформлення звітної частини", "", ""]]
    x += table(rows, [8, 52, 24, 16], size=24)
    x += para("", spacing=SINGLE, ind=NO_IND)
    x += l("Здобувач ____________________   Микола КОСЯК")
    x += l("                    ( підпис )")
    x += l("Керівник роботи ____________________   Олена ТРОФИМЕНКО")
    x += l("                    ( підпис )", sect=sect_xml(header=False))
    return x


ANNOT_UA = (
    "Кваліфікаційна робота на тему «Дослідження ефективності застосування великих мовних моделей для "
    "автоматизації відповідей на типові запити користувачів» на здобуття другого (магістерського) рівня "
    "вищої освіти за спеціальністю [[код і назва]], освітньою програмою [[назва]], містить [[N]] рисунків, "
    "[[N]] таблиць, [[N]] додатків, [[N]] літературних джерел за переліком посилань. Робота викладена на "
    "[[N]] сторінках загального тексту і [[N]] сторінках основного тексту.",
    "Метою роботи є обґрунтування вибору великої мовної моделі, стратегії промпту та схеми маршрутизації "
    "для автоматизації відповідей на типові запити користувачів за результатами експериментального порівняння.",
    "Методи досліджень базуються на контрольованому експерименті з фіксованим набором запитів, пошуку за "
    "BM25, формалізованій метриці якості та оцінюванні незалежним LLM-суддею, непараметричній статистиці "
    "і симуляції каскадної маршрутизації.",
    "Розроблено методику оцінювання якості, безпеки та вартості автоматичних відповідей і програмний "
    "прототип LLM-AutoBench, що реалізує конвеєр «запит – пошук знань – відповідь моделі – рішення про "
    "передачу оператору» та модуль експериментів. [[Основні отримані результати: дописати після "
    "завершення серій H1–H3.]]",
    "Результати можуть бути використані службами підтримки користувачів та розробниками систем "
    "автоматизації для вибору моделі й промпту. Напрями подальших досліджень: вплив мови запиту, "
    "локальні моделі, перевірка на реальних журналах звернень.",
)
KEY_UA = ("ВЕЛИКА МОВНА МОДЕЛЬ, АВТОМАТИЗАЦІЯ ПІДТРИМКИ, ПРОМПТ-ІНЖИНІРИНГ, ОЦІНЮВАННЯ ЯКОСТІ, "
          "ГАЛЮЦИНАЦІЇ, КАСКАДНА МАРШРУТИЗАЦІЯ, ПОШУК ЗНАНЬ.")
ANNOT_EN = (
    "Qualification work on the topic “Study of the Effectiveness of Large Language Models in Automating "
    "Responses to Common User Queries” for the second (master’s) level of higher education, specialty "
    "[[code and name]], educational program [[name]], contains [[N]] figures, [[N]] tables, [[N]] "
    "appendices and [[N]] references. The work is set out on [[N]] pages of total text and [[N]] pages of "
    "main text.",
    "The aim of the work is to substantiate the choice of a large language model, a prompting strategy and "
    "a routing scheme for automating responses to typical user queries on the basis of an experimental "
    "comparison.",
    "The methods rely on a controlled experiment with a fixed query set, BM25 retrieval, a formalised "
    "quality metric, evaluation by an independent LLM judge, non-parametric statistics and a simulation "
    "of cascade routing.",
    "A methodology for evaluating the quality, safety and cost of automatic answers and the LLM-AutoBench "
    "software prototype have been developed; the prototype implements the pipeline “query – knowledge "
    "retrieval – model answer – escalation decision” and an experiment module. [[Main results: to be "
    "added after the H1–H3 series are completed.]]",
    "The results can be used by support services and developers of automation systems to select a model "
    "and a prompt. Directions for further research include the effect of query language, local models "
    "and validation on real support logs.",
)
KEY_EN = ("LARGE LANGUAGE MODEL, SUPPORT AUTOMATION, PROMPT ENGINEERING, QUALITY EVALUATION, "
          "HALLUCINATIONS, CASCADE ROUTING, KNOWLEDGE RETRIEVAL.")


def annotation_pages():
    x = para("АНОТАЦІЯ", style="StructTitle", page_break=False)
    for t in ANNOT_UA:
        x += para(t)
    x += para(KEY_UA, ind=NO_IND, spacing=SINGLE, jc="left")
    x += para("ANNOTATION", style="StructTitle", page_break=True)
    for t in ANNOT_EN:
        x += para(t)
    x += para(KEY_EN, ind=NO_IND, spacing=SINGLE, jc="left")
    return x


def toc_page():
    fld = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
           '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'
           '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
           + run("Зміст оновиться автоматично (у Word: ПКМ – Оновити поле).") +
           '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    x = para("ЗМІСТ", style="StructTitle", page_break=True)
    x += f"<w:p><w:pPr><w:ind w:firstLine=\"0\"/></w:pPr>{fld}</w:p>"
    x += para("", spacing=SINGLE, ind=NO_IND, sect=sect_xml(header=False))
    return x


ABBREVS = [
    ("API", "прикладний програмний інтерфейс (Application Programming Interface)"),
    ("BM25", "функція ранжування за ймовірнісною моделлю релевантності (Best Matching 25)"),
    ("FAQ", "перелік поширених запитань і відповідей (Frequently Asked Questions)"),
    ("FC", "повнота фактів у відповіді (Fact Coverage)"),
    ("FN", "пропуск потрібної передачі запиту оператору (False Negative)"),
    ("FP", "зайва передача запиту оператору (False Positive)"),
    ("HR", "частка галюцинацій (Hallucination Rate)"),
    ("LLM", "велика мовна модель (Large Language Model)"),
    ("RAG", "генерація, доповнена пошуком (Retrieval-Augmented Generation)"),
    ("RPD", "ліміт запитів на добу (Requests Per Day)"),
    ("RPM", "ліміт запитів на хвилину (Requests Per Minute)"),
    ("SS", "лексична близькість до еталонної відповіді (Semantic Similarity, лексична оцінка)"),
    ("hit@k", "частка запитів, для яких правильний фрагмент знань потрапив у перші k результатів"),
    ("Q_det", "детермінована інтегральна оцінка якості відповіді"),
    ("ЕП", "електронний підпис"),
]


def abbrev_page():
    x = para("ПЕРЕЛІК УМОВНИХ СКОРОЧЕНЬ", style="Heading1")
    for a, b in ABBREVS:
        x += para(f"{a} – {b}", jc="both")
    return x


def sources_page():
    x = para("ПЕРЕЛІК ПОСИЛАНЬ", style="Heading1", page_break=True)
    for n, key in enumerate(used_sources, 1):
        x += para(f"{n}. {SOURCES[key]}", jc="both")
    return x


# ---------- стилі та пакет ----------
def styles_xml():
    rpr = (f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" w:eastAsia="{FONT}"/>'
           '<w:sz w:val="28"/><w:szCs w:val="28"/><w:lang w:val="uk-UA"/>')

    def st(sid, name, ppr="", rp="", based="Normal", nxt=None, q=True):
        n = f'<w:next w:val="{nxt}"/>' if nxt else ""
        b = f'<w:basedOn w:val="{based}"/>' if based else ""
        return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>{b}{n}'
                f'{"<w:qFormat/>" if q else ""}<w:pPr>{ppr}</w:pPr><w:rPr>{rp}</w:rPr></w:style>')

    heading_base = ('<w:keepNext/><w:keepLines/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:docDefaults><w:rPrDefault><w:rPr>{rpr}</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
        '<w:spacing w:before="0" w:after="0" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        f'<w:pPr><w:widowControl/><w:ind w:firstLine="{FIRST_LINE}"/><w:jc w:val="both"/></w:pPr></w:style>'
        + st("Heading1", "heading 1",
             heading_base + '<w:pageBreakBefore/><w:spacing w:before="0" w:after="420" w:line="360" w:lineRule="auto"/>'
             '<w:ind w:firstLine="0"/><w:jc w:val="center"/><w:outlineLvl w:val="0"/>', "", nxt="Normal")
        + st("Heading2", "heading 2",
             heading_base + '<w:spacing w:before="420" w:after="420" w:line="360" w:lineRule="auto"/>'
             f'<w:ind w:firstLine="{FIRST_LINE}"/><w:jc w:val="left"/><w:outlineLvl w:val="1"/>', "", nxt="Normal")
        + st("Heading3", "heading 3",
             heading_base + '<w:spacing w:before="210" w:after="210" w:line="360" w:lineRule="auto"/>'
             f'<w:ind w:firstLine="{FIRST_LINE}"/><w:jc w:val="left"/><w:outlineLvl w:val="2"/>', "", nxt="Normal")
        + st("StructTitle", "Structural title",
             heading_base + '<w:spacing w:before="0" w:after="420" w:line="360" w:lineRule="auto"/>'
             '<w:ind w:firstLine="0"/><w:jc w:val="center"/>', "", nxt="Normal")
        + st("TableCaption", "Table caption",
             '<w:keepNext/><w:spacing w:before="210" w:after="0" w:line="360" w:lineRule="auto"/>'
             '<w:ind w:firstLine="0"/><w:jc w:val="left"/>', "", nxt="Normal")
        + st("FigCaption", "Figure caption",
             '<w:spacing w:before="0" w:after="210" w:line="360" w:lineRule="auto"/>'
             '<w:ind w:firstLine="0"/><w:jc w:val="center"/>', "", nxt="Normal")
        + st("Dash", "List dash",
             '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
             f'<w:ind w:left="0" w:firstLine="{FIRST_LINE}"/><w:jc w:val="both"/>', "")
        + st("TOC1", "toc 1", '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="'
             f'{TEXT_W}"/></w:tabs><w:ind w:firstLine="0"/><w:jc w:val="left"/>', "")
        + st("TOC2", "toc 2", '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="'
             f'{TEXT_W}"/></w:tabs><w:ind w:left="284" w:firstLine="0"/><w:jc w:val="left"/>', "")
        + st("TOC3", "toc 3", '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="'
             f'{TEXT_W}"/></w:tabs><w:ind w:left="567" w:firstLine="0"/><w:jc w:val="left"/>', "")
        + "</w:styles>"
    )


NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="–"/>'
    '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="1134" w:hanging="425"/></w:pPr>'
    f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}"/></w:rPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
)

HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:p><w:pPr><w:ind w:firstLine="0"/><w:spacing w:line="240" w:lineRule="auto"/><w:jc w:val="right"/></w:pPr>'
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
    '</w:p></w:hdr>'
)

SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:updateFields w:val="true"/><w:defaultTabStop w:val="709"/>'
    '<w:characterSpacingControl w:val="doNotCompress"/></w:settings>'
)

CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    '</Types>'
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdSt" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    '<Relationship Id="rIdSet" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
    '<Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
    '</Relationships>'
)


def main():
    body = parse_body(os.path.join(HERE, "body.txt"))  # заповнює used_sources
    doc_body = (title_page() + task_page() + annotation_pages() + toc_page()
                + abbrev_page() + body + sources_page())
    doc_body += f"<w:p/>{sect_xml(header=True, last=True)}"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{doc_body}</w:body></w:document>"
    )
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", styles_xml())
        z.writestr("word/numbering.xml", NUMBERING)
        z.writestr("word/settings.xml", SETTINGS)
        z.writestr("word/header1.xml", HEADER)
    words = len(re.sub(r"<[^>]+>", " ", body).split())
    print("saved", OUT, "| words in body:", words, "| sources used:", len(used_sources))


if __name__ == "__main__":
    main()
