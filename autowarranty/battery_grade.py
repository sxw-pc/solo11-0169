"""退役电池梯次利用定级:已证窗口与未知全程分开记账。

把 BMS 局部充电原件、独立电量计、每串最低单体电压、静置电压、温度按同一
包组成与校准版本对齐;已测 SOC 窗口签发限窗用途证书,未测两端不做百分比
线性外推;全程容量须受控完整循环证明最弱单体后方可签全程证书;电芯重组
后前包测试不得拼接;签发、装运、撤证与升级全部留在时间线里。
"""
import hashlib,json
from .store import connect,source,repeated,at

TOL_AH=0.5
MIN_CELL_SAFE_MV=2500
MAX_CELL_MV=4200
CUTOFF_REACHED_MV=2700
WINDOWED='windowed_storage'
MOBILE='full_range_mobile'

def span_ah(samples,start,end):
    """累计 Ah 采样曲线在 [start,end] SOC 区间的实测电量(线性插值采样点之间)。"""
    pts=sorted((float(s),float(a)) for s,a in samples)
    if start>=end or not pts or start<pts[0][0] or end>pts[-1][0]:raise ValueError('curve_gap')
    def value(x):
        if x<=pts[0][0]:return pts[0][1]
        for (s0,a0),(s1,a1) in zip(pts,pts[1:]):
            if s0<=x<=s1:return a0 if s1==s0 else a0+(a1-a0)*(x-s0)/(s1-s0)
        return pts[-1][1]
    return value(end)-value(start)

def _current(db,pack_id):
    return db.execute('SELECT * FROM pack_composition WHERE pack_id=? ORDER BY generation DESC LIMIT 1',(pack_id,)).fetchone()

