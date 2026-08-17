# Paper / NASA validation of ImpulseCalc

ImpulseCalc is a **2D relative-frame impulse cascade** design board (mean-line +
OpenFOAM-12 `shockFluid` + engineering video). It is **not** a full 3D
stator–rotor URANS stage code. Validation therefore targets quantities that are
actually comparable to published impulse-turbine studies.

## Reference studies

| Source | Role |
|--------|------|
| **Sebelev et al., ETC2019-165** | Baseline small-scale supersonic axial impulse stage geometry & BCs |
| **Seume, Peters, Kunte (2017)** J. Phys. Conf. Ser. 821 012023 | 10 kW ORC ethanol impulse turbine scales + impulse rotor physics |
| **NASA / Goldman TN D-4421** (NTRS 19680010807 family) | Supersonic impulse cascade geometry limits |
| **Perfect-gas normal-shock tables** (NASA / Anderson / Hill–Peterson) | Gas-dynamics unit tests |

### ETC2019-165 Table 1 (baseline stage)

| Parameter | Value | ImpulseCalc mapping |
|-----------|-------|---------------------|
| \(D_m\) | 103.5 mm | \(r_m = D_m/2 = 51.75\) mm |
| \(\alpha_1\) | 20° | recovered from \(\beta_1,U\) |
| \(\beta_1=\beta_2^*\) | 36° | flow \(\beta_1=-36^\circ\) (sign for \(C_t=W_t+U\)) |
| \(Z_2\) | 55 | pitch \(s=2\pi r_m/Z\) |
| \(B_2\) | 9.5 mm | axial chord |
| \(l_2\) | 10 mm | span |
| \(p_0^*\) | 0.51 MPa | cascade static proxy \(\sqrt{p_0 p_2}\) |
| \(T_0^*\) | 320 K | \(T_1\) |
| \(p_2\) | 0.102 MPa | stage exit (nozzle expands; rotor ≈ impulse) |

Velocity-triangle identity used for the paper angles:

\[
\frac{U}{W_1} = \bigl|\sin\beta - \cos\beta\cdot\tan\alpha\bigr|
\quad(\beta=36^\circ,\ \alpha=20^\circ\ \Rightarrow\ U/W_1 \approx 0.2933)
\]

Pure impulse lock: \(\beta_2=-\beta_1\), reaction \(=0\), \(|W_2|=|W_1|\).

### Seume 2017 (geometry / kinematics)

| Parameter | Value |
|-----------|-------|
| \(D_\mathrm{shroud}\) | 63.1 mm |
| Blade height \(h\) | 3.43 mm |
| Tip gap \(\delta\) | 0.13 mm |
| \(N_R\) / \(N_S\) | 33 / 8 |
| \(n_\mathrm{design}\) | 100 000 min⁻¹ |
| \(\varepsilon_\mathrm{design}\) | 40 % |
| LE / TE radius | 0.2 mm (thickened for abrasion) |
| Rotor | constant-section impulse (zero reaction) |
| Stator | Laval nozzles, \(M>3\) in divergent part |

Working fluid in the paper is **ethanol (+5 % water)** with real-gas EOS.
ImpulseCalc uses an **air perfect-gas proxy** for cascade CFD; match
tip speed, blade count, span, and impulse reaction — not \(\eta_{t-s}\) maps.

## What is matchable vs not

**Matchable (acceptance gates)**

1. Velocity triangles: \(|\alpha_1|\approx20^\circ\), \(|\beta_1|=36^\circ\), \(U/W_1\)
2. Pure impulse: \(\beta_2=-\beta_1\), reaction \(\approx0\), \(|U\Delta C_\theta|=2UW|\sin\beta|\)
3. Pitch / solidity from \(Z_2,r_m,B_2\)
4. Normal-shock ratios at \(\gamma=1.4\) (textbook / NASA tables)
5. Goldman axial-exit guideline: feasible \(M_w < 1/\cos\beta\)
6. CFD: blade **walls** in the volume mesh (not STL overlay only)
7. CFD: physical \(T,p,|U|\); non-uniform \(p\) (shocks); peak \(M\gtrsim0.7\,M_{w1}\)

**Not matchable without architecture change**

- Full-stage \(\eta_{t-s}(u/C_0)\) curves (partial admission, disc friction, 3D hub)
- Ethanol real-gas ORC performance maps
- Rotor–stator unsteady interaction (URANS / stage coupling)
- Hub endwall contouring / blade sweep (ETC Part II topics)

## How to re-run

```bash
# Offline paper + NASA gates (no OpenFOAM)
python -m pytest tests/test_paper_validation.py -q

# Full ETC mean-line + mesh + shockFluid + field gates
python scratch/run_etc_cfd_val.py
# → scratch/etc2019_cfd_validation.json
# → output/openfoam_cases/etc2019_165_rotor/
```

Configs:

- `configs/validation/etc2019_165_rotor.json`
- `configs/validation/seume2017_scale.json`

Python API:

```python
from impulsecalc.paper_validation import (
    run_all_paper_validations,
    run_etc2019_cfd_validation,
    write_validation_report,
)
```

## CFD hardening for supersonic paper cases

Published stages run at **relative inlet Mach ~1.2–1.6**. With stair-step blade
walls from `topoSet`/`subsetMesh`, `shockFluid` can FPE (\(T\le0\)) unless:

| Setting | Paper / high-\(M_w\) choice |
|---------|----------------------------|
| Flux scheme | **Tadmor** (more dissipative than Kurganov) |
| Reconstruction | **Minmod** / MinmodV |
| `maxCo` | **0.03** when \(M_w\ge1.2\) |
| Outlet pressure | **~0.95 \(p_1\)** for pure impulse (no rotor expansion) |
| Blade \(t/c\) | **~0.18** paper bucket (not 0.50 educational default) |
| LE fillet | Seume 0.2 mm scale → \(r_\mathrm{LE}/c\approx0.02\)–0.03 |

## Evidence snapshot (ETC case, clean solver exit)

Re-run produces `scratch/etc2019_cfd_validation.json`. Typical match:

| Gate | Result |
|------|--------|
| \(|\alpha_1|\) | 20.00° |
| \(\beta_1,\beta_2\) | −36°, +36° |
| reaction | 0 |
| NASA \(M=2\) shock \(p_2/p_1\) | 4.5 exact |
| blade wall faces | ~186–200 |
| peak Mach proxy | ~1.8 (LE shocks above \(M_{w1}=1.35\)) |
| \(T_\min\) | >250 K (no collapse) |
| solver | `foamRun -solver shockFluid` exit 0 |

## NASA NTRS cross-checks

- Goldman, L. J. & Scullin, V. J., **TN D-4421** (1968) — analytical supersonic turbomachinery blading design  
- Related NTRS: supersonic turbine cascade / partial-admission experiments (Goldman series)  
- Perfect-gas normal-shock identities used as unit tests (independent of CFD)

## Failure policy

If a paper gate fails after a code change:

1. Treat the software as **wrong** for that gate (not the paper).
2. Fix mean-line / mesh / BCs / schemes until `run_all_paper_validations` and
   `validate_cfd_solution` both report `ok: true`.
3. Re-run `scratch/run_etc_cfd_val.py` and commit the JSON evidence.
