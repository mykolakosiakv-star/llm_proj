"""Вставляє розділ з текстового файлу в готовий docx перед переліком посилань і дописує нові джерела.

Використання: py -I insert_chapter.py <docx> <chapter.txt> <new_sources.txt або ->
Стилі беруться з самого docx (ідентифікатори, які залишив Word: 1, 2, 3, Tablecaption, Figurecaption, Listdash).
"""
import io
import re
import shutil
import sys
import zipfile
from xml.sax.saxutils import escape

docx_path, chapter_path = sys.argv[1], sys.argv[2]
sources_path = sys.argv[3] if len(sys.argv) > 3 else "-"

MM = 56.7
TEXT_W = int(210 * MM) - int(25 * MM) - int(10 * MM)
NO_IND = '<w:ind w:firstLine="0" w:left="0"/>'
SINGLE = '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'

TAG = re.compile(r"<(i|sub|sup)>(.*?)</\1>|\[\[(.*?)\]\]", re.S)


def run(text, italic=False, sub=False, sup=False, hl=False, size=None):
    props = ""
    if italic:
        props += "<w:i/>"
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if hl:
        props += '<w:highlight w:val="yellow"/>'
    if sub:
        props += '<w:vertAlign w:val="subscript"/>'
    if sup:
        props += '<w:vertAlign w:val="superscript"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    parts = text.split("\t")
    xml = ""
    for i, p in enumerate(parts):
        if i:
            xml += f"<w:r>{rpr}<w:tab/></w:r>"
        if p:
            xml += f'<w:r>{rpr}<w:t xml:space="preserve">{escape(p)}</w:t></w:r>'
    return xml


def render(text, size=None, italic=False, sub=False, sup=False, hl=False):
    out, pos = "", 0
    for m in TAG.finditer(text):
        if m.start() > pos:
            out += run(text[pos:m.start()], italic, sub, sup, hl, size)
        if m.group(3) is not None:
            out += render(m.group(3), size, italic, sub, sup, True)
        else:
            kind, inner = m.group(1), m.group(2)
            out += render(inner, size, italic or kind == "i", sub or kind == "sub", sup or kind == "sup", hl)
        pos = m.end()
    if pos < len(text):
        out += run(text[pos:], italic, sub, sup, hl, size)
    return out


def para(text="", style=None, ppr_extra="", size=None, raw=None):
    ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr_extra
    body = raw if raw is not None else render(text, size)
    return f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>"


def table(rows, widths, size=24):
    n = len(rows[0])
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
        xml += "<w:tr><w:trPr><w:cantSplit/>" + ("<w:tblHeader/>" if ri == 0 else "") + "</w:trPr>"
        for ci in range(n):
            cell = row[ci] if ci < len(row) else ""
            xml += (f'<w:tc><w:tcPr><w:tcW w:w="{ws[ci]}" w:type="dxa"/></w:tcPr>'
                    + para(cell, ppr_extra=SINGLE + NO_IND + '<w:jc w:val="left"/>', size=size) + "</w:tc>")
        xml += "</w:tr>"
    return xml + "</w:tbl>"


def formula(text, number):
    visible = len(re.sub(r"<[^>]+>", "", text))
    tabs = (f'<w:tabs><w:tab w:val="center" w:pos="{TEXT_W // 2}"/>'
            f'<w:tab w:val="right" w:pos="{TEXT_W}"/></w:tabs>')
    ppr = '<w:keepLines/>' + tabs + SINGLE + NO_IND + '<w:jc w:val="left"/>'
    if visible > 62:
        # довга формула: без центрування, щоб не вилізла за поле
        ppr = '<w:keepLines/>' + f'<w:tabs><w:tab w:val="right" w:pos="{TEXT_W}"/></w:tabs>' + SINGLE + NO_IND
        body = render(text) + run("\t(" + number + ")")
    else:
        body = run("\t") + render(text) + run("\t(" + number + ")")
    spacer = para("", ppr_extra=SINGLE + NO_IND)
    return spacer + f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>" + spacer