def composition(path,file):
    """登记包的电芯组成与校准版本;再次登记即重组,新一代身份不继承前包测试。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'pack_composition',file)
        if repeated(db,'pack_composition',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        if not d['composition'] or not d['calibration']:raise ValueError('invalid_composition')
        moment=at(d['at'])
        cur=_current(db,d['pack_id'])
        if cur and (cur['composition'],cur['calibration'])==(d['composition'],d['calibration']):raise ValueError('identity_unchanged')
        gen=1 if not cur else cur['generation']+1
        db.execute('INSERT INTO pack_composition VALUES(?,?,?,?,?,?,?)',(d['id'],d['pack_id'],gen,d['composition'],d['calibration'],moment,sid))
        superseded=[];revoked=[]
        if gen>1:
            for c in db.execute("SELECT id FROM use_certificate WHERE pack_id=? AND status='issued'",(d['pack_id'],)).fetchall():
                db.execute("UPDATE use_certificate SET status='superseded' WHERE id=?",(c['id'],))
                db.execute('INSERT INTO grade_event(pack_id,kind,ref_id,at,detail) VALUES(?,?,?,?,?)',(d['pack_id'],'certificate_superseded',c['id'],moment,'reassembly'))
                superseded.append(c['id'])
            for o in db.execute("SELECT id FROM use_order WHERE pack_id=? AND status='pending'",(d['pack_id'],)).fetchall():
                db.execute("UPDATE use_order SET status='revoked',closed_at=? WHERE id=?",(moment,o['id']))
                db.execute('INSERT INTO grade_event(pack_id,kind,ref_id,at,detail) VALUES(?,?,?,?,?)',(d['pack_id'],'order_revoked',o['id'],moment,'reassembly_identity_change'))
                revoked.append(o['id'])
        return {'id':d['id'],'pack_id':d['pack_id'],'generation':gen,'superseded':superseded,'revoked_orders':revoked}

def agreement(path,file):
    """录入已签署的收货约定:固定储能站限窗接收,或移动电源厂全程最弱单体门槛。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'use_agreement',file)
        if repeated(db,'use_agreement',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        moment=at(d['at'])
        if d['kind']==WINDOWED:
            if not 0<=d['window_start']<d['window_end']<=100:raise ValueError('invalid_agreement_window')
            ws,we,mn=d['window_start'],d['window_end'],None
        elif d['kind']==MOBILE:
            if d['min_full_ah']<=0:raise ValueError('invalid_agreement_minimum')
            ws,we,mn=None,None,d['min_full_ah']
        else:raise ValueError('unknown_agreement_kind')
        db.execute('INSERT INTO use_agreement VALUES(?,?,?,?,?,?,?,?,?)',(d['id'],d['pack_id'],d['buyer'],d['kind'],ws,we,mn,moment,sid))
        return {'id':d['id'],'kind':d['kind']}

def window_evidence(path,file):
    """对齐到某次充电原件的窗口证据:双电量计、最低单体电压、静置电压、温度、SOC 轨迹。

    两个独立电量计冲突或 SOC 轨迹漂移时隔离本次测试,不取有利值。
    """
    with connect(path) as db,db:
        sid,d,_=source(db,'window_evidence',file)
        if repeated(db,'window_evidence',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        ct=db.execute('SELECT * FROM charge_test WHERE id=?',(d['charge_test_id'],)).fetchone()
        if not ct:raise ValueError('unknown_charge_test')
        if ct['pack_id']!=d['pack_id']:raise ValueError('pack_test_mismatch')
        cur=_current(db,d['pack_id'])
        if not cur:raise ValueError('composition_unregistered')
        if (cur['composition'],cur['calibration'])!=(d['composition'],d['calibration']):raise ValueError('composition_mismatch')
        moment=at(d['at'])
        if ct['ended_at']<cur['at']:raise ValueError('stale_test_identity')
        if moment<ct['ended_at']:raise ValueError('evidence_before_test')
        if d['temp_c']!=ct['temp_c']:raise ValueError('temperature_misaligned')
        if abs(d['coulomb_bms_ah']-ct['amp_hours'])>TOL_AH:raise ValueError('bms_trajectory_mismatch')
        status,detail='valid',None
        trace=d.get('soc_trace')
        if trace:
            try:implied=span_ah(trace,ct['start_soc'],ct['end_soc'])
            except ValueError:status,detail='quarantined','partial_cycle_gap'
            else:
                if abs(implied-ct['amp_hours'])>TOL_AH:status,detail='quarantined','sensor_drift'
        if status=='valid' and abs(d['coulomb_independent_ah']-d['coulomb_bms_ah'])>TOL_AH:status,detail='quarantined','coulomb_conflict'
        db.execute('INSERT INTO window_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['pack_id'],d['charge_test_id'],d['composition'],d['calibration'],d['coulomb_bms_ah'],d['coulomb_independent_ah'],
                    d['min_cell_mv'],d['rest_mv'],d['temp_c'],json.dumps(d.get('soc_trace') or []),json.dumps(d.get('curve') or []),moment,status,detail,sid))
        out={'id':d['id'],'status':status}
        if detail:out['reason']=detail
        return out

def full_cycle(path,file):
    """受控完整循环:全程容量与最弱单体。缺口循环、未放到截止电压或电量计冲突均不能认定全程。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'full_cycle_test',file)
        if repeated(db,'full_cycle_test',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM battery_pack WHERE id=?',(d['pack_id'],)).fetchone():raise ValueError('unknown_pack')
        cur=_current(db,d['pack_id'])
        if not cur:raise ValueError('composition_unregistered')
        if (cur['composition'],cur['calibration'])!=(d['composition'],d['calibration']):raise ValueError('composition_mismatch')
        started,ended=at(d['started_at']),at(d['ended_at'])
        if ended<=started:raise ValueError('invalid_cycle')
        if started<cur['at']:raise ValueError('stale_test_identity')
        if d['amp_hours']<=0 or d['weakest_cell_ah']<=0 or d['weakest_cell_ah']>d['amp_hours']:raise ValueError('invalid_full_cycle')
        status,detail='valid',None
        if abs(d['coulomb_bms_ah']-d['amp_hours'])>TOL_AH or abs(d['coulomb_independent_ah']-d['amp_hours'])>TOL_AH:status,detail='quarantined','coulomb_conflict'
        elif not 0<=d['temp_c']<=45:status,detail='quarantined','temperature_out_of_range'
        elif {d['start_soc'],d['end_soc']}!={0,100}:status,detail='partial','partial_cycle_gap'
        elif d['min_cell_mv']>CUTOFF_REACHED_MV:status,detail='partial','cutoff_voltage_unreached'
        db.execute('INSERT INTO full_cycle_test VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['pack_id'],d['composition'],d['calibration'],d['start_soc'],d['end_soc'],d['amp_hours'],d['weakest_cell_ah'],
                    d['min_cell_mv'],d['rest_mv'],d['temp_c'],d['coulomb_bms_ah'],d['coulomb_independent_ah'],started,ended,status,detail,sid))
        out={'id':d['id'],'status':status}
        if detail:out['reason']=detail
        return out

def order(path,file):
    """按已签约定创建等待中的装运订单。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'use_order',file)
        if repeated(db,'use_order',d['id'],sid):return {'id':d['id'],'replayed':True}
        agr=db.execute('SELECT * FROM use_agreement WHERE id=?',(d['agreement_id'],)).fetchone()
        if not agr:raise ValueError('unknown_agreement')
        if agr['pack_id']!=d['pack_id']:raise ValueError('agreement_pack_mismatch')
        moment=at(d['at'])
        if moment<agr['at']:raise ValueError('order_before_agreement')
        db.execute('INSERT INTO use_order VALUES(?,?,?,?,?,?,?)',(d['id'],d['pack_id'],d['agreement_id'],'pending',moment,None,sid))
        return {'id':d['id'],'status':'pending'}

