from __future__ import annotations

import numpy as np

from .schema import GeometryPayload, SurfaceResults, ThermalFieldPayload

def compute_thermal_field(
    geometry: GeometryPayload,
    surface_results: SurfaceResults,
    hour_index: int,
    zone: str = 'ROOM',
    resolution: tuple[int, int, int] = (32, 32, 16),
    stratification: float = 0.15
) -> ThermalFieldPayload:
    """
    Computes a 3D volumetric temperature field inside a shelter zone using
    inverse-distance-weighted (IDW) interpolation from surface temperatures.
    """
    # Get surface temperatures for the requested zone and hour
    surface_temps = {}
    for s_series in surface_results.surfaces:
        if s_series.zone == zone:
            if s_series.inside_face_temperature_c and len(s_series.inside_face_temperature_c) > hour_index:
                val = s_series.inside_face_temperature_c[hour_index]
                if val is not None:
                    surface_temps[s_series.surface] = val

    valid_surfaces = []
    centroids = []
    areas = []
    temps = []

    for surf in geometry.surfaces:
        if surf.zone == zone and surf.name in surface_temps:
            valid_surfaces.append(surf)
            
            # Compute centroid
            pos = surf.positions
            x = pos[0::3]
            y = pos[1::3]
            z = pos[2::3]
            cx = sum(x) / len(x)
            cy = sum(y) / len(y)
            cz = sum(z) / len(z)
            
            centroids.append([cx, cy, cz])
            areas.append(surf.area_m2)
            temps.append(surface_temps[surf.name])

    nx, ny, nz = resolution
    
    if not valid_surfaces:
        values = [0.0] * (nx * ny * nz)
        return ThermalFieldPayload(
            design_hash=geometry.design_hash,
            zone=zone,
            hour_index=hour_index,
            timestamp=surface_results.timestamps[hour_index],
            dims=resolution,
            bounds_min=geometry.bounds_min,
            bounds_max=geometry.bounds_max,
            values=tuple(values),
            min_c=0.0,
            max_c=0.0,
            surface_count=0
        )

    centroids = np.array(centroids)  # (N, 3)
    areas = np.array(areas)          # (N,)
    temps = np.array(temps)          # (N,)

    # Create 3D grid
    xmin, ymin, zmin = geometry.bounds_min
    xmax, ymax, zmax = geometry.bounds_max

    dx = (xmax - xmin) / nx if nx > 0 else 0
    dy = (ymax - ymin) / ny if ny > 0 else 0
    dz = (zmax - zmin) / nz if nz > 0 else 0

    x_centers = np.linspace(xmin + dx / 2, xmax - dx / 2, nx)
    y_centers = np.linspace(ymin + dy / 2, ymax - dy / 2, ny)
    z_centers = np.linspace(zmin + dz / 2, zmax - dz / 2, nz)

    # meshgrid with indexing='ij' and order (z, y, x) so that when flattened
    # with C-order, x varies fastest, then y, then z.
    Z, Y, X = np.meshgrid(z_centers, y_centers, x_centers, indexing='ij')
    points = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=-1)  # (M, 3)

    # Compute distances and IDW weights
    diff = points[:, None, :] - centroids[None, :, :]  # (M, N, 3)
    dist_sq = np.sum(diff**2, axis=-1)                 # (M, N)
    
    # Clamp minimum distance squared to avoid singularities
    dist_sq = np.maximum(dist_sq, 0.05**2)
    
    weights = areas[None, :] / dist_sq                 # (M, N)
    sum_weights = np.sum(weights, axis=-1)             # (M,)
    
    # IDW interpolated temperatures
    interp_temps = np.sum(weights * temps[None, :], axis=-1) / sum_weights  # (M,)

    # Apply buoyancy/stratification correction
    z_floor = zmin
    z_ceiling = zmax
    
    if z_ceiling > z_floor:
        delta_t = np.max(temps) - np.min(temps)
        z_frac = (points[:, 2] - z_floor) / (z_ceiling - z_floor)
        buoyancy = stratification * z_frac * delta_t
        interp_temps += buoyancy

    return ThermalFieldPayload(
        design_hash=geometry.design_hash,
        zone=zone,
        hour_index=hour_index,
        timestamp=surface_results.timestamps[hour_index],
        dims=resolution,
        bounds_min=geometry.bounds_min,
        bounds_max=geometry.bounds_max,
        values=tuple(interp_temps.tolist()),
        min_c=float(np.min(interp_temps)),
        max_c=float(np.max(interp_temps)),
        surface_count=len(valid_surfaces),
        stratification=stratification,
    )
