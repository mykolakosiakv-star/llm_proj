"""Оновлення переліку посилань: виправлення записів, нові джерела, перенумерація за порядком першої згадки."""
import html
import io
import re
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from docx_edit import Doc, PARA, TT  # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

path, report_path = sys.argv[1], sys.argv[2]
d = Doc(path)

# дрібні виправлення даних джерел
d.sub("Vol. 55, No. 12. Art. 248. DOI: 10.1145/3571730.", "Vol. 55, No. 12. P. 1–38. DOI: 10.1145/3571730.")
d.sub("Proceedings of ACL 2024. 2024. arXiv:2305.17926.", "arXiv:2305.17926. 2023.")
d.sub("Proceedings of the 18th Conference of the EACL: System Demonstrations. 2024. P. 150–158. arXiv:2309.15217.",
      "arXiv:2309.15217. 2023.")
d.sub("Transactions on Machine Learning Research. 2024. arXiv:2305.05176.", "arXiv:2305.05176. 2023.")
d.sub("Journal of Geovisualization and Spatial Analysis. 2026. Vol. 10. DOI", "Journal of Geovisualization and Spatial Analysis. 2026. Vol. 10, No. 1. Art. 19. DOI")

NEW = [
    "Cohen J. Weighted kappa: nominal scale agreement provision for scaled disagreement or partial credit. Psychological Bulletin. "
    "1968. Vol. 70, No. 4. P. 213–220. DOI: 10.1037/h0026256.",
    "Javaherian Pour E., Chen Y., Kesminas A., Rajabifard A., Ghasemi Tootkaboni M. An automated framework for natural "
    "language-based spatial query using large language models. Journal of Geovisualization and Spatial Analysis. 2026. Vol. 10, "
    "No. 1. Art. 19. DOI: 10.1007/s41651-026-00260-9.",
    "Yuan J., Xu Y. et al. Query as anchor: scenario-adaptive user representation via large language model. arXiv:2602.14492. "
    "2026. URL: https://arxiv.org/abs/2602.14492 (дата звернення: 09.11.2026).",
    "Marandi S., Hu Y.-S., Modarres M. Complex system diagnostics using a knowledge graph-informed and large language "
    "model-enhanced framework. arXiv:2505.21291. 2025. URL: https://arxiv.org/abs/2505.21291 (дата звернення: 09.11.2026).",
    "Олійник В., Поночовний П. Експериментальне дослідження компонентів RAG-системи та їх впливу на ефективність чат-ботів "
    "служб підтримки. Адаптивні системи автоматичного управління. 2026. Т. 1, № 48. DOI: 10.20535/1560-8956.48.2026.351879.",
    "Переяславська С., Смагіна О. Вивчення методів обробки природної мови для створення інтелектуальних систем, таких як "
    "чат-боти та системи автоматичного перекладу. Вісник Хмельницького національного університету. Серія: Технічні науки. "
    "2025. № 2 (349). С. 100–108. DOI: 10.31891/2307-5732-2025-349-14.",
    "Румик І. Мовні моделі для автоматизації документування та підтримки управління знаннями у віддалених командах "
    "ІТ-компаній. Економіка та суспільство. 2025. № 80. DOI: 10.32782/2524-0072/2025-80-113.",
    "Мойсеєнко М., Кузишин М., Туровська Л., Мазуренко Ю., Петришин М., Мазуренко О. Великі мовні моделі штучного інтелекту "
    "в медицині. Сучасні інформаційні технології та інноваційні методики навчання в підготовці фахівців: методологія, "
    "теорія, досвід, проблеми. 2024. Вип. 72. С. 73–88. DOI: 10.31652/2412-1142-2024-72-73-88.",
]

paras = list(PARA.finditer(d.xml))
head = next(i for i, m in enumerate(paras)
            if 'w:pStyle w:val="1"' in m.group(0) and Doc.plain(m.group(0)).strip() == "ПЕРЕЛІК ПОСИЛАНЬ" and "PAGEREF" not in m.group(0))
refs = []  # (match, number, text)
for m in paras[head + 1:]:
    t = Doc.plain(m.group(0)).strip()
    mm = re.match(r"(\d+)\.\s+(.*)", t, flags=re.S)
    if mm:
        refs.append((m, int(mm.group(1)), mm.group(2)))
last_num = refs[-1][1]
assert last_num == len(refs), (last_num, len(refs))
ref_ppr = re.search(r"<w:pPr>.*?</w:pPr>", refs[-1][0].group(0), flags=re.S)
ref_ppr = ref_ppr.group(0) if ref_ppr else ""

# уся робота до переліку: збираємо посилання за порядком першої згадки
CIT = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
body_xml = d.xml[:paras[head].start()]
order = []
for m in PARA.finditer(body_xml):
    t = Doc.plain(m.group(0))
    for c in CIT.finditer(t):
        for n in re.findall(r"\d+", c.group(1)):
            n = int(n)
            if n not in order:
                order.append(n)

all_nums = list(range(1, last_num + len(NEW) + 1))
cited = [n for n in order if n <= last_num + len(NEW)]
uncited = [n for n in all_nums if n not in order]
bad = [n for n in order if n > last_num + len(NEW)]
mapping = {old: new for new, old in enumerate(cited, 1)}
# нецитовані джерела (якщо є) ставимо в кінець, щоб не втратити
for old in uncited:
    mapping[old] = len(mapping) + 1

texts = {n: t for _, n, t in refs}
for k, t in enumerate(NEW, last_num + 1):
    texts[k] = t

# перенумерація в тексті
def renum_node(match):
    opent, body, closet = match.groups()
    text = html.unescape(body)

    def f(c):
        nums = sorted(mapping[int(n)] for n in re.findall(r"\d+", c.group(1)))
        return "[" + ", ".join(str(n) for n in nums) + "]"

    new = CIT.sub(f, text)
    return opent + escape(new) + closet


new_body = TT.sub(renum_node, body_xml)
# перевірка: кількість цитувань у тексті не змінилась
cnt_old = sum(len(CIT.findall(Doc.plain(m.group(0)))) for m in PARA.finditer(body_xml))
cnt_new = sum(len(CIT.findall(Doc.plain(m.group(0)))) for m in PARA.finditer(new_body))
assert cnt_old == cnt_new, (cnt_old, cnt_new)

# новий перелік
ref_xml = ""
for new_n in range(1, len(mapping) + 1):
    old_n = next(o for o, n in mapping.items() if n == new_n)
    ref_xml += f'<w:p>{ref_ppr}<w:r><w:t xml:space="preserve">{escape(f"{new_n}. {texts[old_n]}")}</w:t></w:r></w:p>'

first_ref_start = refs[0][0].start()
last_ref_end = refs[-1][0].end()
d.xml = new_body + d.xml[paras[head].start():first_ref_start] + ref_xml + d.xml[last_ref_end:]
d.save()

lines = ["старий -> новий"]
lines += [f"{o} -> {n}" for o, n in sorted(mapping.items(), key=lambda x: x[1])]
lines += ["", f"нецитовані (старі номери): {uncited}", f"посилання на неіснуючі номери: {bad}", f"missing: {d.missing}", ""]
lines += [f"{n}. {texts[next(o for o, nn in mapping.items() if nn == n)]}" for n in range(1, len(mapping) + 1)]
io.open(report_path, "w", encoding="utf-8").write("\n".join(lines))
print("ok", len(mapping), "uncited:", uncited, "bad:", bad)