def _valid_windows(db,pack_id,cur):
    return db.execute("""SELECT w.*,c.start_soc AS c_start,c.end_soc AS c_end,c.amp_hours AS c_ah
                         FROM window_evidence w JOIN charge_test c ON c.id=w.charge_test_id
                         WHERE w.pack_id=? AND w.composition=? AND w.calibration=? AND w.status='valid' ORDER BY w.id""",
                      (pack_id,cur['composition'],cur['calibration'])).fetchall()

def _quarantined_windows(db,pack_id,cur):
    return db.execute("""SELECT w.*,c.start_soc AS c_start,c.end_soc AS c_end FROM window_evidence w JOIN charge_test c ON c.id=w.charge_test_id
                         WHERE w.pack_id=? AND w.composition=? AND w.calibration=? AND w.status='quarantined' ORDER BY w.id""",
                      (pack_id,cur['composition'],cur['calibration'])).fetchall()

def _full_cycles(db,pack_id,cur,status):
    return db.execute('SELECT * FROM full_cycle_test WHERE pack_id=? AND composition=? AND calibration=? AND status=? ORDER BY ended_at,id',
                      (pack_id,cur['composition'],cur['calibration'],status)).fetchall()

def _window_ah(ev,ws,we):
    curve=json.loads(ev['curve'])
    if curve:
        try:return round(span_ah(curve,ws,we),3)
        except ValueError:pass
    if (ws,we)==(ev['c_start'],ev['c_end']):return ev['c_ah']
    raise ValueError('window_unproven')

