import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autowarranty import diagnosis, adas, adas_calibration as cal
from autowarranty.report import audit

FIX = Path(__file__).resolve().parents[1] / 'fixtures'
W, H = 1920, 1080
FX = 2667.0
K1_TRUE = -0.5557
DIST = (FX, FX, W / 2, H / 2, K1_TRUE, 0.0, 0.0, 0.0)
PIN = (FX, FX, W / 2, H / 2, 0.0, 0.0, 0.0, 0.0)


def _doc(name):
    return json.loads((FIX / name).read_text())


def _feed(db, name):
    d = _doc(name)
    if 'width_px' in d:
        adas.camera(db, FIX / name)
    elif 'points' in d:
        adas.board(db, FIX / name)
    elif 'u_px' in d:
        adas.point(db, FIX / name)
    elif 'speed_kph' in d:
        adas.pose(db, FIX / name)


def _load_camera(tmp_path, cam_id, vehicle_fn):
    db = tmp_path / f'{cam_id}.sqlite'
    diagnosis.vehicle(db, FIX / vehicle_fn)
    keep, board_ids = [], set()
    for fn in sorted(os.listdir(FIX)):
        if not fn.startswith('cal-'):
            continue
        d = _doc(fn)
        if d.get('camera_id') == cam_id or d.get('id') == cam_id:
            keep.append(fn)
            if 'board_id' in d:
                board_ids.add(d['board_id'])
    for fn in sorted(os.listdir(FIX)):
        if fn.startswith('cal-board') and _doc(fn)['id'] in board_ids:
            keep.append(fn)

    def rank(fn):
        return 0 if 'camera' in fn else 1 if 'board' in fn else 2 if 'point' in fn else 3
    for fn in sorted(set(keep), key=rank):
        _feed(db, fn)
    return db


# ----------------------------------------------------- the central counterexample
def test_identical_central_corners_pinhole_vs_distorted():
    """Two real lenses share the identical four central corners and an exact
    homography with zero residual, yet differ by ~40 px at the 800 px ring."""
    edge = (W / 2 + 800.0, H / 2.0)
    delta = cal.distortion_map_delta(edge, PIN, DIST)
    assert delta == pytest.approx(40.0, abs=8.0)
    lateral = math.atan2(delta, FX) * cal.LANE_DISTANCE_M
    assert lateral == pytest.approx(0.6, abs=0.15)
    # At the principal-point ray the two models are indistinguishable.
    assert cal.distortion_map_delta((W / 2, H / 2), PIN, DIST) == pytest.approx(0.0, abs=1e-9)


def test_central_only_evidence_yields_identical_envelope_and_unobservable_k1(tmp_path):
    db_p = _load_camera(tmp_path, 'CAMP', 'cal-vehicle-p.json')
    r_p = cal.calibrate(db_p, 'CAMP', '2026-05-10T00:00:00Z', cal_id='CP')
    # The nominal solution stays an anchored pinhole (no pretty single k1 answer).
    assert r_p['summary']['intrinsics']['k1'] == 0.0
    assert r_p['observability']['fit_mode'] == 'anchored_pinhole'
    assert 'k1' in r_p['observability']['unobservable']
    # Four-corner residual is exactly zero while the edge envelope stays wide.
    assert max(v['max_px'] for v in r_p['summary']['per_view']) < 1e-6
    assert r_p['bounds']['edge_landmark_max_px'] > 40.0
    assert r_p['bounds']['lane_lateral_max_m_at_40m'] > cal.LANE_LATERAL_LIMIT_M
    # The most informative next target is an in-frame ground point on the 800 px ring.
    nxt = r_p['next_target']
    assert nxt['kind'] == 'offaxis_ground_board'
    assert abs(nxt['target_pixel_radius_px'] - cal.EDGE_OFFAXIS_RADIUS_PX) < 120.0
    assert 'offaxis_board_views_radius_ge_800px' in nxt['required_next_evidence']


# ------------------------------------------------------------- the two profiles
def test_parking_and_lane_keeping_have_separate_authority(tmp_path):
    db_p = _load_camera(tmp_path, 'CAMP', 'cal-vehicle-p.json')
    cal.calibrate(db_p, 'CAMP', '2026-05-10T00:00:00Z', cal_id='CP')
    # No independent centre landmark yet: parking blocked, lane blocked harder.
    park = cal.release(db_p, 'CAMP', 'parking_assist', '2026-05-10T01:00:00Z', activation_id='AP-BLOCK')
    lane = cal.release(db_p, 'CAMP', 'lane_keeping', '2026-05-10T02:00:00Z', activation_id='AL-BLOCK')
    assert park['decision'] == 'blocked'
    assert 'independent_landmark_verification_missing' in park['reasons']
    assert lane['decision'] == 'blocked'
    assert lane['steering_control'] is False
    assert 'offaxis_board_views_missing' in lane['reasons']


