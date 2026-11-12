"""Exports PDF, Excel, Word et CSV de la répartition d'une classe."""
import csv
import io
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from django.db.models import Prefetch
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from ..models import Membre, Participation

TERRA = "B4532F"
INK = "1C1F23"

# Même ordre que la liste importée : N°, puis Nom, puis Prénoms.
MEMBER_ORDER = (F("number").asc(nulls_last=True), "last_name", "first_name", "search_name")


@dataclass
class Dataset:
    classe: object
    groupes: list
    sans_groupe: list
    total: int

    @property
    def title(self) -> str:
        return self.classe.name

    @property
    def subtitle(self) -> str:
        theme = getattr(self.classe, "theme", "")
        detail = f" · Thème : {theme}" if theme else ""
        return f"Répartition des groupes{detail} · Export du {timezone.localtime():%d/%m/%Y à %H:%M}"

    def flat(self):
        """Lignes (groupe, thème, ordre, N°, nom, prénoms, sexe, matricule, statut)."""
        rows = []
        for g in self.groupes:
            for m in g.membres.all():
                rows.append((g.short_label, g.theme, g.presentation_order or "", *_identity(m),
                             _member_sex(m), m.reference, m.status_label))
        for m in self.sans_groupe:
            rows.append(("Sans groupe", "", "", *_identity(m), _member_sex(m), m.reference, m.status_label))
        return rows


def _identity(member) -> tuple:
    """(N°, Nom, Prénoms). Sans nom séparé, le nom complet va dans « Nom »."""
    last, first = getattr(member, "last_name", ""), getattr(member, "first_name", "")
    if not (last or first):
        last = member.full_name
    return (getattr(member, "number", None) or "", last, first)


def _member_sex(member) -> str:
    return member.get_sex_display() if hasattr(member, "get_sex_display") else getattr(member, "sex", "")


def collect(classe) -> Dataset:
    activities = list(classe.activites.all())
    if activities:
        groups = []
        total = 0
        for activity in activities:
            dataset = collect_activity(activity)
            total += dataset.total
            for group in dataset.groupes:
                groups.append(ExportGroup(
                    f"{activity.title} · {group.short_label}",
                    f"{activity.title} · {group.label}",
                    group.name,
                    group.theme,
                    group.presentation_order,
                    group.capacity,
                    group.membres,
                ))
            if dataset.sans_groupe:
                groups.append(ExportGroup(
                    f"{activity.title} · Sans groupe",
                    f"{activity.title} · Sans groupe",
                    "",
                    activity.theme,
                    None,
                    None,
                    ExportMembers(dataset.sans_groupe),
                ))
        return Dataset(classe, groups, [], total)

    members = Membre.objects.order_by(*MEMBER_ORDER)
    groupes = list(classe.groupes.filter(activite__isnull=True).prefetch_related(Prefetch("membres", queryset=members)))
    sans = list(classe.membres.sans_groupe().order_by(*MEMBER_ORDER))
    return Dataset(classe, groupes, sans, classe.membres.count())


@dataclass
class ExportMember:
    full_name: str
    reference: str
    status_label: str
    sex: str = ""
    number: int | None = None
    last_name: str = ""
    first_name: str = ""

    def get_sex_display(self) -> str:
        return self.sex


class ExportMembers(list):
    def all(self):
        return self

    def count(self):
        return len(self)


@dataclass
class ExportGroup:
    short_label: str
    label: str
    name: str
    theme: str
    presentation_order: int | None
    capacity: int | None
    membres: ExportMembers


@dataclass
class ExportActivity:
    name: str
    theme: str


def collect_activity(activite) -> Dataset:
    participations = {
        item.membre_id: item
        for item in
        activite.participations.select_related("membre", "groupe").order_by("membre__search_name")
    }
    activity_groups = activite.groupes.order_by(F("presentation_order").asc(nulls_last=True), "number")
    grouped = {group.pk: [] for group in activity_groups}
    unassigned = []
    for roster_member in activite.classe.membres.order_by(*MEMBER_ORDER):
        participation = participations.get(roster_member.pk)
        member = ExportMember(
            full_name=roster_member.full_name,
            reference=roster_member.reference,
            status_label=participation.status_label if participation else "Pas encore répondu",
            sex=_member_sex(roster_member),
            number=roster_member.number,
            last_name=roster_member.last_name,
            first_name=roster_member.first_name,
        )
        if participation and participation.groupe_id:
            grouped[participation.groupe_id].append(member)
        else:
            unassigned.append(member)
    groups = []
    for group in activity_groups:
        short_label = f"Groupe {group.number}"
        label = f"{short_label} ({group.name})" if group.name else short_label
        groups.append(ExportGroup(
            short_label, label, group.name, group.theme, group.presentation_order,
            group.capacity, ExportMembers(grouped[group.pk]),
        ))
    return Dataset(ExportActivity(activite.title, activite.theme), groups, unassigned, activite.classe.membres.count())