def certify(path,file):
    """签发用途证书:限窗证只写已证窗口电量;全程证须有效完整循环且最弱单体达标。

    签发时间不得早于约定与所依据证据,升级不得倒签。
    """
    with connect(path) as db,db:
        sid,d,_=source(db,'use_certificate',file)
        if repeated(db,'use_certificate',d['id'],sid):return {'id':d['id'],'replayed':True}
        agr=db.execute('SELECT * FROM use_agreement WHERE id=?',(d['agreement_id'],)).fetchone()
        if not agr:raise ValueError('unknown_agreement')
        if agr['pack_id']!=d['pack_id']:raise ValueError('agreement_pack_mismatch')
        if db.execute('SELECT 1 FROM use_certificate WHERE agreement_id=?',(d['agreement_id'],)).fetchone():raise ValueError('certificate_exists')
        moment=at(d['at'])
        if moment<agr['at']:raise ValueError('backdated_certificate')
        cur=_current(db,d['pack_id'])
        if not cur:raise ValueError('composition_unregistered')
        if agr['kind']==WINDOWED:
            ev=None
            for w in _valid_windows(db,d['pack_id'],cur):
                if w['c_start']<=agr['window_start'] and w['c_end']>=agr['window_end']:ev=w;break
            if not ev:raise ValueError('window_unproven')
            if not MIN_CELL_SAFE_MV<=ev['min_cell_mv']<=MAX_CELL_MV:raise ValueError('min_cell_voltage_invalid')
            ah=_window_ah(ev,agr['window_start'],agr['window_end'])
            if moment<ev['at']:raise ValueError('backdated_certificate')
            scope,ws,we='window',agr['window_start'],agr['window_end']
            detail=f'window_only {ws}-{we} {ah}Ah'
        else:
            fulls=_full_cycles(db,d['pack_id'],cur,'valid')
            if not fulls:raise ValueError('full_range_unproven')
            fc=fulls[-1]
            if fc['weakest_cell_ah']<agr['min_full_ah']:raise ValueError('weakest_cell_below_requirement')
            if moment<fc['ended_at']:raise ValueError('backdated_certificate')
            ah=fc['weakest_cell_ah'];scope,ws,we='full_range',0,100
            detail=f'full_range weakest_cell {ah}Ah'
        db.execute('INSERT INTO use_certificate VALUES(?,?,?,?,?,?,?,?,?,?)',(d['id'],d['pack_id'],d['agreement_id'],scope,ws,we,ah,'issued',moment,sid))
        db.execute('INSERT INTO grade_event(pack_id,kind,ref_id,at,detail) VALUES(?,?,?,?,?)',(d['pack_id'],'certificate_issued',d['id'],moment,detail))
        return {'id':d['id'],'scope':scope,'certified_ah':ah,'status':'issued'}

def ship(path,file):
    """凭已签发证书装运等待中的订单;限窗交付带限制标签。"""
    with connect(path) as db,db:
        sid,d,seen=source(db,'shipment',file)
        if seen:return {'id':d['id'],'order_id':d['order_id'],'status':'shipped','replayed':True}
        o=db.execute('SELECT * FROM use_order WHERE id=?',(d['order_id'],)).fetchone()
        if not o:raise ValueError('unknown_order')
        if o['status']!='pending':raise ValueError('order_not_pending')
        cert=db.execute("SELECT * FROM use_certificate WHERE agreement_id=? AND status='issued'",(o['agreement_id'],)).fetchone()
        if not cert:raise ValueError('certificate_missing')
        moment=at(d['at'])
        if moment<cert['issued_at']:raise ValueError('backdated_shipment')
        if cert['scope']=='window':label=f"仅限 {cert['window_start']}%-{cert['window_end']}% SOC 窗口使用,已证 {cert['certified_ah']}Ah"
        else:label=f"全行程可用,最弱单体 {cert['certified_ah']}Ah"
        db.execute("UPDATE use_order SET status='shipped',closed_at=? WHERE id=?",(moment,o['id']))
        db.execute('INSERT INTO grade_event(pack_id,kind,ref_id,at,detail) VALUES(?,?,?,?,?)',(o['pack_id'],'order_shipped',o['id'],moment,label))
        return {'id':d['id'],'order_id':o['id'],'status':'shipped','label':label}

def _complement(covered):
    out=[];cursor=0
    for s,e in sorted(covered):
        if s>cursor:out.append([cursor,s])
        cursor=max(cursor,e)
    if cursor<100:out.append([cursor,100])
    return out

