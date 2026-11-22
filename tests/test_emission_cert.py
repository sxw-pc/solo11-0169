import json,os,subprocess,sys
from pathlib import Path
import pytest
from autowarranty import diagnosis,emissions,emission_cert,repair
from autowarranty.report import report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'
AT='2026-05-02T09:00:00Z'

def write(tmp,name,doc):
    p=tmp/name;p.write_text(json.dumps(doc),encoding='utf-8');return p

def chain(db):
    diagnosis.vehicle(db,FIX/'vehicle-ok.json')
    emissions.run(db,FIX/'emission-run-step.json')
    emissions.sample(db,FIX/'gas-sample-step.json')
    emission_cert.series(db,FIX/'emission-series-nox-step.json')
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    emission_cert.kernel(db,FIX/'analyzer-kernel-ok.json')
    emission_cert.clock_sync(db,FIX/'clock-sync-ok.json')
    emission_cert.dilution(db,FIX/'dilution-air-ok.json')

def test_tracer_delay_0_vs_2_seconds_flips_road_verdict(tmp_path):
    # 同一原始时序：延迟 0 秒时真实脉冲落在大流量段（1000），延迟 2 秒时落在小流量段（100）
    results={}
    for name,cal in [('zero','calibration-tracer-0.json'),('two','calibration-tracer-2.json')]:
        db=tmp_path/f'{name}.sqlite';chain(db);emission_cert.calibration(db,FIX/cal)
        results[name]=emission_cert.certify(db,'ER2','road',AT)
        assert audit(db)['replay_ok']
    zero,two=results['zero'],results['two']
    # 常规同刻实现对两种物理历史都输出 1000；固定提前 2 秒又都会输出 100
    assert zero['naive_mass']==two['naive_mass']==1000.0
    assert zero['mass_by_delay']==two['mass_by_delay']=={'0':1000.0,'1':1000.0,'2':100.0}
    # 示踪校准锁定延迟后，同一输入给出相反的道路结论
    assert zero['status']=='denied' and zero['mass_min']==zero['mass_max']==1000.0
    assert two['status']=='granted' and two['mass_min']==two['mass_max']==100.0
    assert (zero['calibration_id'],two['calibration_id'])==('CAL1','CAL2')

def test_uncalibrated_interval_suspends_road_but_facility_slip_issues(tmp_path):
    db=tmp_path/'state.sqlite';chain(db)
    road=emission_cert.certify(db,'ER2','road',AT)
    assert road['status']=='suspended' and road['reason']=='delay_uncertainty'
    assert (road['mass_min'],road['mass_max'])==(100.0,1000.0)
    assert road['feasible_delays_s']==[0.0,1.0,2.0]
    assert road['recommended_test']=='tracer_pulse'
    fac=emission_cert.certify(db,'ER2','facility',AT)
    assert fac['status']=='granted' and fac['peak_ppm']==100.0 and fac['threshold']==120.0
    # 证书不改写车辆状态；场内可移车但道路不可准入
    assert report(db)['state']['vehicle'][0]['status']=='registered'
    perm=emission_cert.permits(db,'VIN-1')
    assert (perm['road'],perm['facility'],perm['restriction'])==('suspended','granted','closed_facility_only')
    # 独立试车 released/delivered 不把场内单变成道路排放证
    for func,name in [(diagnosis.diagnose,'diagnosis-ok.json'),(diagnosis.authorize,'authorization-ok.json'),
                      (repair.part,'part-ok.json'),(repair.execute,'repair-ok.json'),(repair.inspect,'inspection-ok.json')]:
        func(db,FIX/name)
    assert report(db)['state']['vehicle'][0]['status']=='delivered'
    perm=emission_cert.permits(db,'VIN-1')
    assert perm['road']=='suspended' and perm['facility']=='granted'
    assert audit(db)['replay_ok']

def test_late_tracer_upgrades_or_denies_suspended_cert(tmp_path):
    db=tmp_path/'up.sqlite';chain(db)
    assert emission_cert.certify(db,'ER2','road',AT)['status']=='suspended'
    out=emission_cert.calibration(db,FIX/'calibration-tracer-2.json')
    assert out['revalidated']==[{'cert_id':'CERT-ER2-road','from':'suspended','to':'granted',
                                 'action':'upgraded','reason':'proven_below_limit'}]
    cert=report(db)['state']['emission_cert'][0]
    assert cert['status']=='granted' and cert['calibration_id']=='CAL2' and cert['mass_max']==100.0
    assert [e['action'] for e in report(db)['state']['cert_event']]==['issued','upgraded']
    assert audit(db)['replay_ok']
    db2=tmp_path/'deny.sqlite';chain(db2)
    emission_cert.certify(db2,'ER2','road',AT)
    out=emission_cert.calibration(db2,FIX/'calibration-tracer-0.json')
    assert out['revalidated'][0]['to']=='denied'
    assert report(db2)['state']['emission_cert'][0]['status']=='denied'
    assert audit(db2)['replay_ok']

