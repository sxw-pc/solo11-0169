import hashlib,json
from .store import connect,source,at,repeated

# 升级暴露判定与车队随机试验分析。服务器指派不等于车端启动：written 回执只证明镜像写入，
# 可信启动须 booted 回执或车端版本证明。运维安全准则只看意向组严重告警是否恶化，供应商绩效
# 准则必须等车端启动证据闭合后用预注册因果估计验收；两者对同一份数据给出不同业务结论。

def wave(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'trial_wave',file)
        if repeated(db,'trial_wave',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM software_package WHERE id=?',(d['package_id'],)).fetchone():raise ValueError('unknown_package')
        if not d['seed'] or d['new_size']<0 or d['control_size']<0:raise ValueError('wave_incomplete')
        db.execute('INSERT INTO trial_wave VALUES(?,?,?,?,?,?,?)',(d['id'],d['seed'],d['package_id'],d['new_size'],d['control_size'],at(d['started_at']),sid))
        return {'id':d['id'],'replayed':False}

def criterion(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'release_criterion',file)
        old=db.execute('SELECT source_id FROM release_criterion WHERE id=? AND version=?',(d['id'],d['version'])).fetchone()
        if old:
            if old['source_id']!=sid:raise ValueError('event_id_conflict')
            return {'id':d['id'],'version':d['version'],'replayed':True}
        if d['kind'] not in ('ops_safety','supplier_performance'):raise ValueError('invalid_criterion_kind')
        if not isinstance(d['version'],int) or d['version']<1:raise ValueError('invalid_criterion_version')
        if not isinstance(d.get('params'),dict):raise ValueError('criterion_params_missing')
        db.execute('INSERT INTO release_criterion VALUES(?,?,?,?,?,?)',(d['id'],d['version'],d['kind'],at(d['signed_at']),json.dumps(d['params'],sort_keys=True),sid))
        return {'id':d['id'],'version':d['version'],'replayed':False}

def attestation(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'version_attestation',file)
        if repeated(db,'version_attestation',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM ecu WHERE id=?',(d['ecu_id'],)).fetchone():raise ValueError('unknown_ecu')
        if not db.execute('SELECT 1 FROM software_package WHERE id=?',(d['package_id'],)).fetchone():raise ValueError('unknown_package')
        db.execute('INSERT INTO version_attestation VALUES(?,?,?,?,?,?,?)',(d['id'],d['ecu_id'],d['package_id'],d['boot_counter'],at(d['booted_at']),at(d['attested_at']),sid))
        return {'id':d['id'],'replayed':False}

def hazard(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'hazard_map',file)
        if repeated(db,'hazard_map',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not d['route_class'] or d['risk_level']<=0:raise ValueError('hazard_incomplete')
        db.execute('INSERT INTO hazard_map VALUES(?,?,?,?,?)',(d['id'],d['route_class'],d['risk_level'],at(d['valid_from']),sid))
        return {'id':d['id'],'replayed':False}

def seed_order(seed,vins):
    return sorted(vins,key=lambda v:hashlib.sha256(f'{seed}:{v}'.encode()).hexdigest())

def _events(db,vin):
    out=[]
    for ecu in db.execute('SELECT id FROM ecu WHERE vin=? ORDER BY id',(vin,)):
        for r in db.execute('SELECT * FROM install_receipt WHERE ecu_id=? ORDER BY observed_at,id',(ecu['id'],)):
            out.append((r['observed_at'],0,r['id'],'receipt',r['package_id'],r['phase']))
        for a in db.execute('SELECT * FROM version_attestation WHERE ecu_id=? ORDER BY booted_at,id',(ecu['id'],)):
            out.append((a['booted_at'],1,a['id'],'attestation',a['package_id'],'booted'))
    return sorted(out)

def _windows(events,package_id,start):
    # unknown:无任何证据;ambiguous:已写入未证启动;proven:可信启动新版;other:证明运行其他版本
    state='unknown';alternating=False;intervals=[];cursor=start
    for ts,_,_,kind,pkg,phase in events:
        if ts>cursor:intervals.append((state,cursor,ts));cursor=ts
        if phase=='booted':
            if pkg==package_id:state='proven'
            else:
                if state=='proven':alternating=True
                state='other'
        elif pkg==package_id and state in ('unknown','other'):state='ambiguous'
    intervals.append((state,cursor,None))
    return intervals,alternating

def _state_at(intervals,t):
    for state,a,b in intervals:
        if a<=t and (b is None or t<b):return state
    return 'unknown'

def _analyze(db):
    wave=db.execute('SELECT * FROM trial_wave ORDER BY started_at DESC,id DESC LIMIT 1').fetchone()
    if not wave:raise ValueError('trial_wave_missing')
    pkg=wave['package_id']
    assigns=[dict(x) for x in db.execute('SELECT * FROM trial_assignment WHERE package_id=? ORDER BY id',(pkg,))]
    sessions={x['id']:dict(x) for x in db.execute('SELECT * FROM drive_session ORDER BY id')}
    by_session={}
    for al in db.execute('SELECT * FROM safety_alert ORDER BY id'):
        by_session.setdefault(al['session_id'],[]).append(dict(al))
    risk={x['route_class']:x['risk_level'] for x in db.execute('SELECT route_class,MAX(risk_level) AS risk_level FROM hazard_map GROUP BY route_class')}
    vins=[a['vin'] for a in assigns]
    order=seed_order(wave['seed'],sorted(set(vins)))
    expected={v:('new' if i<wave['new_size'] else 'control') for i,v in enumerate(order)}
    deviation=(len(vins)!=wave['new_size']+wave['control_size'] or len(set(vins))!=len(vins)
               or any(a['arm']!=expected.get(a['vin']) for a in assigns))
    new_rows=[];ctrl_rows=[]
    for a in assigns:
        vin=a['vin'];t0=a['assigned_at']
        post=[s for s in sessions.values() if s['vin']==vin and s['started_at']>=t0]
        pre_km=sum(s['distance_km'] for s in sessions.values() if s['vin']==vin and s['started_at']<t0)
        events=_events(db,vin)
        if a['arm']=='new':
            intervals,alternating=_windows(events,pkg,t0)
            row={'vin':vin,'alternating':alternating,'pre_km':pre_km,
                 'km':{'proven':0.0,'ambiguous':0.0,'unknown':0.0,'other':0.0},
                 'alerts':{'proven':0,'ambiguous':0,'unknown':0,'other':0},
                 'severities':[],'hazard_alerts':0,'route_km':{},'risk_km':0.0,
                 'has_receipt':any(k=='receipt' and p==pkg for _,_,_,k,p,_ in events),
                 'receipt_phases':[ph for _,_,_,k,p,ph in events if k=='receipt' and p==pkg]}
            for s in post:
                st=_state_at(intervals,s['started_at'])
                row['km'][st]+=s['distance_km']
                row['route_km'][s['route_class']]=row['route_km'].get(s['route_class'],0.0)+s['distance_km']
                row['risk_km']+=s['distance_km']*risk.get(s['route_class'],1.0)
                for al in by_session.get(s['id'],[]):
                    row['alerts'][st]+=1;row['severities'].append(al['severity'])
                    if s['route_class'] in risk:row['hazard_alerts']+=1
            if alternating:  # 多版本交替无法归因,证明暴露降级为存疑
                row['km']['ambiguous']+=row['km']['proven'];row['km']['proven']=0.0
                row['alerts']['ambiguous']+=row['alerts']['proven'];row['alerts']['proven']=0
            new_rows.append(row)
        else:
            contaminated=any(p==pkg for _,_,_,_,p,_ in events)
            row={'vin':vin,'contaminated':contaminated,'pre_km':pre_km,
                 'km':sum(s['distance_km'] for s in post),
                 'risk_km':sum(s['distance_km']*risk.get(s['route_class'],1.0) for s in post),
                 'alerts':sum(len(by_session.get(s['id'],[])) for s in post),
                 'severities':[al['severity'] for s in post for al in by_session.get(s['id'],[])],
                 'hazard_alerts':sum(1 for s in post if s['route_class'] in risk for _ in by_session.get(s['id'],[])),
                 'route_km':{}}
            for s in post:row['route_km'][s['route_class']]=row['route_km'].get(s['route_class'],0.0)+s['distance_km']
            ctrl_rows.append(row)
    def rate(a,k):return a/k*1000 if k>0 else None
    new_km=sum(sum(r['km'].values()) for r in new_rows)
    new_alerts=sum(sum(r['alerts'].values()) for r in new_rows)
    ctrl_km=sum(r['km'] for r in ctrl_rows);ctrl_alerts=sum(r['alerts'] for r in ctrl_rows)
    ctrl_pool=[r for r in ctrl_rows if not r['contaminated']]
    pool_km=sum(r['km'] for r in ctrl_pool);pool_alerts=sum(r['alerts'] for r in ctrl_pool)
    proven_km=sum(r['km']['proven'] for r in new_rows);proven_alerts=sum(r['alerts']['proven'] for r in new_rows)
    amb_km=sum(r['km']['ambiguous'] for r in new_rows);amb_alerts=sum(r['alerts']['ambiguous'] for r in new_rows)
    unk_km=sum(r['km']['unknown'] for r in new_rows);unk_alerts=sum(r['alerts']['unknown'] for r in new_rows)
    oth_km=sum(r['km']['other'] for r in new_rows);oth_alerts=sum(r['alerts']['other'] for r in new_rows)
    ctrl_rate=rate(pool_alerts,pool_km)
    act_lo=rate(proven_alerts,proven_km)  # 世界B:未证启动的车始终运行旧版
    act_hi=rate(proven_alerts+amb_alerts+unk_alerts,proven_km+amb_km+unk_km)  # 世界A:全部实际启动仅漏传回执
    act_bounds=sorted(x for x in (act_lo,act_hi) if x is not None)
    red_bounds=sorted(ctrl_rate-x for x in act_bounds) if ctrl_rate is not None else []
    resolved=[r for r in new_rows if r['km']['ambiguous']==0 and r['km']['unknown']==0]
    coverage=len(resolved)/len(new_rows) if new_rows else 0.0
    follow_up=sorted(({'vin':r['vin'],'ambiguous_km':r['km']['ambiguous'],'unknown_km':r['km']['unknown'],
                       'needed':['version_attestation'] if r['has_receipt'] else ['install_receipt','version_attestation']}
                      for r in new_rows if r['km']['ambiguous']>0 or r['km']['unknown']>0),
                     key=lambda f:(-(f['ambiguous_km']+f['unknown_km']),f['vin']))
    violations=[]
    alt=sorted(r['vin'] for r in new_rows if r['alternating'])
    if alt:violations.append({'kind':'version_alternation','vins':alt})
    cont=sorted(r['vin'] for r in ctrl_rows if r['contaminated'])
    if cont:violations.append({'kind':'control_contamination','vins':cont})
    new_haz=sum(r['hazard_alerts'] for r in new_rows);ctrl_haz=sum(r['hazard_alerts'] for r in ctrl_rows)
    new_share=new_haz/new_alerts if new_alerts else 0.0
    ctrl_share=ctrl_haz/ctrl_alerts if ctrl_alerts else 0.0
    shared=bool(risk) and new_alerts>0 and ctrl_alerts>0 and new_share>=0.5 and ctrl_share>=0.5
    if shared:violations.append({'kind':'shared_hazard_confounding','vins':[]})
    phases={}
    for r in new_rows:
        for ph in r['receipt_phases']:phases[ph]=phases.get(ph,0)+1
    def mix(rows):
        out={}
        for r in rows:
            for rc,km in r['route_km'].items():out[rc]=out.get(rc,0.0)+km
        return out
    new_pre=sum(r['pre_km'] for r in new_rows);ctrl_pre=sum(r['pre_km'] for r in ctrl_rows)
    def cov(km,pre):return km/(km+pre) if km+pre>0 else None
    return {'wave':{'id':wave['id'],'seed':wave['seed'],'package_id':pkg,'new_size':wave['new_size'],'control_size':wave['control_size']},
            'randomization_deviation':deviation,
            'assumptions':{'itt':['randomization_executed_as_seeded','alert_capture_complete_within_sessions',
                                  'arms_comparable_at_equal_mileage_given_route_mix'],
                           'activated':['exposure_state_from_captured_receipts_and_attestations_only',
                                        'written_receipt_is_not_boot_proof',
                                        'version_state_persists_until_contradictory_evidence',
                                        'unrecorded_rollback_only_possible_where_flagged',
                                        'per_vin_independence_given_route_class']},
            'independence_violations':violations,
            'itt':{'new_km':new_km,'control_km':ctrl_km,'new_alerts':new_alerts,'control_alerts':ctrl_alerts,
                   'new_rate':rate(new_alerts,new_km),'control_rate':rate(ctrl_alerts,ctrl_km),
                   'new_severities':[s for r in new_rows for s in r['severities']],
                   'control_severities':[s for r in ctrl_rows for s in r['severities']],
                   'new_coverage':cov(new_km,new_pre),'control_coverage':cov(ctrl_km,ctrl_pre),
                   'route_mix':{'new':mix(new_rows),'control':mix(ctrl_rows)},
                   'risk_adjusted_km':{'new':sum(r['risk_km'] for r in new_rows),'control':sum(r['risk_km'] for r in ctrl_rows)}},
            'exposure':{'proven_km':proven_km,'ambiguous_km':amb_km,'unknown_km':unk_km,'unexposed_km':oth_km,
                        'proven_alerts':proven_alerts,'ambiguous_alerts':amb_alerts,'unknown_alerts':unk_alerts,'unexposed_alerts':oth_alerts,
                        'resolved_vins':len(resolved),'new_arm_vins':len(new_rows),'coverage':coverage,
                        'package_counts':phases},
            'supplier':{'control_pool_km':pool_km,'control_pool_alerts':pool_alerts,'control_rate':ctrl_rate,
                        'control_excluded_vins':cont,
                        'activated_rate_bounds':act_bounds,'reduction_bounds':red_bounds,
                        'naive_estimates':{'receipt_complete_subset':act_lo,'written_as_booted':act_hi,
                                           'itt_mean':rate(new_alerts,new_km)}},
            'hazard':{'classes':sorted(risk),'new_hazard_share':new_share,'control_hazard_share':ctrl_share,
                      'shared_confounding':shared},
            'follow_up':follow_up}

def _decide_ops(an,params):
    itt=an['itt']
    if an['randomization_deviation']:return {'verdict':'halted','reasons':['randomization_deviation']}
    if itt['new_km']<=0 or itt['control_km']<=0:return {'verdict':'insufficient_data','reasons':['empty_arm']}
    sev=params.get('severe_level',4);margin=params.get('max_worsening_ratio',0.0)
    sn=sum(1 for s in itt['new_severities'] if s>=sev)
    sc=sum(1 for s in itt['control_severities'] if s>=sev)
    rn=sn/itt['new_km']*1000;rc=sc/itt['control_km']*1000
    out={'verdict':'approved','reasons':[],
         'severe':{'new':sn,'control':sc,'new_rate':rn,'control_rate':rc,'severe_level':sev}}
    if rn>rc*(1+margin):out.update(verdict='halted',reasons=['severe_alert_worsening'])
    return out

def _decide_supplier(an,params):
    sup=an['supplier'];exp=an['exposure']
    if an['itt']['new_km']<=0 or sup['control_pool_km']<=0:return {'verdict':'insufficient_data','reasons':['empty_arm']}
    reasons=[v['kind'] for v in an['independence_violations']]
    if an['randomization_deviation']:reasons=['randomization_deviation']+reasons
    if exp['coverage']<params.get('min_coverage',0.9):reasons.append('booted_gap_open')
    if reasons:return {'verdict':'suspended','reasons':reasons}
    bounds=sup['reduction_bounds']
    if not bounds:return {'verdict':'insufficient_data','reasons':['empty_arm']}
    lo,hi=bounds[0],bounds[-1]
    thr=params.get('min_reduction_per_1000km',0.0)
    if lo>=thr:return {'verdict':'confirmed','reasons':[]}
    if hi<thr:return {'verdict':'rejected','reasons':['effect_below_registered_threshold']}
    return {'verdict':'suspended','reasons':['effect_not_identified']}

def evaluate(path,criterion_id,decided_at):
    with connect(path) as db,db:
        moment=at(decided_at)
        crit=db.execute('SELECT * FROM release_criterion WHERE id=? ORDER BY version DESC LIMIT 1',(criterion_id,)).fetchone()
        if not crit:raise ValueError('unknown_criterion')
        raw=json.dumps({'criterion_id':criterion_id,'at':moment},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        if db.execute('SELECT 1 FROM source WHERE sha=?',(digest,)).fetchone():
            prior=db.execute('SELECT * FROM trial_decision WHERE criterion_id=? AND decided_at=? ORDER BY id DESC LIMIT 1',(criterion_id,moment)).fetchone()
            if prior:return {**json.loads(prior['detail']),'id':prior['id'],'replayed':True}
        else:db.execute("INSERT INTO source(kind,sha,raw) VALUES('trial_decision',?,?)",(digest,raw))
        an=_analyze(db)
        params=json.loads(crit['params'])
        decision=_decide_ops(an,params) if crit['kind']=='ops_safety' else _decide_supplier(an,params)
        prev=db.execute('SELECT id FROM trial_decision WHERE criterion_id=? ORDER BY id DESC LIMIT 1',(criterion_id,)).fetchone()
        out={'criterion_id':criterion_id,'criterion_version':crit['version'],'kind':crit['kind'],'decided_at':moment,
             'supersedes':prev['id'] if prev else None,'analysis':an,**decision}
        if crit['kind']=='ops_safety' and decision['verdict']=='approved':
            pool=[x['vin'] for x in db.execute('SELECT vin FROM vehicle WHERE vin NOT IN (SELECT vin FROM trial_assignment)')]
            out['next_batch']=seed_order(an['wave']['seed']+':expansion',pool)[:int(params.get('expansion_cap',0.2)*len(pool)+1e-9)]
        if crit['kind']=='supplier_performance':out['follow_up']=an['follow_up']
        cur=db.execute('INSERT INTO trial_decision(criterion_id,criterion_version,decided_at,verdict,detail,supersedes) VALUES(?,?,?,?,?,?)',
                       (criterion_id,crit['version'],moment,decision['verdict'],json.dumps(out,sort_keys=True),out['supersedes']))
        return {**out,'id':cur.lastrowid,'replayed':False}

def exposure(path):
    with connect(path) as db:return _analyze(db)

def captures(path):
    with connect(path) as db:
        return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY 1,2')]
                for t in ('trial_wave','release_criterion','version_attestation','hazard_map','trial_decision')}
