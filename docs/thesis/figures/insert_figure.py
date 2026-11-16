"""Замінює рамку-заглушку рисунка на PNG і виправляє англійську анотацію. Працює над готовим docx.
Використання: py -I insert_figure.py <docx> <png> <фрагмент підпису, напр. 'Рисунок 1.1'>
"""
import re
import shutil
import sys
import zipfile

docx_path, png_path, caption_key = sys.argv[1], sys.argv[2], sys.argv[3]
import os
base = os.path.basename(png_path)
rid, media = "rId_" + base.replace(".", "_"), "media/" + base
fig_id = int(sys.argv[4]) if len(sys.argv) > 4 else 9101
hdr = open(png_path, "rb").read(32)
pw, ph = int.from_bytes(hdr[16:20], "big"), int.from_bytes(hdr[20:24], "big")

zin = zipfile.ZipFile(docx_path)
files = {n: zin.read(n) for n in zin.namelist()}
zin.close()
xml = files["word/document.xml"].decode("utf-8")


def plain(p):
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))


paras = list(re.finditer(r"<w:p[ >].*?</w:p>", xml, flags=re.S))
target = None
for k, m in enumerate(paras):
    if "Місце для рисунка" in plain(m.group(0)) and caption_key in plain(paras[k + 1].group(0)):
        target = m
        break
if target is None:
    raise SystemExit("заглушку не знайдено")

cx = 6200000
cy = int(cx * ph / pw)
drawing = (
    f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0" '
    f'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
    f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{fig_id}" name="{caption_key}" descr="{caption_key}"/>'
    f'<wp:cNvGraphicFramePr/>'
    f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    f'<pic:nvPicPr><pic:cNvPr id="0" name="{base}"/><pic:cNvPicPr/></pic:nvPicPr>'
    f'<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rid}"/>'
    f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
    f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
    f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
    f'</wp:inline></w:drawing></w:r>'
)
new_p = ('<w:p><w:pPr><w:keepNext/><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'
         '<w:ind w:firstLine="0" w:left="0"/><w:jc w:val="center"/></w:pPr>' + drawing + '</w:p>')
xml = xml[:target.start()] + new_p + xml[target.end():]

# англійська анотація: узгодження з українською
a = ('<w:highlight w:val="yellow"/><w:lang w:val="en-US"/></w:rPr><w:t>code and name</w:t>')
if a in xml:
    xml = xml.replace(a, '<w:lang w:val="en-US"/></w:rPr><w:t>F3 “Computer Science”</w:t>', 1)
b = "<w:t>Main results: to be added.</w:t>"
if b in xml:
    xml = xml.replace(b, "<w:t>Main results: to be added upon completion.</w:t>", 1)

files["word/document.xml"] = xml.encode("utf-8")
files[f"word/{media}"] = open(png_path, "rb").read()

rels = files["word/_rels/document.xml.rels"].decode("utf-8")
if rid not in rels:
    rels = rels.replace("</Relationships>",
                        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{media}"/></Relationships>')
files["word/_rels/document.xml.rels"] = rels.encode("utf-8")

ct = files["[Content_Types].xml"].decode("utf-8")
if 'Extension="png"' not in ct:
    ct = ct.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
files["[Content_Types].xml"] = ct.encode("utf-8")

tmp = docx_path + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for n, data in files.items():
        z.writestr(n, data)
shutil.move(tmp, docx_path)
print("ok")
