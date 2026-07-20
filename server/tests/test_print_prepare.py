import io

import pdfplumber
import pikepdf
import pytest
from reportlab.lib.colors import CMYKColor, CMYKColorSep
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.converters.cut_extract import extract_cut_segments
from app.print_prepare import prepare_print
from app.storage import JobStore
from tests.conftest import make_settings

MM = 72 / 25.4

# Cut square in PAGE mm, chosen to sit inside the Letter mark rectangle
# (marks at 50 mm side margins, mark 1 at y=35; safe X 105.9..145.9, Y 65..105)
CUT_X0, CUT_Y0, CUT_X1, CUT_Y1 = 110.0, 70.0, 140.0, 100.0


def artwork_pdf(*, artwork_y_mm: float = 120.0) -> bytes:
    """Base page: one plain artwork rectangle (no cut paths)."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFillColor(CMYKColor(0, 0.8, 0.8, 0))
    c.rect(70 * MM, artwork_y_mm * MM, 60 * MM, 40 * MM, stroke=0, fill=1)
    c.showPage()
    c.save()
    return buffer.getvalue()


def spot_pdf(*, spot_name: str = "CutContour",
             artwork_y_mm: float = 120.0) -> bytes:
    """Artwork + cut rectangle stroked in a named separation."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFillColor(CMYKColor(0, 0.8, 0.8, 0))
    c.rect(70 * MM, artwork_y_mm * MM, 60 * MM, 40 * MM, stroke=0, fill=1)
    c.setStrokeColor(CMYKColorSep(0, 1, 0, 0, spotName=spot_name))
    c.setLineWidth(0.5)
    p = c.beginPath()
    p.moveTo(CUT_X0 * MM, CUT_Y0 * MM)
    p.lineTo(CUT_X1 * MM, CUT_Y0 * MM)
    p.lineTo(CUT_X1 * MM, CUT_Y1 * MM)
    p.lineTo(CUT_X0 * MM, CUT_Y1 * MM)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.showPage()
    c.save()
    return buffer.getvalue()


def tagged_pdf() -> bytes:
    """Artwork + cut rectangle on an ISO 19593-1 Structural/Cutting OCG."""
    base = artwork_pdf()
    with pikepdf.open(io.BytesIO(base)) as pdf:
        ocg = pdf.make_indirect(pikepdf.Dictionary(**{
            "Type": pikepdf.Name("/OCG"),
            "Name": pikepdf.String("Thru-cut layer"),
            "GTS_ProcStepsGroup": pikepdf.Name("/Structural"),
            "GTS_ProcStepsType": pikepdf.Name("/Cutting"),
        }))
        pdf.Root["/OCProperties"] = pikepdf.Dictionary(**{
            "OCGs": pikepdf.Array([ocg]),
            "D": pikepdf.Dictionary(**{"Order": pikepdf.Array([ocg])}),
        })
        page = pdf.pages[0]
        x0, y0 = CUT_X0 * MM, CUT_Y0 * MM
        x1, y1 = CUT_X1 * MM, CUT_Y1 * MM
        content = (
            f"/OC /MC0 BDC 0.5 w {x0:.2f} {y0:.2f} m {x1:.2f} {y0:.2f} l "
            f"{x1:.2f} {y1:.2f} l {x0:.2f} {y1:.2f} l h S EMC"
        ).encode("ascii")
        page.contents_add(pdf.make_stream(content))
        resources = page.obj["/Resources"]
        resources["/Properties"] = pikepdf.Dictionary(**{"MC0": ocg})
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


