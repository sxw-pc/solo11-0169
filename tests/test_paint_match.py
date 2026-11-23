import json,os,subprocess,sys
from pathlib import Path
import pytest
from autowarranty import diagnosis,paint,paint_match
from autowarranty.report import report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def write(tmp,name,doc):
    p=tmp/name;p.write_text(json.dumps(doc),encoding='utf-8');return p

def base_v1(db):
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    paint.sample(db,FIX/'paint-sample-ok.json')
    paint.pigment(db,FIX/'pigment-ok.json')
    paint.panel(db,FIX/'spray-panel-ok.json')

def base_v1_full(db):
    base_v1(db)
    for name in ('pigment-pa.json','pigment-pb.json','pigment-pc.json'):paint.pigment(db,FIX/name)

def fleet_sprayed(db,tmp):
    """日间车队订单: 已试喷的低成本配方直接调漆、领料、实喷."""
    base_v1(db)
    paint_match.order(db,FIX/'paint-order-fleet.json')
    r=paint_match.plan(db,write(tmp,'plan-f1.json',{'id':'PLAN-F1','order_id':'ORD-F1','at':'2026-06-02T09:00:00Z'}))
    assert r['status']=='executable' and r['recipe']=={'PG1':20.0}
    paint_match.draw(db,write(tmp,'draw-f1.json',{'id':'DRAW-F1','mix_order_id':'PLAN-F1','at':'2026-06-02T10:00:00Z'}))
    paint_match.spray(db,write(tmp,'se1.json',{'id':'SE1','mix_order_id':'PLAN-F1','booth':'BOOTH-1','at':'2026-06-02T11:00:00Z'}))
    return r

def test_fleet_order_locks_and_consumes_day_recipe(tmp_path):
    db=tmp_path/'state.sqlite'
    r=fleet_sprayed(db,tmp_path)
    assert r['reserved']==[{'lot':'PG1','ml':20.0}]
    assert paint_match.plan(db,tmp_path/'plan-f1.json')['replayed']
    st=paint_match.stock(db)['PG1']
    assert (st['physical_ml'],st['in_use_ml'],st['reserved_ml'],st['free_ml'])==(980,20,0,980)
    status=paint_match.order_status(db,'ORD-F1')
    assert status['order']['status']=='sprayed'
    assert status['mix_orders'][0]['basis']=='measured_panel:SP1'
    assert report(db)['state']['spray_event'][0]['booth']=='BOOTH-1'
    cap=paint.captures(db)
    assert cap['mix_order'][0]['id']=='PLAN-F1' and [x['kind'] for x in cap['paint_ledger']]==['reserve','draw']
    assert audit(db)['replay_ok']

