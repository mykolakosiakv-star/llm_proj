"""Перенумерація переліку посилань за порядком першої згадки (з можливістю прибрати джерела за початком запису)."""
import html
import io
import re
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from docx_edit import Doc, PARA, TT  # noqa: E402

path, report_path = sys.argv[1], sys.argv[2]
remove_prefixes = sys.argv[3:]
d = Doc(path)
paras = list(PARA.finditer(d.xml))
head = next(i for i, m in enumerate(paras)
            if 'w:pStyle w:val="1"' in m.group(0) and Doc.plain(m.group(0)).strip() == "ПЕРЕЛІК ПОСИЛАНЬ" and "PAGEREF" not in m.group(0))
refs = []
for m in paras[head + 1:]:
    t = Doc.plain(m.group(0)).strip()
    mm = re.match(r"(\d+)\.\s+(.*)", t, flags=re.S)
    if mm:
        refs.append((m, int(mm.group(1)), mm.group(2)))
assert [n for _, n, _ in refs] == list(range(1, len(refs) + 1))
ppr = re.search(r"<w:pPr>.*?</w:pPr>", refs[0][0].group(0), flags=re.S)
ppr = ppr.group(0) if ppr else ""

CIT = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
body = d.xml[:paras[head].start()]
order = []
for m in PARA.finditer(body):
    for c in CIT.finditer(Doc.plain(m.group(0))):
        for n in re.findall(r"\d+", c.group(1)):
            if int(n) not in order:
                order.append(int(n))

removed = {n for _, n, t in refs if any(t.startswith(p) for p in remove_prefixes)}
bad = [n for n in order if n > len(refs) or n in removed]
keep = [n for n in order if n <= len(refs) and n not in removed]
uncited = [n for n in range(1, len(refs) + 1) if n not in order and n not in removed]
final = keep + uncited
mapping = {old: new for new, old in enumerate(final, 1)}


def renum(m):
    o, b, c = m.groups()
    t = html.unescape(b)
    t = CIT.sub(lambda x: "[" + ", ".join(str(n) for n in sorted(mapping[int(v)] for v in re.findall(r"\d+", x.group(1)))) + "]", t)
    return o + escape(t) + c


new_body = TT.sub(renum, body)
texts = {n: t for _, n, t in refs}
ref_xml = "".join(
    f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(f"{i}. {texts[old]}")}</w:t></w:r></w:p>'
    for i, old in enumerate(final, 1))
d.xml = new_body + d.xml[paras[head].start():refs[0][0].start()] + ref_xml + d.xml[refs[-1][0].end():]
d.save()
lines = ["старий -> новий"] + [f"{o} -> {n}" for o, n in sorted(mapping.items(), key=lambda x: x[1])]
lines += ["", f"прибрано: {sorted(removed)}", f"нецитовані (додано в кінець): {uncited}", f"посилання на прибрані/неіснуючі: {bad}"]
io.open(report_path, "w", encoding="utf-8").write("\n".join(lines))
print("ok", len(final), "removed", sorted(removed), "uncited", uncited, "bad", bad)
