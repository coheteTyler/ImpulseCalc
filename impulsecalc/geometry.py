"""2D impulse blade profile: lines + camber arc + LE/TE ends + throat metrics.

Flight metal knobs beyond classic t/c / lines / fillets:
  - free stagger (override ½(β1+β2))
  - camber distribution (front ↔ aft loaded, independent of thickness peak)
  - LE shape: circular | elliptical | wedge
  - TE thickness /c and TE wedge angle
  - throat opening o and o/s from real passage geometry
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BladeShapeParams:
    """Impulse blade = two arcs (upper + lower) that meet at LE/TE.

    Primary (bucket) knobs — all fractions of chord unless noted:
      upper_sagitta_c  height of upper surface arc (0.5 ≈ semi-circle)
      lower_sagitta_c  height of lower surface arc (must be < upper)
      le_fillet_r_c    optional LE blend radius / chord (0 = sharp point)
      te_fillet_r_c    optional TE blend radius / chord (0 = sharp point)

    Mid thickness ≈ (upper − lower)·chord. Tips are points (or filleted).
    Legacy fields (arc_bulge, thickness_ratio, …) map onto these for old JSON.
    """

    # --- Dual-arc primary controls ---
    upper_sagitta_c: float = 0.48  # outer/top arc height / chord
    lower_sagitta_c: float = 0.28  # inner/bottom arc height / chord
    le_fillet_r_c: float = 0.0  # 0 = sharp LE point
    te_fillet_r_c: float = 0.0  # 0 = sharp TE point
    n_points: int = 160
    profile_family: str = "impulse_bucket"  # impulse_bucket | airfoil

    # --- Legacy / airfoil compat (still serialized) ---
    thickness_ratio: float = 0.20  # mid solid ~ (upper-lower); also airfoil t/c
    thickness_peak_x: float = 0.40
    inlet_line_frac: float = 0.0
    outlet_line_frac: float = 0.0
    arc_bulge: float = 1.10  # maps → upper_sagitta if dual fields absent
    stagger_deg: float | None = None
    camber_dist: float = 0.5
    le_shape: str = "circular"
    te_thickness_c: float = 0.02
    te_wedge_deg: float = 10.0
    wall_thickness_c: float = 0.20
    bucket_suction_cutback: float = 0.0

    def clamp(self) -> "BladeShapeParams":
        fam = str(self.profile_family or "impulse_bucket").lower().strip()
        if fam in ("bucket", "impulse", "scoop", "pelton", "rocket", "turbopump"):
            fam = "impulse_bucket"
        if fam not in ("impulse_bucket", "airfoil"):
            fam = "impulse_bucket"
        self.profile_family = fam

        # Dual-arc heights
        self.upper_sagitta_c = float(min(max(self.upper_sagitta_c, 0.12), 0.70))
        self.lower_sagitta_c = float(min(max(self.lower_sagitta_c, 0.02), 0.65))
        # Lower must stay below upper (positive solid)
        if self.lower_sagitta_c >= self.upper_sagitta_c - 0.04:
            self.lower_sagitta_c = max(0.02, self.upper_sagitta_c - 0.08)
        # Sync legacy thickness ≈ mid solid / c
        self.thickness_ratio = float(self.upper_sagitta_c - self.lower_sagitta_c)
        self.wall_thickness_c = float(self.thickness_ratio)
        # Fillets: 0 allowed (sharp). Cap modestly; pitch check is external.
        self.le_fillet_r_c = float(min(max(self.le_fillet_r_c, 0.0), 0.15))
        self.te_fillet_r_c = float(min(max(self.te_fillet_r_c, 0.0), 0.15))
        self.n_points = int(min(max(self.n_points, 48), 240))

        self.thickness_peak_x = float(min(max(self.thickness_peak_x, 0.20), 0.70))
        self.inlet_line_frac = float(min(max(self.inlet_line_frac, 0.0), 0.35))
        self.outlet_line_frac = float(min(max(self.outlet_line_frac, 0.0), 0.35))
        self.arc_bulge = float(min(max(self.arc_bulge, 0.4), 2.2))
        self.camber_dist = float(min(max(self.camber_dist, 0.0), 1.0))
        ls = str(self.le_shape or "circular").lower().strip()
        if ls not in ("circular", "elliptical", "wedge"):
            ls = "circular"
        self.le_shape = ls
        self.te_thickness_c = float(min(max(self.te_thickness_c, 0.0), 0.15))
        self.te_wedge_deg = float(min(max(self.te_wedge_deg, 0.0), 35.0))
        self.bucket_suction_cutback = float(min(max(self.bucket_suction_cutback, 0.0), 0.85))
        if self.stagger_deg is not None:
            try:
                self.stagger_deg = float(self.stagger_deg)
            except (TypeError, ValueError):
                self.stagger_deg = None
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "BladeShapeParams":
        if not d:
            return cls().clamp()
        aliases = {
            "stagger": "stagger_deg",
            "camber_distribution": "camber_dist",
            "te_thickness": "te_thickness_c",
            "te_wedge": "te_wedge_deg",
            "le": "le_fillet_r_c",
            "te": "te_fillet_r_c",
            "profile": "profile_family",
            "family": "profile_family",
            "wall_t": "wall_thickness_c",
            "wall_thickness": "wall_thickness_c",
            "suction_cutback": "bucket_suction_cutback",
            "upper_h": "upper_sagitta_c",
            "lower_h": "lower_sagitta_c",
            "outer_sagitta_c": "upper_sagitta_c",
            "inner_sagitta_c": "lower_sagitta_c",
            "h_upper_c": "upper_sagitta_c",
            "h_lower_c": "lower_sagitta_c",
        }
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        cleaned: dict[str, Any] = {}
        for k, v in d.items():
            key = aliases.get(k, k)
            if key in known:
                cleaned[key] = v
        # Legacy: arc_bulge + thickness_ratio → dual sagitta if new fields not set
        if "upper_sagitta_c" not in cleaned and "arc_bulge" in cleaned:
            try:
                b = float(cleaned.get("arc_bulge", 1.1))
                cleaned["upper_sagitta_c"] = 0.5 * min(max(0.85 + 0.15 * (b - 1.0), 0.75), 1.25)
            except (TypeError, ValueError):
                pass
        if "lower_sagitta_c" not in cleaned:
            try:
                up = float(cleaned.get("upper_sagitta_c", 0.48))
                tr = float(cleaned.get("thickness_ratio", cleaned.get("wall_thickness_c", 0.20)))
                cleaned["lower_sagitta_c"] = max(0.05, up - min(max(tr, 0.08), 0.35))
            except (TypeError, ValueError):
                pass
        if "stagger_deg" in cleaned:
            st = cleaned["stagger_deg"]
            if st is None or (isinstance(st, str) and st.strip().lower() in ("", "auto", "none", "null")):
                cleaned["stagger_deg"] = None
        return cls(**cleaned).clamp()


@dataclass
class BladeGeometry:
    chord_m: float = 0.01
    thickness_ratio: float = 0.20
    pitch_m: float | None = None
    solidity: float = 1.13688
    beta1_deg: float = 72.0
    beta2_deg: float = -72.0
    n_points: int = 160
    shape: BladeShapeParams = field(default_factory=BladeShapeParams)

    def resolved_pitch(self) -> float:
        if self.pitch_m is not None and self.pitch_m > 0:
            return float(self.pitch_m)
        return float(self.chord_m) / max(float(self.solidity), 1e-6)

    def resolved_solidity(self) -> float:
        pitch = self.resolved_pitch()
        return float(self.chord_m) / max(pitch, 1e-12)

    def effective_shape(self) -> BladeShapeParams:
        s = BladeShapeParams.from_dict(self.shape.to_dict() if self.shape else None)
        s.n_points = max(s.n_points, self.n_points)
        return s.clamp()


def _unit(dx: float, dy: float) -> tuple[float, float]:
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def meanline_lines_arc(
    chord_m: float,
    beta1_deg: float,
    beta2_deg: float,
    inlet_line_frac: float,
    outlet_line_frac: float,
    arc_bulge: float,
    n: int,
    stagger_deg: float | None = None,
    camber_dist: float = 0.5,
) -> list[tuple[float, float]]:
    """
    Meanline in chord frame, then staggered into cascade axes:

      straight at β₁  →  smooth camber arc  →  straight at β₂

    LE is always (0,0) and TE always (chord, 0) in chord frame.

    ``stagger_deg`` None → auto ½(β1+β2). Free stagger reorients the chord
    without changing metal inlet/exit angles relative to cascade axes... wait:
    metal angles β are absolute in cascade frame; stagger is chord angle.

    Convention: β1/β2 are metal angles in cascade axes. Chord is placed at
    stagger λ; relative metal angles to chord are ξ = β − λ.
    """
    b1 = math.radians(beta1_deg)
    b2 = math.radians(beta2_deg)
    if stagger_deg is None:
        stagger = 0.5 * (b1 + b2)
    else:
        stagger = math.radians(float(stagger_deg))
    xi1 = b1 - stagger  # metal angle relative to chord
    xi2 = b2 - stagger
    c = float(chord_m)
    n = max(int(n), 20)
    bulge = float(max(arc_bulge, 0.2))
    cd = float(min(max(camber_dist, 0.0), 1.0))

    f_in = max(float(inlet_line_frac), 0.0)
    f_out = max(float(outlet_line_frac), 0.0)
    if f_in + f_out > 0.85:
        s = 0.85 / (f_in + f_out)
        f_in *= s
        f_out *= s
    L_in = f_in * c
    L_out = f_out * c

    p1 = (L_in * math.cos(xi1), L_in * math.sin(xi1))
    p2 = (c - L_out * math.cos(xi2), 0.0 - L_out * math.sin(xi2))

    phi = abs(xi1 - xi2)
    if phi > 1e-12:
        R = (c / 2.0) / math.sin(phi / 2.0)
        h_circ = R * (4.0 / 3.0) * math.tan(phi / 4.0)
    else:
        h_circ = c / 3.0
    span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    h = min(h_circ, max(span / 3.0, 1e-9))
    h *= bulge
    # Camber distribution: bias Bezier handles front (cd→0) vs aft (cd→1)
    h1 = h * (1.35 - 0.70 * cd)
    h2 = h * (0.65 + 0.70 * cd)

    b0 = p1
    b3 = p2
    b1c = (p1[0] + h1 * math.cos(xi1), p1[1] + h1 * math.sin(xi1))
    b2c = (p2[0] - h2 * math.cos(xi2), p2[1] - h2 * math.sin(xi2))

    def bez(t: float) -> tuple[float, float]:
        u = 1.0 - t
        x = (
            u * u * u * b0[0]
            + 3 * u * u * t * b1c[0]
            + 3 * u * t * t * b2c[0]
            + t * t * t * b3[0]
        )
        y = (
            u * u * u * b0[1]
            + 3 * u * u * t * b1c[1]
            + 3 * u * t * t * b2c[1]
            + t * t * t * b3[1]
        )
        return (x, y)

    w_in = max(f_in, 0.0)
    w_out = max(f_out, 0.0)
    w_arc = max(1.0 - w_in - w_out, 0.15)
    w_sum = w_in + w_out + w_arc
    n_in = max(int(round((n - 1) * w_in / w_sum)), 0)
    n_out = max(int(round((n - 1) * w_out / w_sum)), 0)
    n_arc = max(n - 1 - n_in - n_out, 2)

    pts_ch: list[tuple[float, float]] = []
    if n_in == 0:
        pts_ch.append((0.0, 0.0))
    else:
        for i in range(n_in + 1):
            t = i / n_in
            pts_ch.append((t * p1[0], t * p1[1]))
    for i in range(1, n_arc + 1):
        t = i / n_arc
        pts_ch.append(bez(t))
    te = (c, 0.0)
    if n_out > 0:
        for i in range(1, n_out + 1):
            t = i / n_out
            pts_ch.append(
                (p2[0] + t * (te[0] - p2[0]), p2[1] + t * (te[1] - p2[1]))
            )
    else:
        if abs(pts_ch[-1][0] - c) > 1e-12 or abs(pts_ch[-1][1]) > 1e-12:
            pts_ch.append(te)

    pts_ch[0] = (0.0, 0.0)
    pts_ch[-1] = (c, 0.0)

    cs, sn = math.cos(stagger), math.sin(stagger)
    return [(px * cs - py * sn, px * sn + py * cs) for px, py in pts_ch]


def _camber_y(t: float, c: float, camber: float, bulge: float) -> float:
    """Legacy parabolic camber height (kept for callers / tests)."""
    if abs(camber) < 1e-12:
        return 0.0
    phi = abs(camber)
    R = c / (2.0 * math.sin(phi / 2.0))
    h = R * (1.0 - math.cos(phi / 2.0)) * float(bulge)
    y = 4.0 * h * t * (1.0 - t)
    return y if camber >= 0 else -y


def circular_arc_meanline(
    chord_m: float, beta1_deg: float, beta2_deg: float, n: int = 60
) -> list[tuple[float, float]]:
    """Legacy pure circular-arc meanline (no straight wedges)."""
    return meanline_lines_arc(chord_m, beta1_deg, beta2_deg, 0.0, 0.0, 1.0, n)


def _thickness_half(
    s: float,
    t_max: float,
    peak_x: float,
    le_r_c: float,
    te_r_c: float,
    chord_m: float,
    *,
    le_shape: str = "circular",
    te_thickness_c: float = 0.004,
    te_wedge_deg: float = 8.0,
) -> float:
    """Half-thickness along meanline parameter s∈[0,1]."""
    if s <= peak_x:
        u = s / max(peak_x, 1e-9)
        base = t_max * (math.sin(0.5 * math.pi * u) ** 1.05)
    else:
        u = (1.0 - s) / max(1.0 - peak_x, 1e-9)
        base = t_max * (math.sin(0.5 * math.pi * u) ** 1.15)

    half = 0.5 * base
    c = max(chord_m, 1e-9)
    te_half_target = 0.5 * te_thickness_c * c

    le_blend = math.exp(-((s / max(le_r_c * 3.0, 1e-4)) ** 2))
    te_blend = math.exp(-(((1.0 - s) / max(te_r_c * 3.0, 1e-4)) ** 2))

    # LE family half-thickness
    le_half = 0.0
    if le_r_c > 0 and s < 0.30:
        rr = le_r_c * c
        xx = min(s * c, rr * 2.0)
        shape = (le_shape or "circular").lower()
        if shape == "elliptical":
            # ellipse: half = b * sqrt(1 - ((rr-x)/rr)^2), b = 0.55*rr (flatter nose)
            b_ell = 0.55 * rr
            xi = min(xx, rr)
            le_half = b_ell * math.sqrt(max(1.0 - ((rr - xi) / max(rr, 1e-12)) ** 2, 0.0))
        elif shape == "wedge":
            # linear wedge half-angle ~ atan(le_r / (2*le_r)) ≈ 26° scale by rr
            slope = 0.45  # dy/dx near LE
            le_half = slope * xx
            le_half = min(le_half, 0.5 * t_max)
        else:
            # circular
            xi = min(s * c, rr)
            le_half = math.sqrt(max(rr * rr - (rr - xi) ** 2, 0.0))

    # TE family: enforce thickness + wedge taper
    te_half = te_half_target
    if s > 0.65:
        # wedge: linear growth of half-thickness toward mid from TE
        wedge_rad = math.radians(max(te_wedge_deg, 0.0) / 2.0)
        dist_from_te = (1.0 - s) * c
        wedge_half = te_half_target + dist_from_te * math.tan(wedge_rad)
        # blend wedge into base
        te_half = min(wedge_half, 0.5 * t_max)

    half = (
        half * (1.0 - 0.9 * le_blend - 0.85 * te_blend)
        + le_half * le_blend
        + te_half * te_blend
    )
    # hard floor near TE for manufacturing thickness
    if s > 0.92:
        half = max(half, te_half_target * (s - 0.85) / 0.15)
    return max(half, 0.0)



def _rocket_thickness_half(
    s: float,
    t_max: float,
    peak_x: float,
    le_half: float,
    te_half: float,
) -> float:
    """Half-thickness: ends thin, middle thick (LP rocket turbopump blade).

    s in [0,1] along meanline. Peak at peak_x. LE/TE forced thinner than mid.
    """
    s = min(max(float(s), 0.0), 1.0)
    peak_x = min(max(float(peak_x), 0.15), 0.75)
    t_max = max(float(t_max), 1e-9)
    le_h = min(max(float(le_half), 0.04 * t_max), 0.40 * t_max)
    te_h = min(max(float(te_half), 0.03 * t_max), 0.28 * t_max)
    mid_h = 0.5 * t_max
    if s <= peak_x:
        u = s / peak_x
        w = math.sin(0.5 * math.pi * u) ** 1.05
        half = le_h + (mid_h - le_h) * w
    else:
        u = (s - peak_x) / max(1.0 - peak_x, 1e-9)
        w = math.sin(0.5 * math.pi * (1.0 - u)) ** 1.15
        half = te_h + (mid_h - te_h) * w
    return max(half, 0.5 * min(le_h, te_h))


def _circular_arc_le_te(
    c: float,
    h: float,
    n: int,
    *,
    open_positive_y: bool = True,
) -> list[tuple[float, float]]:
    """Circular arc from LE(0,0) to TE(c,0) with sagitta h (height of cup).

    Both endpoints are exact. No rectangular end-caps — the arc lands on the tips.
    """
    c = max(float(c), 1e-9)
    h = max(float(h), 1e-9)
    n = max(int(n), 8)
    # Circle through (0,0) and (c,0) with sagitta h
    R = (0.25 * c * c + h * h) / (2.0 * h)
    cx = 0.5 * c
    # Center on the opposite side of the cup so the arc bows to +y or -y
    if open_positive_y:
        cy = -(R - h)  # arc in +y
    else:
        cy = +(R - h)  # arc in -y
    a0 = math.atan2(0.0 - cy, 0.0 - cx)
    a1 = math.atan2(0.0 - cy, c - cx)
    best = None
    best_score = -1e99
    sign = 1.0 if open_positive_y else -1.0
    for flip in (0, 1):
        da = a1 - a0
        while da <= -math.pi:
            da += 2 * math.pi
        while da > math.pi:
            da -= 2 * math.pi
        if flip:
            da = da - 2 * math.pi if da > 0 else da + 2 * math.pi
        angs = [a0 + da * i / (n - 1) for i in range(n)]
        mid_a = angs[n // 2]
        my = cy + R * math.sin(mid_a)
        score = sign * my
        if score > best_score:
            best_score = score
            best = angs
    pts = [(cx + R * math.cos(a), cy + R * math.sin(a)) for a in best]
    pts[0] = (0.0, 0.0)
    pts[-1] = (c, 0.0)
    return pts


def _arc_circle_params(c: float, h: float) -> tuple[float, float, float]:
    """Circle through LE(0,0)–TE(c,0) with sagitta h (+y cup). Returns (cx, cy, R)."""
    c = max(float(c), 1e-9)
    h = max(float(h), 1e-9)
    R = (0.25 * c * c + h * h) / (2.0 * h)
    return 0.5 * c, h - R, R


def _circle_circle_intersections(
    c0: tuple[float, float],
    r0: float,
    c1: tuple[float, float],
    r1: float,
) -> list[tuple[float, float]]:
    """Up to two intersection points of circles (c0,r0) and (c1,r1)."""
    x0, y0 = c0
    x1, y1 = c1
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d < 1e-15 or r0 < 0 or r1 < 0:
        return []
    if d > r0 + r1 + 1e-12 or d < abs(r0 - r1) - 1e-12:
        return []
    # distance from c0 to line of intersection
    a = (r0 * r0 - r1 * r1 + d * d) / (2.0 * d)
    h2 = r0 * r0 - a * a
    if h2 < -1e-12:
        return []
    h = math.sqrt(max(h2, 0.0))
    xm = x0 + a * dx / d
    ym = y0 + a * dy / d
    if h < 1e-15:
        return [(xm, ym)]
    rx, ry = -dy * (h / d), dx * (h / d)
    return [(xm + rx, ym + ry), (xm - rx, ym - ry)]


def _fillet_arc_points(
    center: tuple[float, float],
    radius: float,
    p_start: tuple[float, float],
    p_end: tuple[float, float],
    prefer: tuple[float, float],
    n: int = 12,
) -> list[tuple[float, float]]:
    """Arc of given radius from p_start→p_end; choose orientation closer to prefer."""
    cx, cy = center
    r = max(float(radius), 1e-15)
    a0 = math.atan2(p_start[1] - cy, p_start[0] - cx)
    a1 = math.atan2(p_end[1] - cy, p_end[0] - cx)
    n = max(int(n), 3)

    def sweep(ccw: bool) -> list[float]:
        da = a1 - a0
        while da <= -math.pi:
            da += 2 * math.pi
        while da > math.pi:
            da -= 2 * math.pi
        if ccw and da < 0:
            da += 2 * math.pi
        if not ccw and da > 0:
            da -= 2 * math.pi
        return [a0 + da * i / (n - 1) for i in range(n)]

    def score(angs: list[float]) -> float:
        mid = angs[len(angs) // 2]
        px = cx + r * math.cos(mid)
        py = cy + r * math.sin(mid)
        return -((px - prefer[0]) ** 2 + (py - prefer[1]) ** 2)

    best = sweep(True)
    if score(sweep(False)) > score(best):
        best = sweep(False)
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in best]


def _max_tip_fillet_radius(c: float, h_u: float, h_l: float, pitch: float) -> float:
    """Largest tip fillet that still fits: diameter ≤ inter-blade gap (~pitch at tips).

    Also limited by dual-arc offset geometry (outer radius must exceed fillet).
    """
    _, _, R_u = _arc_circle_params(c, h_u)
    t_mid = max(h_u - h_l, 1e-12)
    # User rule: fillet diameter ≤ distance between blades at tip ≈ pitch
    by_pitch = 0.5 * max(float(pitch), 1e-12)
    # Keep a little solid at mid; don't exceed outer arc curvature room
    by_geom = min(0.45 * t_mid, 0.35 * R_u, 0.20 * c)
    return max(0.0, min(by_pitch, by_geom))


def _dual_arc_closed_polygon(
    c: float,
    h_u: float,
    h_l: float,
    n: int,
    r_le: float = 0.0,
    r_te: float = 0.0,
) -> list[tuple[float, float]]:
    """Upper + lower circular arcs LE↔TE, optional tip fillets, closed metal outline.

    Winding: upper LE→TE, TE fillet (or point), lower TE→LE, LE fillet (or point).
    Solid is the band between arcs (thick mid; pointed or filleted tips).
    """
    c = max(float(c), 1e-9)
    h_u = max(float(h_u), 1e-9)
    h_l = max(float(h_l), 1e-12)
    if h_l >= h_u - 1e-9:
        h_l = max(0.5 * h_u, h_u - max(0.04 * c, 1e-6))
    n = max(int(n), 48)

    upper = _circular_arc_le_te(c, h_u, n, open_positive_y=True)
    lower = _circular_arc_le_te(c, h_l, n, open_positive_y=True)

    r_le = max(float(r_le), 0.0)
    r_te = max(float(r_te), 0.0)

    if r_le <= 1e-15 and r_te <= 1e-15:
        poly: list[tuple[float, float]] = list(upper)
        for i in range(n - 2, 0, -1):
            poly.append(lower[i])
        poly.append(upper[0])
        return poly

    cx_u, cy_u, R_u = _arc_circle_params(c, h_u)
    cx_l, cy_l, R_l = _arc_circle_params(c, h_l)
    Cu, Cl = (cx_u, cy_u), (cx_l, cy_l)

    def tip_data(r: float, want_le: bool):
        if r <= 1e-15 or R_u <= r + 1e-12:
            return None
        hits = _circle_circle_intersections(Cu, R_u - r, Cl, R_l + r)
        if not hits:
            return None
        F = min(hits, key=lambda p: p[0]) if want_le else max(hits, key=lambda p: p[0])
        dxu, dyu = F[0] - Cu[0], F[1] - Cu[1]
        Lu = math.hypot(dxu, dyu) or 1.0
        p_u = (Cu[0] + dxu / Lu * R_u, Cu[1] + dyu / Lu * R_u)
        dxl, dyl = F[0] - Cl[0], F[1] - Cl[1]
        Ll = math.hypot(dxl, dyl) or 1.0
        p_l = (Cl[0] + dxl / Ll * R_l, Cl[1] + dyl / Ll * R_l)
        return {"F": F, "r": r, "p_u": p_u, "p_l": p_l}

    le = tip_data(r_le, True)
    te = tip_data(r_te, False)

    def nearest_idx(pts, q):
        best_i, best_d = 0, 1e99
        for i, p in enumerate(pts):
            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    i_u0 = nearest_idx(upper, le["p_u"]) if le else 0
    i_u1 = nearest_idx(upper, te["p_u"]) if te else n - 1
    i_l0 = nearest_idx(lower, le["p_l"]) if le else 0
    i_l1 = nearest_idx(lower, te["p_l"]) if te else n - 1
    i_u0 = max(0, min(i_u0, n - 3))
    i_u1 = max(i_u0 + 2, min(i_u1, n - 1))
    i_l0 = max(0, min(i_l0, n - 3))
    i_l1 = max(i_l0 + 2, min(i_l1, n - 1))

    poly: list[tuple[float, float]] = []

    # upper LE → TE
    if le:
        poly.append(le["p_u"])
        start_u = i_u0 + 1
    else:
        poly.append(upper[0])
        start_u = 1
    end_u = i_u1 if te else n - 1
    for i in range(start_u, end_u):
        poly.append(upper[i])
    if te:
        poly.append(te["p_u"])
    else:
        poly.append(upper[-1])

    # TE fillet upper → lower
    if te:
        tip = (c, 0.0)
        te_arc = _fillet_arc_points(te["F"], te["r"], te["p_u"], te["p_l"], tip, 12)
        poly.extend(te_arc[1:])

    # lower TE → LE
    start_l = (i_l1 - 1) if te else (n - 2)
    end_l = i_l0 if le else 0
    for i in range(start_l, end_l, -1):
        poly.append(lower[i])
    if le:
        poly.append(le["p_l"])
    else:
        poly.append(lower[0])

    # LE fillet lower → upper
    if le:
        tip = (0.0, 0.0)
        le_arc = _fillet_arc_points(le["F"], le["r"], le["p_l"], le["p_u"], tip, 14)
        poly.extend(le_arc[1:])

    if poly and (
        abs(poly[0][0] - poly[-1][0]) > 1e-15 or abs(poly[0][1] - poly[-1][1]) > 1e-15
    ):
        poly.append(poly[0])
    return poly


def _impulse_bucket_polygon(geom: BladeGeometry) -> list[tuple[float, float]]:
    """Radial impulse bucket = two arcs (upper + lower) meeting at LE/TE.

    Primary knobs (fractions of chord):
      upper_sagitta_c  — upper surface arc height (≈0.5 → semi-circle)
      lower_sagitta_c  — lower surface arc height (must be < upper)
      le_fillet_r_c    — optional LE tip blend (0 = sharp); diameter capped by pitch
      te_fillet_r_c    — optional TE tip blend

    Mid solid thickness ≈ (upper − lower)·chord. Tips are geometric points or fillets.
    Neighbor blades: upper of one faces lower of the next across pitch s.
    """
    sh = geom.effective_shape()
    c = max(float(geom.chord_m), 1e-9)
    n = max(int(sh.n_points), 120)
    pitch = geom.resolved_pitch()

    h_u = float(sh.upper_sagitta_c) * c
    h_l = float(sh.lower_sagitta_c) * c
    # Positive solid metal between arcs
    if h_l >= h_u - 0.04 * c:
        h_l = max(0.02 * c, h_u - 0.08 * c)

    r_cap = _max_tip_fillet_radius(c, h_u, h_l, pitch)
    r_le = min(max(float(sh.le_fillet_r_c) * c, 0.0), r_cap)
    r_te = min(max(float(sh.te_fillet_r_c) * c, 0.0), r_cap)

    return _dual_arc_closed_polygon(c, h_u, h_l, n, r_le=r_le, r_te=r_te)


def blade_closed_polygon(geom: BladeGeometry) -> list[tuple[float, float]]:
    sh = geom.effective_shape()
    if sh.profile_family == "impulse_bucket":
        return _impulse_bucket_polygon(geom)
    turn = abs(float(geom.beta1_deg) - float(geom.beta2_deg))
    bulge = float(sh.arc_bulge)
    if turn > 90.0:
        bulge = max(bulge, 1.05)
        bulge = min(bulge, 1.8)
    ml = meanline_lines_arc(
        geom.chord_m,
        geom.beta1_deg,
        geom.beta2_deg,
        sh.inlet_line_frac,
        sh.outlet_line_frac,
        bulge,
        sh.n_points,
        stagger_deg=sh.stagger_deg,
        camber_dist=sh.camber_dist,
    )
    if len(ml) < 3:
        return ml
    c = float(geom.chord_m)
    t_max = sh.thickness_ratio * c
    le_half = min(sh.le_fillet_r_c * c, 0.35 * t_max)
    te_half = min(0.5 * sh.te_thickness_c * c, 0.25 * t_max)
    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    n = len(ml)
    for i, (x, y) in enumerate(ml):
        if i == 0:
            dx, dy = ml[1][0] - x, ml[1][1] - y
        elif i == n - 1:
            dx, dy = x - ml[i - 1][0], y - ml[i - 1][1]
        else:
            dx = ml[i + 1][0] - ml[i - 1][0]
            dy = ml[i + 1][1] - ml[i - 1][1]
        nx, ny = _unit(-dy, dx)
        s = i / max(n - 1, 1)
        half = _rocket_thickness_half(s, t_max, sh.thickness_peak_x, le_half, te_half)
        upper.append((x + nx * half, y + ny * half))
        lower.append((x - nx * half, y - ny * half))
    poly = _apply_end_fillets(upper, lower, le_half, te_half)
    if poly and poly[0] != poly[-1]:
        poly.append(poly[0])
    return poly


def _apply_end_fillets(
    upper: list[tuple[float, float]],
    lower: list[tuple[float, float]],
    le_r: float,
    te_r: float,
) -> list[tuple[float, float]]:
    """Replace raw LE/TE points with circular fillet arcs."""
    if len(upper) < 4 or len(lower) < 4:
        return upper + list(reversed(lower))

    def fillet_arc(
        p_u: tuple[float, float],
        p_l: tuple[float, float],
        toward: tuple[float, float],
        radius: float,
        n_arc: int = 12,
        leading: bool = True,
    ) -> list[tuple[float, float]]:
        if radius <= 1e-12:
            return [p_u] if leading else [p_l]
        mx = 0.5 * (p_u[0] + p_l[0])
        my = 0.5 * (p_u[1] + p_l[1])
        tx, ty = toward[0] - mx, toward[1] - my
        tx, ty = _unit(tx, ty)
        cx = mx + tx * radius
        cy = my + ty * radius
        a_u = math.atan2(p_u[1] - cy, p_u[0] - cx)
        a_l = math.atan2(p_l[1] - cy, p_l[0] - cx)

        def lerp_angles(a0, a1, steps, ccw: bool):
            da = a1 - a0
            while da <= -math.pi:
                da += 2 * math.pi
            while da > math.pi:
                da -= 2 * math.pi
            if ccw and da < 0:
                da += 2 * math.pi
            if not ccw and da > 0:
                da -= 2 * math.pi
            return [a0 + da * i / steps for i in range(steps + 1)]

        test_ccw = lerp_angles(a_u, a_l, 4, True)
        test_cw = lerp_angles(a_u, a_l, 4, False)

        def score(angs):
            sx = sy = 0.0
            for a in angs:
                px = cx + radius * math.cos(a)
                py = cy + radius * math.sin(a)
                sx += (px - toward[0]) * (mx - toward[0]) + (py - toward[1]) * (my - toward[1])
            return sx

        angs = test_ccw if score(test_ccw) > score(test_cw) else test_cw
        return [(cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angs]

    le_toward = upper[min(3, len(upper) - 1)]
    te_toward = upper[max(0, len(upper) - 4)]
    le_arc = fillet_arc(upper[0], lower[0], le_toward, le_r, 14, True)
    te_arc = fillet_arc(upper[-1], lower[-1], te_toward, te_r, 12, False)

    body_u = upper[1:-1]
    body_l = list(reversed(lower[1:-1]))
    le_pts = list(reversed(le_arc))
    te_pts = te_arc
    poly = le_pts + body_u + te_pts + body_l
    return poly


def cascade_blade_outlines(geom: BladeGeometry, n_blades: int = 3) -> list[list[tuple[float, float]]]:
    pitch = geom.resolved_pitch()
    base = blade_closed_polygon(geom)
    mid = n_blades // 2
    return [[(x, y + (k - mid) * pitch) for x, y in base] for k in range(n_blades)]


# Sensible cascade-tunnel extents as fractions of chord (engineering defaults).
DEFAULT_X_UP_C = 0.5   # inlet length ahead of LE
DEFAULT_X_DN_C = 1.0   # outlet length aft of TE


def clamp_domain_extents(x_up_c: float, x_dn_c: float) -> tuple[float, float]:
    """Clamp inlet/outlet chord-fractions to a practical CFD range."""
    x_up = float(min(max(float(x_up_c), 0.05), 5.0))
    x_dn = float(min(max(float(x_dn_c), 0.05), 8.0))
    return x_up, x_dn


def compute_domain_bounds(
    chord_m: float,
    pitch_m: float,
    n_blades: int = 3,
    x_up_c: float = DEFAULT_X_UP_C,
    x_dn_c: float = DEFAULT_X_DN_C,
) -> dict[str, float]:
    """
    Pure 2D cascade flow domain in the chord frame.

    Convention (engineering cascade “wind tunnel”):
      - LE at x=0, TE at x=chord
      - inlet patch at x_min = −(x_up_c)·c   (relative-flow BC)
      - outlet patch at x_max = c + (x_dn_c)·c
      - top/bottom pitch-periodic cyclics spanning n_blades·pitch

    Returns finite bounds plus derived lengths for UI / meta.
    """
    c = max(float(chord_m), 1e-9)
    pitch = max(float(pitch_m), 1e-12)
    n = max(int(n_blades), 1)
    x_up, x_dn = clamp_domain_extents(x_up_c, x_dn_c)
    y_span = n * pitch
    x_min = -x_up * c
    x_max = c + x_dn * c
    return {
        "x_min": x_min,
        "x_max": x_max,
        "y_min": -0.5 * y_span,
        "y_max": 0.5 * y_span,
        "chord_m": c,
        "pitch_m": pitch,
        "x_up_c": x_up,
        "x_dn_c": x_dn,
        "inlet_length_m": x_up * c,
        "outlet_length_m": x_dn * c,
        "axial_length_m": x_max - x_min,
        "y_span_m": y_span,
        "n_blades": float(n),
    }


def domain_bounds(
    geom: BladeGeometry,
    n_blades: int = 3,
    x_up_c: float = DEFAULT_X_UP_C,
    x_dn_c: float = DEFAULT_X_DN_C,
) -> dict[str, float]:
    """Cascade domain from blade geometry + optional inlet/outlet chord-fractions."""
    return compute_domain_bounds(
        chord_m=geom.chord_m,
        pitch_m=geom.resolved_pitch(),
        n_blades=n_blades,
        x_up_c=x_up_c,
        x_dn_c=x_dn_c,
    )


def _seg_dist(
    ax: float, ay: float, bx: float, by: float, px: float, py: float
) -> float:
    """Distance from point P to segment AB."""
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-24:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    qx, qy = ax + t * abx, ay + t * aby
    return math.hypot(px - qx, py - qy)


def throat_metrics(geom: BladeGeometry) -> dict[str, float]:
    """Minimum passage opening o between adjacent blades and o/s.

    Uses closed profile of one blade vs the next (pitch-shifted). Returns
    finite positive o and o_s when geometry is valid.
    """
    pitch = geom.resolved_pitch()
    poly = blade_closed_polygon(geom)
    if len(poly) < 6 or pitch <= 1e-12:
        return {
            "throat_o_m": 0.0,
            "opening_o_s": 0.0,
            "pitch_m": pitch,
            "throat_x_m": 0.0,
        }

    # Drop closing duplicate
    if poly[0] == poly[-1]:
        poly = poly[:-1]

    # Adjacent blade = +pitch in y
    other = [(x, y + pitch) for x, y in poly]

    # Sample subset for speed
    step = max(1, len(poly) // 48)
    pts_a = poly[::step]
    min_d = float("inf")
    throat_x = 0.0
    for i in range(len(other) - 1):
        ax, ay = other[i]
        bx, by = other[i + 1]
        for px, py in pts_a:
            d = _seg_dist(ax, ay, bx, by, px, py)
            if d < min_d:
                min_d = d
                throat_x = px
    # also reverse roles
    pts_b = other[::step]
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        bx, by = poly[i + 1]
        for px, py in pts_b:
            d = _seg_dist(ax, ay, bx, by, px, py)
            if d < min_d:
                min_d = d
                throat_x = 0.5 * (px + ax)

    if not math.isfinite(min_d) or min_d > 1e6:
        min_d = 0.0
    o = max(float(min_d), 0.0)
    o_s = o / pitch if pitch > 0 else 0.0
    return {
        "throat_o_m": o,
        "opening_o_s": o_s,
        "pitch_m": pitch,
        "throat_x_m": float(throat_x),
    }


def resolved_stagger_deg(geom: BladeGeometry) -> float:
    sh = geom.effective_shape()
    if sh.stagger_deg is not None:
        return float(sh.stagger_deg)
    return 0.5 * (float(geom.beta1_deg) + float(geom.beta2_deg))


def blade_preview_payload(
    geom: BladeGeometry,
    n_blades: int = 1,
    *,
    x_up_c: float = DEFAULT_X_UP_C,
    x_dn_c: float = DEFAULT_X_DN_C,
    flow_beta1_deg: float | None = None,
    flow_beta2_deg: float | None = None,
    w1_m_s: float | None = None,
    p1_pa: float | None = None,
    t1_k: float | None = None,
    mach_w1: float | None = None,
    mean_radius_m: float | None = None,
    span_m: float | None = None,
    tip_radius_m: float | None = None,
    hub_radius_m: float | None = None,
    n_blades_machine: int | None = None,
    blade_name: str | None = None,
) -> dict[str, Any]:
    """JSON-friendly preview for the web UI (includes cascade flow domain box).

    Domain bounds use the same ``domain_bounds`` helper as ``write_blockMesh`` so the
    purple box is the exact region that will be meshed and solved.
    """
    sh = geom.effective_shape()
    poly = blade_closed_polygon(geom)
    # Meanline for display: mid-wall of radial bucket, or camber line for airfoil
    if sh.profile_family == "impulse_bucket":
        c = max(float(geom.chord_m), 1e-9)
        h_u = float(sh.upper_sagitta_c) * c
        h_l = float(sh.lower_sagitta_c) * c
        if h_l >= h_u - 0.04 * c:
            h_l = max(0.02 * c, h_u - 0.08 * c)
        h_mid = 0.5 * (h_u + h_l)
        ml = _circular_arc_le_te(c, h_mid, min(sh.n_points, 60), open_positive_y=True)
    else:
        ml = meanline_lines_arc(
            geom.chord_m,
            geom.beta1_deg,
            geom.beta2_deg,
            sh.inlet_line_frac,
            sh.outlet_line_frac,
            sh.arc_bulge,
            min(sh.n_points, 60),
            stagger_deg=sh.stagger_deg,
            camber_dist=sh.camber_dist,
        )
    n_show = max(int(n_blades), 1)
    blades = cascade_blade_outlines(geom, n_show) if n_show > 1 else [poly]
    throat = throat_metrics(geom)
    # SAME helper as openfoam_case.write_blockmesh → mesh parity
    domain = domain_bounds(geom, n_blades=n_show, x_up_c=x_up_c, x_dn_c=x_dn_c)
    pitch = geom.resolved_pitch()
    sol = geom.resolved_solidity()
    # Relative inlet flow (velocity-triangle β1, not metal) for W arrows on inlet patch
    fb1 = float(flow_beta1_deg if flow_beta1_deg is not None else geom.beta1_deg)
    w1 = float(w1_m_s) if w1_m_s is not None else None
    b1r = math.radians(fb1)
    wx = (w1 * math.cos(b1r)) if w1 is not None else None
    wy = (w1 * math.sin(b1r)) if w1 is not None else None
    # Blade-row mid y positions for spacing annotations
    mid = n_show // 2
    blade_centers = [
        {
            "index": k,
            "y_m": (k - mid) * pitch,
            "le_x": 0.0,
            "te_x": float(geom.chord_m),
        }
        for k in range(n_show)
    ]
    spacing_pairs = []
    for k in range(n_show - 1):
        y0 = (k - mid) * pitch
        y1 = (k + 1 - mid) * pitch
        spacing_pairs.append(
            {
                "y0": y0,
                "y1": y1,
                "pitch_m": pitch,
                "x_m": 0.35 * float(geom.chord_m),  # mid-chordish for dimension line
            }
        )
    return {
        "chord_m": geom.chord_m,
        "pitch_m": pitch,
        "solidity": sol,
        "beta1_deg": geom.beta1_deg,  # metal β1* (geometry)
        "beta2_deg": geom.beta2_deg,  # metal β2*
        "flow_beta1_deg": fb1,
        "flow_beta2_deg": float(flow_beta2_deg if flow_beta2_deg is not None else -fb1),
        "stagger_deg": resolved_stagger_deg(geom),
        "shape": sh.to_dict(),
        "throat": throat,
        "domain": domain,
        "meanline": [{"x": x, "y": y} for x, y in ml],
        "profile": [{"x": x, "y": y} for x, y in poly],
        "cascade": [[{"x": x, "y": y} for x, y in b] for b in blades],
        "blade_centers": blade_centers,
        "spacing_pairs": spacing_pairs,
        "inlet_flow": {
            "beta1_deg": fb1,
            "w1_m_s": w1,
            "Ux": wx,
            "Uy": wy,
            "p1_pa": p1_pa,
            "t1_k": t1_k,
            "mach_w1": mach_w1,
            "patch": "inlet",
            "x_m": domain["x_min"],
            "note": "Working fluid enters at left inlet patch (relative W₁ @ flow β₁)",
        },
        "stage": {
            "blade_name": blade_name,
            "mean_radius_m": mean_radius_m,
            "span_m": span_m,
            "tip_radius_m": tip_radius_m,
            "hub_radius_m": hub_radius_m,
            "n_blades_machine": n_blades_machine,
            "n_blades_domain": n_show,
        },
        "mesh_parity": {
            "same_as_blockMesh": True,
            "n_blades": n_show,
            "pitch_m": pitch,
            "x_min": domain["x_min"],
            "x_max": domain["x_max"],
            "y_min": domain["y_min"],
            "y_max": domain["y_max"],
            "note": (
                "Purple domain box uses domain_bounds() — identical to "
                "system/blockMeshDict vertices written in §3."
            ),
        },
        "legend": [
            "black fill = metal blade (meshed as wall via STL/poly)",
            "blue dashed = camber meanline",
            "purple box = CFD domain (inlet | cyclics | outlet)",
            "green arrows = inlet relative velocity W₁ (working fluid from left)",
            "orange dimension = blade spacing s (pitch)",
        ],
    }
