import json,os,shutil,subprocess,sys
from pathlib import Path
import pytest
from autowarranty import diagnosis,ota,trial,ota_trial
from autowarranty.report import report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'
SEED='seed-fleet-2026'
ASSIGNED='2026-06-01T00:30:00Z'

def write(tmp,name,doc):
    p=tmp/name;p.write_text(json.dumps(doc),encoding='utf-8');return p

def build_fleet(tmp):
    # 100车试验:种子决定50 new/50 control,每车1000km;另有100车未指派构成扩组池。
    db=tmp/'fleet.sqlite'
    ota.package(db,write(tmp,'pk-new.json',{'id':'PK-NEW','role':'gateway','version':'2.0','digest':'sha256-new','minimum_peer':'1.0'}))
    ota.package(db,write(tmp,'pk-old.json',{'id':'PK-OLD','role':'gateway','version':'1.0','digest':'sha256-old','minimum_peer':'1.0'}))
    vins=[f'VIN-{i:03d}' for i in range(1,101)]
    pool=[f'VIN-{i:03d}' for i in range(101,201)]
    for vin in vins+pool:
        diagnosis.vehicle(db,write(tmp,f'veh-{vin}.json',{'vin':vin,'owner':'FLEET','model':'M1'}))
    order=ota_trial.seed_order(SEED,sorted(vins))
    arms={v:('new' if i<50 else 'control') for i,v in enumerate(order)}
    new=[v for v in vins if arms[v]=='new'];ctrl=[v for v in vins if arms[v]=='control']
    for vin in vins:
        ota.ecu(db,write(tmp,f'ecu-{vin}.json',{'id':f'EC-{vin}','vin':vin,'role':'gateway','serial':f'GW-{vin}','version':'1.0','boot_counter':10}))
    ota_trial.wave(db,write(tmp,'wave.json',{'id':'W2','seed':SEED,'package_id':'PK-NEW','new_size':50,'control_size':50,'started_at':'2026-06-01T00:00:00Z'}))
    for vin in vins:
        trial.assignment(db,write(tmp,f'ta-{vin}.json',{'id':f'TA-{vin}','vin':vin,'arm':arms[vin],'package_id':'PK-NEW','assigned_at':ASSIGNED}))
    # new组:20车 written+booted(可信启动),20车仅 written,10车回执缺失
    for i,vin in enumerate(new):
        if i<40:
            ota.receipt(db,write(tmp,f'irw-{vin}.json',{'id':f'IR-W-{vin}','ecu_id':f'EC-{vin}','package_id':'PK-NEW','phase':'written','boot_counter':11,'observed_at':'2026-06-01T01:00:00Z'}))
        if i<20:
            ota.receipt(db,write(tmp,f'irb-{vin}.json',{'id':f'IR-B-{vin}','ecu_id':f'EC-{vin}','package_id':'PK-NEW','phase':'booted','boot_counter':11,'observed_at':'2026-06-01T02:00:00Z'}))
    for vin in vins:
        trial.session(db,write(tmp,f'ds-{vin}.json',{'id':f'DS-{vin}','vin':vin,'started_at':'2026-06-02T08:00:00Z','ended_at':'2026-06-02T18:00:00Z','distance_km':1000.0,'route_class':'urban'}))
    # new组共5次误报:可信启动20车中1次,存疑30车中4次;control组共8次
    targets=[(new[0],'N0'),(new[20],'N20'),(new[21],'N21'),(new[22],'N22'),(new[23],'N23')]+[(c,f'C{j}') for j,c in enumerate(ctrl[:8])]
    for vin,tag in targets:
        trial.alert(db,write(tmp,f'al-{tag}.json',{'id':f'AL-{tag}','session_id':f'DS-{vin}','kind':'false_brake','occurred_at':'2026-06-02T12:00:00Z','severity':2}))
    ota_trial.criterion(db,write(tmp,'crit-ops.json',{'id':'OPS','kind':'ops_safety','version':1,'signed_at':'2026-05-31T00:00:00Z',
                                                      'params':{'severe_level':4,'max_worsening_ratio':0.0,'expansion_cap':0.2}}))
    ota_trial.criterion(db,write(tmp,'crit-sup.json',{'id':'SUP','kind':'supplier_performance','version':1,'signed_at':'2026-05-31T00:00:00Z',
                                                      'params':{'min_coverage':0.9,'min_reduction_per_1000km':0.07}}))
    return {'db':db,'new':new,'ctrl':ctrl,'pool':pool}

