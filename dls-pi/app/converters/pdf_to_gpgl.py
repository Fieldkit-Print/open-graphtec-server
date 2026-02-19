from __future__ import annotations

import io
from math import hypot
from typing import Any

import pdfplumber

from ..protocol import ETX


POINT_TO_MM = 25.4 / 72.0
Point = tuple[float, float]
Segment = tuple[float, float, float, float]


def _to_0p1mm(value_in_points: float) -> int:
    mm = value_in_points * POINT_TO_MM
    return int(round(mm * 10.0))


def _segment_to_gpgl_points(
    x0_pt: float, y0_pt: float, x1_pt: float, y1_pt: float
) -> tuple[int, int, int, int]:
    return (
        _to_0p1mm(x0_pt),
        _to_0p1mm(y0_pt),
        _to_0p1mm(x1_pt),
        _to_0p1mm(y1_pt),
    )


def _distance_point_to_line(point: Point, line_start: Point, line_end: Point) -> float:
    x, y = point
    x1, y1 = line_start
    x2, y2 = line_end

    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return hypot(x - x1, y - y1)

    num = abs(dy * x - dx * y + x2 * y1 - y2 * x1)
    den = hypot(dx, dy)
    return num / den


def _flatten_cubic_bezier(
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    *,
    tolerance_points: float = 0.5,
    max_depth: int = 10,
) -> list[Point]:
    def recurse(
        q0: Point, q1: Point, q2: Point, q3: Point, depth: int
    ) -> list[Point]:
        d1 = _distance_point_to_line(q1, q0, q3)
        d2 = _distance_point_to_line(q2, q0, q3)
        if (max(d1, d2) <= tolerance_points) or (depth >= max_depth):
            return [q0, q3]

        # De Casteljau split at t=0.5
        q01 = ((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0)
        q12 = ((q1[0] + q2[0]) / 2.0, (q1[1] + q2[1]) / 2.0)
        q23 = ((q2[0] + q3[0]) / 2.0, (q2[1] + q3[1]) / 2.0)
        q012 = ((q01[0] + q12[0]) / 2.0, (q01[1] + q12[1]) / 2.0)
        q123 = ((q12[0] + q23[0]) / 2.0, (q12[1] + q23[1]) / 2.0)
        q0123 = ((q012[0] + q123[0]) / 2.0, (q012[1] + q123[1]) / 2.0)

        left = recurse(q0, q01, q012, q0123, depth + 1)
        right = recurse(q0123, q123, q23, q3, depth + 1)
        return left[:-1] + right

    return recurse(p0, p1, p2, p3, 0)


def _to_point(value: Any) -> Point:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    raise ValueError(f"Path point format is not supported: {value!r}")


def _add_path_segments_from_curve(
    curve_obj: dict[str, Any],
    *,
    tolerance_points: float,
) -> list[Segment]:
    segments: list[Segment] = []
    path = curve_obj.get("path")

    if not path:
        pts = curve_obj.get("pts") or []
        if len(pts) >= 2:
            for i in range(len(pts) - 1):
                p0 = _to_point(pts[i])
                p1 = _to_point(pts[i + 1])
                segments.append((p0[0], p0[1], p1[0], p1[1]))
        return segments

    current: Point | None = None
    subpath_start: Point | None = None

    for command in path:
        if not command:
            continue
        op = str(command[0]).lower()
        params = command[1:]

        if op == "m":
            current = _to_point(params[0])
            subpath_start = current
            continue

        if current is None:
            continue

        if op == "l":
            end = _to_point(params[0])
            segments.append((current[0], current[1], end[0], end[1]))
            current = end
            continue

        if op == "c":
            c1 = _to_point(params[0])
            c2 = _to_point(params[1])
            end = _to_point(params[2])
            points = _flatten_cubic_bezier(
                current, c1, c2, end, tolerance_points=tolerance_points
            )
            for i in range(len(points) - 1):
                p0 = points[i]
                p1 = points[i + 1]
                segments.append((p0[0], p0[1], p1[0], p1[1]))
            current = end
            continue

        if op == "v":
            c1 = current
            c2 = _to_point(params[0])
            end = _to_point(params[1])
            points = _flatten_cubic_bezier(
                current, c1, c2, end, tolerance_points=tolerance_points
            )
            for i in range(len(points) - 1):
                p0 = points[i]
                p1 = points[i + 1]
                segments.append((p0[0], p0[1], p1[0], p1[1]))
            current = end
            continue

        if op == "y":
            c1 = _to_point(params[0])
            end = _to_point(params[1])
            c2 = end
            points = _flatten_cubic_bezier(
                current, c1, c2, end, tolerance_points=tolerance_points
            )
            for i in range(len(points) - 1):
                p0 = points[i]
                p1 = points[i + 1]
                segments.append((p0[0], p0[1], p1[0], p1[1]))
            current = end
            continue

        if op == "h":
            if subpath_start is not None:
                end = subpath_start
                segments.append((current[0], current[1], end[0], end[1]))
                current = end
            continue

        # Unknown operators are ignored to stay forward-compatible.

    return segments


def _collect_segments(
    page: pdfplumber.page.Page, *, curve_tolerance_points: float
) -> tuple[list[Segment], dict[str, int]]:
    segments: list[Segment] = []
    counts = {
        "line_objects": 0,
        "rect_objects": 0,
        "curve_objects": 0,
    }

    for line in page.lines:
        counts["line_objects"] += 1
        segments.append(
            (
                float(line["x0"]),
                float(line["y0"]),
                float(line["x1"]),
                float(line["y1"]),
            )
        )

    for rect in page.rects:
        if rect.get("stroke") is False:
            continue
        counts["rect_objects"] += 1
        x0 = float(rect["x0"])
        x1 = float(rect["x1"])
        y0 = float(rect["y0"])
        y1 = float(rect["y1"])
        segments.extend(
            [
                (x0, y0, x1, y0),
                (x1, y0, x1, y1),
                (x1, y1, x0, y1),
                (x0, y1, x0, y0),
            ]
        )

    for curve in page.curves:
        if curve.get("stroke") is False:
            continue
        counts["curve_objects"] += 1
        segments.extend(
            _add_path_segments_from_curve(
                curve, tolerance_points=curve_tolerance_points
            )
        )

    return segments, counts


def _deduplicate_segments(segments: list[Segment]) -> list[tuple[int, int, int, int]]:
    deduped: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()

    for x0_pt, y0_pt, x1_pt, y1_pt in segments:
        x0, y0, x1, y1 = _segment_to_gpgl_points(x0_pt, y0_pt, x1_pt, y1_pt)
        if x0 == x1 and y0 == y1:
            continue

        forward = (x0, y0, x1, y1)
        reverse = (x1, y1, x0, y0)
        key = min(forward, reverse)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(forward)

    return deduped


def _build_gpgl_command_from_segments(
    segments: list[tuple[int, int, int, int]]
) -> bytes:
    if not segments:
        raise ValueError("No usable vector segments were found.")

    point_to_indexes: dict[tuple[int, int], set[int]] = {}
    for idx, (x0, y0, x1, y1) in enumerate(segments):
        point_to_indexes.setdefault((x0, y0), set()).add(idx)
        point_to_indexes.setdefault((x1, y1), set()).add(idx)

    unvisited = set(range(len(segments)))
    command_parts = ["J1,"]

    def mark_visited(index: int) -> None:
        if index not in unvisited:
            return
        unvisited.remove(index)
        x0, y0, x1, y1 = segments[index]
        point_to_indexes.get((x0, y0), set()).discard(index)
        point_to_indexes.get((x1, y1), set()).discard(index)

    while unvisited:
        start_index = min(unvisited)
        x0, y0, x1, y1 = segments[start_index]
        command_parts.append(f"M{x0},{y0},")
        command_parts.append(f"D{x1},{y1},")
        mark_visited(start_index)
        current = (x1, y1)

        while True:
            candidates = point_to_indexes.get(current, set()) & unvisited
            if not candidates:
                break
            next_index = min(candidates)
            sx, sy, ex, ey = segments[next_index]
            if (sx, sy) == current:
                next_point = (ex, ey)
            elif (ex, ey) == current:
                next_point = (sx, sy)
            else:
                break
            command_parts.append(f"D{next_point[0]},{next_point[1]},")
            mark_visited(next_index)
            current = next_point

    command_parts.append(ETX)
    return "".join(command_parts).encode("ascii", errors="ignore")


def convert_pdf_to_gpgl(
    pdf_bytes: bytes,
    *,
    max_segments: int = 20000,
    strict_vectors_only: bool = True,
    curve_tolerance_points: float = 0.5,
) -> tuple[bytes, dict[str, Any]]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages.")

        page = pdf.pages[0]
        if strict_vectors_only and page.images:
            raise ValueError(
                "PDF contains raster images. "
                "This service accepts vector cut lines only."
            )

        segments, counts = _collect_segments(
            page, curve_tolerance_points=curve_tolerance_points
        )

    if not segments:
        raise ValueError(
            "No vector line data found on first page. "
            "Expected stroked vector paths/lines."
        )

    deduped_segments = _deduplicate_segments(segments)
    if not deduped_segments:
        raise ValueError(
            "Vector objects were found, but no usable cut segments remained "
            "after filtering."
        )

    if len(deduped_segments) > max_segments:
        raise ValueError(
            f"Too many segments ({len(deduped_segments)}). "
            f"Max supported in this build is {max_segments}."
        )

    command_sequence = _build_gpgl_command_from_segments(deduped_segments)

    page_width_0p1mm = _to_0p1mm(float(page.width))
    page_height_0p1mm = _to_0p1mm(float(page.height))

    details: dict[str, Any] = {
        "page_width_0p1mm": page_width_0p1mm,
        "page_height_0p1mm": page_height_0p1mm,
        "segment_count_raw": len(segments),
        "segment_count": len(deduped_segments),
        "line_objects": counts["line_objects"],
        "rect_objects": counts["rect_objects"],
        "curve_objects": counts["curve_objects"],
        "strict_vectors_only": strict_vectors_only,
        "curve_tolerance_points": curve_tolerance_points,
        "converter_note": (
            "Hardened converter: stroked lines/rectangles/curves on first page."
        ),
    }
    return command_sequence, details
