"""Exports PDF, Excel, Word et CSV de la répartition d'une classe."""
import csv
import io
from dataclasses import dataclass
from pathlib import Path

from django.db.models import Prefetch
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from ..models import Membre, Participation

TERRA = "B4532F"
INK = "1C1F23"


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
        return f"Répartition en groupes. Export du {timezone.localtime():%d/%m/%Y à %H:%M}"

    def flat(self):
        """Lignes (groupe, nom, matricule, statut) : groupes dans l'ordre, puis sans groupe."""
        rows = []
        for g in self.groupes:
            for m in g.membres.all():
                rows.append((g.short_label, m.full_name, m.reference, "Affecté"))
        for m in self.sans_groupe:
            rows.append(("Sans groupe", m.full_name, m.reference, m.status_label))
        return rows


def collect(classe) -> Dataset:
    members = Membre.objects.order_by("search_name")
    groupes = list(classe.groupes.prefetch_related(Prefetch("membres", queryset=members)))
    sans = list(classe.membres.sans_groupe().order_by("search_name"))
    return Dataset(classe, groupes, sans, classe.membres.count())


@dataclass
class ExportMember:
    full_name: str
    reference: str
    status_label: str


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
    capacity: int | None
    membres: ExportMembers


@dataclass
class ExportActivity:
    name: str


def collect_activity(activite) -> Dataset:
    participations = list(
        activite.participations.select_related("membre", "groupe").order_by("membre__search_name")
    )
    activity_groups = activite.groupes.order_by(F("presentation_order").asc(nulls_last=True), "number")
    grouped = {group.pk: [] for group in activity_groups}
    unassigned = []
    for participation in participations:
        member = ExportMember(
            participation.membre.full_name,
            participation.membre.reference,
            participation.status_label,
        )
        if participation.groupe_id:
            grouped[participation.groupe_id].append(member)
        else:
            unassigned.append(member)
    groups = []
    for group in activity_groups:
        short_label = f"Groupe {group.number}"
        label = f"{short_label} ({group.name})" if group.name else short_label
        groups.append(ExportGroup(short_label, label, group.name, group.capacity, ExportMembers(grouped[group.pk])))
    return Dataset(ExportActivity(activite.title), groups, unassigned, len(participations))


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
    writer.writerow(["Groupe", "Nom", "Matricule", "Statut"])
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
    summary = [(g.short_label, g.name, g.membres.count(), g.capacity or "") for g in ds.groupes]
    summary.append(("Sans groupe", "", len(ds.sans_groupe), ""))
    summary.append(("Total", "", ds.total, ""))
    sheet(ws, ["Groupe", "Nom du groupe", "Effectif", "Capacité"], summary, [18, 28, 12, 12])
    last = ws.max_row
    for c in ws[last]:
        c.font = Font(bold=True)

    sheet(wb.create_sheet("Liste complète"), ["Groupe", "Nom", "Matricule", "Statut"], ds.flat(), [18, 38, 16, 22])
    sheet(
        wb.create_sheet("Sans groupe"),
        ["Nom", "Matricule", "Statut"],
        [(m.full_name, m.reference, m.status_label) for m in ds.sans_groupe],
        [38, 16, 22],
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

    title = doc.add_heading(ds.title, level=0)
    for run in title.runs:
        run.font.color.rgb = accent
    doc.add_paragraph(ds.subtitle)

    doc.add_heading("Résumé", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, ("Groupe", "Effectif", "Capacité")):
        cell.text = text
    for g in ds.groupes:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = g.label, str(g.membres.count()), str(g.capacity or "")
    cells = table.add_row().cells
    cells[0].text, cells[1].text = "Sans groupe", str(len(ds.sans_groupe))
    cells = table.add_row().cells
    cells[0].text, cells[1].text = "Total", str(ds.total)

    def block(heading, members):
        doc.add_heading(heading, level=1)
        if not members:
            doc.add_paragraph("Personne.")
            return
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light List Accent 1"
        for cell, text in zip(t.rows[0].cells, ("N°", "Nom", "Matricule")):
            cell.text = text
        for i, m in enumerate(members, 1):
            cells = t.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = str(i), m.full_name, m.reference

    for g in ds.groupes:
        block(f"{g.label}, {g.membres.count()} personne(s)", list(g.membres.all()))
    block(f"Sans groupe, {len(ds.sans_groupe)} personne(s)", ds.sans_groupe)

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
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _register_fonts()
    terra, ink, line = colors.HexColor("#" + TERRA), colors.HexColor("#" + INK), colors.HexColor("#E4E1DC")
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=22, leading=26, textColor=terra, spaceAfter=4)
    sub = ParagraphStyle("sub", fontName=regular, fontSize=9, textColor=colors.HexColor("#5B6168"), spaceAfter=14)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=13, leading=16, textColor=ink, spaceBefore=14, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName=regular, fontSize=10, leading=13, textColor=ink)

    def make_table(header, rows, widths):
        data = [header] + [[Paragraph(str(c), cell) if i else str(c) for i, c in enumerate(r)] for r in rows]
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
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#5B6168"))
        canvas.drawString(2 * cm, 1.2 * cm, ds.title)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    story = [Paragraph(ds.title, h1), Paragraph(ds.subtitle, sub), Paragraph("Résumé", h2)]
    summary = [(g.label, g.membres.count(), g.capacity or "") for g in ds.groupes]
    summary += [("Sans groupe", len(ds.sans_groupe), ""), ("Total", ds.total, "")]
    story.append(make_table(["Groupe", "Effectif", "Capacité"], summary, [9 * cm, 3 * cm, 3 * cm]))

    def block(title, members):
        rows = [(i, m.full_name, m.reference) for i, m in enumerate(members, 1)] or [("", "Personne", "")]
        return KeepTogether([
            Paragraph(title, h2),
            make_table(["N°", "Nom", "Matricule"], rows, [1.4 * cm, 11 * cm, 4.2 * cm]),
            Spacer(1, 4),
        ])

    for g in ds.groupes:
        story.append(block(f"{g.label}, {g.membres.count()} personne(s)", list(g.membres.all())))
    story.append(block(f"Sans groupe, {len(ds.sans_groupe)} personne(s)", ds.sans_groupe))

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                      bottomMargin=2 * cm, title=ds.title).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


EXPORTERS = {
    "pdf": (to_pdf, "application/pdf"),
    "xlsx": (to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "csv": (to_csv, "text/csv; charset=utf-8"),
}
