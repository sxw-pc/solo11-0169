"""Multi-pose camera calibration, feasibility envelopes and function-scope releases.

The four-corner board gives an exact homography with zero pixel residual even when
radial freedom exists, because every central observation is explained by the
homography itself.  This module therefore never returns a single "pretty" solution
from central data alone:

* intrinsics/extrinsics/distortion are jointly fitted across every board pose;
* unobservable parameters are named explicitly via the Jacobian's numerical rank;
* centre and edge landmark errors are bounded over every imaging model that stays
  compatible with the original evidence and the lens priors;
* the most informative next board placement is derived from the null directions;
* parking-assist (centre-only, no steering authority) and lane-keeping (full field,
  40 m lateral error <= 0.2 m across loads and speeds) are separate activation
  profiles and cannot be substituted for one another.

Pure standard library: the deployment environment ships without numpy/scipy.
"""
import json
import math
import hashlib

from .store import connect, at
from . import adas

# ---------------------------------------------------------------- policy ----
PARKING_MAX_SPEED_KPH = 10
LANE_REQUIRED_SPEED_KPH = 30
LANE_DISTANCE_M = 40.0
LANE_LATERAL_LIMIT_M = 0.20
CENTRE_RESIDUAL_LIMIT_PX = 2.0
EDGE_RESIDUAL_LIMIT_PX = 2.0
EDGE_OFFAXIS_RADIUS_PX = 800.0
MIN_OFFAXIS_RADIUS_PX = 400.0
MIN_LOAD_LEVELS = 2
DEFAULT_MOUNT_HEIGHT_M = 1.5
# A 1 mrad pitch drift is penalised like a sub-pixel reprojection mismatch,
# strong enough to break the distortion/pose nullspace without over-constraining
# genuine load-induced pitch deformation.
PITCH_PRIOR_WEIGHT = 800.0

# Conservative physical priors for an automotive wide-angle unit.  They bound the
# family of imaging models that remain compatible with centre-only evidence.
PRIOR = {
    'k1': (-0.58, 0.05),
    'k2': (-0.20, 0.20),
    'p1': (-0.02, 0.02),
    'p2': (-0.02, 0.02),
}
LM_MAX_ITERS = 60
LM_LAMBDA0 = 1e-3
LM_EPS = 1e-10
RANK_TOL = 1e-8
DIST_NAMES = ('k1', 'k2', 'p1', 'p2')

# ------------------------------------------------------------- linear algebra
def _matmul(a, b):
    n, m, k = len(a), len(b[0]), len(b)
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        ai = a[i]
        oi = out[i]
        for t in range(k):
            ait = ai[t]
            if ait == 0.0:
                continue
            bt = b[t]
            for j in range(m):
                oi[j] += ait * bt[j]
    return out


def _transpose(a):
    return [list(row) for row in zip(*a)]


