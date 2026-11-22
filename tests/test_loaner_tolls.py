import json
import sqlite3
from pathlib import Path

import pytest

from autowarranty import diagnosis, loaner, loaner_tolls
from autowarranty.report import audit, report

FIX = Path(__file__).resolve().parents[1] / 'fixtures'


def write(tmp, name, doc):
    p = tmp / name
    p.write_text(json.dumps(doc), encoding='utf-8')
    return p


def handover(tmp_path, agreement_id='LA1'):
    doc = json.loads((FIX / 'tag-handover-lv1.json').read_text())
    doc['agreement_id'] = agreement_id
    return write(tmp_path, 'handover-%s.json' % agreement_id, doc)


def base(tmp_path, agreement='loan-agreement-ok.json', network='road-network-tunnel.json'):
    db = tmp_path / 'state.sqlite'
    diagnosis.vehicle(db, FIX / 'vehicle-ok.json')
    loaner.asset(db, FIX / 'loaner-ok.json')
    loaner.agreement(db, FIX / agreement)
    agreement_id = json.loads((FIX / agreement).read_text())['id']
    batch = json.loads((FIX / 'toll-fix-batch.json').read_text())
    loaner_tolls.fix_batch(
        db, write(tmp_path, 'batch-%s.json' % agreement_id,
                  {**batch, 'agreement_id': agreement_id}))
    loaner.toll(db, FIX / 'toll-receipt-ok.json')
    if network:
        loaner_tolls.road_network(db, FIX / network)
    return db


def decision(result, subject):
    rows = result.get('receipts') or []
    for bundle in result.get('agreements') or []:
        rows = rows + bundle.get('receipts', [])
    rows = rows + result.get('packages', [])
    return next(d for d in rows if d['subject_id'] == subject)


def test_same_gps_two_physical_routes_are_ambiguous(tmp_path):
    db = base(tmp_path)
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    receipt = decision(result, 'TR1')
    assert receipt['path_support'] == 'ambiguous'
    assert receipt['candidate_paths'], 'both tunnel and free candidates must be kept'
    assert receipt['candidate_paths'][0]['uses_toll'] or \
        any(p['uses_toll'] for p in receipt['candidate_paths'])
    assert any(not p['uses_toll'] for p in receipt['candidate_paths'])
    # 未签名 OCR 票不能当实车见证，更不得直接向车主收费
    assert receipt['status'] == 'disputed'
    assert receipt['customer_cents'] == 0
    assert receipt['shop_verify_cents'] == 500


def test_unsigned_ocr_is_not_vehicle_witness(tmp_path):
    db = base(tmp_path)
    loaner_tolls.ocr_read(db, FIX / 'ocr-unsigned-toll.json')
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    assert receipt['trust'] == 'ocr-only'
    assert receipt['customer_cents'] == 0
    assert receipt['status'] == 'disputed'
    assert 'authenticated_gate_signature' in receipt['required_evidence']


def test_misread_plate_and_fleet_appeal_keep_ticket_disputed(tmp_path):
    db = base(tmp_path)
    loaner_tolls.ocr_read(db, FIX / 'ocr-misread-toll.json')
    loaner_tolls.plate_appeal(db, FIX / 'plate-appeal-misread.json')
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    assert receipt['trust'] == 'contested'
    assert receipt['status'] == 'disputed'
    assert receipt['customer_cents'] == 0
    reasons = {c['kind'] for c in receipt['identity_chain']}
    assert {'ocr', 'plate_appeal'} <= reasons


def test_actual_toll_charges_only_with_trusted_gate_signature(tmp_path):
    db = base(tmp_path)
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    # 只有交接、没有闸口签名：仍不能证明这笔通行
    handover = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    assert handover['status'] == 'disputed'
    assert handover['customer_cents'] == 0
    # 可信闸口签名 + 标签身份：可追收
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    signed = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    assert signed['trust'] == 'trusted-gate'
    assert signed['status'] == 'provable'
    assert signed['customer_cents'] == 500
    assert signed['shop_verify_cents'] == 0
    assert signed['required_evidence'] == []


def test_free_route_real_trip_is_not_charged_without_gate_proof(tmp_path):
    # 物理上走了 F：只有两张原始定位点，收费系统仍报了 OCR 票
    db = base(tmp_path)
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    free_candidates = [p for p in receipt['candidate_paths'] if not p['uses_toll']]
    assert free_candidates, 'free route must remain a live candidate'
    assert receipt['customer_cents'] == 0
    assert receipt['shop_verify_cents'] == 500


