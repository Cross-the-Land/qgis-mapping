# qgis-mapping

QGIS mapping tools for Cross the Land operations.

## Westbound 2026 routes

Convert all GPX files into QGIS-ready line and waypoint layers:

```powershell
python .\Routes\westbound2026\gpx_to_qgis.py
```

The files are written to `Routes\westbound2026\qgis_output`. In QGIS, add
these two vector layers:

- `westbound2026_route_lines.geojson` — one feature per route
- `westbound2026_route_points.geojson` — one feature per waypoint, including
  route name and waypoint sequence

The `routes` subfolder also contains a separate line and point layer for every
route. Run `python .\Routes\westbound2026\gpx_to_qgis.py --help` for options.