def _eligibility(agr,windows,quarantined,fulls,partials):
    base={'id':agr['id'],'kind':agr['kind'],'buyer':agr['buyer']}
    if agr['kind']==WINDOWED:
        ws,we=agr['window_start'],agr['window_end']
        for ev in windows:
            if ev['c_start']<=ws and ev['c_end']>=we:
                if not MIN_CELL_SAFE_MV<=ev['min_cell_mv']<=MAX_CELL_MV:
                    return {**base,'eligible':False,'reason':'min_cell_voltage_invalid','action':'reject_module'}
                return {**base,'eligible':True,'certifiable_ah':_window_ah(ev,ws,we),
                        'basis':['window_proven','coulomb_counters_consistent','min_cell_voltage_valid','temperature_aligned'],
                        'action':'certify_available'}
        if any(q['c_start']<=ws and q['c_end']>=we for q in quarantined):
            return {**base,'eligible':False,'reason':'coulomb_conflict_quarantined','action':'recalibrate_and_repeat'}
        return {**base,'eligible':False,'reason':'window_unproven','action':'request_window_test'}
    if fulls:
        fc=fulls[-1]
        if fc['weakest_cell_ah']>=agr['min_full_ah']:
            return {**base,'eligible':True,'certifiable_ah':fc['weakest_cell_ah'],
                    'basis':['controlled_full_cycle','weakest_cell_meets_requirement'],'action':'certify_available'}
        return {**base,'eligible':False,'reason':'weakest_cell_below_requirement','weakest_cell_ah':fc['weakest_cell_ah'],'action':'revoke_pending_orders'}
    out={**base,'eligible':False,'reason':'full_range_unproven','action':'suspend_and_request_full_cycle'}
    if partials:out['partial_tests']=[p['id'] for p in partials]
    return out

def _evaluate(db,pack_id):
    pack=db.execute('SELECT * FROM battery_pack WHERE id=?',(pack_id,)).fetchone()
    if not pack:raise ValueError('unknown_pack')
    cur=_current(db,pack_id)
    if not cur:raise ValueError('composition_unregistered')
    windows=_valid_windows(db,pack_id,cur)
    quarantined=_quarantined_windows(db,pack_id,cur)
    fulls=_full_cycles(db,pack_id,cur,'valid')
    partials=_full_cycles(db,pack_id,cur,'partial')
    proven=[{'window':[w['c_start'],w['c_end']],'amp_hours':w['c_ah'],'evidence':w['id'],'charge_test':w['charge_test_id']} for w in windows]
    proven_ah=round(sum(p['amp_hours'] for p in proven),3)
    res=db.execute('SELECT milliohms,temp_c,measured_at FROM resistance_test WHERE pack_id=? ORDER BY measured_at DESC LIMIT 1',(pack_id,)).fetchone()
    if fulls:
        fc=fulls[-1]
        full={'status':'proven','measured_ah':fc['amp_hours'],'weakest_cell_ah':fc['weakest_cell_ah'],
              'possible_ah':{'min':fc['amp_hours'],'max':fc['amp_hours']},'extrapolation':'not_applicable','reasons':[]}
    else:
        reasons=['untested_end_segments','cutoff_voltage_unscanned','static_resistance_cannot_bound_capacity']
        reasons+=[f"{q['detail']}_quarantined" if q['detail'] else 'evidence_quarantined' for q in quarantined]
        reasons+=sorted({p['detail'] for p in partials if p['detail']})
        naive=None
        if len(proven)==1:
            w=proven[0];naive=round(w['amp_hours']*100/(w['window'][1]-w['window'][0]),3)
        full={'status':'unproven','measured_ah':None,'weakest_cell_ah':None,
              'possible_ah':{'min':proven_ah,'max':float(pack['nominal_ah'])},
              'naive_extrapolation_ah':naive,'extrapolation':'rejected','reasons':reasons}
    covered=[tuple(p['window']) for p in proven]
    plan=[{'window':[s,e],'discriminates':False,'reason':'already_proven'} for s,e in covered]
    plan+=[{'window':u,'discriminates':True,'reason':'untested_segment_separates_candidates'} for u in _complement(covered)]
    plan+=[{'window':[q['c_start'],q['c_end']],'discriminates':True,'reason':'recalibrate_and_repeat','quarantined':q['id']} for q in quarantined]
    if not fulls:plan.append({'window':[0,100],'kind':'controlled_full_cycle','discriminates':True,'reason':'decisive_full_range'})
    agrs=[_eligibility(a,windows,quarantined,fulls,partials)
          for a in db.execute('SELECT * FROM use_agreement WHERE pack_id=? ORDER BY id',(pack_id,)).fetchall()]
    return {'pack_id':pack_id,'generation':cur['generation'],'composition':cur['composition'],'calibration':cur['calibration'],
            'proven_windows':proven,'proven_window_ah':proven_ah,'full_range':full,
            'resistance':dict(res) if res else None,
            'power':{'status':'unproven','note':'single_static_resistance_cannot_bound_untested_power'},
            'agreements':agrs,'supplementary_plan':plan}

