"""Export conversations to Markdown and PDF."""
from __future__ import annotations

import datetime as _dt
import html
import io
import re
from pathlib import Path
from typing import Any




ACCENT = "#6d4fe0"

INK = "#1b1f2a"

MUTED = "#6b7385"

USER_BG = "#efeafe"

BOT_BG = "#f5f6f9"

def conversation_title(messages: list[dict[str, Any]]) -> str:
    first = next((m for m in messages if m.get("role") == "user"), None)
    if not first:
        return "Conversation"
    text = (first.get("content") or "").strip() or "Conversation"
    return text.splitlines()[0][:90]

def safe_filename(title: str, ext: str) -> str:
    base = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip().replace(" ", "_")[:60] or "conversation"
    return f"KORA_{base}.{ext}"

def _visible(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]

def _fmt_ts(ts: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%d %b %Y, %H:%M")
    except Exception:
        return ""

def _source_label(src: dict[str, Any]) -> str:
    parts = [str(src.get("source") or "Document")]
    if src.get("page") is not None:
        parts.append(f"page {src['page']}")
    if src.get("sheet"):
        parts.append(f"sheet {src['sheet']}")
    if src.get("row") is not None:
        parts.append(f"row {src['row']}")
    if src.get("slide") is not None:
        parts.append(f"slide {src['slide']}")
    if src.get("section"):
        parts.append(str(src["section"]))
    return " · ".join(parts)

def to_markdown(messages: list[dict[str, Any]]) -> str:
    msgs = _visible(messages)
    out = [f"# {conversation_title(messages)}", "", f"*Exported from KORA on {_dt.datetime.now():%d %b %Y, %H:%M}*", ""]
    for m in msgs:
        who = "You" if m["role"] == "user" else "KORA"
        stamp = _fmt_ts(m.get("ts"))
        out += [f"## {who}" + (f"  \n*{stamp}*" if stamp else ""), ""]
        if m.get("content"):
            out += [m["content"].strip(), ""]
        srcs = m.get("sources") or []
        if srcs:
            out += ["**Sources**", ""] + [f"- {_source_label(s)}" for s in srcs] + [""]
    return "\n".join(out).rstrip() + "\n"

_FONT_CANDIDATES = [
    # (regular, bold, italic, mono)
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf", "C:/Windows/Fonts/consola.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/cour.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Italic.ttf", "/System/Library/Fonts/Supplemental/Courier New.ttf"),
]

_fonts: dict[str, str] | None = None

def _register_fonts() -> dict[str, str]:
    """Use a real Unicode TTF when the OS has one (so accents, ₹, €, —, etc. render);
    otherwise fall back to the built-in Helvetica set."""
    global _fonts
    if _fonts is not None:
        return _fonts
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for reg, bold, ital, mono in _FONT_CANDIDATES:
        if Path(reg).exists() and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("KoraSans", reg))
                pdfmetrics.registerFont(TTFont("KoraSans-Bold", bold))
                pdfmetrics.registerFont(TTFont("KoraSans-Italic", ital if Path(ital).exists() else reg))
                pdfmetrics.registerFont(TTFont("KoraMono", mono if Path(mono).exists() else reg))
                pdfmetrics.registerFontFamily("KoraSans", normal="KoraSans", bold="KoraSans-Bold", italic="KoraSans-Italic", boldItalic="KoraSans-Bold")
                _fonts = {"sans": "KoraSans", "bold": "KoraSans-Bold", "mono": "KoraMono", "unicode": "1"}
                return _fonts
            except Exception:
                continue
    _fonts = {"sans": "Helvetica", "bold": "Helvetica-Bold", "mono": "Courier", "unicode": ""}
    return _fonts

def _clean(text: str, unicode_ok: bool) -> str:
    text = re.sub(r"[\U00010000-\U0010FFFF]", "", text)           # emoji etc. -> not renderable in PDF fonts
    text = text.replace("\u200b", "").replace("\ufe0f", "")
    if not unicode_ok:
        text = text.encode("latin-1", "replace").decode("latin-1")
    return text

def _inline(text: str) -> str:
    """Markdown inline -> reportlab mini-HTML."""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r'<font face="KORA_MONO" color="#b03a62">\1</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" color="%s">\1</a>' % ACCENT, text)
    return text

