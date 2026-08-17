"""Matplotlib figures for ImpulseCalc."""

from __future__ import annotations

from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .geometry import BladeGeometry, cascade_blade_outlines
from .meanline import MeanlineResult


def figure_velocity_triangles(result: MeanlineResult, *, dpi: int = 120):
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 4.0), dpi=dpi, facecolor="#f5f2e8")
    for ax, title, Ca, Ct, Wa, Wt, U, C, W, alpha, beta in [
        (
            axes[0], "Inlet triangle",
            result.c_axial1_m_s, result.c_theta1_m_s,
            result.c_axial1_m_s, result.c_theta1_m_s - result.u_m_s,
            result.u_m_s, result.c1_m_s, result.w1_m_s,
            result.alpha1_deg, result.beta1_deg,
        ),
        (
            axes[1], "Outlet triangle",
            result.c_axial2_m_s, result.c_theta2_m_s,
            result.c_axial2_m_s, result.c_theta2_m_s - result.u_m_s,
            result.u_m_s, result.c2_m_s, result.w2_m_s,
            result.alpha2_deg, result.beta2_deg,
        ),
    ]:
        ax.set_facecolor("#faf8f0")
        ax.annotate("", xy=(Ca, Ct), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", color="#0000aa", lw=2))
        ax.text(Ca * 0.55, Ct * 0.55, f"C={C:.0f}", color="#0000aa", fontsize=9)
        ax.annotate("", xy=(Wa, Wt), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", color="#aa0000", lw=2))
        ax.text(Wa * 0.55, Wt * 0.55 - 40, f"W={W:.0f}", color="#aa0000", fontsize=9)
        ax.annotate("", xy=(Ca, Ct), xytext=(Wa, Wt),
                    arrowprops=dict(arrowstyle="->", color="#007700", lw=2))
        ax.text((Ca + Wa) / 2 + 20, (Ct + Wt) / 2, f"U={U:.0f}", color="#007700", fontsize=9)
        ax.axhline(0, color="#666", lw=0.5)
        ax.axvline(0, color="#666", lw=0.5)
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=0.35)
        ax.set_xlabel("Axial (m/s)")
        ax.set_ylabel("Tangential (m/s)")
        ax.set_title(f"{title}\nα={alpha:.1f}°  β={beta:.1f}°")
    fig.suptitle(
        f"Velocity triangles · r={result.degree_of_reaction:.3f} · "
        f"Euler={result.euler_work_j_kg/1e3:.1f} kJ/kg · Mw1={result.mach_w1:.2f}",
        fontsize=11,
    )
    fig.tight_layout()
    return fig


def figure_cascade_outline(geom: BladeGeometry, n_blades: int = 3, *, dpi: int = 120):
    fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=dpi, facecolor="#f5f2e8")
    ax.set_facecolor("#faf8f0")
    for poly in cascade_blade_outlines(geom, n_blades=n_blades):
        xs = [p[0] * 1000 for p in poly]
        ys = [p[1] * 1000 for p in poly]
        ax.fill(xs, ys, facecolor="#ccc", edgecolor="#000", lw=1.1)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]")
    ax.set_ylabel("y [mm]")
    ax.set_title(f"Cascade · {n_blades} blades · c={geom.chord_m*1000:.1f} mm")
    ax.grid(True, alpha=0.35)
    fig.tight_layout()
    return fig


def figure_cp_vs_x(
    x_c_upper: Sequence[float],
    cp_upper: Sequence[float],
    x_c_lower: Sequence[float],
    cp_lower: Sequence[float],
    *,
    title: str = "Surface Cp",
    dpi: int = 120,
    shock_x: Sequence[float] | None = None,
    shock_labels: Sequence[str] | None = None,
):
    """x_c_upper / cp_upper = pressure side; lower lists = suction side (historical names)."""
    fig, ax = plt.subplots(figsize=(7.5, 4.0), dpi=dpi, facecolor="#f5f2e8")
    ax.set_facecolor("#faf8f0")
    ax.plot(x_c_upper, cp_upper, "b-", lw=1.8, label="Pressure side (PS)")
    ax.plot(x_c_lower, cp_lower, "r-", lw=1.8, label="Suction side (SS)")
    if shock_x:
        for i, x in enumerate(shock_x):
            lab = None
            if shock_labels and i < len(shock_labels):
                lab = shock_labels[i]
            ax.axvline(float(x), color="#aa5500", ls="--", lw=1.2, alpha=0.85, label=lab)
    ax.invert_yaxis()
    ax.set_xlabel("x/c")
    ax.set_ylabel("Cp = (p − p_ref) / q_ref")
    ax.set_title(title)
    # de-dupe legend entries
    handles, labels = ax.get_legend_handles_labels()
    seen = set()
    uh, ul = [], []
    for h, lab in zip(handles, labels):
        if lab in seen or not lab:
            continue
        seen.add(lab)
        uh.append(h)
        ul.append(lab)
    ax.legend(uh, ul, fontsize=9)
    ax.grid(True, alpha=0.35)
    fig.tight_layout()
    return fig
