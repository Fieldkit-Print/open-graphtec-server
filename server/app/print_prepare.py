"""Turn a pre-imposed print PDF into a marked print file + registered cut job.

The contract (one computation, two artifacts):
- the incoming page is never re-rendered — marks and barcode are added as
  an overlay on top of the untouched original objects
- cut paths are read from ISO 19593-1 Structural/Cutting layers, falling
  back to configured spot-color names (default "CutContour")
- the SAME layout computation stamps the print overlay and produces the
  registered cut job's regmark vector and TB distances, so print and cut
  cannot drift
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber
import pikepdf

from . import marks
from .config import Settings
from .converters.cut_extract import extract_cut_segments
from .protocol import sanitize_job_name
from .storage import JobStore

PT_PER_MM = marks.PT_PER_MM


@dataclass(frozen=True)
class PrepareResult:
    pdf_bytes: bytes
    job_id: int
    barcode_link_info: str
    name: str
    segment_count: int
    cut_source: str
    warnings: list[str] = field(default_factory=list)


def _check_keep_clear(
    pdf_bytes: bytes, layout: marks.MarkLayout
) -> list[str]:
    """Return violations: content inside barcode/mark keep-clear zones."""
    zones_pt = [
        tuple(v * PT_PER_MM for v in zone)
        for zone in marks.keep_clear_zones(layout)
    ]
    violations: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        for kind, objects in page.objects.items():
            for obj in objects:
                try:
                    ox0, oy0 = float(obj["x0"]), float(obj["y0"])
                    ox1, oy1 = float(obj["x1"]), float(obj["y1"])
                except (KeyError, TypeError, ValueError):
                    continue
                for zx0, zy0, zx1, zy1 in zones_pt:
                    if ox0 < zx1 and ox1 > zx0 and oy0 < zy1 and oy1 > zy0:
                        violations.append(
                            f"{kind} object at "
                            f"({ox0 / PT_PER_MM:.0f}, {oy0 / PT_PER_MM:.0f}) mm "
                            f"intrudes into a barcode/mark keep-clear zone"
                        )
                        break
                if len(violations) >= 5:
                    return violations
    return violations


def prepare_print(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    store: JobStore,
    name: str,
    barcode_link_info: str | None = None,
) -> PrepareResult:
    warnings: list[str] = []

    extraction = extract_cut_segments(
        pdf_bytes, spot_names=settings.cut_spot_colors
    )
    if extraction.source != "processing-steps":
        warnings.append(
            "Cut paths were identified by legacy spot-color name "
            f"({', '.join(settings.cut_spot_colors)}); prefer ISO 19593-1 "
            "processing steps."
        )

    page_w_mm = extraction.page_width_pt / PT_PER_MM
    page_h_mm = extraction.page_height_pt / PT_PER_MM
    barcode = barcode_link_info or store.allocate_barcode(
        settings.barcode_prefix
    )
    layout = marks.compute_layout(page_w_mm, page_h_mm, barcode)

    violations = _check_keep_clear(pdf_bytes, layout)
    if violations:
        raise ValueError(
            "Artwork intrudes into reserved zones: " + "; ".join(violations)
        )

    # Cut geometry into the mark frame; must land inside the mark rectangle.
    segments_mm: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in extraction.segments:
        mx0, my0 = marks.page_to_mark_frame(
            layout, x0 / PT_PER_MM, y0 / PT_PER_MM
        )
        mx1, my1 = marks.page_to_mark_frame(
            layout, x1 / PT_PER_MM, y1 / PT_PER_MM
        )
        segments_mm.append((mx0, my0, mx1, my1))
    xs = [v for s in segments_mm for v in (s[0], s[2])]
    ys = [v for s in segments_mm for v in (s[1], s[3])]
    if min(xs) < 1.0 or min(ys) < 1.0 or max(xs) > layout.lx - 1.0 or (
        max(ys) > layout.ly - 1.0
    ):
        raise ValueError(
            "Cut paths extend outside the registration-mark rectangle "
            f"({layout.ly:.0f} x {layout.lx:.0f} mm inside the marks); "
            "cut content must stay at least 1 mm inside the marks."
        )

    # Print artifact: untouched original + marks overlay.
    overlay_bytes = marks.build_overlay_pdf(layout)
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf, \
            pikepdf.open(io.BytesIO(overlay_bytes)) as overlay:
        pdf.pages[0].add_overlay(overlay.pages[0])
        out = io.BytesIO()
        pdf.save(out)
        marked_pdf = out.getvalue()

    # Cut artifact: same layout numbers feed the job record.
    gpgl = marks.build_gpgl_job(
        layout, segments_mm, steps_per_mm=settings.gpgl_steps_per_mm
    )
    job_name = sanitize_job_name(name, fallback=barcode)
    job_id = store.create_job(
        name=job_name,
        barcode_link_info=barcode,
        command_type=0,
        regmark_fx=layout.regmark_fx,
        regmark_fy=layout.regmark_fy,
        regmark_rx=0,
        regmark_ry=0,
        command_sequence=gpgl,
    )
    return PrepareResult(
        pdf_bytes=marked_pdf,
        job_id=job_id,
        barcode_link_info=barcode,
        name=job_name,
        segment_count=len(segments_mm),
        cut_source=extraction.source,
        warnings=warnings,
    )