def test_fixed_daily_package_charges_300_without_double_counting(tmp_path):
    db = base(tmp_path, agreement='loan-fixed-agreement.json')
    loaner_tolls.ocr_read(db, FIX / 'ocr-unsigned-toll.json')
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    package = decision(result, 'PKG:LA2')
    assert package['customer_cents'] == 300
    assert package['days'] == 1
    receipt = decision(result, 'TR1')
    assert receipt['customer_cents'] == 0
    assert receipt['shop_verify_cents'] == 500
    assert receipt['status'] == 'disputed'
    # 包费 300，绝不叠加成 800
    assert result['totals']['customer_receivable_cents'] == 300


def test_fixed_package_stays_300_even_with_late_trusted_signature(tmp_path):
    db = base(tmp_path, agreement='loan-fixed-agreement.json')
    loaner_tolls.tag_handover(db, handover(tmp_path, 'LA2'))
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    assert decision(result, 'PKG:LA2')['customer_cents'] == 300
    assert decision(result, 'TR1')['customer_cents'] == 0
    assert result['totals']['customer_receivable_cents'] == 300


def test_late_trusted_ticket_tops_up_only_new_passage(tmp_path):
    db = base(tmp_path)
    first = loaner_tolls.settle(db, '2026-05-01T10:00:00Z')
    assert first['totals']['customer_receivable_cents'] == 0
    assert first['agreements'][0]['disputed_cents'] == 500
    # 可信票/签名迟到：已收 0，增量补收 500，不改原票
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    later = loaner_tolls.settle(db, '2026-05-04T00:00:00Z')
    assert later['totals']['customer_receivable_cents'] == 500
    assert later['agreements'][0]['disputed_cents'] == 0
    kinds = [(a['subject_id'], a['kind'], a['cents']) for a in later['adjustments']]
    assert ('TR1', 'top-up', 500) in kinds
    assert report(db)['state']['toll_receipt'][0]['cents'] == 500
    assert audit(db)['replay_ok']


def test_trusted_ticket_arriving_after_dispute_does_not_rewrite_receipt(tmp_path):
    db = base(tmp_path)
    loaner_tolls.settle(db, '2026-05-01T10:00:00Z')
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    loaner_tolls.settle(db, '2026-05-04T00:00:00Z')
    rows = report(db)['state']['toll_decision']
    tr1 = [r for r in rows if r['subject_id'] == 'TR1']
    assert [r['revision'] for r in tr1] == [1, 2]
    assert tr1[0]['status'] == 'disputed'
    assert tr1[1]['status'] == 'provable'


def test_closed_tunnel_creates_dispute_and_refund(tmp_path):
    db = base(tmp_path, network='road-network-tunnel.json')
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    open_result = loaner_tolls.settle(db, '2026-05-01T10:00:00Z')
    assert open_result['totals']['customer_receivable_cents'] == 500
    loaner_tolls.road_network(db, FIX / 'road-network-closure.json')
    closed = loaner_tolls.settle(db, '2026-05-04T00:00:00Z')
    receipt = decision(closed, 'TR1')
    assert receipt['status'] == 'disputed'
    assert receipt['customer_cents'] == 0
    kinds = [a['kind'] for a in closed['adjustments']]
    assert 'refund' in kinds
    assert any(a['kind'] == 'refund' and a['cents'] == 500 for a in closed['adjustments'])


def test_overlapping_agreements_assign_receipt_once(tmp_path):
    db = base(tmp_path)
    loaner_tolls.agreement(db, FIX / 'loan-overlap-agreement.json')
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    owners = [d['agreement_id'] for d in result['receipts'] if d['subject_id'] == 'TR1']
    assert owners == ['LA1'], 'receipt must be attributed exactly once, to the active lease'


def test_superseding_agreement_wins_once(tmp_path):
    db = base(tmp_path)
    original = json.loads((FIX / 'loan-agreement-ok.json').read_text())
    replacement = {**original, 'id': 'LA9', 'version': 2, 'supersedes': 'LA1'}
    loaner_tolls.agreement(db, write(tmp_path, 'replacement.json', replacement))
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    assert decision(result, 'TR1')['agreement_id'] == 'LA9'
    assert decision(result, 'TR1')['customer_cents'] == 500


