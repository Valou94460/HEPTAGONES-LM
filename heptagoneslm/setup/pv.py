"""Procès-verbal de recette (document Word .docx), à signer avant la mise en production.

Généré sans dépendance (OOXML écrit directement) : identification de l'installation, verdict,
chaque point de contrôle avec son statut, comparaisons détaillées avec les serveurs de licences,
vérification de l'installation, réserves, et cadre de signature (rédacteur, client).
Le document s'ouvre dans Word et LibreOffice, et s'exporte en PDF depuis ces logiciels.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

from heptagoneslm import __version__
from heptagoneslm.setup.recette import plural

STATUS_TEXT = {"ok": "Conforme", "warning": "Réserve", "error": "Non conforme", "info": "Information"}
STATUS_FILL = {"ok": "D8F0DC", "warning": "FCEBC2", "error": "F6D0D0", "info": "E6EAF0"}
ACCENT = "1F4E99"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------------- briques OOXML


def _x(text) -> str:
    return escape(str(text if text is not None else ""), {'"': "&quot;"})


def _run(text: str, bold=False, size=None, color=None, italic=False, font=None) -> str:
    props = ""
    if font:      # cases à cocher : police qui contient le caractère (Calibri ne l'a pas)
        props += f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    parts = str(text).split("\n")
    body = '<w:br/>'.join(f'<w:t xml:space="preserve">{_x(p)}</w:t>' for p in parts)
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{body}</w:r>"


def _p(content: str = "", style: str | None = None, align: str | None = None, space_after=None,
       keep_next=False) -> str:
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if keep_next:
        props += "<w:keepNext/>"
    if space_after is not None:
        props += f'<w:spacing w:after="{space_after}"/>'
    if align:
        props += f'<w:jc w:val="{align}"/>'
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{content}</w:p>"


def _text_p(text: str, **kw) -> str:
    style = kw.pop("style", None)
    align = kw.pop("align", None)
    space_after = kw.pop("space_after", None)
    keep_next = kw.pop("keep_next", False)
    return _p(_run(text, **kw), style=style, align=align, space_after=space_after, keep_next=keep_next)


def _cell(content, width: int, fill: str | None = None, bold=False, size=18, header=False) -> str:
    shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    paras = content if isinstance(content, list) else [content]
    body = "".join(_p(_run(t, bold=bold or header, size=size, color="FFFFFF" if header else None), space_after=0)
                   for t in paras) or _p()
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}'
            f'<w:tcMar><w:top w:w="40" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/></w:tcMar></w:tcPr>{body}</w:tc>')


def _table(columns: list[str], rows: list[list], widths: list[int], fills: list | None = None, size=18) -> str:
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    head = ('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>'
            + "".join(_cell(c, w, fill=ACCENT, header=True, size=size) for c, w in zip(columns, widths)) + "</w:tr>")
    body = ""
    for i, row in enumerate(rows):
        row_fills = (fills[i] if fills else None) or [None] * len(row)
        body += ("<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                 + "".join(_cell(v, w, fill=f, size=size) for v, w, f in zip(row, widths, row_fills)) + "</w:tr>")
    borders = "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="B7C2D0"/>'
                      for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
            f'<w:tblLayout w:type="fixed"/><w:tblLook w:val="04A0"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
            f"{head}{body}</w:tbl>" + _p(space_after=60))


# -------------------------------------------------------------------------------------- document


def build_pv(state: dict, meta: dict | None = None) -> bytes:
    """``state`` : RecetteRun.state() ; ``meta`` : client, rédacteur, valideur, commentaire."""
    meta = meta or {}
    items = state.get("items", [])
    v = state.get("verdict", {})
    started = _date(state.get("started"))
    body = []
    body.append(_text_p("Procès-verbal de recette", style="Title"))
    subtitle = [f"HeptagonesLM {state.get('version') or __version__}", meta.get("client"),
                f"serveur {state.get('host') or '?'}", started]
    body.append(_text_p(" · ".join(x for x in subtitle if x), color="5A6578", size=20, space_after=240))

    body.append(_text_p("1. Identification", style="Heading1", keep_next=True))
    ident = [
        ["Client / site", meta.get("client") or "—"],
        ["Serveur HeptagonesLM", state.get("host") or "—"],
        ["Version installée", state.get("version") or __version__],
        ["Configuration", state.get("config_path") or "—"],
        ["Date de la recette", started],
        ["Période de collecte contrôlée", plural(int(state.get("hours", 24)), "heure")],
        ["Preuves enregistrées", state.get("folder") or "—"],
        ["Sorties anonymisées", "Oui" if state.get("anonymized") else "Non"],
    ]
    body.append(_table(["Élément", "Valeur"], ident, [2800, 6838]))

    body.append(_text_p("2. Synthèse", style="Heading1", keep_next=True))
    c = v.get("counts", {})
    body.append(_p(_run("Verdict : ", bold=True) + _run(v.get("label", "—"), bold=True,
                                                          color={"ok": "1E7B34", "warning": "9A6700",
                                                                 "error": "B42318"}.get(v.get("level"), None))))
    pending = v.get("pending") or 0
    body.append(_text_p(f"{plural(c.get('ok', 0), 'point conforme', 'points conformes')}, "
                        f"{plural(c.get('warning', 0), 'réserve')}, {plural(c.get('error', 0), 'non-conformité')}, "
                        f"{plural(c.get('info', 0), 'information')}"
                        + (f" ; {plural(pending, 'point')} en attente de confirmation par l'opérateur." if pending
                           else "."), space_after=120))
    if meta.get("comment"):
        body.append(_p(_run("Commentaire : ", bold=True) + _run(meta["comment"])))

    body.append(_text_p("3. Points de contrôle", style="Heading1", keep_next=True))
    rows, fills = [], []
    for it in items:
        comment = [it.get("detail") or ""] + list(it.get("details") or [])[:6]
        if it.get("confirm"):
            answer = {True: "oui", False: "non", None: "en attente"}[it.get("confirmed")]
            comment.append(f"Confirmation de l'opérateur ({it['confirm']}) : {answer}")
        rows.append([it.get("category", ""), it.get("label", ""), STATUS_TEXT.get(it.get("status"), it.get("status")),
                     [t for t in comment if t]])
        fills.append([None, None, STATUS_FILL.get(it.get("status")), None])
    body.append(_table(["Domaine", "Point de contrôle", "Résultat", "Constat"], rows, [1900, 2600, 1300, 3838], fills, 16))

    tables = [it for it in items if it.get("table") and it["id"].startswith("srv:")]
    if tables:
        body.append(_text_p("4. Comparaison avec les serveurs de licences", style="Heading1", keep_next=True))
        body.append(_text_p("Jetons lus directement dans la sortie du serveur de licences (lmstat / rlmstat), juste "
                            "après une collecte planifiée, comparés à l'enregistrement de HeptagonesLM.",
                            italic=True, color="5A6578", size=18))
        for it in tables:
            body.append(_text_p(it["label"], style="Heading2", keep_next=True))
            t = it["table"]
            if not t["rows"]:      # serveur sans licence (fichier de test) : pas de tableau vide
                body.append(_text_p("Aucune feature déclarée par ce serveur : rien à comparer.", italic=True,
                                    color="5A6578", size=18))
                continue
            fills = [[None] * 6 + [STATUS_FILL["ok"] if r[-1] == "identique" else STATUS_FILL["warning"]]
                     for r in t["rows"]]
            body.append(_table(t["columns"], t["rows"], [1900, 1100, 1250, 1050, 1250, 1100, 1988], fills, 15))

    inst = next((it for it in items if it.get("id") == "installation" and it.get("table")), None)
    if inst:
        body.append(_text_p("5. Vérification de l'installation", style="Heading1", keep_next=True))
        t = inst["table"]
        rev = {v_: k for k, v_ in STATUS_TEXT.items()}
        status_of = {"Conforme": "ok", "Avertissement": "warning", "Erreur": "error", "Information": "info"}
        fills = [[None, None, STATUS_FILL.get(status_of.get(r[2], rev.get(r[2], ""))), None] for r in t["rows"]]
        body.append(_table(t["columns"], t["rows"], [1900, 2700, 1300, 3738], fills, 16))

    reserves = [it for it in items if it.get("status") in ("warning", "error")]
    body.append(_text_p("6. Réserves et actions correctives", style="Heading1", keep_next=True))
    if reserves:
        rows = [[it.get("label", ""), STATUS_TEXT.get(it["status"]), it.get("detail") or "", "", ""] for it in reserves]
        body.append(_table(["Point", "Gravité", "Constat", "Action / responsable", "Échéance"], rows,
                           [2300, 1200, 3138, 1900, 1100], [[None, STATUS_FILL[it["status"]], None, None, None]
                                                            for it in reserves], 16))
    else:
        body.append(_text_p("Aucune réserve."))

    body.append(_text_p("7. Décision et signatures", style="Heading1", keep_next=True))
    body.append(_text_p("Le client prononce la recette :", keep_next=True))
    for choice in ("sans réserve, mise en production autorisée", "avec les réserves listées au § 6, mise en "
                   "production autorisée", "refusée, nouvelle recette après correction"):
        body.append(_p(_run("☐", font="Segoe UI Symbol") + _run(f"  {choice}"), space_after=40, keep_next=True))
    sign = [["Rédigé par (intégrateur)", meta.get("redacteur") or "", "", ""],
            ["Validé par (client)", meta.get("valideur") or "", "", ""]]
    body.append(_table(["Rôle", "Nom", "Date", "Visa"], sign, [2600, 2900, 1700, 2438], size=18))
    body.append(_text_p(f"Document généré par HeptagonesLM-Setup {__version__} le "
                        f"{datetime.now():%d/%m/%Y à %H:%M}. Les preuves (sorties brutes des serveurs de licences, "
                        f"rapport Excel, résultats détaillés) sont conservées dans {state.get('folder') or '—'}.",
                        italic=True, color="5A6578", size=16))
    return _package("".join(body), meta, started)


def _date(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso or "—")


STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W_NS}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>
<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="fr-FR"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:color w:val="{ACCENT}"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:color w:val="{ACCENT}"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
</w:styles>"""

FOOTER = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="{W_NS}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:t xml:space="preserve">PV de recette HeptagonesLM · page </w:t></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:t xml:space="preserve"> sur </w:t></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r>
<w:r><w:rPr><w:color w:val="7A8496"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>
</w:p></w:ftr>"""


def _package(body_xml: str, meta: dict, started: str) -> bytes:
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W_NS}" '
                f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<w:body>{body_xml}<w:sectPr><w:footerReference w:type=\"default\" r:id=\"rIdFooter\"/>"
                f'<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" '
                f'w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.wordprocessingml.footer+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
                     'package.core-properties+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
            'core-properties" Target="docProps/core.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/footer" Target="footer1.xml"/></Relationships>')
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>PV de recette HeptagonesLM {_x(started)}</dc:title>"
            f"<dc:creator>{_x(meta.get('redacteur') or 'HeptagonesLM-Setup')}</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/footer1.xml", FOOTER)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()
