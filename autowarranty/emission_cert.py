"""动态排放核证：分析仪响应延迟反卷积、NOx 质量区间与道路/场内双权限。

气体分析仪的浓度通道滞后于排气流量计，同一时刻的 ppm×流量会把加速前的
脉冲错配到加速后的大流量。本模块不赌事后对齐：把原始时序、响应核、时钟
同步、稀释气流与校准证书登记进测试链，对可行的响应延迟集合逐一反卷积
（纯延迟核即平移）并积分质量，输出峰值、质量区间与阈值裁决。缺段与截顶
不补零：缺段使质量上界无界，截顶点只作下界。道路证须对整个允许延迟范围
证明质量低于阈值，否则暂停发证并要求示踪校准；场内移车单只看峰值浓度。
"""
import hashlib,json,math
from datetime import datetime
from .store import connect,source,repeated,at

ROAD_MASS_LIMIT=500.0          # 市政道路准入：每行驶循环 NOx 质量上限（ppm·L/s·s 相对单位）
FACILITY_PEAK_LIMIT_PPM=120.0  # 封闭试验场低速移车：峰值浓度上限 ppm
CHANNELS=('nox_ppm','exhaust_lps')
SCOPES=('road','facility')

def _grid(doc):
    if float(doc['hz'])<=0:raise ValueError('invalid_hz')
    pts=doc['points']
    if not pts:raise ValueError('empty_series')
    ts=[float(p[0]) for p in pts];step=1.0/float(doc['hz'])
    for a,b in zip(ts,ts[1:]):
        if abs((b-a)-step)>1e-6:raise ValueError('nonuniform_series')
    if any(float(p[1])<0 for p in pts):raise ValueError('negative_value')
    return ts

def series(path,file):
    """登记原始浓度/流量时序（含采样频率与截顶标记），同一 (run,channel) 只锁一份原件。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'emission_series',file)
        if repeated(db,'emission_series',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM emission_run WHERE id=?',(d['run_id'],)).fetchone():raise ValueError('unknown_run')
        if d['channel'] not in CHANNELS:raise ValueError('unknown_channel')
        if d['channel']=='nox_ppm' and not d.get('analyzer'):raise ValueError('analyzer_required')
        ts=_grid(d)
        clipped=[float(x) for x in d.get('clipped',[])]
        if any(x not in set(ts) for x in clipped):raise ValueError('clip_orphan')
        if db.execute('SELECT 1 FROM emission_series WHERE run_id=? AND channel=?',(d['run_id'],d['channel'])).fetchone():raise ValueError('series_conflict')
        db.execute('INSERT INTO emission_series VALUES(?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['run_id'],d['channel'],float(d['hz']),d.get('unit',''),d.get('analyzer'),
                    json.dumps([[float(p[0]),float(p[1])] for p in d['points']]),json.dumps(clipped),sid))
        return {'id':d['id'],'replayed':False}

def kernel(path,file):
    """登记分析仪响应核：允许的响应延迟范围 [delay_min_s, delay_max_s] 与一阶时间常数。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'analyzer_kernel',file)
        if repeated(db,'analyzer_kernel',d['id'],sid):return {'id':d['id'],'replayed':True}
        lo,hi,tau=float(d['delay_min_s']),float(d['delay_max_s']),float(d.get('tau_s',0.0))
        if not 0<=lo<=hi or tau<0:raise ValueError('invalid_kernel')
        if db.execute('SELECT 1 FROM analyzer_kernel WHERE analyzer=?',(d['analyzer'],)).fetchone():raise ValueError('kernel_conflict')
        db.execute('INSERT INTO analyzer_kernel VALUES(?,?,?,?,?,?)',(d['id'],d['analyzer'],lo,hi,tau,sid))
        return {'id':d['id'],'replayed':False}

