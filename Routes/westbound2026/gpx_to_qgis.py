#!/usr/bin/env python3
"""Convert GPX routes into line and waypoint layers that QGIS can open.

The script uses only Python's standard library.  By default it reads every
``*.gpx`` file beside this script and writes:

* ``qgis_output/westbound2026_route_lines.geojson`` -- one line per route
* ``qgis_output/westbound2026_route_points.geojson`` -- one point per waypoint
* ``qgis_output/routes/`` -- a line and point layer for each individual route

GeoJSON uses WGS 84 longitude/latitude coordinates (EPSG:4326), which QGIS
recognises automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
NS = {"gpx": GPX_NAMESPACE}


@dataclass(frozen=True)
class RoutePoint:
    name: str
    longitude: float
    latitude: float
    overfly: bool | None


@dataclass(frozen=True)
class Route:
    route_id: str
    route_name: str
    origin: str
    destination: str
    source_file: str
    points: tuple[RoutePoint, ...]


def child_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Return stripped child text, accepting both namespaced and plain GPX."""
    child = element.find(f"gpx:{tag}", NS)
    if child is None:
        child = element.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def route_elements(root: ET.Element) -> list[ET.Element]:
    elements = root.findall("gpx:rte", NS)
    if not elements:
        elements = root.findall("rte")
    return elements


def route_point_elements(route: ET.Element) -> list[ET.Element]:
    elements = route.findall("gpx:rtept", NS)
    if not elements:
        elements = route.findall("rtept")
    return elements


def parse_optional_bool(value: str) -> bool | None:
    if not value:
        return None
    normalized = value.casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def parse_gpx(path: Path) -> list[Route]:
    """Read all GPX route elements from *path*."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid GPX XML in {path.name}: {exc}") from exc

    parsed: list[Route] = []
    elements = route_elements(root)
    if not elements:
        raise ValueError(f"No GPX route (<rte>) found in {path.name}")

    for route_number, route_element in enumerate(elements, start=1):
        points: list[RoutePoint] = []
        for point_number, point_element in enumerate(
            route_point_elements(route_element), start=1
        ):
            try:
                latitude = float(point_element.attrib["lat"])
                longitude = float(point_element.attrib["lon"])
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"Invalid coordinates in {path.name}, point {point_number}"
                ) from exc

            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError(
                    f"Coordinates out of range in {path.name}, point {point_number}"
                )

            point_name = child_text(
                point_element, "name", f"POINT_{point_number:03d}"
            )
            points.append(
                RoutePoint(
                    name=point_name,
                    longitude=longitude,
                    latitude=latitude,
                    overfly=parse_optional_bool(child_text(point_element, "overfly")),
                )
            )

        if len(points) < 2:
            raise ValueError(
                f"Route {route_number} in {path.name} has fewer than two points"
            )

        fallback_name = path.stem
        route_name = child_text(route_element, "name", fallback_name)
        route_id = route_name if len(elements) == 1 else f"{route_name}-{route_number}"
        parsed.append(
            Route(
                route_id=route_id,
                route_name=route_name,
                origin=points[0].name,
                destination=points[-1].name,
                source_file=path.name,
                points=tuple(points),
            )
        )

    return parsed


def route_properties(route: Route) -> dict[str, Any]:
    return {
        "route_id": route.route_id,
        "route_name": route.route_name,
        "origin": route.origin,
        "destination": route.destination,
        "point_count": len(route.points),
        "source_file": route.source_file,
    }


def line_feature(route: Route) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": route.route_id,
        "properties": route_properties(route),
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [point.longitude, point.latitude] for point in route.points
            ],
        },
    }


def point_features(route: Route) -> Iterable[dict[str, Any]]:
    last_point = len(route.points)
    for sequence, point in enumerate(route.points, start=1):
        properties = route_properties(route)
        properties.update(
            {
                "point_id": f"{route.route_id}:{sequence:03d}",
                "sequence": sequence,
                "waypoint": point.name,
                "point_role": (
                    "origin"
                    if sequence == 1
                    else "destination"
                    if sequence == last_point
                    else "enroute"
                ),
                "overfly": point.overfly,
                "longitude": point.longitude,
                "latitude": point.latitude,
            }
        )
        yield {
            "type": "Feature",
            "id": properties["point_id"],
            "properties": properties,
            "geometry": {
                "type": "Point",
                "coordinates": [point.longitude, point.latitude],
            },
        }


def feature_collection(features: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "VATSIM Cross the Land westbound 2026",
        "features": list(features),
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write formatted JSON atomically so an interrupted run cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(data, temporary, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "route"


def discover_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.casefold() != ".gpx":
            raise ValueError(f"Input file is not GPX: {input_path}")
        return [input_path]
    if input_path.is_dir():
        paths = sorted(
            (path for path in input_path.iterdir() if path.suffix.casefold() == ".gpx"),
            key=lambda item: item.name.casefold(),
        )
        if not paths:
            raise ValueError(f"No .gpx files found in {input_path}")
        return paths
    raise ValueError(f"Input path does not exist: {input_path}")


def convert(input_path: Path, output_dir: Path, per_route: bool = True) -> tuple[int, int]:
    routes: list[Route] = []
    for path in discover_inputs(input_path):
        routes.extend(parse_gpx(path))

    duplicate_ids = sorted(
        route_id
        for route_id in {route.route_id for route in routes}
        if sum(route.route_id == route_id for route in routes) > 1
    )
    if duplicate_ids:
        raise ValueError(f"Duplicate route ID(s): {', '.join(duplicate_ids)}")

    line_features = [line_feature(route) for route in routes]
    all_points = [feature for route in routes for feature in point_features(route)]
    write_json(
        output_dir / "westbound2026_route_lines.geojson",
        feature_collection(line_features),
    )
    write_json(
        output_dir / "westbound2026_route_points.geojson",
        feature_collection(all_points),
    )

    if per_route:
        routes_dir = output_dir / "routes"
        for route in routes:
            stem = safe_filename(route.route_id)
            write_json(
                routes_dir / f"{stem}_line.geojson",
                feature_collection([line_feature(route)]),
            )
            write_json(
                routes_dir / f"{stem}_points.geojson",
                feature_collection(point_features(route)),
            )

    return len(routes), len(all_points)


def build_parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Convert GPX routes to QGIS-ready line and point GeoJSON layers."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=script_dir,
        help="GPX file or directory (default: the directory containing this script)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=script_dir / "qgis_output",
        help="Output directory (default: qgis_output beside this script)",
    )
    parser.add_argument(
        "--combined-only",
        action="store_true",
        help="Do not create the two individual GeoJSON layers for every route",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        route_count, point_count = convert(
            args.input.resolve(),
            args.output_dir.resolve(),
            per_route=not args.combined_only,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Converted {route_count} routes and {point_count} route points to "
        f"{args.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