@pytest.fixture()
def store(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    yield store
    store.close()


# -- extraction ------------------------------------------------------------


def test_extracts_spot_color_cut_paths() -> None:
    extraction = extract_cut_segments(spot_pdf())
    assert extraction.source == "spot-color"
    assert len(extraction.segments) == 4
    xs = [v for s in extraction.segments for v in (s[0], s[2])]
    assert min(xs) == pytest.approx(CUT_X0 * MM, abs=0.5)
    assert max(xs) == pytest.approx(CUT_X1 * MM, abs=0.5)


def test_extracts_processing_steps_cut_paths() -> None:
    extraction = extract_cut_segments(tagged_pdf())
    assert extraction.source == "processing-steps"
    assert len(extraction.segments) == 4


def test_custom_spot_name_and_no_cut_error() -> None:
    pdf = spot_pdf(spot_name="Thru-cut")
    extraction = extract_cut_segments(pdf, spot_names=("Thru-cut",))
    assert len(extraction.segments) == 4
    with pytest.raises(ValueError, match="No cut paths"):
        extract_cut_segments(pdf, spot_names=("CutContour",))
    with pytest.raises(ValueError, match="No cut paths"):
        extract_cut_segments(artwork_pdf())


# -- barcode allocation ----------------------------------------------------


def test_allocate_barcode(store) -> None:
    first = store.allocate_barcode("F")
    second = store.allocate_barcode("F")
    assert first == "F00000001"
    assert second == "F00000002"
    assert len(first) == 9
    with pytest.raises(ValueError):
        store.allocate_barcode("G")


# -- prepare ---------------------------------------------------------------


def test_prepare_print_produces_marked_pdf_and_job(tmp_path, store) -> None:
    settings = make_settings(tmp_path)
    result = prepare_print(
        spot_pdf(), settings=settings, store=store, name="fixture-job"
    )
    assert result.barcode_link_info == "F00000001"
    assert result.cut_source == "spot-color"
    assert result.warnings  # legacy spot naming warned

    # The job: TB-prefixed GP-GL with the layout's distances (Letter page:
    # lx = 279.4-50-35 = 194.4 mm, ly = 215.9-100 = 115.9 mm)
    job = store.get_job(result.job_id)
    assert job.barcode_link_info == result.barcode_link_info
    assert job.regmark_fx == 60 and job.regmark_fy == 400
    assert job.command_sequence.startswith(b"TB99\x03TB51,200\x03TB52,2\x03")
    assert b"TB24,1944,1159\x03" in job.command_sequence
    assert job.command_sequence.endswith(b"TB0\x03")

    # The print PDF: original artwork retained + marks/barcode overlaid
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        page = pdf.pages[0]
        rects = page.rects
        # Start Mark: 50 mm wide block at the right end of the barcode band
        assert any(
            abs((r["x1"] - r["x0"]) / MM - 50.0) < 0.2
            and abs(r["y0"] / MM - 20.0) < 0.2
            for r in rects
        ), "start mark missing from overlay"
        # Plenty of barcode bars present
        bars = [r for r in rects
                if abs(r["y0"] / MM - 20.0) < 0.2
                and (r["x1"] - r["x0"]) / MM < 2.0]
        assert len(bars) > 50
        # Original artwork rectangle still there (60 x 40 mm)
        assert any(
            abs((r["x1"] - r["x0"]) / MM - 60.0) < 0.2
            and abs((r["y1"] - r["y0"]) / MM - 40.0) < 0.2
            for r in rects
        ), "original artwork lost"


def test_prepare_honors_explicit_barcode(tmp_path, store) -> None:
    settings = make_settings(tmp_path)
    result = prepare_print(
        tagged_pdf(), settings=settings, store=store,
        name="tagged", barcode_link_info="A0700TEST",
    )
    assert result.barcode_link_info == "A0700TEST"
    assert result.cut_source == "processing-steps"
    assert not result.warnings


def test_prepare_rejects_zone_intrusion(tmp_path, store) -> None:
    settings = make_settings(tmp_path)
    # Artwork rectangle down in the barcode band strip
    with pytest.raises(ValueError, match="keep-clear"):
        prepare_print(
            spot_pdf(artwork_y_mm=5.0),
            settings=settings, store=store, name="bad",
        )


def test_print_worker_hot_folder_flow(tmp_path, store) -> None:
    from app.print_worker import PrintPrepareWorker

    settings = make_settings(tmp_path, ingest_file_min_age_seconds=0.0)
    worker = PrintPrepareWorker(settings=settings, store=store)

    good = settings.print_inbox_dir / "labels_A0800TEST.pdf"
    good.write_bytes(spot_pdf())
    bad = settings.print_inbox_dir / "no-cut-lines.pdf"
    bad.write_bytes(artwork_pdf())

    worker._poll_once()

    outputs = list(settings.print_outbox_dir.iterdir())
    assert [p.name for p in outputs] == ["labels_A0800TEST_A0800TEST.pdf"]
    assert not good.exists()
    jobs = store.find_job_metas_for_barcode("A0800TEST")
    assert len(jobs) == 1

    assert not bad.exists()
    errors = [p.name for p in settings.print_error_dir.iterdir()]
    assert any(n.endswith("no-cut-lines.error.txt") for n in errors)
    status = worker.get_status()
    assert status["prepared_count"] == 1 and status["error_count"] == 1


def test_prepare_rejects_cut_outside_marks(tmp_path, store) -> None:
    settings = make_settings(tmp_path)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setStrokeColor(CMYKColorSep(0, 1, 0, 0, spotName="CutContour"))
    p = c.beginPath()
    p.moveTo(55 * MM, 40 * MM)          # x=55: within 6 mm of left mark arm
    p.lineTo(200 * MM, 250 * MM)        # runs to the top-right corner zone
    c.drawPath(p, stroke=1, fill=0)
    c.showPage()
    c.save()
    with pytest.raises(ValueError):
        prepare_print(
            buffer.getvalue(), settings=settings, store=store, name="edge",
        )