def clock_sync(path,file):
    """登记时钟同步包：分析仪钟相对流量计钟的偏移与漂移。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'clock_sync',file)
        if repeated(db,'clock_sync',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM emission_run WHERE id=?',(d['run_id'],)).fetchone():raise ValueError('unknown_run')
        if float(d['drift_ppm'])<0:raise ValueError('invalid_clock_sync')
        if db.execute('SELECT 1 FROM clock_sync WHERE run_id=? AND analyzer=?',(d['run_id'],d['analyzer'])).fetchone():raise ValueError('clock_sync_conflict')
        db.execute('INSERT INTO clock_sync VALUES(?,?,?,?,?,?)',(d['id'],d['run_id'],d['analyzer'],float(d['offset_s']),float(d['drift_ppm']),sid))
        return {'id':d['id'],'replayed':False}

def dilution(path,file):
    """登记稀释气流，并与 run 的排气流量×(稀释倍数-1) 交叉校验。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'dilution_air',file)
        if repeated(db,'dilution_air',d['id'],sid):return {'id':d['id'],'replayed':True}
        run=db.execute('SELECT * FROM emission_run WHERE id=?',(d['run_id'],)).fetchone()
        if not run:raise ValueError('unknown_run')
        lpm=float(d['lpm'])
        if lpm<0:raise ValueError('invalid_dilution')
        expected=run['exhaust_lpm']*(run['dilution_factor']-1.0)
        if abs(lpm-expected)>0.01*abs(expected)+1e-9:raise ValueError('dilution_inconsistent')
        if db.execute('SELECT 1 FROM dilution_air WHERE run_id=?',(d['run_id'],)).fetchone():raise ValueError('dilution_conflict')
        db.execute('INSERT INTO dilution_air VALUES(?,?,?,?)',(d['id'],d['run_id'],lpm,sid))
        return {'id':d['id'],'replayed':False}