@pytest.fixture(scope='session')
def base(tmp_path_factory):
    return build_fleet(tmp_path_factory.mktemp('fleet'))

@pytest.fixture()
def fleet(tmp_path,base):
    db=tmp_path/'fleet.sqlite';shutil.copy(base['db'],db)
    return {**base,'db':db}

def test_ops_approves_limited_expansion_while_supplier_suspended(fleet):
    # 同一5对8:运维准则批准有限扩组,供应商准则不得把好看的子集均值签成合同效果
    ops=ota_trial.evaluate(fleet['db'],'OPS','2026-06-10T00:00:00Z')
    assert ops['verdict']=='approved'
    assert len(ops['next_batch'])==20 and set(ops['next_batch'])<=set(fleet['pool'])
    assert ops['analysis']['itt']['new_alerts']==5 and ops['analysis']['itt']['control_alerts']==8
    sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-10T00:00:00Z')
    assert sup['verdict']=='suspended' and sup['reasons']==['booted_gap_open']
    assert len(sup['follow_up'])==30
    assert {f['vin'] for f in sup['follow_up']}.isdisjoint(fleet['new'][:20])
    lo,hi=sup['analysis']['supplier']['reduction_bounds']
    assert lo==pytest.approx(0.06) and hi==pytest.approx(0.11)
    naive=sup['analysis']['supplier']['naive_estimates']
    assert naive['receipt_complete_subset']==pytest.approx(0.05)
    assert naive['written_as_booted']==pytest.approx(0.1)
    assert naive['itt_mean']==pytest.approx(0.1)
    assert audit(fleet['db'])['replay_ok']

def test_identical_exports_two_worlds_opposite_supplier_verdicts(tmp_path,fleet):
    # 两世界当前导出完全相同:世界A的30车实际启动仅漏传回执,世界B的30车始终运行旧版
    world_a=tmp_path/'worldA.sqlite';world_b=tmp_path/'worldB.sqlite'
    base_sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-10T00:00:00Z')
    assert base_sup['verdict']=='suspended'
    shutil.copy(fleet['db'],world_a);shutil.copy(fleet['db'],world_b)
    assert report(world_a)==report(world_b)
    tmp=tmp_path
    for vin in fleet['new'][20:]:
        ota_trial.attestation(world_a,write(tmp,f'va-{vin}.json',{'id':f'VA-{vin}','ecu_id':f'EC-{vin}','package_id':'PK-NEW','boot_counter':11,'booted_at':'2026-06-01T02:30:00Z','attested_at':'2026-06-06T00:00:00Z'}))
        ota_trial.attestation(world_b,write(tmp,f'vb-{vin}.json',{'id':f'VB-{vin}','ecu_id':f'EC-{vin}','package_id':'PK-OLD','boot_counter':11,'booted_at':'2026-06-01T02:30:00Z','attested_at':'2026-06-06T00:00:00Z'}))
    sup_a=ota_trial.evaluate(world_a,'SUP','2026-06-12T00:00:00Z')
    sup_b=ota_trial.evaluate(world_b,'SUP','2026-06-12T00:00:00Z')
    # 世界A:实际启用率0.1/千km,降幅0.06低于注册阈值0.07,拒付;世界B:0.05,降幅0.11,确认
    assert sup_a['verdict']=='rejected'
    assert sup_a['analysis']['supplier']['activated_rate_bounds']==pytest.approx([0.1,0.1])
    assert sup_b['verdict']=='confirmed'
    assert sup_b['analysis']['supplier']['activated_rate_bounds']==pytest.approx([0.05,0.05])
    # 追证前的效果界同时包住两个世界的相反结论
    lo,hi=base_sup['analysis']['supplier']['reduction_bounds']
    assert lo<=0.06<=hi and lo<=0.11<=hi
    assert audit(world_a)['replay_ok'] and audit(world_b)['replay_ok']