def test_late_tracer_revokes_when_kernel_contradicted(tmp_path):
    db=tmp_path/'rev.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');emissions.run(db,FIX/'emission-run-step.json')
    emission_cert.series(db,FIX/'emission-series-nox-step.json')
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    kern=json.loads((FIX/'analyzer-kernel-ok.json').read_text());kern.update(delay_min_s=2.0,delay_max_s=2.0)
    emission_cert.kernel(db,write(tmp_path,'kernel-fixed.json',kern))
    emission_cert.clock_sync(db,FIX/'clock-sync-ok.json');emission_cert.dilution(db,FIX/'dilution-air-ok.json')
    assert emission_cert.certify(db,'ER2','road',AT)['status']=='granted'
    assert emission_cert.certify(db,'ER2','facility',AT)['status']=='granted'
    late={'id':'CAL3','analyzer':'AN1','method':'tracer_pulse','measured_delay_s':0.0,'at':'2026-05-04T09:00:00Z'}
    out=emission_cert.calibration(db,write(tmp_path,'cal-late.json',late))
    assert out['revalidated']==[{'cert_id':'CERT-ER2-road','from':'granted','to':'suspended',
                                 'action':'revoked','reason':'kernel_contradicted'}]
    certs={c['id']:c for c in report(db)['state']['emission_cert']}
    assert certs['CERT-ER2-road']['status']=='suspended'
    assert certs['CERT-ER2-facility']['status']=='granted'  # 已出具场内单保留，峰值不受延迟影响
    assert [e['action'] for e in report(db)['state']['cert_event']]==['issued','issued','revoked']
    assert audit(db)['replay_ok']

def test_saturation_clipping_not_silently_treated_as_true_value(tmp_path):
    db=tmp_path/'clip.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');emissions.run(db,FIX/'emission-run-step.json')
    nox=json.loads((FIX/'emission-series-nox-step.json').read_text());nox.update(id='ES-N2',clipped=[6])
    emission_cert.series(db,write(tmp_path,'nox-clip.json',nox))
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    emission_cert.kernel(db,FIX/'analyzer-kernel-ok.json')
    emission_cert.clock_sync(db,FIX/'clock-sync-ok.json');emission_cert.dilution(db,FIX/'dilution-air-ok.json')
    road=emission_cert.certify(db,'ER2','road',AT)
    assert road['status']=='suspended' and road['reason']=='clipping_unbounded'
    assert road['mass_min']==100.0 and road['mass_max'] is None  # 截顶点只作下界
    fac=emission_cert.certify(db,'ER2','facility',AT)
    assert fac['status']=='suspended' and fac['reason']=='clipping_unbounded'
    assert audit(db)['replay_ok']

def test_missing_tail_segment_not_zero_filled(tmp_path):
    db=tmp_path/'miss.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');emissions.run(db,FIX/'emission-run-step.json')
    nox=json.loads((FIX/'emission-series-nox-step.json').read_text());nox.update(id='ES-N3',points=nox['points'][:10])
    emission_cert.series(db,write(tmp_path,'nox-short.json',nox))
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    emission_cert.kernel(db,FIX/'analyzer-kernel-ok.json')
    emission_cert.clock_sync(db,FIX/'clock-sync-ok.json');emission_cert.dilution(db,FIX/'dilution-air-ok.json')
    road=emission_cert.certify(db,'ER2','road',AT)
    assert road['status']=='suspended' and road['reason']=='missing_segment'
    assert road['mass_min']==100.0 and road['mass_max'] is None  # 缺段使上界无界而非补零
    fac=emission_cert.certify(db,'ER2','facility',AT)
    assert fac['status']=='suspended' and fac['reason']=='missing_segment'
    assert audit(db)['replay_ok']