def parse(path):
    lines = io.open(path, encoding="utf-8").read().split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i].rstrip()
        i += 1
        if not ln.strip() or ln.startswith("//"):
            continue
        if ln.startswith("### "):
            out.append(para(ln[4:], "3"))
        elif ln.startswith("## "):
            out.append(para(ln[3:], "2"))
        elif ln.startswith("# "):
            out.append(para(ln[2:], "1"))
        elif ln.startswith("!f "):
            body, num = ln[3:].rsplit("||", 1)
            out.append(formula(body.strip(), num.strip()))
        elif ln.startswith("!table "):
            cap, widths, rows = ln[7:], None, []
            while i < len(lines) and (lines[i].startswith("|") or lines[i].startswith("!widths")):
                if lines[i].startswith("!widths"):
                    widths = [int(x) for x in lines[i].split()[1:]]
                else:
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(para(cap, "Tablecaption", '<w:keepNext/>'))
            out.append(table(rows, widths or [100 // len(rows[0])] * len(rows[0])))
            out.append(para("", ppr_extra=SINGLE + NO_IND))
        elif ln.startswith("!fig "):
            cap = ln[5:]
            note = ""
            if i < len(lines) and lines[i].startswith("!box "):
                note, i = lines[i][5:].strip(), i + 1
            border = ('<w:pBdr>' + "".join(
                f'<w:{s} w:val="single" w:sz="6" w:space="6" w:color="000000"/>' for s in ("top", "left", "bottom", "right"))
                + '</w:pBdr>')
            out.append(para("[[Місце для рисунка: " + note + "]]", ppr_extra='<w:keepNext/>' + border + SINGLE + NO_IND + '<w:jc w:val="center"/>'))
            out.append(para(cap, "Figurecaption"))
        elif ln.startswith("- "):
            out.append(para(ln[2:], "Listdash"))
        else:
            out.append(para(ln))
    return "".join(out)


def plain(p):
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))


def main():
    zin = zipfile.ZipFile(docx_path)
    files = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    xml = files["word/document.xml"].decode("utf-8")

    paras = list(re.finditer(r"<w:p[ >].*?</w:p>", xml, flags=re.S))
    ref_head = None
    last_ref = None
    for m in paras:
        p = m.group(0)
        t = plain(p)
        if ref_head is None and 'w:pStyle w:val="1"' in p and t.strip() == "ПЕРЕЛІК ПОСИЛАНЬ" and "fldChar" not in p:
            ref_head = m
        if ref_head is not None and re.match(r"\s*\d+\.\s", t):
            last_ref = m
    if ref_head is None or last_ref is None:
        raise SystemExit("не знайдено перелік посилань")

    chapter = parse(chapter_path)

    new_refs = ""
    if sources_path != "-":
        ppr = re.search(r"<w:pPr>.*?</w:pPr>", last_ref.group(0), flags=re.S)
        ppr = ppr.group(0) if ppr else "<w:pPr/>"
        n = int(re.match(r"\s*(\d+)\.", plain(last_ref.group(0))).group(1))
        for line in io.open(sources_path, encoding="utf-8").read().split("\n"):
            if line.strip():
                n += 1
                new_refs += f"<w:p>{ppr}{run(f'{n}. {line.strip()}')}</w:p>"

    # спершу вставляємо джерела (вони пізніше за текстом), потім розділ
    xml = xml[:last_ref.end()] + new_refs + xml[last_ref.end():]
    xml = xml[:ref_head.start()] + chapter + xml[ref_head.start():]
    files["word/document.xml"] = xml.encode("utf-8")

    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in files.items():
            z.writestr(n, data)
    shutil.move(tmp, docx_path)
    print("вставлено; розмір тексту розділу:", len(re.sub(r"<[^>]+>", "", chapter)), "символів")


main()
