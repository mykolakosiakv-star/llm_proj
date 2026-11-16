"""Невеликий рушій правок docx на місці: заміна тексту з урахуванням розбиття на фрагменти (runs),
вставка абзаців, зняття жовтого виділення. Форматування абзаців і стилі зберігаються."""
import html
import re
import shutil
import zipfile
from xml.sax.saxutils import escape

TT = re.compile(r"(<w:t(?: [^>]*)?>)(.*?)(</w:t>)", re.S)
PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
SENT = "\u0001"


class Doc:
    def __init__(self, path):
        self.path = path
        z = zipfile.ZipFile(path)
        self.files = {n: z.read(n) for n in z.namelist()}
        z.close()
        self.xml = self.files["word/document.xml"].decode("utf-8")
        self.missing = []

    # ---------- читання ----------
    @staticmethod
    def plain(pxml):
        return html.unescape("".join(m.group(2) for m in TT.finditer(pxml)))

    def find_par(self, contains=None, startswith=None, style=None, nth=0):
        hits = []
        for m in PARA.finditer(self.xml):
            p = m.group(0)
            t = self.plain(p)
            if startswith is not None and not t.lstrip().startswith(startswith):
                continue
            if contains is not None and contains not in t:
                continue
            if style is not None and f'w:pStyle w:val="{style}"' not in p:
                continue
            if "fldChar" in p and style is None and "PAGEREF" in p:
                continue
            hits.append(m)
        return hits[nth] if len(hits) > nth else None

    # ---------- заміна тексту ----------
    def _sub_par(self, pxml, old, new, unhl):
        nodes = list(TT.finditer(pxml))
        texts = [html.unescape(m.group(2)) for m in nodes]
        full = "".join(texts)
        i = full.find(old)
        if i < 0:
            return None
        j = i + len(old)
        offs, pos = [], 0
        for t in texts:
            offs.append((pos, pos + len(t)))
            pos += len(t)
        new_texts = list(texts)
        touched, first = set(), True
        for k, (a, b) in enumerate(offs):
            if b <= i or a >= j:
                continue
            s, e = max(i, a) - a, min(j, b) - a
            if first:
                new_texts[k] = texts[k][:s] + new + texts[k][e:]
                first = False
            else:
                new_texts[k] = texts[k][e:]
            touched.add(k)
        out, last = [], 0
        for k, m in enumerate(nodes):
            out.append(pxml[last:m.start()])
            opent, _, closet = m.groups()
            body = new_texts[k]
            if k in touched:
                if "xml:space" not in opent:
                    opent = opent.replace("<w:t", '<w:t xml:space="preserve"', 1)
                if unhl:
                    body = SENT + body
            out.append(opent + escape(body) + closet)
            last = m.end()
        out.append(pxml[last:])
        res = "".join(out)
        if unhl:
            res = re.sub(
                r"<w:r(?: [^>]*)?>(?:(?!</w:r>).)*?" + SENT + r"(?:(?!</w:r>).)*?</w:r>",
                lambda mm: re.sub(r"<w:highlight[^>]*/>", "", mm.group(0)),
                res, flags=re.S)
            res = res.replace(SENT, "")
        return res

    def sub(self, old, new, unhl=False, label=None):
        for m in PARA.finditer(self.xml):
            r = self._sub_par(m.group(0), old, new, unhl)
            if r is not None:
                self.xml = self.xml[:m.start()] + r + self.xml[m.end():]
                return True
        self.missing.append(label or old[:70])
        return False

    # ---------- абзаци ----------
    @staticmethod
    def runs(text, lang=None):
        out = ""
        for part in re.split(r"(\[\[.*?\]\])", text):
            if not part:
                continue
            hl = part.startswith("[[")
            body = part[2:-2] if hl else part
            props = ('<w:highlight w:val="yellow"/>' if hl else "") + (f'<w:lang w:val="{lang}"/>' if lang else "")
            rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
            out += f'<w:r>{rpr}<w:t xml:space="preserve">{escape(body)}</w:t></w:r>'
        return out

    def _ppr(self, pxml, keep_style=True):
        m = re.search(r"<w:pPr>.*?</w:pPr>", pxml, flags=re.S)
        return m.group(0) if m else ""

    def rewrite(self, text, lang=None, **find):
        m = self.find_par(**find)
        if m is None:
            self.missing.append("rewrite " + str(find))
            return False
        new = f"<w:p>{self._ppr(m.group(0))}{self.runs(text, lang)}</w:p>"
        self.xml = self.xml[:m.start()] + new + self.xml[m.end():]
        return True

    def delete(self, **find):
        m = self.find_par(**find)
        if m is None:
            self.missing.append("delete " + str(find))
            return False
        self.xml = self.xml[:m.start()] + self.xml[m.end():]
        return True

    def insert_after(self, text, ppr_from=None, style=None, lang=None, **find):
        m = self.find_par(**find)
        if m is None:
            self.missing.append("insert_after " + str(find))
            return False
        src = m.group(0) if ppr_from is None else ppr_from
        ppr = self._ppr(src) if style is None else f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        self.xml = self.xml[:m.end()] + f"<w:p>{ppr}{self.runs(text, lang)}</w:p>" + self.xml[m.end():]
        return True

    def insert_before(self, text, style=None, ppr_from=None, lang=None, **find):
        m = self.find_par(**find)
        if m is None:
            self.missing.append("insert_before " + str(find))
            return False
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else self._ppr(ppr_from or m.group(0))
        self.xml = self.xml[:m.start()] + f"<w:p>{ppr}{self.runs(text, lang)}</w:p>" + self.xml[m.start():]
        return True

    def save(self):
        self.files["word/document.xml"] = self.xml.encode("utf-8")
        tmp = self.path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for n, d in self.files.items():
                z.writestr(n, d)
        shutil.move(tmp, self.path)