def test_oem_order_needs_narrowband_then_resprays(tmp_path):
    db=tmp_path/'state.sqlite'
    base_v1_full(db)
    paint_match.order(db,FIX/'paint-order-oem.json')
    # 同一套 D65 事实: 原漆与候选试板都缺路灯敏感波段 → 补测工单, 先保留物料
    r1=paint_match.plan(db,write(tmp_path,'plan-o1.json',{'id':'PLAN-O1','order_id':'ORD-O1','at':'2026-06-02T09:30:00Z'}))
    assert r1['status']=='pending_measurement'
    assert r1['reserved']==[{'lot':'PG1','ml':20.0}]
    subs={(m['subject_kind'],m['subject_id']) for m in r1['work_orders'][0]['measurements']}
    assert ('paint_sample','PS1') in subs and ('spray_panel','SP1') in subs
    assert {('pigment_lot',x) for x in ('PG1','PA','PB','PC')}<=subs
    assert all(m['bands']==[590] for m in r1['work_orders'][0]['measurements'])
    c1=paint_match.candidates(db,'ORD-O1')
    assert c1['distinguishing']['bands']==[590]
    sp1=[p for p in c1['panels'] if p['panel']=='SP1'][0]
    assert sp1['per_illuminant']['D65']=='proven' and sp1['per_illuminant']['LAMP']=='unmeasured'
    # 补测路灯敏感波段: 原漆 0.25, SP1 与 PG1/PA 在 590 强反射, PB/PC 无该峰
    for name in ('band-measure-ps1-590.json','band-measure-sp1-590.json','band-measure-pg1-590.json',
                 'band-measure-pa-590.json','band-measure-pb-590.json','band-measure-pc-590.json'):
        paint_match.measure(db,FIX/name)
    # 重求解: SP1 夜间失配被拒, 模型给出夜间一致配方, 预约随之换批
    r2=paint_match.plan(db,write(tmp_path,'plan-o2.json',{'id':'PLAN-O2','order_id':'ORD-O1','at':'2026-06-03T09:00:00Z'}))
    assert r2['status']=='pending_spray_test' and r2['recipe']=={'PB':10.0,'PC':10.0}
    st=paint_match.stock(db)
    assert st['PG1']['reserved_ml']==0 and st['PG1']['free_ml']==1000
    assert st['PB']['reserved_ml']==10 and st['PC']['reserved_ml']==10
    c2=paint_match.candidates(db,'ORD-O1')
    sp1=[p for p in c2['panels'] if p['panel']=='SP1'][0]
    assert sp1['per_illuminant']['LAMP']=='mismatch' and sp1['verdict']=='rejected'
    assert c2['solver']['status']=='ok' and c2['distinguishing']['bands']==[590]
    # 试喷新配方并补测, 证据齐全后才可领料
    paint.panel(db,FIX/'spray-panel-b.json')
    paint_match.measure(db,FIX/'band-measure-sp3-590.json')
    r3=paint_match.plan(db,write(tmp_path,'plan-o3.json',{'id':'PLAN-O3','order_id':'ORD-O1','at':'2026-06-03T14:00:00Z'}))
    assert r3['status']=='executable' and r3['recipe']=={'PB':10.0,'PC':10.0}
    paint_match.draw(db,write(tmp_path,'draw-o1.json',{'id':'DRAW-O1','mix_order_id':'PLAN-O3','at':'2026-06-03T15:00:00Z'}))
    paint_match.spray(db,write(tmp_path,'se2.json',{'id':'SE2','mix_order_id':'PLAN-O3','booth':'BOOTH-2','at':'2026-06-03T16:00:00Z'}))
    st=paint_match.stock(db)
    assert st['PB']['physical_ml']==990 and st['PC']['physical_ml']==990
    assert st['PG1']['physical_ml']==1000
    assert paint_match.order_status(db,'ORD-O1')['order']['status']=='sprayed'
    assert audit(db)['replay_ok']

def test_same_d65_facts_two_routes_diverge(tmp_path):
    db=tmp_path/'state.sqlite'
    base_v1_full(db)
    paint_match.order(db,FIX/'paint-order-fleet.json')
    paint_match.order(db,FIX/'paint-order-oem.json')
    rf=paint_match.plan(db,write(tmp_path,'plan-f1.json',{'id':'PLAN-F1','order_id':'ORD-F1','at':'2026-06-02T09:00:00Z'}))
    ro=paint_match.plan(db,write(tmp_path,'plan-o1.json',{'id':'PLAN-O1','order_id':'ORD-O1','at':'2026-06-02T09:30:00Z'}))
    assert rf['status']=='executable' and ro['status']=='pending_measurement'
    assert paint_match.stock(db)['PG1']['reserved_ml']==40
    paint_match.draw(db,write(tmp_path,'draw-f1.json',{'id':'DRAW-F1','mix_order_id':'PLAN-F1','at':'2026-06-02T10:00:00Z'}))
    paint_match.spray(db,write(tmp_path,'se1.json',{'id':'SE1','mix_order_id':'PLAN-F1','booth':'BOOTH-1','at':'2026-06-02T11:00:00Z'}))
    # 相同 D65 事实 → 不同库存与工序: 日间单已实喷, OEM 单仍持补测工单
    assert paint_match.order_status(db,'ORD-F1')['order']['status']=='sprayed'
    oem=paint_match.order_status(db,'ORD-O1')
    assert oem['order']['status']=='pending_measurement'
    assert oem['work_orders'][0]['kind']=='measure'
    # 日间订单的试喷确认不能挪给 OEM 订单: SP1 在 LAMP 下仍未证
    sp1=paint_match.candidates(db,'ORD-O1')['panels'][0]
    assert sp1['panel']=='SP1' and sp1['per_illuminant']['LAMP']=='unmeasured'
    with pytest.raises(ValueError,match='mix_not_executable'):
        paint_match.draw(db,write(tmp_path,'draw-o1.json',{'id':'DRAW-O1','mix_order_id':'PLAN-O1','at':'2026-06-02T12:00:00Z'}))
    assert audit(db)['replay_ok']

