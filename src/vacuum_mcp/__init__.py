"""vacuum-mcp: one voice layer for robot vacuums, one backend per brand.

The neutral layer (this package outside ``backends/``) holds the readings, the
capabilities and, later, the tools and their spoken answers. It knows no brand's
codes. Each brand's backend (``backends/<brand>.py``) turns what its robot reports
into ``Readings`` and says, per model, what it can do and which readings it trusts.
See the design vacuum-mcp-v1.md, step 1.
"""
