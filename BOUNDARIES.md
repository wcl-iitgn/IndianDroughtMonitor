# Administrative boundaries (states, UTs, districts)

Every boundary the site draws or counts with comes from **one** district dataset, processed by
`build_boundaries.py`, so states, districts, the 0.0625-degree raster and the national outline can
never disagree.

## Source
`DISTRICT_BOUNDARY.shp` (Lambert Conformal Conic, 810 features = 782 named districts + 28 unnamed
inter-state "DISPUTED" slivers). Put the shapefile set in `data/boundaries/_source/` (it is ~180 MB and
git-ignored) or pass `--source <file.shp|file.zip>`.

## What the build does
1. Reproject to WGS84, drop the Z coordinate, repair invalid geometry (Samastipur).
2. Merge each of the 28 disputed slivers (45 km2 in total) into the adjoining district of a claimant
   state with which it shares the longest border (`--disputed exclude` leaves them out instead).
3. Split Puducherry's single multipart polygon into Puducherry, Karaikal, Mahe and Yanam.
4. Rebuild a *conforming* topology (snap to ~11 m, node, polygonise, give each face to one district;
   hairline overlaps -> smaller district, gap slivers < 1 km2 -> neighbour with the longest shared
   border). Real holes (Bangladeshi enclaves, harbour inlets) are kept.
5. Topology-preserving simplification at two detail levels (0.004 deg national, 0.001 deg per state).
6. Derive states as the exact dissolve of their districts, then the raster and the outline.
7. Normalise names (Title Case, "and" -> "&", Delhi "Central" -> "Central Delhi", Keralam, ...).

## State ids (stable)
2..37 as before; **38 = Ladakh**; **9 = Dadra & Nagar Haveli and Daman & Diu** (old id 10 retired);
**18 = Keralam**. Raster value 0 = outside India, 1 = coastal/border fringe (keeps the land mask flush
with the outline; not a state), >= 2 = state/UT id.

## Known limits of the source (not fixable by code)
* **Gujarat**: no Vav-Tharad district (34th district, carved from Banas Kantha) - 33 shown.
* **Delhi**: pre-2026 layout (11 districts + "Nazul"); the 13-district layout in force since
  1 Jan 2026 needs new geometry.
* Smallest Lakshadweep islets shrink at the coarse national detail level (Lakshadweep -14 % of 30 km2);
  per-state files use finer detail.

## Verify
`python3 verify_boundaries.py` re-reads only the shipped outputs and checks ids, names, district counts,
state = union of districts, raster/district-grid agreement and the outline.
