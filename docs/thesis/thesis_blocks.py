"""Побудова блоків docx для записки: абзаци з курсивом та індексами, таблиці, рисунки. Доповнює docx_edit.Doc."""
import html
import os
import re

from docx_edit import Doc, PARA

NB = " "
FIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures")
EMPTY_AFTER_TABLE = '<w:p><w:pPr><w:spacing w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/></w:pPr></w:p>'


def esc(t):
    return html.escape(t, quote=False)


def runs(text, sz=None):
    """Текст у runs: Q_det стає Q з нижнім індексом, <i>..</i> курсивом, пробіл перед % нерозривним."""
    text = text.replace("Q_det", "<i>Q</i><sub>det</sub>").replace(" %", NB + "%")
    size = f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>' if sz else ""
    res = []
    for part in re.split(r"(<i>.*?</i>|<sub>.*?</sub>)", text):
        if not part:
            continue
        pr = ""
        if part.startswith("<i>"):
            part, pr = part[3:-4], "<w:i/>"
        elif part.startswith("<sub>"):
            part, pr = part[5:-6], '<w:i/><w:vertAlign w:val="subscript"/>'
        rpr = f"<w:rPr>{pr}{size}</w:rPr>" if (pr or size) else ""
        res.append(f'<w:r>{rpr}<w:t xml:space="preserve">{esc(part)}</w:t></w:r>')
    return "".join(res)


def par(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{runs(text)}</w:p>"


def table_xml(header, rows, widths):
    b = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>' for s in
                ("top", "left", "bottom", "right", "insideH", "insideV"))
    x = (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{b}</w:tblBorders>'
         '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="85" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
         '</w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
         'w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>' + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
         + "</w:tblGrid>")

    def row(cells, head=False):
        trpr = "<w:trPr><w:cantSplit/>" + ("<w:tblHeader/>" if head else "") + "</w:trPr>"
        tcs = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/></w:tcPr><w:p><w:pPr><w:keepNext/>'
            '<w:spacing w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:jc w:val="left"/></w:pPr>'
            f'{runs(c, 24)}</w:p></w:tc>' for c, w in zip(cells, widths))
        return f"<w:tr>{trpr}{tcs}</w:tr>"

    return x + row(header, True) + "".join(row(r) for r in rows) + "</w:tbl>"


def png_size(path):
    hdr = open(path, "rb").read(32)
    return int.from_bytes(hdr[16:20], "big"), int.from_bytes(hdr[20:24], "big")


def add_media(doc, png):
    """Кладе PNG у docx і повертає rId зв'язку."""
    rid = "rId_fig_" + png.replace(".", "_")
    doc.files["word/media/" + png] = open(os.path.join(FIG_DIR, png), "rb").read()
    rels = doc.files["word/_rels/document.xml.rels"].decode("utf-8")
    if f'Target="media/{png}"' in rels:
        return re.search(r'Id="([^"]+)"[^>]*Target="media/' + re.escape(png) + '"', rels).group(1)
    rels = rels.replace("</Relationships>",
                        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{png}"/></Relationships>')
    doc.files["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    return rid


def figure_xml(doc, png, caption, width_in, pic_id):
    pw, ph = png_size(os.path.join(FIG_DIR, png))
    cx = int(width_in * 914400)
    cy = int(cx * ph / pw)
    rid = add_media(doc, png)
    name = esc(caption.split(" – ")[0])
    return (
        '<w:p><w:pPr><w:keepNext/><w:spacing w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:rPr><w:noProof/></w:rPr><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{pic_id}" name="{name}" descr="{name}"/><wp:cNvGraphicFramePr/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="0" name="{png}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r></w:p>'
        f'<w:p><w:pPr><w:pStyle w:val="Figurecaption"/></w:pPr><w:r><w:t>{esc(caption)}</w:t></w:r></w:p>')


def find(doc, startswith=None, contains=None, style=None):
    for m in PARA.finditer(doc.xml):
        p = m.group(0)
        t = Doc.plain(p).strip()
        if style and f'w:pStyle w:val="{style}"' not in p:
            continue
        if startswith is not None and not t.startswith(startswith):
            continue
        if contains is not None and contains not in t:
            continue
        return m
    return None


def rewrite(doc, text, **where):
    """Замінює текст абзацу, зберігаючи його властивості; повертає False, якщо абзац не знайдено."""
    m = find(doc, **where)
    if m is None:
        doc.missing.append("rewrite " + str(where)[:70])
        return False
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", m.group(0), flags=re.S)
    doc.xml = doc.xml[:m.start()] + f"<w:p>{ppr.group(0) if ppr else ''}{runs(text)}</w:p>" + doc.xml[m.end():]
    return True


def insert_after(doc, xml, **where):
    m = find(doc, **where)
    if m is None:
        doc.missing.append("insert_after " + str(where)[:70])
        return False
    doc.xml = doc.xml[:m.end()] + xml + doc.xml[m.end():]
    return True


def replace_table(doc, caption_start, header, rows, widths, new_caption=None):
    """Замінює таблицю, що йде за підписом; підпис за потреби оновлює."""
    m = find(doc, startswith=caption_start, style="Tablecaption")
    if m is None:
        doc.missing.append("table " + caption_start)
        return False
    a = doc.xml.index("<w:tbl>", m.end())
    b = doc.xml.index("</w:tbl>", a) + len("</w:tbl>")
    doc.xml = doc.xml[:a] + table_xml(header, rows, widths) + doc.xml[b:]
    if new_caption:
        rewrite(doc, new_caption, startswith=caption_start, style="Tablecaption")
    return True


def refresh_picture(doc, png, width_in=None):
    """Оновлює вже вставлений рисунок новим файлом і перераховує висоту за його пропорціями."""
    i = doc.xml.find(f'name="{png}"')
    if i < 0:
        doc.missing.append("picture " + png)
        return False
    a = doc.xml.rfind("<w:drawing>", 0, i)
    b = doc.xml.index("</w:drawing>", i)
    seg = doc.xml[a:b]
    # Word після збереження перейменовує файли в media, тому шлях береться зі зв'язку рисунка
    rid = re.search(r'r:embed="([^"]+)"', seg).group(1)
    rels = doc.files["word/_rels/document.xml.rels"].decode("utf-8")
    target = re.search(r'<Relationship [^>]*Id="' + re.escape(rid) + r'"[^>]*>', rels).group(0)
    target = re.search(r'Target="([^"]+)"', target).group(1)
    doc.files["word/" + target] = open(os.path.join(FIG_DIR, png), "rb").read()
    cx = int(width_in * 914400) if width_in else int(re.search(r'<wp:extent cx="(\d+)"', seg).group(1))
    pw, ph = png_size(os.path.join(FIG_DIR, png))
    cy = int(cx * ph / pw)
    seg = re.sub(r'<wp:extent cx="\d+" cy="\d+"/>', f'<wp:extent cx="{cx}" cy="{cy}"/>', seg)
    seg = re.sub(r'<a:ext cx="\d+" cy="\d+"/>', f'<a:ext cx="{cx}" cy="{cy}"/>', seg)
    doc.xml = doc.xml[:a] + seg + doc.xml[b:]
    return True