def to_pdf(messages: list[dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph,
                                    Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle)

    f = _register_fonts()
    uni = bool(f["unicode"])
    title = _clean(conversation_title(messages), uni)
    msgs = _visible(messages)

    def mono(t: str) -> str:
        return t.replace("KORA_MONO", f["mono"])

    base = ParagraphStyle("base", fontName=f["sans"], fontSize=10, leading=15, textColor=colors.HexColor(INK), alignment=TA_LEFT)
    st = {
        "body": base,
        "h1": ParagraphStyle("h1", parent=base, fontName=f["bold"], fontSize=14, leading=18, spaceBefore=8, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName=f["bold"], fontSize=12, leading=16, spaceBefore=6, spaceAfter=3),
        "h3": ParagraphStyle("h3", parent=base, fontName=f["bold"], fontSize=10.5, leading=15, spaceBefore=4, spaceAfter=2),
        "li": ParagraphStyle("li", parent=base, spaceAfter=2),
        "code": ParagraphStyle("code", parent=base, fontName=f["mono"], fontSize=8.5, leading=11.5, backColor=colors.HexColor("#eceef3"), borderPadding=6, spaceBefore=4, spaceAfter=8, leftIndent=4),
        "who": ParagraphStyle("who", parent=base, fontName=f["bold"], fontSize=10, leading=13),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8, leading=11, textColor=colors.HexColor(MUTED)),
        "src": ParagraphStyle("src", parent=base, fontSize=8.5, leading=12, textColor=colors.HexColor(MUTED)),
        "tag": ParagraphStyle("tag", parent=base, fontSize=8.5, leading=12, textColor=colors.HexColor(ACCENT)),
        "title": ParagraphStyle("title", parent=base, fontName=f["bold"], fontSize=20, leading=25, spaceAfter=4),
    }

    buf = io.BytesIO()
    width, height = A4
    margin = 20 * mm
    content_w = width - 2 * margin
    bubble_w = content_w - 8 * mm

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(f["sans"], 8)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(margin, 12 * mm, "Exported from KORA")
        canvas.drawRightString(width - margin, 12 * mm, f"Page {doc.page}")
        canvas.setStrokeColor(colors.HexColor("#e1e4ec"))
        canvas.line(margin, 16 * mm, width - margin, 16 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=18 * mm, bottomMargin=22 * mm,
                            title=title, author="KORA", subject="KORA conversation export")

    def md_blocks(text: str, avail: float) -> list:
        """Small Markdown subset: headings, bullets, numbered lists, tables, code fences, paragraphs."""
        text = _clean(text.replace("\r\n", "\n"), uni)
        lines = text.split("\n")
        out: list = []
        i = 0
        para: list[str] = []

        def flush_para():
            if para:
                out.append(Paragraph(mono(_inline(" ".join(s.strip() for s in para))), st["body"]))
                out.append(Spacer(1, 4))
                para.clear()

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith("```"):
                flush_para()
                code = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code.append(lines[i])
                    i += 1
                i += 1
                wrapped = []
                for c in code:  # hard-wrap so long lines don't run off the page
                    c = c.replace("\t", "    ")
                    while len(c) > 78:
                        wrapped.append(c[:92]); c = c[92:]
                    wrapped.append(c)
                out.append(Preformatted("\n".join(wrapped) or " ", st["code"]))
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                flush_para()
                level = min(len(m.group(1)), 3)
                out.append(Paragraph(mono(_inline(m.group(2))), st[f"h{level}"]))
                i += 1
                continue
            if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
                flush_para()
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    if not re.match(r"^\s*\|?\s*:?-{2,}", lines[i]):
                        rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                ncol = max(len(r) for r in rows)
                cell = ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11.5)
                head = ParagraphStyle("head", parent=cell, fontName=f["bold"])
                data = [[Paragraph(mono(_inline(c)), head if ri == 0 else cell) for c in r + [""] * (ncol - len(r))] for ri, r in enumerate(rows)]
                t = Table(data, colWidths=[avail / ncol] * ncol, repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e9f2")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd4e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                out += [t, Spacer(1, 6)]
                continue
            lm = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$", line)
            if lm:
                flush_para()
                ordered = lm.group(2)[0].isdigit()
                items = []
                while i < len(lines):
                    lm = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$", lines[i])
                    if not lm:
                        break
                    items.append(ListItem(Paragraph(mono(_inline(lm.group(3))), st["li"]), leftIndent=14))
                    i += 1
                out.append(ListFlowable(items, bulletType="1" if ordered else "bullet", start=1 if ordered else "•",
                                        bulletFontName=f["sans"], bulletFontSize=9, leftIndent=14, bulletColor=colors.HexColor(ACCENT)))
                out.append(Spacer(1, 4))
                continue
            if not stripped:
                flush_para()
            else:
                para.append(stripped)
            i += 1
        flush_para()
        return out

    def message_block(m: dict[str, Any]) -> list:
        is_user = m["role"] == "user"
        who = "You" if is_user else "KORA"
        accent = colors.HexColor(ACCENT if is_user else "#2f9e75")
        stamp = _fmt_ts(m.get("ts"))
        head = Paragraph(f'{who}<font name="{f["sans"]}" size="8" color="{MUTED}">{"   " + stamp if stamp else ""}</font>', st["who"])
        inner: list = [head, Spacer(1, 4)]
        if m.get("content"):
            inner += md_blocks(m["content"], bubble_w - 6)
        srcs = m.get("sources") or []
        if srcs:
            inner += [Spacer(1, 2), HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#d5d9e4")), Spacer(1, 3),
                      Paragraph("<b>Sources</b>", st["src"])]
            for s in srcs:
                inner.append(Paragraph("• " + html.escape(_clean(_source_label(s), uni)), st["src"]))
        # one-cell table gives the tinted "bubble" with a coloured left rule; it may split across pages
        box = Table([[inner]], colWidths=[content_w])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(USER_BG if is_user else BOT_BG)),
            ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [box, Spacer(1, 9)]

    story: list = [
        Paragraph(html.escape(title), st["title"]),
        Paragraph(f"KORA conversation · {len(msgs)} message{'s' if len(msgs) != 1 else ''} · exported {_dt.datetime.now():%d %b %Y, %H:%M}", st["meta"]),
        Spacer(1, 6), HRFlowable(width="100%", thickness=1.2, color=colors.HexColor(ACCENT)), Spacer(1, 12),
    ]
    for m in msgs:
        story += message_block(m)
    if not msgs:
        story.append(Paragraph("This conversation has no messages yet.", st["body"]))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