def evaluate(path,pack_id):
    """定级查询:已证窗口、仍可能的全程容量集合、各约定资格与补测规划。"""
    with connect(path) as db:return _evaluate(db,pack_id)

def decide(path,pack_id,at_time):
    """按当前证据了结等待中的订单:全程已证且最弱单体不达标的移动电源订单撤销;限窗已交付不动。"""
    with connect(path) as db,db:
        moment=at(at_time)
        raw=json.dumps({'pack_id':pack_id,'at':moment},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        seen=db.execute('SELECT 1 FROM source WHERE sha=?',(digest,)).fetchone()
        if not seen:db.execute("INSERT INTO source(kind,sha,raw) VALUES('grade_decision',?,?)",(digest,raw))
        ev=_evaluate(db,pack_id)
        revoked=[]
        if not seen:
            for o in db.execute("SELECT * FROM use_order WHERE pack_id=? AND status='pending'",(pack_id,)).fetchall():
                agr=db.execute('SELECT * FROM use_agreement WHERE id=?',(o['agreement_id'],)).fetchone()
                if agr['kind']==MOBILE and ev['full_range']['status']=='proven' and ev['full_range']['weakest_cell_ah']<agr['min_full_ah']:
                    db.execute("UPDATE use_order SET status='revoked',closed_at=? WHERE id=?",(moment,o['id']))
                    db.execute('INSERT INTO grade_event(pack_id,kind,ref_id,at,detail) VALUES(?,?,?,?,?)',(pack_id,'order_revoked',o['id'],moment,'weakest_cell_below_requirement'))
                    revoked.append(o['id'])
        orders={s:[x['id'] for x in db.execute('SELECT id FROM use_order WHERE pack_id=? AND status=? ORDER BY id',(pack_id,s))] for s in ('pending','shipped','revoked')}
        return {'pack_id':pack_id,'at':moment,'revoked_now':revoked,'orders':orders,
                'full_range':ev['full_range'],'agreements':ev['agreements'],'replayed':bool(seen)}

def certificates(path,pack_id=None):
    """用途证书、订单与签发/装运/撤证/升级时间线。"""
    with connect(path) as db:
        where=' WHERE pack_id=?' if pack_id else ''
        args=(pack_id,) if pack_id else ()
        return {'certificates':[dict(x) for x in db.execute('SELECT * FROM use_certificate'+where+' ORDER BY issued_at,id',args)],
                'orders':[dict(x) for x in db.execute('SELECT * FROM use_order'+where+' ORDER BY ordered_at,id',args)],
                'timeline':[dict(x) for x in db.execute('SELECT * FROM grade_event'+where+' ORDER BY id',args)]}