def _solve(a, b):
    """Gaussian elimination with partial pivoting; A is n*n, b n-vector."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-14:
            raise ValueError('singular_system')
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _jac_rank_svd(jac):
    """Numerical rank and nullspace via eigen-decomposition of J^T J.

    Jacobi eigenvalues are more than adequate for the small parameter counts here
    and keep the dependency set at the standard library.
    """
    if not jac:
        return 0, []
    jtj = _matmul(_transpose(jac), jac)
    n = len(jtj)
    off = [[0.0] * n for _ in range(n)]
    for i in range(n):
        off[i][i] = 1.0
    for sweep in range(100):
        largest = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                largest = max(largest, abs(jtj[p][q]))
                if abs(jtj[p][q]) < 1e-15:
                    continue
                tau = (jtj[q][q] - jtj[p][p]) / (2.0 * jtj[p][q])
                t = math.copysign(1.0, tau) / (abs(tau) + math.sqrt(1.0 + tau * tau)) if tau != 0.0 else 1.0
                c = 1.0 / math.sqrt(1.0 + t * t)
                s = t * c
                for k in range(n):
                    akp, akq = jtj[k][p], jtj[k][q]
                    jtj[k][p] = c * akp - s * akq
                    jtj[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = jtj[p][k], jtj[q][k]
                    jtj[p][k] = c * apk - s * aqk
                    jtj[q][k] = s * apk + c * aqk
                for k in range(n):
                    okp, okq = off[k][p], off[k][q]
                    off[k][p] = c * okp - s * okq
                    off[k][q] = s * okp + c * okq
        if largest < 1e-14:
            break
    eig = [jtj[i][i] for i in range(n)]
    scale = max([abs(v) for v in eig] + [1.0])
    rank = sum(1 for v in eig if v > RANK_TOL * scale)
    null_vecs = [[off[r][i] for r in range(n)]
                 for i, v in enumerate(eig) if v <= RANK_TOL * scale]
    return rank, null_vecs


def _inverse(a):
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-14:
            raise ValueError('singular_system')
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        pivot_row = [x / pv for x in m[col]]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col]
            m[r] = [m[r][c] - factor * pivot_row[c] for c in range(2 * n)]
        m[col] = pivot_row
    return [row[n:] for row in m]
# ------------------------------------------------------------- camera model --
def project_points(points, intrinsics, rotvec, tvec):
    """OpenCV-convention pinhole + k1,k2 radial and p1,p2 tangential distortion."""
    fx, fy, cx, cy, k1, k2, p1, p2 = intrinsics
    theta = math.sqrt(sum(v * v for v in rotvec))
    if theta < 1e-12:
        r0, r1, r2 = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    else:
        x, y, z = (v / theta for v in rotvec)
        c, s, C = math.cos(theta), math.sin(theta), 1.0 - math.cos(theta)
        r0 = (c + x * x * C, x * y * C - z * s, x * z * C + y * s)
        r1 = (y * x * C + z * s, c + y * y * C, y * z * C - x * s)
        r2 = (z * x * C - y * s, z * y * C + x * s, c + z * z * C)
    out = []
    for X, Y in points:
        xc = r0[0] * X + r0[1] * Y + tvec[0]
        yc = r1[0] * X + r1[1] * Y + tvec[1]
        zc = r2[0] * X + r2[1] * Y + tvec[2]
        xn, yn = xc / zc, yc / zc
        r2v = xn * xn + yn * yn
        radial = 1.0 + r2v * (k1 + r2v * k2)
        xd = xn * radial + 2.0 * p1 * xn * yn + p2 * (r2v + 2.0 * xn * xn)
        yd = yn * radial + p1 * (r2v + 2.0 * yn * yn) + 2.0 * p2 * xn * yn
        out.append((fx * xd + cx, fy * yd + cy))
    return out


def undistort_pixel(uv, intrinsics, max_iter=40):
    """Inverse of the distortion map via Newton iteration on the 2D residual."""
    fx, fy, cx, cy, k1, k2, p1, p2 = intrinsics
    xd, yd = (uv[0] - cx) / fx, (uv[1] - cy) / fy
    x, y = xd, yd
    for _ in range(max_iter):
        r2v = x * x + y * y
        radial = 1.0 + r2v * (k1 + r2v * k2)
        dx = 2.0 * p1 * x * y + p2 * (r2v + 2.0 * x * x)
        dy = p1 * (r2v + 2.0 * y * y) + 2.0 * p2 * x * y
        fxv, fyv = x * radial + dx - xd, y * radial + dy - yd
        d_radial = k1 + 2.0 * k2 * r2v
        j00 = radial + 2.0 * x * x * d_radial + 2.0 * p1 * y + 6.0 * p2 * x
        j01 = 2.0 * x * y * d_radial + 2.0 * p1 * x + 2.0 * p2 * y
        j10 = 2.0 * x * y * d_radial + 2.0 * p1 * x + 2.0 * p2 * y
        j11 = radial + 2.0 * y * y * d_radial + 6.0 * p1 * y + 2.0 * p2 * x
        det = j00 * j11 - j01 * j10
        if abs(det) < 1e-15:
            break
        x -= (j11 * fxv - j01 * fyv) / det
        y -= (-j10 * fxv + j00 * fyv) / det
        if abs(fxv) + abs(fyv) < 1e-11:
            break
    return x, y


def homography(world, pixels):
    """Normalised DLT homography mapping planar world points to pixels."""
    mx = sum(p[0] for p in world) / len(world)
    my = sum(p[1] for p in world) / len(world)
    sx = (math.sqrt(2.0) /
          (sum(math.hypot(p[0] - mx, p[1] - my) for p in world) / len(world) or 1.0))
    sy = (math.sqrt(2.0) /
          (sum(math.hypot(p[0] - mx, p[1] - my) for p in world) / len(world) or 1.0))
    Tw = [[sx, 0.0, -sx * mx], [0.0, sy, -sy * my], [0.0, 0.0, 1.0]]
    ux = sum(p[0] for p in pixels) / len(pixels)
    uy = sum(p[1] for p in pixels) / len(pixels)
    su = (math.sqrt(2.0) /
          (sum(math.hypot(p[0] - ux, p[1] - uy) for p in pixels) / len(pixels) or 1.0))
    Tp = [[su, 0.0, -su * ux], [0.0, su, -su * uy], [0.0, 0.0, 1.0]]
    rows = []
    for (X, Y), (u, v) in zip(world, pixels):
        Xn = sx * X - sx * mx
        Yn = sy * Y - sy * my
        un = su * u - su * ux
        vn = su * v - su * uy
        rows.append([-Xn, -Yn, -1.0, 0.0, 0.0, 0.0, un * Xn, un * Yn, un])
        rows.append([0.0, 0.0, 0.0, -Xn, -Yn, -1.0, vn * Xn, vn * Yn, vn])
    a = _matmul(_transpose(rows), rows)
    n = 9
    off = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(100):
        largest = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                largest = max(largest, abs(a[p][q]))
                if abs(a[p][q]) < 1e-15:
                    continue
                tau = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, tau) / (abs(tau) + math.sqrt(1.0 + tau * tau)) if tau != 0.0 else 1.0
                c = 1.0 / math.sqrt(1.0 + t * t)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    okp, okq = off[k][p], off[k][q]
                    off[k][p] = c * okp - s * okq
                    off[k][q] = s * okp + c * okq
        if largest < 1e-14:
            break
    eig = [(a[i][i], [off[r][i] for r in range(n)]) for i in range(n)]
    _, vec = min(eig, key=lambda item: item[0])
    flat = [vec[r][0] if isinstance(vec[r], list) else vec[r] for r in range(n)]
    Hn = [flat[0:3], flat[3:6], flat[6:9]]
    return _matmul(_inverse(Tp), _matmul(Hn, Tw))

def reprojection_errors(world, pixels, intrinsics, rotvec, tvec):
    pred = project_points(world, intrinsics, rotvec, tvec)
    return [math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pred, pixels)]


# --------------------------------------------------------- ground / lane math
def ground_ray(uv, intrinsics, pitch_rad):
    """Unit ray (camera frame, z forward) for a pixel under mount pitch."""
    xn, yn = undistort_pixel(uv, intrinsics)
    rx, ry, rz = xn, yn, 1.0
    norm = math.sqrt(rx * rx + ry * ry + rz * rz)
    rx, ry, rz = rx / norm, ry / norm, rz / norm
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    return rx, ry * cp - rz * sp, ry * sp + rz * cp


def pixel_to_ground(uv, intrinsics, pitch_rad, height_m):
    rx, ry_w, rz_w = ground_ray(uv, intrinsics, pitch_rad)
    if ry_w <= 1e-9:
        raise ValueError('ray_never_reaches_ground')
    t = height_m / ry_w
    return t * rx, t * rz_w


def ground_to_pixel(xy, intrinsics, pitch_rad, height_m):
    # World frame: camera at origin, y points down, ground plane y = +height_m.
    x, z = xy
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    xc = x
    yc = cp * height_m + sp * z
    zc = -sp * height_m + cp * z
    xn, yn = xc / zc, yc / zc
    fx, fy, cx, cy, k1, k2, p1, p2 = intrinsics
    r2v = xn * xn + yn * yn
    radial = 1.0 + r2v * (k1 + r2v * k2)
    xd = xn * radial + 2.0 * p1 * xn * yn + p2 * (r2v + 2.0 * xn * xn)
    yd = yn * radial + p1 * (r2v + 2.0 * yn * yn) + 2.0 * p2 * xn * yn
    return fx * xd + cx, fy * yd + cy

# ------------------------------------------------------------------ the model
class CalibrationModel:
    """Parameter vector: [fx, fy, cx, cy, k1, k2, p1, p2] then 6 per view."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.views = []
        # Soft priors {parameter_index: (value, weight)} used to break the
        # ground-plane distortion/pose coupling: a board's measured mount pitch
        # anchors that view's pitch component instead of letting it absorb k1.
        self.priors = {}
        self.params = [width * 0.9, width * 0.9, width / 2.0, height / 2.0,
                       0.0, 0.0, 0.0, 0.0]

    @staticmethod
    def _unpack(params, views):
        intr = tuple(params[:8])
        poses = []
        for i in range(len(views)):
            base = 8 + 6 * i
            poses.append((tuple(params[base:base + 3]), tuple(params[base + 3:base + 6])))
        return intr, poses

    def residuals(self, params):
        intr, poses = self._unpack(params, self.views)
        out = []
        for view, (r, t) in zip(self.views, poses):
            pred = project_points(view['world_mm'], intr, r, t)
            for (u, v), (pu, pv) in zip(view['pixels'], pred):
                out.append(pu - u)
                out.append(pv - v)
        # Append pitch prior residuals (weighted so a 1 mrad deviation costs the
        # same as a fraction-of-a-pixel mismatch).
        for idx, (value, weight) in self.priors.items():
            out.append((params[idx] - value) * weight)
        return out

    def jacobian(self, params):
        r0 = self.residuals(params)
        nres, npar = len(r0), len(params)
        jac = [[0.0] * npar for _ in range(nres)]
        for j in range(npar):
            step = max(1e-6, abs(params[j]) * 1e-7)
            bumped = params[:]
            bumped[j] += step
            r1 = self.residuals(bumped)
            for i in range(nres):
                jac[i][j] = (r1[i] - r0[i]) / step
        return jac

    def initial_from_homographies(self):
        """Initialise intrinsics and per-view extrinsics.

        Focal length comes from the principal-point-corrected homography column
        norm.  Ground-plane views get their pose from analytic ray/ground-plane
        intersection (the near-pitch-zero homography cannot separate fy from
        camera height, so that ambiguity is reported later through the rank);
        other views use a rotation-column decomposition.
        """
        cx, cy = self.width / 2.0, self.height / 2.0
        fx_guesses, view_inits = [], []
        for view in self.views:
            H = homography(view['world_mm'], view['pixels'])
            h0, h1, h2 = H[0], H[1], H[2]
            g0 = [h0[0] - cx * h2[0], h0[1] - cy * h2[1], h0[2]]
            fx_view = math.sqrt(sum(v * v for v in g0))
            if not (100.0 < fx_view < self.width * 5.0):
                fx_view = self.width * 0.9
            fx_guesses.append(fx_view)
            if view.get('ground_plane'):
                # Exact ground-board pose matching ground_to_pixel: the board
                # basis rotation (+pi/2 about x) composed with mount pitch, and
                # the height translation rotated with the pitch.
                pitch = view.get('pitch_mrad', 0.0) / 1000.0
                h_mm = DEFAULT_MOUNT_HEIGHT_M * 1000.0
                rv = [math.pi / 2.0 - pitch, 0.0, 0.0]
                t_mm = [0.0, h_mm, -h_mm * math.sin(pitch)]
                view_inits.append((rv, t_mm))
                continue
            g1 = [h1[0] - cx * h2[0], h1[1] - cy * h2[1], h1[2]]

            def norm(v):
                d = math.sqrt(sum(x * x for x in v)) or 1e-12
                return [x / d for x in v]
            r1 = norm(g0)
            r2 = norm(g1)
            dot = sum(a * b for a, b in zip(r1, r2))
            r2 = norm([b - dot * a for a, b in zip(r1, r2)])
            r3 = [r1[1] * r2[2] - r1[2] * r2[1],
                  r1[2] * r2[0] - r1[0] * r2[2],
                  r1[0] * r2[1] - r1[1] * r2[0]]
            t_mm = [h2[0] - cx * h2[2], h2[1] - cy * h2[1], h2[2]]
            if t_mm[2] < 0:
                r1, r2, r3, t_mm = [-v for v in r1], [-v for v in r2], [-v for v in r3], [-v for v in t_mm]
            tr = r1[0] + r2[1] + r3[2]
            theta = math.acos(max(-1.0, min(1.0, (tr - 1.0) / 2.0)))
            if theta < 1e-10:
                rv = [0.0, 0.0, 0.0]
            else:
                w = 1.0 / (2.0 * math.sin(theta))
                rv = [(r2[2] - r3[1]) * w * theta,
                      (r3[0] - r1[2]) * w * theta,
                      (r1[1] - r2[0]) * w * theta]
            view_inits.append((rv, t_mm))
        fx0 = sum(fx_guesses) / len(fx_guesses)
        self.params[:8] = [fx0, fx0, cx, cy, 0.0, 0.0, 0.0, 0.0]
        for i, (rv, t_mm) in enumerate(view_inits):
            base = 8 + 6 * i
            self.params[base:base + 6] = list(rv) + list(t_mm)
    def fit(self):
        """Two-stage fit.

        Stage 1 anchors cx/cy at the image centre and distortion at zero, fitting
        only focal length and per-view extrinsics: with centre-only evidence a
        free joint fit would manufacture an arbitrary pretty k1, so the distortion
        freedom is carried by the feasibility envelope instead.  Stage 2 runs a
        full joint fit (distortion included) only when off-axis views make the
        radial terms genuinely observable; otherwise the anchored nominal is kept.
        """
        self.initial_from_homographies()
        init_params = self.params[:]
        params = init_params[:]
        params[2], params[3] = self.width / 2.0, self.height / 2.0
        for j in range(4, 8):
            params[j] = 0.0
        params = self._lm(params, {2, 3, 4, 5, 6, 7})
        self.free_fit_done = False
        if self._has_offaxis_views():
            anchored_rms = self._rms(params)
            # Stage 2 restarts from the analytic ground poses: the anchored fit
            # deliberately bends poses to hide distortion, and seeding from those
            # bent poses traps the joint fit in a wrong basin.
            #
            # Only k1 is fitted.  k2 and the tangential terms are not separable
            # from k1 under any achievable single-camera geometry here (the rank
            # analysis names them explicitly), so freeing them manufactures a
            # non-unique answer; their freedom is carried by the envelope instead.
            base = init_params[:]
            base[2], base[3] = self.width / 2.0, self.height / 2.0
            candidates = []
            lo, hi = PRIOR['k1']
            seeds = [0.0] + [lo + (hi - lo) * k / 8.0 for k in range(9)]
            for seed in seeds:
                q = base[:]
                q[4] = seed
                candidates.append(self._lm(q, {2, 3, 5, 6, 7}))
            free_params = min(candidates, key=self._rms)
            if self._rms(free_params) < anchored_rms:
                params, self.free_fit_done = free_params, True
        self.params = params
        return self.summary(params)

    def _has_offaxis_views(self):
        cx, cy = self.width / 2.0, self.height / 2.0
        for view in self.views:
            if any(math.hypot(u - cx, v - cy) >= MIN_OFFAXIS_RADIUS_PX for u, v in view['pixels']):
                return True
        return False

    def _rms(self, params):
        res = self.residuals(params)
        return math.sqrt(sum(v * v for v in res) / max(1, len(res)))

    def _lm(self, params, fixed, max_iters=LM_MAX_ITERS):
        lam = LM_LAMBDA0
        free_idx = [i for i in range(len(params)) if i not in fixed]
        for _ in range(max_iters):
            res = self.residuals(params)
            cost = sum(v * v for v in res)
            full = self.jacobian(params)
            jac = [[row[i] for i in free_idx] for row in full]
            jtj = _matmul(_transpose(jac), jac)
            g = [sum(-j[k] * r for j, r in zip(jac, res)) for k in range(len(free_idx))]
            diag = [jtj[k][k] if jtj[k][k] > 1e-12 else 1e-12 for k in range(len(free_idx))]
            accepted = False
            for _ in range(12):
                a = [row[:] for row in jtj]
                for k in range(len(a)):
                    a[k][k] += lam * diag[k]
                try:
                    delta = _solve(a, g)
                except ValueError:
                    lam *= 10.0
                    continue
                trial = params[:]
                for k, d in zip(free_idx, delta):
                    trial[k] += d
                # Keep distortion coefficients inside the physical lens prior so
                # an unconstrained LM step cannot escape into non-physical k1.
                for dist_j, name in enumerate(DIST_NAMES):
                    idx = 4 + dist_j
                    if idx in free_idx:
                        lo, hi = PRIOR[name]
                        trial[idx] = min(hi, max(lo, trial[idx]))
                trial_cost = sum(v * v for v in self.residuals(trial))
                if trial_cost < cost:
                    params, lam, accepted = trial, max(lam / 10.0, 1e-12), True
                    break
                lam *= 10.0
            if not accepted or cost < 1e-18:
                break
        return params

    def summary(self, params):
        intr, poses = self._unpack(params, self.views)
        per_view = []
        for view, (r, t) in zip(self.views, poses):
            errs = reprojection_errors(view['world_mm'], view['pixels'], intr, r, t)
            per_view.append({'board_id': view['board_id'],
                             'captured_at': view.get('captured_at'),
                             'rms_px': math.sqrt(sum(e * e for e in errs) / len(errs)),
                             'max_px': max(errs),
                             'ground_plane': view.get('ground_plane', False),
                             'verification': view.get('verification', False)})
        return {'intrinsics': dict(zip(('fx', 'fy', 'cx', 'cy', 'k1', 'k2', 'p1', 'p2'), intr)),
                'poses': [{'board_id': v['board_id'], 'rotvec': list(r), 'tvec_mm': list(t)}
                          for v, (r, t) in zip(self.views, poses)],
                'per_view': per_view}

    def observability(self, params=None):
        """Numerical rank of the joint Jacobian and the named null directions.

        The Jacobian columns are normalised to unit norm first so that millimetre
        translations and radian rotations can be compared on the same scale;
        otherwise a merely small-but-identified parameter looks unobservable.
        """
        params = params or self.params
        jac = self.jacobian(params)
        npar = len(jac[0]) if jac else 0
        scales = []
        for j in range(npar):
            col_norm = math.sqrt(sum(row[j] * row[j] for row in jac)) or 1.0
            scales.append(col_norm)
        normed = [[row[j] / scales[j] for j in range(npar)] for row in jac]
        rank, null_vecs = _jac_rank_svd(normed)
        names = ['fx', 'fy', 'cx', 'cy', 'k1', 'k2', 'p1', 'p2']
        for i, view in enumerate(self.views):
            names += [f'view{i}:rx', f'view{i}:ry', f'view{i}:rz',
                      f'view{i}:tx', f'view{i}:ty', f'view{i}:tz']
        directions = []
        for vec in null_vecs:
            mag = math.sqrt(sum(v * v for v in vec)) or 1.0
            top = sorted(enumerate(vec), key=lambda iv: abs(iv[1]), reverse=True)[:4]
            directions.append([{'param': names[i], 'weight': round(v / mag, 6)} for i, v in top])
        unobservable = set()
        for vec in null_vecs:
            for i, v in enumerate(vec):
                if abs(v) > 0.2:
                    unobservable.add(names[i])
        return {'rank': rank, 'parameter_count': len(names),
                'fit_mode': 'free_joint' if getattr(self, 'free_fit_done', False) else 'anchored_pinhole',
                'unobservable': sorted(unobservable), 'null_directions': directions}