def test_late_severe_alert_halts_and_preserves_prior_expansion(tmp_path,fleet):
    first=ota_trial.evaluate(fleet['db'],'OPS','2026-06-10T00:00:00Z')
    assert first['verdict']=='approved'
    trial.alert(fleet['db'],write(tmp_path,'al-late.json',{'id':'AL-LATE','session_id':f"DS-{fleet['new'][5]}",'kind':'false_brake','occurred_at':'2026-06-02T13:00:00Z','severity':5}))
    second=ota_trial.evaluate(fleet['db'],'OPS','2026-06-11T00:00:00Z')
    assert second['verdict']=='halted' and second['reasons']==['severe_alert_worsening']
    assert second['supersedes']==first['id']
    rows=ota_trial.captures(fleet['db'])['trial_decision']
    assert [r['verdict'] for r in rows]==['approved','halted']  # 先前有限扩组事实保留
    ota_trial.criterion(fleet['db'],write(tmp_path,'crit-ops-v2.json',{'id':'OPS','kind':'ops_safety','version':2,'signed_at':'2026-06-12T00:00:00Z',
                                                                      'params':{'severe_level':6,'max_worsening_ratio':0.0,'expansion_cap':0.2}}))
    third=ota_trial.evaluate(fleet['db'],'OPS','2026-06-13T00:00:00Z')
    assert third['verdict']=='approved' and third['criterion_version']==2 and third['supersedes']==second['id']
    assert audit(fleet['db'])['replay_ok']

def test_randomization_deviation_blocks_both_criteria(tmp_path,fleet):
    first=ota_trial.evaluate(fleet['db'],'OPS','2026-06-10T00:00:00Z')
    assert first['verdict']=='approved'
    diagnosis.vehicle(fleet['db'],write(tmp_path,'veh-rogue.json',{'vin':'VIN-201','owner':'FLEET','model':'M1'}))
    trial.assignment(fleet['db'],write(tmp_path,'ta-rogue.json',{'id':'TA-ROGUE','vin':'VIN-201','arm':'new','package_id':'PK-NEW','assigned_at':ASSIGNED}))
    ops=ota_trial.evaluate(fleet['db'],'OPS','2026-06-11T00:00:00Z')
    assert ops['verdict']=='halted' and ops['reasons']==['randomization_deviation']
    assert ops['supersedes']==first['id']
    sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-11T00:00:00Z')
    assert sup['verdict']=='suspended' and 'randomization_deviation' in sup['reasons']
    assert audit(fleet['db'])['replay_ok']

def test_shared_hazard_map_blocks_supplier_confirmation(tmp_path,fleet):
    # 共享危险路段地图使两组告警都集中在同一高风险道路类,不得伪称独立对照
    ota_trial.hazard(fleet['db'],FIX/'hazard-map-ok.json')
    for vin in fleet['new'][20:]:
        ota_trial.attestation(fleet['db'],write(tmp_path,f'vb-{vin}.json',{'id':f'VB-{vin}','ecu_id':f'EC-{vin}','package_id':'PK-OLD','boot_counter':11,'booted_at':'2026-06-01T02:30:00Z','attested_at':'2026-06-06T00:00:00Z'}))
    sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-12T00:00:00Z')
    assert sup['verdict']=='suspended' and 'shared_hazard_confounding' in sup['reasons']
    assert sup['analysis']['hazard']['shared_confounding']
    assert audit(fleet['db'])['replay_ok']