def test_tag_lent_to_another_vehicle_is_contested(tmp_path):
    db = base(tmp_path)
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner.asset(db, write(tmp_path, 'lv9.json', {'id': 'LV9', 'plate': 'PL-9',
                                                 'model': 'COMPACT'}))
    other = {'id': 'HO2', 'loaner_id': 'LV9', 'plate': 'PL-9', 'tag': 'TAG1',
             'borrower': 'OWNER-2', 'from_at': '2026-05-01T08:30:00Z',
             'to_at': '2026-05-01T12:00:00Z'}
    loaner_tolls.tag_handover(db, write(tmp_path, 'ho2.json', other))
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z'), 'TR1')
    assert receipt['trust'] == 'contested'
    assert receipt['customer_cents'] == 0
    assert receipt['shop_verify_cents'] == 500


def test_return_boundary_ends_lease_coverage(tmp_path):
    db = base(tmp_path)
    loaner_tolls.loan_return(db, FIX / 'loan-return-early.json')
    # 09:05 的票仍在还车（18:00）之前，租约仍覆盖
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-02T00:00:00Z'), 'TR1')
    assert receipt['agreement_id'] == 'LA1'
    assert receipt['customer_cents'] == 500
    # 还车后再来的票不再属于该租约
    late = {'id': 'TR2', 'plate': 'PL-1', 'gate': 'G1',
            'passed_at': '2026-05-01T19:00:00Z', 'cents': 500}
    loaner.toll(db, write(tmp_path, 'tr2.json', late))
    after = decision(loaner_tolls.reconcile(db, '2026-05-02T00:00:00Z'), 'TR2')
    assert after['status'] == 'not_attributable'
    assert after['customer_cents'] == 0


def test_superseding_agreement_reuses_prior_fixes(tmp_path):
    db = base(tmp_path)
    original = json.loads((FIX / 'loan-agreement-ok.json').read_text())
    replacement = {**original, 'id': 'LA9', 'version': 2, 'supersedes': 'LA1'}
    loaner_tolls.agreement(db, write(tmp_path, 'replacement.json', replacement))
    # 交接记录仍挂在旧版本 LA1 上，版本链应视为同一租约
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    result = loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    tr1 = decision(result, 'TR1')
    assert tr1['agreement_id'] == 'LA9'
    # 旧版本 LA1 的定位点仍用于该租约版本链的轨迹段
    assert tr1['trajectory'][0]['id'] == 'GF-A'
    assert tr1['trust'] == 'trusted-gate'
    assert tr1['customer_cents'] == 500


def test_ocr_read_persists_image_digest(tmp_path):
    db = base(tmp_path)
    doc = json.loads((FIX / 'ocr-unsigned-toll.json').read_text())
    doc['image_sha'] = 'a' * 64
    loaner_tolls.ocr_read(db, write(tmp_path, 'ocr-img.json', doc))
    row = report(db)['state']['ocr_read'][0]
    assert row['image_sha'] == 'a' * 64


def test_shop_payment_facts_are_preserved(tmp_path):
    db = base(tmp_path)
    paid = {'id': 'SP1', 'receipt_id': 'TR1', 'kind': 'shop-paid',
            'cents': 500, 'at': '2026-05-02T00:00:00Z'}
    loaner_tolls.toll_payment(db, write(tmp_path, 'sp1.json', paid))
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    result = loaner_tolls.settle(db, '2026-05-04T00:00:00Z')
    assert result['totals']['shop_net_paid_cents'] == 500
    assert result['toll_payments'][0]['kind'] == 'shop-paid'
    # 门店已向收费方付款不改变车主应收
    assert result['totals']['customer_receivable_cents'] == 500
    assert report(db)['state']['toll_payment'][0]['cents'] == 500
    assert audit(db)['replay_ok']


def test_payments_are_facts_and_never_rewritten(tmp_path):
    db = base(tmp_path, agreement='loan-fixed-agreement.json')
    loaner_tolls.customer_payment(db, FIX / 'customer-payment-package.json')
    result = loaner_tolls.settle(db, '2026-05-04T00:00:00Z')
    agreement = result['agreements'][0]
    assert agreement['customer_receivable_cents'] == 300
    assert agreement['customer_paid_cents'] == 300
    assert agreement['customer_outstanding_cents'] == 0
    assert report(db)['state']['customer_payment'][0]['cents'] == 300


