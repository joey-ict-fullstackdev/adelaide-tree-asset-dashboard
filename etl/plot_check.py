"""Sanity plot: trees over suburb outlines. Trees must sit inside polygons."""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display in WSL, write a PNG instead
import matplotlib.pyplot as plt

from load import load_suburbs, load_trees

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("plot_check.png")

trees = load_trees()
suburbs = load_suburbs()
assert trees.crs == suburbs.crs, (trees.crs, suburbs.crs)  # CRS must match before any spatial step

minx, miny, maxx, maxy = trees.total_bounds
nearby = suburbs.cx[minx:maxx, miny:maxy]  # keep only suburbs touching the tree area
print(f"{len(nearby)} suburbs overlap the tree bounding box")

fig, ax = plt.subplots(figsize=(10, 10))
nearby.plot(ax=ax, color="none", edgecolor="grey", linewidth=0.8)
trees.plot(ax=ax, markersize=1, color="green")
ax.set_xlim(minx - 0.01, maxx + 0.01)
ax.set_ylim(miny - 0.01, maxy + 0.01)
ax.set_title(f"{len(trees)} street trees over suburb boundaries (EPSG:4326)")
fig.savefig(out, dpi=150, bbox_inches="tight")
print(f"saved {out}")