def test_clock_drift_widens_delay_uncertainty(tmp_path):
    db=tmp_path/'drift.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');emissions.run(db,FIX/'emission-run-step.json')
    nox=json.loads((FIX/'emission-series-nox-step.json').read_text());nox.update(id='ES-N4',points=nox['points']+[[12,0],[13,0]])
    emission_cert.series(db,write(tmp_path,'nox-long.json',nox))
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    emission_cert.kernel(db,FIX/'analyzer-kernel-ok.json')
    sync=json.loads((FIX/'clock-sync-ok.json').read_text());sync.update(id='CS2',drift_ppm=100000.0)
    emission_cert.clock_sync(db,write(tmp_path,'sync-drift.json',sync))
    emission_cert.dilution(db,FIX/'dilution-air-ok.json')
    road=emission_cert.certify(db,'ER2','road',AT)
    assert road['status']=='suspended' and road['reason']=='delay_uncertainty'
    assert road['feasible_delays_s']==[0.0,1.0,2.0,3.0]  # 漂移 0.1s/s × 10s 把可行集扩到 3 秒
    assert road['mass_by_delay']['3']==100.0
    assert (road['mass_min'],road['mass_max'])==(100.0,1000.0)
    assert audit(db)['replay_ok']

def test_full_chain_registered_and_report_locked(tmp_path):
    db=tmp_path/'state.sqlite';chain(db)
    emission_cert.certify(db,'ER2','road',AT);emission_cert.certify(db,'ER2','facility',AT)
    rep=report(db);st=rep['state']
    assert len(st['emission_series'])==2 and len(st['analyzer_kernel'])==1
    assert len(st['clock_sync'])==1 and len(st['dilution_air'])==1
    assert {c['scope'] for c in st['emission_cert']}=={'road','facility'}
    kinds={s['kind'] for s in rep['sources']}
    assert {'emission_series','analyzer_kernel','clock_sync','dilution_air','emission_cert'}<=kinds
    r=emissions.readings(db)
    assert r['kernels'][0]['delay_max_s']==2.0 and r['clock_syncs'][0]['offset_s']==0.0
    assert len(r['series'])==2 and len(r['certs'])==2
    assert audit(db)['replay_ok']

def test_chain_validation_rejects_bad_inputs(tmp_path):
    db=tmp_path/'v.sqlite'
    diagnosis.vehicle(db,FIX/'vehicle-ok.json');emissions.run(db,FIX/'emission-run-step.json')
    with pytest.raises(ValueError,match='dilution_inconsistent'):
        emission_cert.dilution(db,write(tmp_path,'dil.json',{'id':'DA9','run_id':'ER2','lpm':5.0}))
    nox=json.loads((FIX/'emission-series-nox-step.json').read_text())
    nox['id']='ES-N9';nox['points'][3][0]=3.5
    with pytest.raises(ValueError,match='nonuniform_series'):
        emission_cert.series(db,write(tmp_path,'nu.json',nox))
    with pytest.raises(ValueError,match='unknown_scope'):
        emission_cert.certify(db,'ER2','roadworthy',AT)
    emission_cert.series(db,FIX/'emission-series-nox-step.json')
    emission_cert.series(db,FIX/'emission-series-flow-step.json')
    with pytest.raises(ValueError,match='kernel_missing'):
        emission_cert.certify(db,'ER2','road',AT)
    assert report(db)['state']['emission_cert']==[]

def test_cli_emission_chain_and_late_tracer(tmp_path):
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parent.parent)}
    db=tmp_path/'cli.sqlite'
    def run(*args):
        p=subprocess.run([sys.executable,'-m','autowarranty',*args],env=env,capture_output=True,text=True)
        assert p.returncode==0,p.stderr
        return json.loads(p.stdout)
    run('vehicle-import',str(FIX/'vehicle-ok.json'),'--db',str(db))
    run('emission-run-import',str(FIX/'emission-run-step.json'),'--db',str(db))
    run('emission-series-import',str(FIX/'emission-series-nox-step.json'),'--db',str(db))
    run('emission-series-import',str(FIX/'emission-series-flow-step.json'),'--db',str(db))
    run('analyzer-kernel-import',str(FIX/'analyzer-kernel-ok.json'),'--db',str(db))
    run('clock-sync-import',str(FIX/'clock-sync-ok.json'),'--db',str(db))
    run('dilution-air-import',str(FIX/'dilution-air-ok.json'),'--db',str(db))
    out=run('emission-certify','--db',str(db),'--run','ER2','--scope','road','--at',AT)
    assert out['status']=='suspended' and out['reason']=='delay_uncertainty'
    run('calibration-import',str(FIX/'calibration-tracer-2.json'),'--db',str(db))
    out=run('emission-permits','--db',str(db),'--vin','VIN-1')
    assert out['road']=='granted'
    assert run('audit','--db',str(db))['replay_ok']