def test_fix_outside_agreement_is_rejected(tmp_path):
    db = tmp_path / 'state.sqlite'
    diagnosis.vehicle(db, FIX / 'vehicle-ok.json')
    loaner.asset(db, FIX / 'loaner-ok.json')
    loaner.agreement(db, FIX / 'loan-agreement-ok.json')
    late = {'id': 'GF-LATE', 'agreement_id': 'LA1',
            'observed_at': '2026-06-01T09:00:00Z', 'x_m': 100, 'y_m': 200,
            'accuracy_m': 10}
    with pytest.raises(ValueError, match='fix_outside_agreement'):
        loaner.fix(db, write(tmp_path, 'late.json', late))


def test_bracketing_gap_too_long_requires_evidence(tmp_path):
    db = tmp_path / 'state.sqlite'
    diagnosis.vehicle(db, FIX / 'vehicle-ok.json')
    loaner.asset(db, FIX / 'loaner-ok.json')
    loaner.agreement(db, FIX / 'loan-agreement-ok.json')
    loaner.toll(db, FIX / 'toll-receipt-ok.json')
    loaner_tolls.road_network(db, FIX / 'road-network-tunnel.json')
    far = {'id': 'FB2', 'agreement_id': 'LA1', 'fixes': [
        {'id': 'GF-C', 'observed_at': '2026-05-01T08:00:00Z', 'x_m': 100,
         'y_m': 200, 'accuracy_m': 20},
        {'id': 'GF-D', 'observed_at': '2026-05-02T08:00:00Z', 'x_m': 3000,
         'y_m': 200, 'accuracy_m': 20}]}
    loaner_tolls.fix_batch(db, write(tmp_path, 'far.json', far))
    receipt = decision(loaner_tolls.reconcile(db, '2026-05-02T18:00:00Z'), 'TR1')
    assert receipt['path_support'] == 'gap-too-long'
    assert receipt['status'] == 'not_attributable'
    assert 'bracketing_gps_fix_with_accuracy' in receipt['required_evidence']


def test_reconcile_does_not_touch_customer_vehicle_state(tmp_path):
    db = base(tmp_path)
    before = report(db)['state']['vehicle']
    loaner_tolls.reconcile(db, '2026-05-01T18:00:00Z')
    after = report(db)['state']['vehicle']
    assert before == after
    assert after[0]['vin'] == 'VIN-1'
    assert after[0]['status'] == 'registered'


def test_explain_reports_paths_identity_and_trust(tmp_path):
    db = base(tmp_path)
    loaner_tolls.tag_handover(db, FIX / 'tag-handover-lv1.json')
    loaner_tolls.gate_signature(db, FIX / 'gate-signature-tunnel.json')
    loaner_tolls.settle(db, '2026-05-01T18:00:00Z')
    explained = loaner_tolls.explain(db, 'TR1')['decisions'][0]
    assert explained['trust'] == 'trusted-gate'
    assert explained['trajectory'][0]['id'] == 'GF-A'
    assert any(c['kind'] == 'gate_signature' for c in explained['identity_chain'])
    assert any(c['kind'] == 'tag_handover' for c in explained['identity_chain'])
    assert len(explained['candidate_paths']) >= 2


def test_cli_toll_workflow(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    db = tmp_path / 'cli.sqlite'

    def run(*args):
        p = subprocess.run([sys.executable, '-m', 'autowarranty', *args],
                           env=env, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)

    run('vehicle-import', str(FIX / 'vehicle-ok.json'), '--db', str(db))
    run('loaner-import', str(FIX / 'loaner-ok.json'), '--db', str(db))
    run('loan-agreement-import', str(FIX / 'loan-agreement-ok.json'), '--db', str(db))
    run('toll-fix-batch-import', str(FIX / 'toll-fix-batch.json'), '--db', str(db))
    run('toll-receipt-import', str(FIX / 'toll-receipt-ok.json'), '--db', str(db))
    run('road-network-import', str(FIX / 'road-network-tunnel.json'), '--db', str(db))
    out = run('toll-reconcile', '--db', str(db), '--at', '2026-05-01T18:00:00Z')
    assert out['receipts'][0]['status'] == 'disputed'
    out = run('toll-settle', '--db', str(db), '--at', '2026-05-01T18:00:00Z')
    assert out['totals']['customer_receivable_cents'] == 0
    assert run('toll-ledger', '--db', str(db))['totals']['disputed_cents'] == 500