# ------------------------------------------------------------- feasibility box
def _refit_with_fixed(model, warm, fixed, max_iters=25):
    """Re-optimise every parameter except the indices in `fixed`."""
    free_idx = [i for i in range(len(warm)) if i not in fixed]
    params = warm[:]
    lam = LM_LAMBDA0
    for _ in range(max_iters):
        res = model.residuals(params)
        cost = sum(v * v for v in res)
        full = model.jacobian(params)
        jac = [[row[i] for i in free_idx] for row in full]
        jtj = _matmul(_transpose(jac), jac)
        g = [sum(-j[k] * r for j, r in zip(jac, res)) for k in range(len(free_idx))]
        diag = [jtj[k][k] if jtj[k][k] > 1e-12 else 1e-12 for k in range(len(free_idx))]
        accepted = False
        for _ in range(10):
            a = [row[:] for row in jtj]
            for k in range(len(a)):
                a[k][k] += lam * diag[k]
            try:
                delta = _solve(a, g)
            except ValueError:
                lam *= 10.0
                continue
            trial = params[:]
            for k, d in zip(free_idx, delta):
                trial[k] += d
            trial_cost = sum(v * v for v in model.residuals(trial))
            if trial_cost < cost:
                params, lam, accepted = trial, max(lam / 10.0, 1e-12), True
                break
            lam *= 10.0
        if not accepted or cost < 1e-18:
            break
    rms = math.sqrt(sum(v * v for v in model.residuals(params)) / max(1, len(model.residuals(params))))
    return params, rms