def test_control_contamination_excluded_and_suspends(tmp_path,fleet):
    vin=fleet['ctrl'][0]
    ota.receipt(fleet['db'],write(tmp_path,'ir-ctrl.json',{'id':'IR-CTRL','ecu_id':f'EC-{vin}','package_id':'PK-NEW','phase':'written','boot_counter':11,'observed_at':'2026-06-01T01:30:00Z'}))
    sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-10T00:00:00Z')
    assert sup['verdict']=='suspended' and 'control_contamination' in sup['reasons']
    assert sup['analysis']['supplier']['control_excluded_vins']==[vin]
    assert sup['analysis']['supplier']['control_pool_km']==49000.0
    assert audit(fleet['db'])['replay_ok']

def test_version_alternation_downgrades_proven_exposure(tmp_path,fleet):
    vin=fleet['new'][0]
    ota.receipt(fleet['db'],write(tmp_path,'ir-rollback.json',{'id':'IR-ROLLBACK','ecu_id':f'EC-{vin}','package_id':'PK-OLD','phase':'booted','boot_counter':12,'observed_at':'2026-06-03T01:00:00Z'}))
    exp=ota_trial.exposure(fleet['db'])
    assert exp['exposure']['proven_km']==19000.0
    assert any(v['kind']=='version_alternation' and vin in v['vins'] for v in exp['independence_violations'])
    sup=ota_trial.evaluate(fleet['db'],'SUP','2026-06-10T00:00:00Z')
    assert sup['verdict']=='suspended' and 'version_alternation' in sup['reasons']
    assert audit(fleet['db'])['replay_ok']

def test_written_receipt_alone_does_not_expose_40km_trip(tmp_path):
    # 车库断电只留下 written 回执的车,40km 行程不算暴露于新版;车端版本证明才能闭合
    db=tmp_path/'state.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');diagnosis.vehicle(db,FIX/'vehicle-control-ok.json')
    ota.ecu(db,FIX/'ecu-ok.json');ota.package(db,FIX/'software-package-ok.json');ota.receipt(db,FIX/'install-receipt-ok.json')
    trial.assignment(db,FIX/'trial-assignment-ok.json');trial.assignment(db,FIX/'trial-assignment-control-ok.json')
    trial.session(db,FIX/'drive-session-ok.json');trial.session(db,FIX/'drive-session-control-ok.json')
    trial.alert(db,FIX/'safety-alert-ok.json')
    ota_trial.wave(db,FIX/'trial-wave-ok.json')
    ota_trial.criterion(db,FIX/'release-criterion-ops-ok.json');ota_trial.criterion(db,FIX/'release-criterion-supplier-ok.json')
    exp=ota_trial.exposure(db)
    assert exp['exposure']['ambiguous_km']==40.0 and exp['exposure']['proven_km']==0.0
    sup=ota_trial.evaluate(db,'SUP','2026-06-10T00:00:00Z')
    assert sup['verdict']=='suspended' and 'booted_gap_open' in sup['reasons']
    assert sup['follow_up'][0]['vin']=='VIN-1' and sup['follow_up'][0]['needed']==['version_attestation']
    ota_trial.attestation(db,FIX/'version-attestation-ok.json')
    exp2=ota_trial.exposure(db)
    assert exp2['exposure']['proven_km']==40.0 and exp2['exposure']['ambiguous_km']==0.0
    sup2=ota_trial.evaluate(db,'SUP','2026-06-11T00:00:00Z')
    assert sup2['verdict']=='rejected'  # 40km 上确有1次误报,启用后降幅为负
    ops=ota_trial.evaluate(db,'OPS','2026-06-11T00:00:00Z')
    assert ops['verdict']=='approved' and ops['next_batch']==[]
    assert audit(db)['replay_ok']

def test_cli_evaluate_and_exposure(tmp_path,fleet):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parent.parent)}
    p=subprocess.run([sys.executable,'-m','autowarranty','ota-trial-evaluate','--db',str(fleet['db']),'--criterion','OPS','--at','2026-06-10T00:00:00Z'],env=env,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    out=json.loads(p.stdout)
    assert out['verdict']=='approved' and len(out['next_batch'])==20
    p=subprocess.run([sys.executable,'-m','autowarranty','ota-trial-exposure','--db',str(fleet['db'])],env=env,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['exposure']['proven_km']==20000.0