def test_batch_competition_never_double_books(tmp_path):
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-2.json')
    paint.sample(db,FIX/'paint-sample-v2.json')
    paint.pigment(db,FIX/'pigment-pa.json')
    paint.panel(db,FIX/'spray-panel-v2.json')
    for name in ('paint-order-f2.json','paint-order-f3.json','paint-order-f4.json'):
        paint_match.order(db,FIX/name)
    r2=paint_match.plan(db,write(tmp_path,'plan-f2.json',{'id':'PLAN-F2','order_id':'ORD-F2','at':'2026-06-02T09:00:00Z'}))
    r4=paint_match.plan(db,write(tmp_path,'plan-f4.json',{'id':'PLAN-F4','order_id':'ORD-F4','at':'2026-06-02T09:20:00Z'}))
    assert (r2['status'],r4['status'])==('executable','executable')
    # 多车共同预留: 20+10 恰好占满 30ml 批次
    st=paint_match.stock(db)['PA']
    assert (st['reserved_ml'],st['free_ml'])==(30,0)
    # 第三张调漆单要 20ml: 库存不足, 不得重复占用同一毫升
    r3=paint_match.plan(db,write(tmp_path,'plan-f3.json',{'id':'PLAN-F3','order_id':'ORD-F3','at':'2026-06-02T09:40:00Z'}))
    assert r3['status']=='awaiting_stock' and r3['reserved']==[]
    assert paint_match.stock(db)['PA']['reserved_ml']==30
    assert not [x for x in paint_match.order_status(db,'ORD-F3')['ledger'] if x['kind']=='reserve']
    # 领料消耗后库存更紧, 重规划仍等待
    paint_match.draw(db,write(tmp_path,'draw-f2.json',{'id':'DRAW-F2','mix_order_id':'PLAN-F2','at':'2026-06-02T10:00:00Z'}))
    r3b=paint_match.plan(db,write(tmp_path,'plan-f3b.json',{'id':'PLAN-F3B','order_id':'ORD-F3','at':'2026-06-02T11:00:00Z'}))
    assert r3b['status']=='awaiting_stock'
    st=paint_match.stock(db)['PA']
    assert (st['physical_ml'],st['reserved_ml'],st['free_ml'])==(10,10,0)
    assert st['reserved_ml']<=st['physical_ml']
    assert audit(db)['replay_ok']

def test_late_lamp_measurement_rework_reverses_inventory(tmp_path):
    db=tmp_path/'state.sqlite'
    fleet_sprayed(db,tmp_path)
    # 不倒填: 早于实喷时刻的夜间测量被拒绝
    with pytest.raises(ValueError,match='measurement_before_subject'):
        paint_match.measure(db,write(tmp_path,'m-bad.json',{'id':'M-BAD','subject_kind':'spray_event','subject_id':'SE1',
            'bands':[590],'values':[0.85],'measured_at':'2026-06-02T10:00:00Z'}))
    # 无差异的测量不能触发返工
    paint_match.measure(db,write(tmp_path,'m-ok.json',{'id':'M-OK','subject_kind':'paint_sample','subject_id':'PS1',
        'bands':[450],'values':[0.2],'measured_at':'2026-06-03T08:00:00Z'}))
    with pytest.raises(ValueError,match='rework_unjustified'):
        paint_match.rework(db,write(tmp_path,'rw0.json',{'id':'RW-0','order_id':'ORD-F1','measurement_id':'M-OK','at':'2026-06-04T09:00:00Z'}))
    # 迟到的路灯测量: 原漆 0.25, 已喷车身 0.85 → 推翻已喷配方
    paint_match.measure(db,FIX/'band-measure-ps1-590.json')
    paint_match.measure(db,FIX/'band-measure-se1-590.json')
    r=paint_match.rework(db,write(tmp_path,'rw1.json',{'id':'RW-1','order_id':'ORD-F1','measurement_id':'M-SE1-590','at':'2026-06-04T09:00:00Z'}))
    assert r['escalated']==['LAMP'] and r['written_off']==[{'lot':'PG1','ml':20.0}]
    # 领料与实喷事实保留, 库存冲销, 订单进入返工
    state=report(db)['state']
    assert state['spray_event'][0]['id']=='SE1'
    assert [x['kind'] for x in state['paint_ledger']]==['reserve','draw','writeoff']
    assert state['mix_order'][0]['status']=='overturned'
    assert state['paint_order'][0]['status']=='rework'
    st=paint_match.stock(db)['PG1']
    assert (st['drawn_ml'],st['written_off_ml'],st['in_use_ml'],st['physical_ml'])==(20,20,0,980)
    # 重规划: 要求升级为 D65+LAMP, 颜料未测路灯波段 → 补测工单而非直接调漆
    r2=paint_match.plan(db,write(tmp_path,'plan-f1b.json',{'id':'PLAN-F1B','order_id':'ORD-F1','at':'2026-06-04T10:00:00Z'}))
    assert r2['status']=='pending_measurement' and r2['required']==['D65','LAMP']
    lots={m['subject_id'] for m in r2['work_orders'][0]['measurements'] if m['subject_kind']=='pigment_lot'}
    assert 'PG1' in lots
    assert audit(db)['replay_ok']