def feasibility_envelope(model, fit_params, tol_px):
    """Every imaging model compatible with the evidence and the lens priors.

    For each distortion direction the largest perturbation that can still be
    re-fitted (all other identifiable parameters free) inside the pixel tolerance
    is found by bisection; the resulting interval is intersected with the physical
    prior.  Coefficients that the nominal fit had to fix because they are not
    separable (k2, tangential terms) stay fixed here too, so the envelope measures
    the genuinely surviving freedom rather than re-opening an unidentifiable
    nullspace.  Boundary vertices are checked by their own re-fit RMS.
    """
    base_rms = math.sqrt(sum(v * v for v in model.residuals(fit_params)) /
                         max(1, len(model.residuals(fit_params))))
    limit = max(tol_px, base_rms + 1e-6)
    # In free_joint mode only k1 is identifiable; k2,p1,p2 stay fixed.  The
    # envelope measures distortion freedom alone, so the already-solved focal
    # lengths and principal point are held fixed too — otherwise fx/fy would
    # re-absorb every k1 perturbation and the box would never shrink.
    unidentifiable_dist = {5, 6, 7} if getattr(model, 'free_fit_done', False) else set()
    envelope_fixed = {0, 1, 2, 3} | unidentifiable_dist if getattr(model, 'free_fit_done', False) else unidentifiable_dist
    intervals = {}
    for j, name in enumerate(DIST_NAMES):
        idx = 4 + j
        if idx in unidentifiable_dist:
            intervals[name] = [fit_params[idx], fit_params[idx]]
            continue
        lo_prior, hi_prior = PRIOR[name]
        reach_lo, reach_hi = fit_params[idx] - lo_prior, hi_prior - fit_params[idx]
        spans = []
        for direction, reach in ((-1.0, reach_lo), (1.0, reach_hi)):
            low, high = 0.0, max(reach, 1e-6)
            for _ in range(8):
                mid = (low + high) / 2.0
                trial = fit_params[:]
                trial[idx] = fit_params[idx] + direction * mid
                _, rms = _refit_with_fixed(model, trial, {idx} | envelope_fixed)
                if rms <= limit:
                    low = mid
                else:
                    high = mid
            spans.append(min(reach, low))
        intervals[name] = [fit_params[idx] - spans[0], fit_params[idx] + spans[1]]
    # Explicit vertices: nominal plus every corner of the distortion box.  A
    # vertex is admissible when *its own* re-fit stays inside the tolerance;
    # vertices that fail are rejected rather than silently shrinking the box.
    vertices = [fit_params[:]]
    rejected = []
    keys = list(intervals)
    for mask in range(1, 1 << len(keys)):
        v = fit_params[:]
        for bit, name in enumerate(keys):
            v[4 + bit] = intervals[name][1 if (mask >> bit) & 1 else 0]
        refit, rms = _refit_with_fixed(
            model, v, {4 + k for k in range(len(keys))} | envelope_fixed)
        if rms <= limit:
            vertices.append(refit)
        else:
            rejected.append({'mask': mask, 'rms_px': rms})
    return {'base_rms_px': base_rms, 'tolerance_px': tol_px,
            'distortion_intervals': intervals, 'vertices': vertices,
            'rejected_vertices': rejected}