def safe_cell(value):
    """Neutralise l'injection de formules dans Excel/CSV."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def filename(classe, ext: str) -> str:
    title = getattr(classe, "name", getattr(classe, "title", "classe"))
    return f"{slugify(title) or 'classe'}-{timezone.localdate():%Y-%m-%d}.{ext}"


# --- CSV --------------------------------------------------------------------
def to_csv(ds: Dataset) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Groupe", "Thème", "Ordre de passage", "N°", "Nom", "Prénoms", "Sexe", "Matricule", "Statut"])
    for row in ds.flat():
        writer.writerow([safe_cell(c) for c in row])
    return out.getvalue().encode("utf-8-sig")


# --- Excel ------------------------------------------------------------------
def to_xlsx(ds: Dataset) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=TERRA)

    def sheet(ws, header, rows, widths):
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment = head_font, head_fill, Alignment(vertical="center")
        for r in rows:
            ws.append([safe_cell(v) for v in r])
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions

    wb = Workbook()
    ws = wb.active
    ws.title = "Résumé"
    summary = [
        (g.short_label, g.name, g.theme, g.presentation_order or "", g.membres.count(), g.capacity or "")
        for g in ds.groupes
    ]
    summary.append(("Sans groupe", "", "", "", len(ds.sans_groupe), ""))
    summary.append(("Total classe", "", "", "", ds.total, ""))
    sheet(ws, ["Groupe", "Nom", "Thème", "Ordre", "Effectif", "Capacité"], summary, [18, 24, 32, 12, 12, 12])
    last = ws.max_row
    for c in ws[last]:
        c.font = Font(bold=True)

    sheet(
        wb.create_sheet("Liste complète"),
        ["Groupe", "Thème", "Ordre de passage", "N°", "Nom", "Prénoms", "Sexe", "Matricule", "Statut"],
        ds.flat(), [18, 30, 16, 8, 24, 34, 12, 18, 24],
    )
    sheet(
        wb.create_sheet("Sans groupe"),
        ["N°", "Nom", "Prénoms", "Sexe", "Matricule", "Statut"],
        [(*_identity(m), _member_sex(m), m.reference, m.status_label) for m in ds.sans_groupe],
        [8, 24, 34, 12, 18, 24],
    )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --- Word -------------------------------------------------------------------
def to_docx(ds: Dataset) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    accent = RGBColor.from_string(TERRA)
    doc.styles["Heading 1"].font.color.rgb = accent

    title = doc.add_heading(ds.title, level=0)
    for run in title.runs:
        run.font.color.rgb = accent
    doc.add_paragraph(ds.subtitle)

    doc.add_heading("Résumé", level=1)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, ("Groupe", "Thème", "Passage", "Effectif", "Capacité")):
        cell.text = text
    for g in ds.groupes:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = g.label, g.theme
        cells[2].text = str(g.presentation_order or "")
        cells[3].text, cells[4].text = str(g.membres.count()), str(g.capacity or "")
    cells = table.add_row().cells
    cells[0].text, cells[3].text = "Sans groupe", str(len(ds.sans_groupe))
    cells = table.add_row().cells
    cells[0].text, cells[3].text = "Total classe", str(ds.total)

    def block(heading, members):
        doc.add_heading(heading, level=1)
        if not members:
            doc.add_paragraph("Personne.")
            return
        t = doc.add_table(rows=1, cols=5)
        t.style = "Light List Accent 1"
        for cell, text in zip(t.rows[0].cells, ("N°", "Nom", "Prénoms", "Sexe", "Matricule")):
            cell.text = text
        for m in members:
            number, last, first = _identity(m)
            cells = t.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = str(number), last, first
            cells[3].text, cells[4].text = _member_sex(m), m.reference

    for g in ds.groupes:
        details = []
        if g.theme:
            details.append(f"Thème : {g.theme}")
        if g.presentation_order:
            details.append(f"Passage : {g.presentation_order}")
        suffix = f" · {' · '.join(details)}" if details else ""
        block(f"{g.label}, {g.membres.count()} personne(s){suffix}", list(g.membres.all()))
    block(f"Sans groupe ou sans réponse, {len(ds.sans_groupe)} personne(s)", ds.sans_groupe)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# --- PDF --------------------------------------------------------------------
_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
    "C:/Windows/Fonts",
]


def _register_fonts():
    """DejaVu gère les lettres africaines (ɔ, ɖ, ŋ…). Repli sur Helvetica si absente."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for folder in _FONT_DIRS:
        regular, bold = Path(folder, "DejaVuSans.ttf"), Path(folder, "DejaVuSans-Bold.ttf")
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("CohorteSans", str(regular)))
            pdfmetrics.registerFont(TTFont("CohorteSans-Bold", str(bold)))
            return "CohorteSans", "CohorteSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def to_pdf(ds: Dataset) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _register_fonts()
    page_size = landscape(A4)
    terra, ink, line = colors.HexColor("#" + TERRA), colors.HexColor("#" + INK), colors.HexColor("#E4E1DC")
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=22, leading=26, textColor=terra, spaceAfter=4)
    sub = ParagraphStyle("sub", fontName=regular, fontSize=9, textColor=colors.HexColor("#5B6168"), spaceAfter=14)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=13, leading=16, textColor=ink, spaceBefore=14, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName=regular, fontSize=10, leading=13, textColor=ink)

    def make_table(header, rows, widths):
        head_cell = ParagraphStyle("table-head", parent=cell, fontName=bold, textColor=colors.white)
        data = [[Paragraph(escape(str(value)), head_cell) for value in header]]
        data.extend([[Paragraph(escape(str(value)), cell) for value in row] for row in rows])
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), terra),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), bold),
            ("FONTNAME", (0, 1), (-1, -1), regular),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, line),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAF8F6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#5B6168"))
        canvas.drawString(2 * cm, 1.2 * cm, ds.title)
        canvas.drawRightString(page_size[0] - 1.5 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    story = [Paragraph(ds.title, h1), Paragraph(ds.subtitle, sub), Paragraph("Résumé", h2)]
    summary = [
        (g.label, g.theme or "—", g.presentation_order or "—", g.membres.count(), g.capacity or "—")
        for g in ds.groupes
    ]
    summary += [("Sans groupe", "—", "—", len(ds.sans_groupe), "—"), ("Total classe", "—", "—", ds.total, "—")]
    story.append(make_table(
        ["Groupe", "Thème", "Passage", "Effectif", "Capacité"],
        summary,
        [5.4 * cm, 8.2 * cm, 2.2 * cm, 2.4 * cm, 2.4 * cm],
    ))

    def block(title, members):
        rows = [(number or "—", last or "—", first or "—", _member_sex(m) or "—", m.reference or "—", m.status_label)
                for m in members for number, last, first in [_identity(m)]] or [("", "Personne", "", "", "", "")]
        return [
            Paragraph(title, h2),
            make_table(["N°", "Nom", "Prénoms", "Sexe", "Matricule", "État"], rows,
                       [1.6 * cm, 6.5 * cm, 9.2 * cm, 2.3 * cm, 3.3 * cm, 3.6 * cm]),
            Spacer(1, 5),
        ]

    for g in ds.groupes:
        group_title = g.label
        if g.theme:
            group_title += f" · Thème : {g.theme}"
        if g.presentation_order:
            group_title += f" · Passage {g.presentation_order}"
        story.extend(block(f"{group_title}, {g.membres.count()} personne(s)", list(g.membres.all())))
    story.extend(block(f"Sans groupe ou sans réponse, {len(ds.sans_groupe)} personne(s)", ds.sans_groupe))

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=page_size, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm,
                      bottomMargin=1.8 * cm, title=ds.title).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


EXPORTERS = {
    "pdf": (to_pdf, "application/pdf"),
    "xlsx": (to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "csv": (to_csv, "text/csv; charset=utf-8"),
}