def test_geometry_and_calibration_coordinates(tmp_path):
    db=tmp_path/'state.sqlite'
    base_v1_full(db)
    with pytest.raises(ValueError,match='unknown_illuminant'):
        paint_match.order(db,write(tmp_path,'ord-bad.json',{'id':'ORD-X','vin':'VIN-1','panel':'door-left',
            'illuminants':['MOONLIGHT'],'needed_ml':20,'at':'2026-06-02T08:00:00Z'}))
    # 珠光角度订单: 45/0 的旧数据与补测都不满足 75/0 测量坐标
    paint_match.order(db,write(tmp_path,'ord-g1.json',{'id':'ORD-G1','vin':'VIN-1','panel':'door-left','kind':'oem_multi',
        'illuminants':['D65','LAMP'],'needed_ml':20,'geometry':'75/0','at':'2026-06-02T08:00:00Z'}))
    r=paint_match.plan(db,write(tmp_path,'plan-g1.json',{'id':'PLAN-G1','order_id':'ORD-G1','at':'2026-06-02T09:00:00Z'}))
    assert r['status']=='pending_measurement'
    assert all(m['geometry']=='75/0' for m in r['work_orders'][0]['measurements'])
    paint_match.measure(db,write(tmp_path,'m-g45.json',{'id':'M-G45','subject_kind':'paint_sample','subject_id':'PS1',
        'bands':[590],'values':[0.25],'geometry':'45/0','measured_at':'2026-06-03T08:00:00Z'}))
    paint_match.measure(db,write(tmp_path,'m-gcal0.json',{'id':'M-GCAL0','subject_kind':'paint_sample','subject_id':'PS1',
        'bands':[590],'values':[0.25],'geometry':'75/0','calibration':'CAL0','measured_at':'2026-06-03T08:30:00Z'}))
    r2=paint_match.plan(db,write(tmp_path,'plan-g2.json',{'id':'PLAN-G2','order_id':'ORD-G1','at':'2026-06-03T09:00:00Z'}))
    assert r2['status']=='pending_measurement'
    paint_match.measure(db,write(tmp_path,'m-gok.json',{'id':'M-GOK','subject_kind':'paint_sample','subject_id':'PS1',
        'bands':[590],'values':[0.25],'geometry':'75/0','calibration':'CAL1','measured_at':'2026-06-03T08:45:00Z'}))
    r3=paint_match.plan(db,write(tmp_path,'plan-g3.json',{'id':'PLAN-G3','order_id':'ORD-G1','at':'2026-06-03T09:30:00Z'}))
    assert r3['status']=='pending_measurement'
    assert audit(db)['replay_ok']

def test_cli_paint_match_flow(tmp_path):
    db=str(tmp_path/'cli.sqlite')
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
    def run(*args):
        p=subprocess.run([sys.executable,'-m','autowarranty',*args],env=env,capture_output=True,text=True)
        assert p.returncode==0,p.stderr
        return json.loads(p.stdout)
    for cmd,name in [('vehicle-import','vehicle-ok.json'),('paint-sample-import','paint-sample-ok.json'),
                     ('pigment-import','pigment-ok.json'),('spray-panel-import','spray-panel-ok.json'),
                     ('paint-order-import','paint-order-fleet.json')]:
        run(cmd,str(FIX/name),'--db',db)
    plan=write(tmp_path,'plan.json',{'id':'PLAN-C1','order_id':'ORD-F1','at':'2026-06-02T09:00:00Z'})
    out=run('paint-plan',str(plan),'--db',db)
    assert out['status']=='executable' and out['recipe']=={'PG1':20.0}
    assert run('paint-stock','--db',db)['PG1']['reserved_ml']==20.0
    assert run('paint-order-status','--db',db,'--order','ORD-F1')['order']['status']=='executable'
    c=run('paint-candidates','--db',db,'--order','ORD-F1')
    assert c['panels'][0]['per_illuminant']['D65']=='proven'
    assert run('audit','--db',db)['replay_ok']