def _distort_normalized(xn, yn, intr):
    fx, fy, cx, cy, k1, k2, p1, p2 = intr
    r2v = xn * xn + yn * yn
    radial = 1.0 + r2v * (k1 + r2v * k2)
    xd = xn * radial + 2.0 * p1 * xn * yn + p2 * (r2v + 2.0 * xn * xn)
    yd = yn * radial + p1 * (r2v + 2.0 * yn * yn) + 2.0 * p2 * xn * yn
    return fx * xd + cx, fy * yd + cy


def distortion_map_delta(pixel, nom_intr, alt_intr):
    """Pixel discrepancy for one normalized ray re-distorted by another model."""
    xn, yn = undistort_pixel(pixel, nom_intr)
    p0 = _distort_normalized(xn, yn, nom_intr)
    p1 = _distort_normalized(xn, yn, alt_intr)
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def landmark_bounds(model, vertices, poses, height_m=DEFAULT_MOUNT_HEIGHT_M):
    """Worst-case centre/edge pixel and 40 m lane lateral error over the envelope."""
    w, h = model.width, model.height
    centre = (w / 2.0, h / 2.0)
    edge_pixels = [(w / 2.0 + s * EDGE_OFFAXIS_RADIUS_PX, h / 2.0) for s in (-1.0, 1.0)]
    pitch_set = sorted({0.0} | {math.radians(p['pitch_mdeg'] / 1000.0) for p in poses})
    nominal = vertices[0]
    nom_intr, _ = CalibrationModel._unpack(nominal, model.views)
    nom_fx = nom_intr[0]
    centre_px = edge_px = centre_distortion_px = 0.0
    lane40 = []
    edge40 = []
    for vert in vertices[1:]:
        intr, _ = CalibrationModel._unpack(vert, model.views)
        # Total envelope discrepancy (includes focal/principal-point drift).
        centre_px = max(centre_px, distortion_map_delta(centre, nom_intr, intr))
        # Distortion-only discrepancy at the principal-point ray: the radial and
        # tangential terms vanish at r=0, so this is exactly zero regardless of
        # k1..p2.  This is the quantity an independent centre landmark can verify
        # for the parking profile without any off-axis evidence.
        dist_only = tuple(nom_intr[:4]) + tuple(intr[4:])
        centre_distortion_px = max(
            centre_distortion_px, distortion_map_delta(centre, nom_intr, dist_only))
        for pixel in edge_pixels:
            delta = distortion_map_delta(pixel, nom_intr, intr)
            edge_px = max(edge_px, delta)
            # Angular error of the edge ray, expressed as lateral error at 40 m.
            edge40.append(math.atan2(delta, nom_fx) * LANE_DISTANCE_M)
        for pitch in pitch_set:
            for sign in (-1.0, 1.0):
                pixel0 = ground_to_pixel((sign * 1.75, LANE_DISTANCE_M), nom_intr, pitch, height_m)
                try:
                    x_alt, _ = pixel_to_ground(pixel0, intr, pitch, height_m)
                except ValueError:
                    continue
                lane40.append(abs(x_alt - sign * 1.75))
    return {
        'centre_landmark_max_px': round(centre_px, 4),
        'centre_distortion_only_px': round(centre_distortion_px, 6),
        'edge_landmark_max_px': round(edge_px, 4),
        'lane_lateral_max_m_at_40m': round(max(edge40 or [0.0]), 4),
        'lane_lateral_ground_backprojection_m': round(max(lane40 or [0.0]), 4),
        'pitches_evaluated_mrad': sorted({round(p * 1000.0, 6) for p in pitch_set}),
    }

