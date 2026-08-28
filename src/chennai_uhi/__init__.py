"""Chennai UHI three-stage geospatial pipeline.

Stages (strict sequence):
  1. EXTRACTOR — fetch raw layers + structured fetch log
  2. VALIDATOR — allow-list, integrity, temporal/spatial/CRS/stats checks
  3. CLEANER   — only PASSED layers → EPSG:32644 shared grid → clean/

Nothing from the extractor is analysis-ready. Only cleaner outputs for
validator-PASSED layers count as clean data.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
