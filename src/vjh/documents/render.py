"""Render CV and cover letter plans to .docx (python-docx) and .pdf (Microsoft Word).

Facts come from the master profile and are never taken from the AI plan.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..models.profile import HomeLocation, MasterProfile
from ..models.settings import CVConventions
from .plan import CVPlan, LetterPlan

ACCENT = RGBColor(0x1F, 0x3A, 0x5F)

LABELS = {
    "en": dict(profile="Profile", experience="Professional Experience", education="Education",
               skills="Skills", languages="Languages", personal="Personal details", born="Date of birth",
               nationality="Nationality", work="Work permit",
               licence="Driving licence", since="since", native="Native / bilingual",
               completed="completed", phone="Phone", email="Email"),
    "de": dict(profile="Profil", experience="Berufserfahrung", education="Ausbildung",
               skills="Kenntnisse", languages="Sprachen", personal="Persönliche Daten", born="Geburtsdatum",
               nationality="Staatsbürgerschaft", work="Arbeitserlaubnis",
               licence="Führerschein", since="seit", native="Muttersprache / zweisprachig",
               completed="Abschluss", phone="Telefon", email="E-Mail"),
    "it": dict(profile="Profilo", experience="Esperienza professionale", education="Istruzione",
               skills="Competenze", languages="Lingue", personal="Dati personali", born="Data di nascita",
               nationality="Cittadinanza", work="Permesso di lavoro",
               licence="Patente", since="dal", native="Madrelingua / bilingue",
               completed="conseguita", phone="Telefono", email="E-mail"),
}
# Translations for the CV (add more as needed; untranslated names are shown as written).
NATIONALITY = {
    "de": {"Italian": "Italienisch", "Dutch": "Niederländisch", "German": "Deutsch", "Austrian": "Österreichisch",
           "French": "Französisch", "Spanish": "Spanisch", "Portuguese": "Portugiesisch", "Polish": "Polnisch",
           "Belgian": "Belgisch", "British": "Britisch", "American": "US-amerikanisch"},
    "it": {"Italian": "Italiana", "Dutch": "Olandese", "German": "Tedesca", "Austrian": "Austriaca",
           "French": "Francese", "Spanish": "Spagnola", "Portuguese": "Portoghese", "Polish": "Polacca",
           "Belgian": "Belga", "British": "Britannica", "American": "Statunitense"},
}
LANGUAGE_NAME = {
    "de": {"Italian": "Italienisch", "Dutch": "Niederländisch", "English": "Englisch", "German": "Deutsch",
           "French": "Französisch", "Spanish": "Spanisch", "Portuguese": "Portugiesisch", "Polish": "Polnisch"},
    "it": {"Italian": "Italiano", "Dutch": "Olandese", "English": "Inglese", "German": "Tedesco",
           "French": "Francese", "Spanish": "Spagnolo", "Portuguese": "Portoghese", "Polish": "Polacco"},
}
MONTHS = {"en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                 "October", "November", "December"],
          "de": ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
                 "Oktober", "November", "Dezember"],
          "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                 "settembre", "ottobre", "novembre", "dicembre"]}


def city_name(home: HomeLocation | None, lang: str, names: dict[str, str] | None) -> str:
    """The home city in the document language (settings: candidate.home_city_names)."""
    names = names or {}
    return names.get(lang) or (home.city if home else "")


def ym(v: str | None) -> str:
    return f"{v[5:7]}/{v[:4]}" if v else "?"


def long_date(d: date, lang: str) -> str:
    m = MONTHS[lang][d.month - 1]
    return f"{d.day}. {m} {d.year}" if lang == "de" else f"{d.day} {m} {d.year}"


def birth_date(iso: str, lang: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS['en'][int(m) - 1]} {y}" if lang == "en" else f"{d}.{m}.{y}"


def language_line(lang_skill, lang: str) -> str:
    L = LABELS[lang]
    name = LANGUAGE_NAME.get(lang, {}).get(lang_skill.language, lang_skill.language)
    if lang_skill.level == "native":
        level = L["native"]
    else:
        level = lang_skill.level + (f" - {lang_skill.certificate}" if lang_skill.certificate else "")
    if lang_skill.note:
        level += f" - {lang_skill.note}"
    return f"{name}: {level}"


# ---------------- low-level helpers ----------------

def _base(doc: Document) -> None:
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    pf = st.paragraph_format
    pf.space_after = Pt(2)
    pf.space_before = Pt(0)
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Cm(1.8)
        s.left_margin = s.right_margin = Cm(2.0)


def _heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(11)
    r.font.color.rgb = ACCENT
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "1F3A5F")):
        bottom.set(qn(k), v)
    border.append(bottom)
    p._p.get_or_add_pPr().append(border)


def _bullet(doc: Document, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(1)
    p.add_run(text)


def _no_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def _line(cell_or_doc, text: str, bold=False, size=None, color=None, align=None):
    p = cell_or_doc.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    if align:
        p.alignment = align
    return p


# ---------------- CV ----------------

def render_cv(plan: CVPlan, profile: MasterProfile, home: HomeLocation | None, lang: str,
              out_path: Path, photo: Path | None, conventions: CVConventions | None = None,
              city_names: dict[str, str] | None = None) -> Path:
    conv = conventions or CVConventions()
    L = LABELS[lang]
    doc = Document()
    _base(doc)
    pers = profile.personal
    exp_by = {x.id: x for x in profile.experiences}
    edu_by = {e.id: e for e in profile.education}

    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_borders(table)
    left, right = table.rows[0].cells
    left.width, right.width = Cm(13.0), Cm(4.0)
    left.paragraphs[0].add_run(pers.full_name).bold = True
    left.paragraphs[0].runs[0].font.size = Pt(22)
    left.paragraphs[0].runs[0].font.color.rgb = ACCENT
    if home:
        street = f"{home.street}, " if home.street else ""
        _line(left, f"{street}{home.postcode or ''} {city_name(home, lang, city_names)}".strip())
    contact = " | ".join(x for x in (f"{L['phone']}: {pers.phone}" if pers.phone else "",
                                     f"{L['email']}: {pers.email}" if pers.email else "") if x)
    if contact:
        _line(left, contact)
    details = []
    if conv.date_of_birth and pers.date_of_birth:
        details.append(f"{L['born']}: {birth_date(pers.date_of_birth, lang)}")
    if conv.nationality and pers.nationalities:
        nat = ", ".join(NATIONALITY.get(lang, {}).get(n, n) for n in pers.nationalities)
        details.append(f"{L['nationality']}: {nat}")
    if conv.work_permit and pers.right_to_work and pers.right_to_work_note:
        details.append(f"{L['work']}: {pers.right_to_work_note}")
    if conv.driving_licence and any("driving licence" in e.claim.lower() for e in profile.evidence):
        details.append(f"{L['licence']}: B")
    for d in details:
        _line(left, d, size=9.5)
    if conv.photo and photo and photo.exists():
        right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        right.paragraphs[0].add_run().add_picture(str(photo), width=Cm(3.2))

    if plan.summary.text:
        _heading(doc, L["profile"])
        doc.add_paragraph(plan.summary.text)

    _heading(doc, L["experience"])
    for block in plan.experience:
        x = exp_by[block.experience_id]
        dates = f"{L['since']} {ym(x.start)}" if x.current else f"{ym(x.start)} - {ym(x.end)}"
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(5)
        p.add_run(x.title).bold = True
        p.add_run(f"  |  {x.employer}")
        p.add_run(f"  |  {dates}").italic = True
        sub = ", ".join(v for v in (x.location, x.employment_type) if v)
        if sub:
            _line(doc, sub, size=9.5)
        for b in block.bullets:
            _bullet(doc, b.text)

    _heading(doc, L["education"])
    for block in plan.education:
        e = edu_by[block.education_id]
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(5)
        p.add_run(f"{e.degree} - {e.field}").bold = True
        p.add_run(f"  |  {e.institution}  |  ")
        p.add_run(f"{L['completed']} {ym(e.completed)}").italic = True
        for b in block.bullets:
            _bullet(doc, b.text)

    if plan.skills:
        _heading(doc, L["skills"])
        for g in plan.skills:
            p = doc.add_paragraph()
            p.add_run(f"{g.group}: ").bold = True
            p.add_run("; ".join(i.text for i in g.items))

    _heading(doc, L["languages"])
    doc.add_paragraph("  |  ".join(language_line(s, lang) for s in profile.languages))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


# ---------------- cover letter ----------------

def clean_location(loc: str | None) -> str | None:
    """'Graz, Graz, Austria' -> 'Graz, Austria' (repeated parts removed)."""
    if not loc:
        return None
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    return ", ".join(dict.fromkeys(parts)) or None


# compact levels: (font size, paragraph spacing, top/bottom margin cm)
LETTER_LAYOUTS = [(11, 8, 1.8), (10.5, 6, 1.5), (10, 4, 1.2)]


def render_letter(plan: LetterPlan, profile: MasterProfile, home: HomeLocation | None, lang: str,
                  out_path: Path, employer: str, job_location: str | None, today: date,
                  compact: int = 0, city_names: dict[str, str] | None = None) -> Path:
    size, gap, margin = LETTER_LAYOUTS[min(compact, len(LETTER_LAYOUTS) - 1)]
    doc = Document()
    _base(doc)
    doc.styles["Normal"].font.size = Pt(size)
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Cm(margin)
    pers = profile.personal
    _line(doc, pers.full_name, bold=True, size=size + 2, color=ACCENT)
    contact = []
    if home:
        city = city_name(home, lang, city_names)
        contact.append(", ".join(x for x in (home.street, f"{home.postcode or ''} {city}".strip()) if x))
    contact += [v for v in (pers.phone, pers.email) if v]
    _line(doc, " | ".join(contact))
    doc.add_paragraph()
    _line(doc, employer, bold=True)
    if clean_location(job_location):
        _line(doc, clean_location(job_location))
    place = city_name(home, lang, city_names)
    _line(doc, f"{place}, {long_date(today, lang)}" if place else long_date(today, lang),
          align=WD_ALIGN_PARAGRAPH.RIGHT)
    p = _line(doc, plan.subject, bold=True)
    p.paragraph_format.space_before = Pt(gap)
    p.paragraph_format.space_after = Pt(gap)
    doc.add_paragraph(plan.salutation).paragraph_format.space_after = Pt(gap)
    for para in plan.paragraphs:
        p = doc.add_paragraph(para.text)
        p.paragraph_format.space_after = Pt(gap)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    doc.add_paragraph(plan.sign_off).paragraph_format.space_after = Pt(gap * 2)
    doc.add_paragraph(pers.full_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


# ---------------- reading back, PDF ----------------

def docx_text(path: Path) -> str:
    """All text in the document, in reading order (body paragraphs and table cells)."""
    doc = Document(str(path))
    parts: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            parts.append("".join(t.text or "" for t in child.iter(qn("w:t"))))
        elif child.tag == qn("w:tbl"):
            for p in child.iter(qn("w:p")):
                parts.append("".join(t.text or "" for t in p.iter(qn("w:t"))))
    return "\n".join(x for x in parts if x.strip())


def to_pdf(docx_path: Path) -> tuple[Path | None, str | None]:
    """Convert with Microsoft Word (docx2pdf). Returns (pdf_path, error)."""
    pdf = docx_path.with_suffix(".pdf")
    import os
    os.environ.setdefault("TQDM_DISABLE", "1")     # no progress bars on the console
    try:
        from docx2pdf import convert
        convert(str(docx_path), str(pdf))
    except Exception as exc:  # noqa: BLE001 - Word missing/busy: keep the .docx, report it
        return None, f"PDF conversion failed ({type(exc).__name__}: {exc}). The .docx is still available."
    return (pdf, None) if pdf.exists() else (None, "PDF conversion produced no file")


def pdf_pages(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)