# ------------------------------------------------------------ evidence coverage
def _evidence_hash(evidence):
    payload = json.dumps({
        'camera': evidence['camera']['id'],
        'views': [[v['board_id'], v['captured_at'], v['pixels']] for v in evidence['view_groups']],
        'poses': [[p['id'], p['speed_kph'], p['pitch_mdeg'], p['load_kg']] for p in evidence['road_poses']],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def evidence_coverage(evidence):
    cam = evidence['camera']
    cx, cy = cam['width_px'] / 2.0, cam['height_px'] / 2.0
    max_radius = 0.0
    for vg in evidence['view_groups']:
        for u, v in vg['pixels']:
            max_radius = max(max_radius, math.hypot(u - cx, v - cy))
    poses = evidence['road_poses']
    loads = sorted({p['load_kg'] for p in poses})
    pitches = sorted({p['pitch_mdeg'] for p in poses})
    return {
        'view_count': len(evidence['view_groups']),
        'max_corner_radius_px': round(max_radius, 3),
        'has_offaxis_views': max_radius >= MIN_OFFAXIS_RADIUS_PX,
        'has_independent_verification': any(vg.get('verification') for vg in evidence['view_groups']),
        'has_ground_boards': any(vg.get('ground_plane') for vg in evidence['view_groups']),
        'load_levels_kg': loads,
        'load_level_count': len(loads),
        'pitch_levels_mdeg': pitches,
        'max_speed_kph': max((p['speed_kph'] for p in poses), default=0),
        'pose_count': len(poses),
    }


def suggest_next_target(evidence, fit_params, envelope_bounds):
    """Most informative next board placement: a ground point whose projection
    lands on the 800 px off-axis ring where the current envelope is
    unconstrained, plus the dynamic samples needed to make the model unique."""
    cam = evidence['camera']
    intr = tuple(fit_params[:8])
    poses = evidence['road_poses']
    pitch = math.radians((sum(p['pitch_mdeg'] for p in poses) / len(poses)) / 1000.0) if poses else 0.0
    cx, cy = cam['width_px'] / 2.0, cam['height_px'] / 2.0
    # Sweep azimuth/depth inside the image; keep the in-frame point closest to the
    # target off-axis ring, preferring a practical 5-15 m working distance.
    best = None
    for azimuth_deg in range(10, 46, 5):
        azimuth = math.radians(azimuth_deg)
        for z_mm in range(4000, 16001, 250):
            x_mm = z_mm * math.tan(azimuth)
            u, v = ground_to_pixel((x_mm / 1000.0, z_mm / 1000.0), intr, pitch, DEFAULT_MOUNT_HEIGHT_M)
            if not (0 <= u < cam['width_px'] and 0 <= v < cam['height_px']):
                continue
            radius = math.hypot(u - cx, v - cy)
            score = abs(radius - EDGE_OFFAXIS_RADIUS_PX)
            if best is None or score < best[0]:
                best = (score, x_mm, z_mm, radius, azimuth_deg)
    if best is None:
        best = (0.0, 2000.0, 8000.0, 0.0, 25)
    _, x_mm, z_mm, radius, azimuth_deg = best
    required = ['offaxis_board_views_radius_ge_800px']
    loads = {p['load_kg'] for p in poses}
    if len(loads) < MIN_LOAD_LEVELS:
        required.append('repeat_sampling_at_second_load_level')
    if max((p['speed_kph'] for p in poses), default=0) < LANE_REQUIRED_SPEED_KPH:
        required.append('dynamic_pitch_sample_at_speed_ge_30kph')
    if not any(vg['verification'] for vg in evidence['view_groups']):
        required.append('independent_landmark_verification_board')
    return {
        'kind': 'offaxis_ground_board',
        'ground_x_mm': round(x_mm, 1),
        'ground_z_mm': round(z_mm, 1),
        'azimuth_deg': azimuth_deg,
        'target_pixel_radius_px': round(radius, 2),
        'edge_uncertainty_px_today': envelope_bounds['edge_landmark_max_px'],
        'required_next_evidence': required,
    }


def solve_calibration(evidence, tol_px=CENTRE_RESIDUAL_LIMIT_PX):
    """Joint intrinsics + per-view extrinsics fit; never hides unobservability."""
    if not evidence['view_groups']:
        raise ValueError('insufficient_observations')
    model = CalibrationModel(evidence['camera']['width_px'], evidence['camera']['height_px'])
    model.views = evidence['view_groups']
    # Anchor each ground view's mount-pitch component to its board/road-test
    # measurement so the pose cannot absorb the radial distortion freedom.
    for i, view in enumerate(model.views):
        if view.get('ground_plane'):
            pitch = view.get('pitch_mrad', 0.0) / 1000.0
            # rvec x-component (base+0) carries the combined board-basis and
            # mount pitch rotation about the camera x axis.
            model.priors[8 + 6 * i + 0] = (math.pi / 2.0 - pitch, PITCH_PRIOR_WEIGHT)
    summary = model.fit()
    observability = model.observability()
    envelope = feasibility_envelope(model, model.params, tol_px)
    bounds = landmark_bounds(model, envelope['vertices'], evidence['road_poses'])
    coverage = evidence_coverage(evidence)
    next_target = suggest_next_target(evidence, model.params, bounds)
    return {
        'model': model,
        'params': model.params,
        'summary': summary,
        'observability': observability,
        'envelope': envelope,
        'bounds': bounds,
        'coverage': coverage,
        'next_target': next_target,
    }

# ------------------------------------------------------------- persistence ---
def _derived_source(db, kind, doc):
    raw = json.dumps(doc, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    old = db.execute('SELECT id FROM source WHERE sha=?', (digest,)).fetchone()
    if old:
        return old['id'], True
    return db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)', (kind, digest, raw)).lastrowid, False


def _ensure_mount(db, cam):
    row = db.execute('SELECT * FROM camera_mount WHERE camera_id=?', (cam['id'],)).fetchone()
    if row:
        return row
    sid = db.execute('INSERT INTO camera_mount(camera_id,mount_generation,serial,lens_model,updated_at,source_id) VALUES(?,?,?,?,?,?)',
                     (cam['id'], 0, cam['serial'], cam['lens_model'], None, None)).lastrowid
    return db.execute('SELECT * FROM camera_mount WHERE camera_id=?', (cam['id'],)).fetchone()


def _load_result(db, cal_id):
    row = db.execute('SELECT * FROM calibration WHERE id=?', (cal_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    for key in ('params_json', 'observability_json', 'bounds_json', 'basis_json', 'next_target_json', 'summary_json'):
        out[key.replace('_json', '')] = json.loads(out.pop(key))
    return out


def calibrate(path, camera_id, moment, cal_id=None):
    """Solve against every original observation and persist the candidate set.

    The repair-work order delivery decision is never touched: this writes only
    calibration/activation ledger rows.
    """
    moment = at(moment)
    with connect(path) as db, db:
        evidence = adas._load_calibration_evidence(db, camera_id)
        cam = evidence['camera']
        mount = _ensure_mount(db, cam)
        result = solve_calibration(evidence)
        basis = {'evidence_hash': _evidence_hash(evidence),
                 'view_groups': [[v['board_id'], v['captured_at']] for v in evidence['view_groups']],
                 'road_pose_ids': [p['id'] for p in evidence['road_poses']]}
        default_id = f"CAL-{camera_id}-{basis['evidence_hash']}"
        cid = cal_id or default_id
        doc = {'id': cid, 'camera_id': camera_id, 'at': moment}
        sid, replayed = _derived_source(db, 'calibration', doc)
        if replayed:
            return {'id': cid, 'replayed': True, **_load_result(db, cid)}
        prior = db.execute("SELECT id FROM calibration WHERE camera_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                           (camera_id,)).fetchone()
        db.execute('INSERT INTO calibration VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            cid, camera_id, cam['vin'], mount['serial'], mount['lens_model'], mount['mount_generation'],
            prior['id'] if prior else None,
            'same_mount_evidence_extended' if prior else 'initial_solve',
            json.dumps(result['params']), json.dumps(result['observability']),
            json.dumps(result['bounds']), json.dumps(basis), json.dumps(result['next_target']),
            json.dumps({'per_view': result['summary']['per_view']}),
            'candidate', moment, sid))
        withdrawal = _withdraw_if_overturned(db, camera_id, cid, result, moment, sid)
        return {'id': cid, 'replayed': False, 'status': 'candidate',
                'summary': result['summary'], 'observability': result['observability'],
                'envelope': {'distortion_intervals': result['envelope']['distortion_intervals'],
                             'base_rms_px': result['envelope']['base_rms_px']},
                'bounds': result['bounds'], 'coverage': result['coverage'],
                'next_target': result['next_target'], 'mount_generation': mount['mount_generation'],
                'withdrawal': withdrawal}


def _parking_decision(coverage, bounds, summary):
    """Parking uses only the centre region with no steering authority.

    Radial/tangential distortion vanishes on the principal-point ray, so an
    independent centre landmark can verify parking fitness from centre-only
    evidence; the wide envelope that blocks lane-keeping is irrelevant here.
    """
    reasons = []
    if not coverage['has_independent_verification']:
        reasons.append('independent_landmark_verification_missing')
    centre_ok = bounds['centre_distortion_only_px'] <= CENTRE_RESIDUAL_LIMIT_PX
    fit_ok = all(v['max_px'] <= CENTRE_RESIDUAL_LIMIT_PX for v in summary['per_view'])
    if not centre_ok:
        reasons.append('centre_landmark_error_over_limit')
    if not fit_ok:
        reasons.append('board_reprojection_over_limit')
    if reasons:
        return 'blocked', reasons
    return 'restricted', ['centre_region_only', 'low_speed_static_marks_only', 'steering_control_prohibited']


def _lane_decision(coverage, bounds):
    reasons = []
    if not coverage['has_offaxis_views']:
        reasons.append('offaxis_board_views_missing')
    if coverage['load_level_count'] < MIN_LOAD_LEVELS:
        reasons.append('load_coverage_below_two_levels')
    if coverage['max_speed_kph'] < LANE_REQUIRED_SPEED_KPH:
        reasons.append('no_dynamic_sample_at_speed_ge_30kph')
    if bounds['lane_lateral_max_m_at_40m'] > LANE_LATERAL_LIMIT_M:
        reasons.append('lane_lateral_error_at_40m_over_0.20m')
    if reasons:
        return 'blocked', reasons
    return 'enabled', ['full_field_verified', 'lateral_error_within_0.20m_at_40m']


def release(path, camera_id, profile, moment, activation_id=None, calibration_id=None):
    """Publish one activation profile. Parking and lane keep separate authority."""
    moment = at(moment)
    if profile not in ('parking_assist', 'lane_keeping'):
        raise ValueError('unknown_profile')
    with connect(path) as db, db:
        cam = db.execute('SELECT * FROM camera_unit WHERE id=?', (camera_id,)).fetchone()
        if not cam:
            raise ValueError('unknown_camera')
        mount = _ensure_mount(db, cam)
        if calibration_id is None:
            row = db.execute(
                "SELECT id FROM calibration WHERE camera_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (camera_id,)).fetchone()
            if not row:
                raise ValueError('no_calibration_available')
            calibration_id = row['id']
        cal = db.execute('SELECT * FROM calibration WHERE id=?', (calibration_id,)).fetchone()
        if not cal:
            raise ValueError('unknown_calibration')
        if cal['camera_id'] != camera_id:
            raise ValueError('calibration_camera_mismatch')
        if cal['mount_generation'] != mount['mount_generation']:
            raise ValueError('stale_mount_generation_lens_or_bracket_changed')
        bounds = json.loads(cal['bounds_json'])
        fit_summary = json.loads(cal['summary_json'])
        # Coverage is recomputed from the original observations so the decision is
        # reproducible without trusting any derived row.
        coverage = evidence_coverage(adas._load_calibration_evidence(db, camera_id))
        if profile == 'parking_assist':
            decision, reasons = _parking_decision(coverage, bounds, fit_summary)
            steering = 0
        else:
            decision, reasons = _lane_decision(coverage, bounds)
            steering = 1 if decision == 'enabled' else 0
        aid = activation_id or f"ACT-{profile}-{calibration_id}"
        doc = {'id': aid, 'camera_id': camera_id, 'profile': profile,
               'calibration_id': calibration_id, 'at': moment}
        sid, replayed = _derived_source(db, 'activation', doc)
        if replayed:
            row = db.execute('SELECT * FROM activation WHERE id=?', (aid,)).fetchone()
            return {'id': aid, 'replayed': True, 'decision': row['decision'], 'status': row['status']}
        db.execute('INSERT INTO activation VALUES(?,?,?,?,?,?,?,?,?,?,?)', (
            aid, calibration_id, camera_id, profile, decision, steering,
            json.dumps(reasons), json.dumps(reasons),
            'active' if decision != 'blocked' else 'blocked', moment, sid))
        if decision != 'blocked':
            db.execute("UPDATE calibration SET status='released' WHERE id=?", (calibration_id,))
        return {'id': aid, 'replayed': False, 'profile': profile, 'decision': decision,
                'steering_control': bool(steering), 'restrictions': reasons,
                'reasons': reasons, 'calibration_id': calibration_id,
                'mount_generation': mount['mount_generation'],
                'status': 'active' if decision != 'blocked' else 'blocked'}

# ------------------------------------------------------- withdrawals / identity
def _open_tasks(db, camera_id):
    return [dict(r) for r in db.execute(
        'SELECT * FROM withdrawal_task WHERE camera_id=? ORDER BY created_at,id', (camera_id,))]


def _withdraw_if_overturned(db, camera_id, new_cal_id, result, moment, sid):
    """New evidence that breaks an already released parking parameter set must
    leave the activation history in place and emit an executable withdrawal task
    instead of silently overwriting the old version."""
    fit_broken = any(v['max_px'] > CENTRE_RESIDUAL_LIMIT_PX for v in result['summary']['per_view'])
    centre_broken = result['bounds']['centre_landmark_max_px'] > CENTRE_RESIDUAL_LIMIT_PX
    tasks = []
    if not (fit_broken or centre_broken):
        return tasks
    rows = db.execute(
        "SELECT a.id AS aid,a.calibration_id AS cal_id FROM activation a "
        "JOIN calibration c ON c.id=a.calibration_id "
        "WHERE a.camera_id=? AND a.status='active'", (camera_id,)).fetchall()
    seen = set()
    for row in rows:
        db.execute("UPDATE activation SET status='withdrawn' WHERE id=?", (row['aid'],))
        if row['cal_id'] not in seen:
            db.execute("UPDATE calibration SET status='withdrawn' WHERE id=?", (row['cal_id'],))
            seen.add(row['cal_id'])
        tid = f"WD-{row['aid']}-{new_cal_id}"
        if not db.execute('SELECT 1 FROM withdrawal_task WHERE id=?', (tid,)).fetchone():
            db.execute('INSERT INTO withdrawal_task VALUES(?,?,?,?,?,?,?,?,?)', (
                tid, row['cal_id'], row['aid'], camera_id,
                'new_evidence_overturns_released_parameters',
                'open', moment, None, sid))
            tasks.append(tid)
    return tasks


def withdraw(path, calibration_id, reason, moment):
    """Explicit revocation: history rows stay, an executable task is emitted."""
    moment = at(moment)
    with connect(path) as db, db:
        cal = db.execute('SELECT * FROM calibration WHERE id=?', (calibration_id,)).fetchone()
        if not cal:
            raise ValueError('unknown_calibration')
        doc = {'calibration_id': calibration_id, 'reason': reason, 'at': moment}
        sid, replayed = _derived_source(db, 'withdrawal', doc)
        if replayed:
            return {'calibration_id': calibration_id, 'replayed': True,
                    'tasks': [dict(r) for r in db.execute(
                        'SELECT * FROM withdrawal_task WHERE calibration_id=?', (calibration_id,))]}
        tasks = []
        for act in db.execute("SELECT * FROM activation WHERE calibration_id=? AND status='active'",
                              (calibration_id,)).fetchall():
            db.execute("UPDATE activation SET status='withdrawn' WHERE id=?", (act['id'],))
            tid = f"WD-{act['id']}-manual"
            db.execute('INSERT INTO withdrawal_task VALUES(?,?,?,?,?,?,?,?,?)', (
                tid, calibration_id, act['id'], cal['camera_id'], reason,
                'open', moment, None, sid))
            tasks.append(tid)
        db.execute("UPDATE calibration SET status='withdrawn' WHERE id=?", (calibration_id,))
        return {'calibration_id': calibration_id, 'replayed': False, 'tasks': tasks}


def register_mount_change(path, camera_id, new_serial, new_lens_model, moment):
    """Lens replacement or bracket removal breaks the old parameter identity.

    The mount generation advances; every prior calibration remains in the ledger
    as history but can no longer be released against the new generation.
    """
    moment = at(moment)
    with connect(path) as db, db:
        cam = db.execute('SELECT * FROM camera_unit WHERE id=?', (camera_id,)).fetchone()
        if not cam:
            raise ValueError('unknown_camera')
        mount = _ensure_mount(db, cam)
        if new_serial == mount['serial'] and new_lens_model == mount['lens_model']:
            raise ValueError('mount_identity_unchanged')
        doc = {'camera_id': camera_id, 'serial': new_serial,
               'lens_model': new_lens_model, 'at': moment}
        sid, replayed = _derived_source(db, 'mount_change', doc)
        if replayed:
            row = db.execute('SELECT * FROM camera_mount WHERE camera_id=?', (camera_id,)).fetchone()
            return {'camera_id': camera_id, 'replayed': True,
                    'mount_generation': row['mount_generation']}
        db.execute('UPDATE camera_mount SET mount_generation=mount_generation+1,serial=?,lens_model=?,updated_at=? WHERE camera_id=?',
                   (new_serial, new_lens_model, moment, camera_id))
        gen = mount['mount_generation'] + 1
        tasks = []
        for act in db.execute("SELECT * FROM activation WHERE camera_id=? AND status='active'",
                              (camera_id,)).fetchall():
            db.execute("UPDATE activation SET status='withdrawn' WHERE id=?", (act['id'],))
            tid = f"WD-{act['id']}-mount{gen}"
            db.execute('INSERT INTO withdrawal_task VALUES(?,?,?,?,?,?,?,?,?)', (
                tid, act['calibration_id'], act['id'], camera_id,
                'lens_or_bracket_replaced_identity_cut', 'open', moment, None, sid))
            tasks.append(tid)
        db.execute("UPDATE calibration SET status='superseded' WHERE camera_id=? AND status IN ('candidate','released')",
                   (camera_id,))
        return {'camera_id': camera_id, 'replayed': False,
                'mount_generation': gen, 'tasks': tasks}


def execute_withdrawal(path, task_id, moment):
    """Mark an open withdrawal task as executed on the vehicle side."""
    moment = at(moment)
    with connect(path) as db, db:
        task = db.execute('SELECT * FROM withdrawal_task WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise ValueError('unknown_withdrawal_task')
        if task['status'] == 'executed':
            return {'id': task_id, 'replayed': True, 'status': 'executed'}
        doc = {'task_id': task_id, 'at': moment}
        sid, replayed = _derived_source(db, 'withdrawal_execution', doc)
        if replayed:
            row = db.execute('SELECT * FROM withdrawal_task WHERE id=?', (task_id,)).fetchone()
            return {'id': task_id, 'replayed': True, 'status': row['status']}
        db.execute('UPDATE withdrawal_task SET status=?,executed_at=? WHERE id=?',
                   ('executed', moment, task_id))
        return {'id': task_id, 'replayed': False, 'status': 'executed'}


# ----------------------------------------------------------------- queries ----
def calibrations(path, camera_id=None):
    with connect(path) as db:
        sql = 'SELECT * FROM calibration'
        args = ()
        if camera_id:
            sql += ' WHERE camera_id=?'; args = (camera_id,)
        sql += ' ORDER BY created_at,id'
        return [_load_result(db, r['id']) for r in db.execute(sql, args)]


def activations(path, camera_id=None):
    with connect(path) as db:
        sql = 'SELECT * FROM activation'
        args = ()
        if camera_id:
            sql += ' WHERE camera_id=?'; args = (camera_id,)
        return [dict(r) for r in db.execute(sql + ' ORDER BY created_at,id', args)]


def withdrawal_tasks(path, camera_id=None):
    with connect(path) as db:
        sql = 'SELECT * FROM withdrawal_task'
        args = ()
        if camera_id:
            sql += ' WHERE camera_id=?'; args = (camera_id,)
        return [dict(r) for r in db.execute(sql + ' ORDER BY created_at,id', args)]


def mount_state(path, camera_id):
    with connect(path) as db:
        row = db.execute('SELECT * FROM camera_mount WHERE camera_id=?', (camera_id,)).fetchone()
        return dict(row) if row else None
