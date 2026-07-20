"""Extract cut paths from a print PDF.

Two identification mechanisms, in priority order:

1. ISO 19593-1 processing steps: vector content inside a marked-content
   section (BDC /OC) whose Optional Content Group carries
   GTS_ProcStepsGroup = Structural and GTS_ProcStepsType in
   {Cutting, PartialCutting} (checked both directly on the OCG dict and
   nested under GTS_Metadata).
2. Legacy spot-color convention: paths stroked or filled in a Separation
   (or DeviceN) color space whose colorant matches a configured name
   (default "CutContour"), case-insensitive.

Returns line segments in PDF page coordinates (points, origin bottom-left),
with beziers flattened.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1
from pdfminer.psparser import PSLiteral
from pdfminer.utils import apply_matrix_pt, mult_matrix

from .pdf_to_gpgl import _flatten_cubic_bezier

Segment = tuple[float, float, float, float]

CUT_PROC_STEP_TYPES = {"Cutting", "PartialCutting"}


def _name_of(value: Any) -> str | None:
    value = resolve1(value)
    if isinstance(value, PSLiteral):
        return str(value.name)
    if isinstance(value, (str, bytes)):
        return value.decode() if isinstance(value, bytes) else value
    return None


def _is_cut_ocg(ocg: Any) -> bool:
    ocg = resolve1(ocg)
    if not isinstance(ocg, dict):
        return False
    candidates = [ocg]
    metadata = resolve1(ocg.get("GTS_Metadata"))
    if isinstance(metadata, dict):
        candidates.append(metadata)
    for source in candidates:
        group = _name_of(source.get("GTS_ProcStepsGroup"))
        step_type = _name_of(source.get("GTS_ProcStepsType"))
        if group == "Structural" and step_type in CUT_PROC_STEP_TYPES:
            return True
    return False


def _oc_props_is_cut(props: Any) -> bool:
    """props of a BDC /OC — an OCG, or an OCMD wrapping OCG(s)."""
    props = resolve1(props)
    if not isinstance(props, dict):
        return False
    if _is_cut_ocg(props):
        return True
    ocgs = resolve1(props.get("OCGs"))
    if isinstance(ocgs, list):
        return any(_is_cut_ocg(ocg) for ocg in ocgs)
    if ocgs is not None:
        return _is_cut_ocg(ocgs)
    return False


@dataclass
class _Context:
    spot_names: frozenset[str]           # lowercase
    oc_cut_depth: int = 0
    segments: list[Segment] = field(default_factory=list)
    from_processing_steps: int = 0
    from_spot_color: int = 0


class _CutDevice(PDFDevice):
    """Collects painted paths flagged as cut content by the interpreter."""

    def __init__(self, rsrcmgr: PDFResourceManager, ctx: _Context) -> None:
        super().__init__(rsrcmgr)
        self.ctx = ctx
        self.ctm = (1, 0, 0, 1, 0, 0)
        self._stroke_cut = False
        self._fill_cut = False

    def set_ctm(self, ctm) -> None:
        self.ctm = ctm

    def paint_path(self, graphicstate, stroke, fill, evenodd, path) -> None:
        by_layer = self.ctx.oc_cut_depth > 0
        by_spot = (stroke and self._stroke_cut) or (fill and self._fill_cut)
        if not (by_layer or by_spot):
            return

        def to_page(x, y):
            return apply_matrix_pt(self.ctm, (x, y))

        subpaths: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        start: tuple[float, float] | None = None
        for op in path:
            kind = op[0]
            if kind == "m":
                if len(current) > 1:
                    subpaths.append(current)
                current = [to_page(op[1], op[2])]
                start = current[0]
            elif kind == "l" and current:
                current.append(to_page(op[1], op[2]))
            elif kind == "c" and current:
                flat = _flatten_cubic_bezier(
                    current[-1],
                    to_page(op[1], op[2]),
                    to_page(op[3], op[4]),
                    to_page(op[5], op[6]),
                )
                current.extend(flat[1:])
            elif kind == "v" and current:
                flat = _flatten_cubic_bezier(
                    current[-1], current[-1],
                    to_page(op[1], op[2]),
                    to_page(op[3], op[4]),
                )
                current.extend(flat[1:])
            elif kind == "y" and current:
                end = to_page(op[3], op[4])
                flat = _flatten_cubic_bezier(
                    current[-1], to_page(op[1], op[2]), end, end
                )
                current.extend(flat[1:])
            elif kind == "h" and current and start is not None:
                current.append(start)
                subpaths.append(current)
                current = [start]
        if len(current) > 1:
            subpaths.append(current)

        emitted = 0
        for subpath in subpaths:
            for i in range(len(subpath) - 1):
                self.ctx.segments.append((*subpath[i], *subpath[i + 1]))
                emitted += 1
        if emitted:
            if by_layer:
                self.ctx.from_processing_steps += emitted
            else:
                self.ctx.from_spot_color += emitted


class _CutInterpreter(PDFPageInterpreter):
    """Tracks separation colorants and processing-steps layers."""

    def __init__(self, rsrcmgr, device) -> None:
        super().__init__(rsrcmgr, device)
        self._sep_map: dict[str, frozenset[str]] = {}
        self._stroke_colorants: frozenset[str] = frozenset()
        self._fill_colorants: frozenset[str] = frozenset()
        self._colorant_stack: list[tuple[frozenset[str], frozenset[str]]] = []
        self._raw_resources: dict = {}

    # -- resources ---------------------------------------------------------

    def init_resources(self, resources) -> None:
        super().init_resources(resources)
        self._raw_resources = resolve1(resources) or {}
        self._sep_map = {}
        cs_dict = resolve1(self._raw_resources.get("ColorSpace")) or {}
        if isinstance(cs_dict, dict):
            for key, spec in cs_dict.items():
                spec = resolve1(spec)
                if not isinstance(spec, list) or not spec:
                    continue
                family = _name_of(spec[0])
                names: list[str] = []
                if family == "Separation" and len(spec) > 1:
                    name = _name_of(spec[1])
                    if name:
                        names = [name]
                elif family == "DeviceN" and len(spec) > 1:
                    raw_names = resolve1(spec[1])
                    if isinstance(raw_names, list):
                        names = [n for n in (_name_of(v) for v in raw_names) if n]
                if names:
                    self._sep_map[key] = frozenset(n.lower() for n in names)

    # -- color spaces ------------------------------------------------------

    def _colorants_for(self, name) -> frozenset[str]:
        key = _name_of(name) or str(name)
        return self._sep_map.get(key, frozenset())

    def do_CS(self, name) -> None:
        self._stroke_colorants = self._colorants_for(name)
        super().do_CS(name)

    def do_cs(self, name) -> None:
        self._fill_colorants = self._colorants_for(name)
        super().do_cs(name)

    def do_q(self) -> None:
        self._colorant_stack.append(
            (self._stroke_colorants, self._fill_colorants)
        )
        super().do_q()

    def do_Q(self) -> None:
        if self._colorant_stack:
            self._stroke_colorants, self._fill_colorants = (
                self._colorant_stack.pop()
            )
        super().do_Q()

    # -- marked content (processing steps layers) --------------------------

    def _resolve_oc_props(self, props):
        props = resolve1(props)
        if isinstance(props, dict):
            return props
        key = _name_of(props)
        if key is None:
            return None
        properties = resolve1(self._raw_resources.get("Properties")) or {}
        if isinstance(properties, dict):
            return resolve1(properties.get(key))
        return None

    def do_BDC(self, tag, props) -> None:
        is_cut = False
        if _name_of(tag) == "OC":
            resolved = self._resolve_oc_props(props)
            is_cut = _oc_props_is_cut(resolved) if resolved else False
        ctx = self.device.ctx
        if not hasattr(self, "_bdc_stack"):
            self._bdc_stack = []
        self._bdc_stack.append(is_cut)
        if is_cut:
            ctx.oc_cut_depth += 1
        super().do_BDC(tag, props)

    def do_BMC(self, tag) -> None:
        if not hasattr(self, "_bdc_stack"):
            self._bdc_stack = []
        self._bdc_stack.append(False)
        super().do_BMC(tag)

    def do_EMC(self) -> None:
        if getattr(self, "_bdc_stack", None):
            if self._bdc_stack.pop():
                self.device.ctx.oc_cut_depth -= 1
        super().do_EMC()

    # -- flag the device before every paint --------------------------------

    def _sync_device(self) -> None:
        targets = self.device.ctx.spot_names
        self.device._stroke_cut = bool(self._stroke_colorants & targets)
        self.device._fill_cut = bool(self._fill_colorants & targets)

    def do_S(self):
        self._sync_device(); super().do_S()

    def do_s(self):
        self._sync_device(); super().do_s()

    def do_f(self):
        self._sync_device(); super().do_f()

    def do_f_a(self):
        self._sync_device(); super().do_f_a()

    def do_F(self):
        self._sync_device(); super().do_F()

    def do_B(self):
        self._sync_device(); super().do_B()

    def do_B_a(self):
        self._sync_device(); super().do_B_a()

    def do_b(self):
        self._sync_device(); super().do_b()

    def do_b_a(self):
        self._sync_device(); super().do_b_a()


@dataclass(frozen=True)
class CutExtraction:
    segments: list[Segment]          # PDF points, page coords
    source: str                      # "processing-steps" | "spot-color" | "mixed"
    page_width_pt: float
    page_height_pt: float


def extract_cut_segments(
    pdf_bytes: bytes, *, spot_names: tuple[str, ...] = ("CutContour",)
) -> CutExtraction:
    parser = PDFParser(io.BytesIO(pdf_bytes))
    document = PDFDocument(parser)
    pages = list(PDFPage.create_pages(document))
    if not pages:
        raise ValueError("PDF has no pages.")
    page = pages[0]

    ctx = _Context(spot_names=frozenset(n.lower() for n in spot_names))
    rsrcmgr = PDFResourceManager()
    device = _CutDevice(rsrcmgr, ctx)
    interpreter = _CutInterpreter(rsrcmgr, device)
    interpreter.process_page(page)

    x0, y0, x1, y1 = [float(v) for v in page.mediabox]
    if ctx.from_processing_steps and ctx.from_spot_color:
        source = "mixed"
    elif ctx.from_processing_steps:
        source = "processing-steps"
    else:
        source = "spot-color"
    if not ctx.segments:
        raise ValueError(
            "No cut paths found. Provide them on an ISO 19593-1 "
            "Structural/Cutting layer or in a spot color named one of: "
            + ", ".join(spot_names)
        )
    return CutExtraction(
        segments=ctx.segments,
        source=source,
        page_width_pt=x1 - x0,
        page_height_pt=y1 - y0,
    )