def calibration(path,file):
    """登记示踪脉冲校准证书；迟到的校准会重估该分析仪已出具的证书（升级或撤证）。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'calibration',file)
        if repeated(db,'calibration',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM analyzer_kernel WHERE analyzer=?',(d['analyzer'],)).fetchone():raise ValueError('unknown_analyzer')
        if d.get('method')!='tracer_pulse':raise ValueError('unsupported_method')
        if float(d['measured_delay_s'])<0:raise ValueError('invalid_delay')
        moment=at(d['at'])
        db.execute('INSERT INTO calibration VALUES(?,?,?,?,?,?)',(d['id'],d['analyzer'],d['method'],float(d['measured_delay_s']),moment,sid))
        return {'id':d['id'],'replayed':False,'revalidated':_revalidate(db,d['analyzer'],moment)}

def _evaluate(db,run_id,scope):
    """对可行延迟集合反卷积并积分质量；返回裁决所需的全部量。缺段/截顶不补零。"""
    run=db.execute('SELECT * FROM emission_run WHERE id=?',(run_id,)).fetchone()
    if not run:raise ValueError('unknown_run')
    conc=db.execute("SELECT * FROM emission_series WHERE run_id=? AND channel='nox_ppm'",(run_id,)).fetchone()
    flow=db.execute("SELECT * FROM emission_series WHERE run_id=? AND channel='exhaust_lps'",(run_id,)).fetchone()
    if not conc or not flow:raise ValueError('series_missing')
    kern=db.execute('SELECT * FROM analyzer_kernel WHERE analyzer=?',(conc['analyzer'],)).fetchone()
    if not kern:raise ValueError('kernel_missing')
    sync=db.execute('SELECT * FROM clock_sync WHERE run_id=? AND analyzer=?',(run_id,conc['analyzer'])).fetchone()
    if not sync:raise ValueError('clock_sync_missing')
    if not db.execute('SELECT 1 FROM dilution_air WHERE run_id=?',(run_id,)).fetchone():raise ValueError('dilution_missing')
    cal=db.execute('SELECT * FROM calibration WHERE analyzer=? ORDER BY at DESC,rowid DESC LIMIT 1',(conc['analyzer'],)).fetchone()
    hz=float(conc['hz'])
    if float(flow['hz'])!=hz:raise ValueError('hz_mismatch')
    t0=datetime.fromisoformat(run['started_at'][:-1]+'+00:00');t1=datetime.fromisoformat(run['ended_at'][:-1]+'+00:00')
    duration=(t1-t0).total_seconds();bins=int(round(duration*hz))
    if bins<=0:raise ValueError('run_window_invalid')
    step=1.0/hz
    cpts={float(t):float(v) for t,v in json.loads(conc['points'])}
    fpts={float(t):float(v) for t,v in json.loads(flow['points'])}
    cclip={float(t) for t in json.loads(conc['clipped'])}
    # 可行位移 = 核延迟 + 时钟偏移 ± 漂移；物理延迟不为负，下界截到 0
    drift=abs(sync['drift_ppm'])*duration/1e6
    lo=max(0.0,kern['delay_min_s']+sync['offset_s']-drift)
    hi=kern['delay_max_s']+sync['offset_s']+drift
    grid=[n/hz for n in range(math.ceil(lo*hz-1e-9),math.floor(hi*hz+1e-9)+1)]
    contradicted=False
    if cal:
        m=cal['measured_delay_s']
        if not lo-1e-9<=m<=hi+1e-9:contradicted=True  # 示踪实测落在登记核之外：测试链自相矛盾
        effective=sorted(set(grid)|{m}) if contradicted else [m]
    else:effective=list(grid)
    sweep=sorted(set(grid)|set(effective))
    df=run['dilution_factor']
    masses={};partial=set()
    for s in sweep:
        n=int(round(s*hz));total=0.0;overlap=0;full=True
        for k in range(bins):
            ct=cpts.get(float(k+n));fq=fpts.get(float(k))
            if ct is None or fq is None:full=False;continue  # 缺段不补零：只累计观测到的部分
            total+=ct*fq*step;overlap+=1
        if not full:partial.add(s)
        masses[s]=df*total if overlap else 0.0
    naive=None;total=0.0;ok=True
    for k in range(bins):  # 常规同刻 ppm×流量 的对照量
        ct=cpts.get(float(k));fq=fpts.get(float(k))
        if ct is None or fq is None:ok=False;break
        total+=ct*fq*step
    if ok:naive=df*total
    ev_m=[masses[s] for s in effective]
    incomplete=any(s in partial for s in effective)
    mass_min=min(ev_m) if ev_m else None
    mass_max=None if (incomplete or not ev_m) else max(ev_m)
    if effective:
        m0=int(round(min(effective)*hz));m1=int(round((bins-1)+max(effective)*hz))
        if any(float(n) in cclip for n in range(m0,m1+1)):mass_max=None  # 截顶只作下界，上界无界
    # 峰值窗口取核范围与有效位移的并集：任一为真都被覆盖
    cand=[lo,hi]+list(effective)
    w0=int(round(min(cand)*hz));w1=int(round((bins-1)+max(cand)*hz))
    wvals=[];peak_full=True
    for n in range(w0,w1+1):
        v=cpts.get(float(n))
        if v is None:peak_full=False
        else:wvals.append(v)
    peak=max(wvals) if wvals else None
    clip_peak=any(float(n) in cclip for n in range(w0,w1+1))
    if scope=='road':
        threshold=ROAD_MASS_LIMIT
        if contradicted:status,reason='suspended','kernel_contradicted'
        elif mass_min is not None and mass_min>=threshold:status,reason='denied','proven_above_limit'
        elif mass_max is not None and mass_max<threshold:status,reason='granted','proven_below_limit'
        elif incomplete:status,reason='suspended','missing_segment'
        elif mass_max is None:status,reason='suspended','clipping_unbounded'
        else:status,reason='suspended','delay_uncertainty'
    else:
        threshold=FACILITY_PEAK_LIMIT_PPM
        if not peak_full:status,reason='suspended','missing_segment'
        elif clip_peak:status,reason='suspended','clipping_unbounded'
        elif peak<=threshold:status,reason='granted','peak_within_limit'
        else:status,reason='denied','peak_exceeds_limit'
    return {'status':status,'reason':reason,'threshold':threshold,'peak_ppm':peak,
            'mass_min':mass_min,'mass_max':mass_max,'naive_mass':naive,
            'mass_by_delay':{f'{s:g}':masses[s] for s in sweep},
            'feasible_delays_s':sorted(effective),
            'recommended_test':'tracer_pulse' if status=='suspended' and reason=='delay_uncertainty' else None,
            'calibration_id':cal['id'] if cal else None}

def _cert_dict(row,ev,replayed):
    out={'id':row['id'],'run_id':row['run_id'],'scope':row['scope'],'status':row['status'],
         'reason':row['reason'],'threshold':row['threshold'],'peak_ppm':row['peak_ppm'],
         'mass_min':row['mass_min'],'mass_max':row['mass_max'],
         'calibration_id':row['calibration_id'],'replayed':replayed}
    if ev:out.update(naive_mass=ev['naive_mass'],mass_by_delay=ev['mass_by_delay'],
                     feasible_delays_s=ev['feasible_delays_s'],recommended_test=ev['recommended_test'])
    return out

def certify(path,run_id,scope,at_time):
    """出具某次行驶循环的道路准入证或场内移车单；只依据排放测试链，不读 repair/inspection。"""
    if scope not in SCOPES:raise ValueError('unknown_scope')
    with connect(path) as db,db:
        moment=at(at_time)
        cid=f'CERT-{run_id}-{scope}'
        old=db.execute('SELECT * FROM emission_cert WHERE id=?',(cid,)).fetchone()
        if old:return _cert_dict(old,None,True)
        raw=json.dumps({'run_id':run_id,'scope':scope,'at':moment},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        row=db.execute('SELECT id FROM source WHERE sha=?',(digest,)).fetchone()
        sid=row['id'] if row else db.execute("INSERT INTO source(kind,sha,raw) VALUES('emission_cert',?,?)",(digest,raw)).lastrowid
        ev=_evaluate(db,run_id,scope)
        road=scope=='road'  # 场内单只锁峰值，不携带道路质量结论，避免被当作道路证
        db.execute('INSERT INTO emission_cert VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (cid,run_id,scope,ev['status'],ev['peak_ppm'],
                    ev['mass_min'] if road else None,ev['mass_max'] if road else None,
                    ev['threshold'],ev['reason'],ev['calibration_id'] if road else None,sid))
        db.execute('INSERT INTO cert_event(cert_id,action,reason,at) VALUES(?,?,?,?)',(cid,'issued',ev['reason'],moment))
        return _cert_dict(db.execute('SELECT * FROM emission_cert WHERE id=?',(cid,)).fetchone(),ev,False)

def _revalidate(db,analyzer,moment):
    """迟到校准后重估该分析仪涉及的证书；只改状态并记事件，不删除已出具记录。"""
    changed=[]
    run_ids=sorted({r['run_id'] for r in db.execute("SELECT run_id FROM emission_series WHERE channel='nox_ppm' AND analyzer=?",(analyzer,))})
    for run_id in run_ids:
        for cert in db.execute('SELECT * FROM emission_cert WHERE run_id=? ORDER BY id',(run_id,)).fetchall():
            ev=_evaluate(db,run_id,cert['scope'])
            road=cert['scope']=='road'
            new=(ev['status'],ev['peak_ppm'],ev['mass_min'] if road else None,ev['mass_max'] if road else None,
                 ev['reason'],ev['calibration_id'] if road else None)
            old=(cert['status'],cert['peak_ppm'],cert['mass_min'],cert['mass_max'],cert['reason'],cert['calibration_id'])
            if new==old:continue
            if ev['status']=='granted' and cert['status']!='granted':action='upgraded'
            elif cert['status']=='granted' and ev['status']!='granted':action='revoked'
            else:action='updated'
            db.execute('UPDATE emission_cert SET status=?,peak_ppm=?,mass_min=?,mass_max=?,reason=?,calibration_id=? WHERE id=?',
                       (*new,cert['id']))
            db.execute('INSERT INTO cert_event(cert_id,action,reason,at) VALUES(?,?,?,?)',(cert['id'],action,ev['reason'],moment))
            changed.append({'cert_id':cert['id'],'from':cert['status'],'to':ev['status'],'action':action,'reason':ev['reason']})
    return changed

def permits(path,vin):
    """按 VIN 汇总两类权限；场内 granted 而道路未 granted 时限制为 closed_facility_only。"""
    with connect(path) as db:
        runs={x['id'] for x in db.execute('SELECT id FROM emission_run WHERE vin=?',(vin,))}
        certs=[dict(x) for x in db.execute('SELECT * FROM emission_cert ORDER BY id') if x['run_id'] in runs]
        out={'vin':vin,'road':None,'facility':None,'restriction':None,
             'certs':[{'id':c['id'],'run_id':c['run_id'],'scope':c['scope'],'status':c['status'],'reason':c['reason']} for c in certs]}
        for scope in SCOPES:
            statuses={c['status'] for c in certs if c['scope']==scope}
            for s in ('granted','suspended','denied'):
                if s in statuses:out[scope]=s;break
        if out['facility']=='granted' and out['road']!='granted':out['restriction']='closed_facility_only'
        return out