def test_distorted_camera_full_evidence_enables_lane_keeping(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    r_d = cal.calibrate(db_d, 'CAMD', '2026-05-10T00:00:00Z', cal_id='CD')
    # Off-axis + two-pitch evidence makes k1 identifiable and recovers the truth.
    assert r_d['observability']['fit_mode'] == 'free_joint'
    assert r_d['summary']['intrinsics']['k1'] == pytest.approx(K1_TRUE, abs=0.01)
    assert max(v['rms_px'] for v in r_d['summary']['per_view']) < 0.05
    park = cal.release(db_d, 'CAMD', 'parking_assist', '2026-05-10T01:00:00Z', activation_id='AD-PARK')
    lane = cal.release(db_d, 'CAMD', 'lane_keeping', '2026-05-10T02:00:00Z', activation_id='AD-LANE')
    assert park['decision'] == 'restricted'
    assert park['steering_control'] is False
    assert 'steering_control_prohibited' in park['restrictions']
    assert lane['decision'] == 'enabled'
    assert lane['steering_control'] is True
    assert r_d['bounds']['lane_lateral_max_m_at_40m'] <= cal.LANE_LATERAL_LIMIT_M


def test_dynamic_pitch_and_two_loads_required_for_lane(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    coverage = cal.evidence_coverage(_evidence(db_d, 'CAMD'))
    assert coverage['has_offaxis_views'] is True
    assert coverage['load_level_count'] >= 2
    assert coverage['max_speed_kph'] >= cal.LANE_REQUIRED_SPEED_KPH


def _evidence(db, cam_id):
    from autowarranty.store import connect
    with connect(db) as conn:
        return cal.adas._load_calibration_evidence(conn, cam_id)


# ------------------------------------------------------------- identity / recall
def test_lens_swap_cuts_parameter_identity_and_emits_withdrawals(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    cal.calibrate(db_d, 'CAMD', '2026-05-10T00:00:00Z', cal_id='C1')
    cal.release(db_d, 'CAMD', 'parking_assist', '2026-05-10T01:00:00Z', activation_id='A1')
    cal.release(db_d, 'CAMD', 'lane_keeping', '2026-05-10T02:00:00Z', activation_id='A2')
    change = cal.register_mount_change(db_d, 'CAMD', 'LENS-NEW', 'WIDE-V2', '2026-05-12T00:00:00Z')
    assert change['mount_generation'] == 1
    assert set(change['tasks']) == {'WD-A1-mount1', 'WD-A2-mount1'}
    # History is retained, never overwritten; activations are marked withdrawn.
    history = {a['id']: a['status'] for a in cal.activations(db_d, 'CAMD')}
    assert history == {'A1': 'withdrawn', 'A2': 'withdrawn'}
    # The old calibration can no longer be released against the new generation.
    with pytest.raises(ValueError, match='stale_mount_generation'):
        cal.release(db_d, 'CAMD', 'lane_keeping', '2026-05-12T01:00:00Z',
                    activation_id='A3', calibration_id='C1')
    executed = cal.execute_withdrawal(db_d, change['tasks'][0], '2026-05-12T02:00:00Z')
    assert executed['status'] == 'executed'


def test_explicit_withdrawal_keeps_activation_history(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    cal.calibrate(db_d, 'CAMD', '2026-05-10T00:00:00Z', cal_id='C1')
    cal.release(db_d, 'CAMD', 'parking_assist', '2026-05-10T01:00:00Z', activation_id='A1')
    out = cal.withdraw(db_d, 'C1', 'field_failure_overturns_parameters', '2026-05-13T00:00:00Z')
    assert out['tasks'] == ['WD-A1-manual']
    assert cal.activations(db_d, 'CAMD')[0]['status'] == 'withdrawn'
    task = cal.withdrawal_tasks(db_d, 'CAMD')[0]
    assert task['reason'] == 'field_failure_overturns_parameters'
    assert task['status'] == 'open'


# ----------------------------------------------------------------- replay / audit
def test_full_ledger_replays_deterministically(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    cal.calibrate(db_d, 'CAMD', '2026-05-10T00:00:00Z', cal_id='C1')
    cal.release(db_d, 'CAMD', 'parking_assist', '2026-05-10T01:00:00Z', activation_id='A1')
    cal.release(db_d, 'CAMD', 'lane_keeping', '2026-05-10T02:00:00Z', activation_id='A2')
    cal.register_mount_change(db_d, 'CAMD', 'LENS-NEW', 'WIDE-V2', '2026-05-12T00:00:00Z')
    cal.execute_withdrawal(db_d, 'WD-A1-mount1', '2026-05-12T02:00:00Z')
    assert audit(db_d)['replay_ok'] is True


def test_observations_endpoint_returns_only_originals(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    cal.calibrate(db_d, 'CAMD', '2026-05-10T00:00:00Z', cal_id='C1')
    obs = adas.observations(db_d)
    assert set(obs) == {'camera_unit', 'target_board', 'image_point', 'road_pose'}


def test_solver_rejects_empty_evidence(tmp_path):
    db_p = _load_camera(tmp_path, 'CAMP', 'cal-vehicle-p.json')
    # A camera with no board observations cannot be solved.
    from autowarranty.store import connect
    with connect(db_p) as conn, conn:
        conn.execute("DELETE FROM image_point")
    with pytest.raises(ValueError, match='insufficient_observations'):
        cal.calibrate(db_p, 'CAMP', '2026-05-10T00:00:00Z', cal_id='EMPTY')


def test_cli_calibration_release_and_mount_change(tmp_path):
    db_d = _load_camera(tmp_path, 'CAMD', 'cal-vehicle-d.json')
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PYTHONPATH': str(root)}

    def cli(*args):
        proc = subprocess.run([sys.executable, '-m', 'autowarranty', *args],
                              env=env, capture_output=True, text=True, cwd=root)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    assert cli('adas-calibrate', '--db', str(db_d), '--camera', 'CAMD',
               '--at', '2026-05-10T00:00:00Z', '--id', 'C1')['status'] == 'candidate'
    lane = cli('adas-release', '--db', str(db_d), '--camera', 'CAMD',
               '--profile', 'lane_keeping', '--at', '2026-05-10T02:00:00Z', '--id', 'A2')
    assert lane['decision'] == 'enabled' and lane['steering_control'] is True
    change = cli('adas-mount-change', '--db', str(db_d), '--camera', 'CAMD',
                 '--serial', 'L2', '--lens', 'WV2', '--at', '2026-05-12T00:00:00Z')
    assert change['mount_generation'] == 1
    assert cli('audit', '--db', str(db_d))['replay_ok'] is True
