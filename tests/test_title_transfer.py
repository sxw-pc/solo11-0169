import json
from pathlib import Path
import pytest
from autowarranty import diagnosis,repair,title,title_transfer
from autowarranty.report import report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def setup(db):
    """门店实车 VIN-1/FRAME-1/ENG-1；REG-A 的 TI1 带 LI1 留置；REG-B 的 TI2 同 VIN 同车主拼写、更晚、无留置（套牌）。"""
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    title.record(db,FIX/'title-ok.json')
    title.lien(db,FIX/'lien-ok.json')
    title.scan(db,FIX/'frame-scan-ok.json')
    title.record(db,FIX/'title-clone.json')
    title_transfer.archive(db,FIX/'factory-archive-ok.json')

def payoff_verified(db):
    title_transfer.registry(db,FIX/'lien-release-ok.json')
    return title_transfer.prepare(db,'VIN-1','payoff','2026-05-11T00:00:00Z')

def test_clone_same_vin_not_merged(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    title.record(db,FIX/'title-ok.json')
    title.lien(db,FIX/'lien-ok.json')
    title.scan(db,FIX/'frame-scan-ok.json')
    title.record(db,FIX/'title-clone.json')
    g=title_transfer.graph(db,'VIN-1')
    assert g['status']=='pending' and g['title_id'] is None
    title_transfer.archive(db,FIX/'factory-archive-ok.json')
    g=title_transfer.graph(db,'VIN-1')
    assert g['status']=='verified' and g['title_id']=='TI1'
    assert g['bound_titles']==['TI1']
    by_title={c['title_id']:c['kind'] for c in g['conflicts'] if 'title_id' in c}
    assert by_title['TI2']=='shared_vin_unbound'
    claim={c['lien_id']:c for c in g['claims']}['LI1']
    assert claim['open'] and claim['creditor']=='BANK-1' and claim['amount_cents']==500000
    assert audit(db)['replay_ok']

def test_delivered_vehicle_still_carries_lien(tmp_path):
    db=tmp_path/'state.sqlite'
    for func,name in [(diagnosis.vehicle,'vehicle-ok.json'),(diagnosis.diagnose,'diagnosis-ok.json'),
                      (diagnosis.authorize,'authorization-ok.json'),(repair.part,'part-ok.json')]:func(db,FIX/name)
    repair.execute(db,FIX/'repair-ok.json');repair.inspect(db,FIX/'inspection-ok.json')
    assert report(db)['state']['vehicle'][0]['status']=='delivered'
    title.record(db,FIX/'title-ok.json');title.lien(db,FIX/'lien-ok.json');title.scan(db,FIX/'frame-scan-ok.json')
    title_transfer.archive(db,FIX/'factory-archive-ok.json')
    g=title_transfer.graph(db,'VIN-1')
    assert g['status']=='verified' and any(c['open'] for c in g['claims'])
    assert title_transfer.prepare(db,'VIN-1','payoff','2026-05-09T00:00:00Z')['status']=='pending'

def test_payoff_route_and_late_release(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    first=title_transfer.prepare(db,'VIN-1','payoff','2026-05-09T00:00:00Z')
    assert first['status']=='pending'
    assert '500000' in str(first['statement']['todos']) and 'BANK-1' in str(first['statement']['todos'])
    title_transfer.registry(db,FIX/'lien-release-ok.json')
    second=title_transfer.prepare(db,'VIN-1','payoff','2026-05-11T00:00:00Z')
    assert second['status']=='verified'
    st=second['statement']
    assert st['headline']=='clean_title' and st['buyer_rights']=='unencumbered_title'
    assert st['proceeds_to']=='seller' and st['creditor_signoff'] is None
    assert st['liens_discharged']==['LI1']
    s=title_transfer.statement(db,'VIN-1')
    assert [l['id'] for l in s['pending']]==['LS-VIN-1-payoff-1']
    assert [l['id'] for l in s['verified']]==['LS-VIN-1-payoff-2']
    assert audit(db)['replay_ok']

def test_assumption_route_keeps_lien_with_vehicle(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    offer=title_transfer.prepare(db,'VIN-1','assumption','2026-05-09T00:00:00Z')
    assert offer['status']=='verified'
    st=offer['statement']
    assert st['headline']=='with_lien' and st['buyer_rights']=='restricted_title'
    assert st['creditor_signoff']==['BANK-1'] and st['todos']==['tripartite_assumption_agreement']
    assert st['assumed_liens'][0]['lien_id']=='LI1' and st['proceeds_to']!='seller'
    with pytest.raises(ValueError,match='assumption_doc_required'):
        title_transfer.sign(db,offer['id'],'BUYER-1',700000,'2026-05-12T00:00:00Z')
    d=title_transfer.sign(db,offer['id'],'BUYER-1',700000,'2026-05-12T00:00:00Z',assumption_doc='TRIP-1')
    assert d['status']=='signed'
    assert report(db)['state']['delivery'][0]['assumption_doc']=='TRIP-1'
    assert audit(db)['replay_ok']

def test_crash_after_external_receipt_recovers_by_receipt_no(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    offer=payoff_verified(db)
    assert offer['id']=='LS-VIN-1-payoff-1'
    d=title_transfer.sign(db,offer['id'],'BUYER-1',900000,'2026-05-12T00:00:00Z')
    assert d['id']=='DL-LS-VIN-1-payoff-1'
    title_transfer.registry(db,FIX/'transfer-receipt-ok.json')
    s=title_transfer.statement(db,'VIN-1')
    assert [x['id'] for x in s['in_transit']]==['DL-LS-VIN-1-payoff-1'] and s['delivered']==[]
    r=title_transfer.recover(db,'RCP-1')
    assert r['status']=='closed' and r['closed_at']=='2026-05-15T00:00:00Z'
    assert title_transfer.recover(db,'RCP-1')['replayed']
    s=title_transfer.statement(db,'VIN-1')
    assert [x['id'] for x in s['delivered']]==['DL-LS-VIN-1-payoff-1'] and s['in_transit']==[]
    assert audit(db)['replay_ok']

def test_duplicate_sale_blocked(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    offer=payoff_verified(db)
    title_transfer.sign(db,offer['id'],'BUYER-1',900000,'2026-05-12T00:00:00Z')
    with pytest.raises(ValueError,match='duplicate_sale_blocked'):
        title_transfer.sign(db,offer['id'],'BUYER-2',950000,'2026-05-13T00:00:00Z')
    with pytest.raises(ValueError,match='entity_already_sold'):
        title_transfer.prepare(db,'VIN-1','assumption','2026-05-13T00:00:00Z')
    assert title_transfer.sign(db,offer['id'],'BUYER-1',900000,'2026-05-12T00:00:00Z')['replayed']
    title_transfer.registry(db,FIX/'transfer-receipt-ok.json')
    title_transfer.recover(db,'RCP-1')
    with pytest.raises(ValueError,match='entity_already_sold'):
        title_transfer.prepare(db,'VIN-1','payoff','2026-05-16T00:00:00Z')
    assert audit(db)['replay_ok']

def test_late_revocation_preserves_delivery(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    offer=payoff_verified(db)
    title_transfer.sign(db,offer['id'],'BUYER-1',900000,'2026-05-12T00:00:00Z')
    title_transfer.registry(db,FIX/'transfer-receipt-ok.json')
    title_transfer.recover(db,'RCP-1')
    before=report(db)['state']['delivery']
    title_transfer.registry(db,FIX/'title-revoke-late.json')
    after=report(db)['state']['delivery']
    assert after==before and after[0]['status']=='closed'
    assert after[0]['buyer']=='BUYER-1' and after[0]['price_cents']==900000
    g=title_transfer.graph(db,'VIN-1')
    assert g['status']=='conflict'
    assert title_transfer.statement(db,'VIN-1')['delivered'][0]['buyer']=='BUYER-1'
    assert audit(db)['replay_ok']

def test_pending_listing_cannot_be_signed(tmp_path):
    db=tmp_path/'state.sqlite';setup(db)
    offer=title_transfer.prepare(db,'VIN-1','payoff','2026-05-09T00:00:00Z')
    assert offer['status']=='pending'
    with pytest.raises(ValueError,match='listing_not_verified'):
        title_transfer.sign(db,offer['id'],'BUYER-1',900000,'2026-05-12T00:00:00Z')
    with pytest.raises(ValueError,match='unknown_receipt'):
        title_transfer.recover(db,'RCP-404')
