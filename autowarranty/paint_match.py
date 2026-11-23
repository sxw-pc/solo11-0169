"""跨照明条件的配方求解、库存锁定与返工冲销。

测量坐标与校准:
  BANDS 为规范波段轴(nm); 旧四波段分光仪读数挂在 LEGACY_BANDS 坐标下,
  仅对默认几何 DEFAULT_GEOMETRY 有效; 每条补测(band_measurement)携带
  geometry(测量角度)与 calibration(校准版本), 坐标不符的测量不参与判定。
涂层模型(不把颜料反射率线性相加):
  涂层反射率 = 清漆透过 CLEAR_T × Kubelka-Munk(K_mix, S_mix, 膜厚 THICKNESS, 底漆 PRIMER_R)
  其中 K_mix/S_mix 按配方浓度在 K/S 空间线性混合, 反射率本身绝不直接相加。
照明判定:
  D65 走门店相机响应 CAMERA(590/750 陷波), LAMP 走窄带路灯功率 × 人眼 VISUAL;
  未测波段以区间 [0,1] 表示"有限波段下仍可能的全光谱族", 误差按区间传播:
  区间上界 ≤ MATCH_TOL 判 proven, 下界 > MATCH_TOL 判 mismatch, 否则 unmeasured。
"""
import json,math
from .store import connect,source,repeated,at

BANDS=[450,550,590,650,750]
LEGACY_BANDS=[450,550,650,750]
DEFAULT_GEOMETRY='45/0'
SHOP_CALIBRATION='CAL1'
THICKNESS=45.0
PRIMER_R={450:0.18,550:0.22,590:0.24,650:0.26,750:0.30}
CLEAR_T={450:0.90,550:0.93,590:0.94,650:0.94,750:0.95}
CAMERA={450:1.0,550:1.0,590:0.0,650:1.0,750:0.0}
VISUAL={450:0.24,550:0.85,590:1.0,650:0.55,750:0.08}
ILLUMINANTS={'D65':{'power':{450:0.94,550:1.0,590:0.90,650:1.03,750:0.95},'response':'camera'},
             'LAMP':{'power':{450:0.0,550:0.02,590:1.0,650:0.02,750:0.0},'response':'visual'}}
MATCH_TOL=0.02
GRID=4
WEIGHTS={e:{b:d['power'][b]*(CAMERA[b] if d['response']=='camera' else VISUAL[b]) for b in BANDS}
         for e,d in ILLUMINANTS.items()}
SUBJECT_TABLE={'paint_sample':('paint_sample','measured_at'),'spray_panel':('spray_panel','sprayed_at'),
               'pigment_lot':('pigment_lot',None),'spray_event':('spray_event','at')}

# ---------- 涂层物理 ----------
def _ks(r):return (1.0-r)**2/(2.0*r)
def _km_r(k,s,primer):
    a=1.0+k/s;b=math.sqrt(a*a-1.0);coth=1.0/math.tanh(b*s*THICKNESS)
    return (1.0-primer*(a-b*coth))/(a-primer+b*coth)
def _predict(concs,lots,band):
    """配方(浓度)在 band 的涂层反射率; 任一颜料缺该波段数据则不可预测."""
    if any(lots[lot]['fam'][band] is None for lot in concs):return None
    k=sum(c*_ks(lots[lot]['fam'][band][0])*lots[lot]['scatter'] for lot,c in concs.items())
    s=sum(c*lots[lot]['scatter'] for lot,c in concs.items())
    return CLEAR_T[band]*_km_r(k,s,PRIMER_R[band])

# ---------- 光谱族(有限波段 → 区间族) ----------
def _measurements(db,kind,sid,geometry):
    rows=db.execute('SELECT * FROM band_measurement WHERE subject_kind=? AND subject_id=? AND geometry=? AND calibration=? ORDER BY measured_at,id',
                    (kind,sid,geometry,SHOP_CALIBRATION)).fetchall()
    out={}
    for r in rows:
        for b,v in zip(json.loads(r['bands_json']),json.loads(r['values_json'])):out[b]=v
    return out
def _legacy(spectrum_json,geometry):
    if geometry!=DEFAULT_GEOMETRY:return {}
    return dict(zip(LEGACY_BANDS,json.loads(spectrum_json)))
def _sample_family(db,sample,geometry):
    fam={b:None for b in BANDS}
    for b,v in _legacy(sample['spectrum_json'],geometry).items():fam[b]=(v,v)
    for b,v in _measurements(db,'paint_sample',sample['id'],geometry).items():fam[b]=(v,v)
    return fam
def _lot_family(db,lot,geometry):
    fam={b:None for b in BANDS}
    for b,v in _legacy(lot['spectrum_json'],geometry).items():fam[b]=(v,v)
    for b,v in _measurements(db,'pigment_lot',lot['id'],geometry).items():fam[b]=(v,v)
    return fam
def _recipe_family(db,recipe_ml,subject_kind,subject_id,lots,geometry):
    """试喷板/实喷事件的光谱族: 配方模型预测为底, 直接补测覆盖."""
    concs=_concs(recipe_ml);fam={b:None for b in BANDS}
    if all(lot in lots for lot in concs):
        for b in BANDS:
            r=_predict(concs,lots,b)
            if r is not None:fam[b]=(r,r)
    for b,v in _measurements(db,subject_kind,subject_id,geometry).items():fam[b]=(v,v)
    return fam
def _concs(recipe_ml):
    total=sum(recipe_ml.values())
    if total<=0:raise ValueError('invalid_recipe')
    return {lot:ml/total for lot,ml in recipe_ml.items()}
def _scale(recipe_ml,needed_ml):
    total=sum(recipe_ml.values())
    if total<=0:raise ValueError('invalid_recipe')
    return {lot:ml/total*needed_ml for lot,ml in recipe_ml.items()}

# ---------- 误差与判定 ----------
def _err_interval(illum,tf,cf):
    lo=hi=den=0.0
    for b,w in WEIGHTS[illum].items():
        if w<=0:continue
        den+=w
        t=tf.get(b) or (0.0,1.0);c=cf.get(b) or (0.0,1.0)
        dmin=0.0 if t[0]<=c[1] and c[0]<=t[1] else min(abs(t[0]-c[1]),abs(c[0]-t[1]))
        dmax=max(abs(t[0]-c[1]),abs(t[1]-c[0]))
        lo+=w*dmin*dmin;hi+=w*dmax*dmax
    return (math.sqrt(lo/den),math.sqrt(hi/den))
def _err_point(illum,target_pt,spec_pt):
    num=den=0.0
    for b,w in WEIGHTS[illum].items():
        if w<=0:continue
        den+=w;num+=w*(target_pt[b]-spec_pt[b])**2
    return math.sqrt(num/den)
def _verdict(err):
    lo,hi=err
    if hi<=MATCH_TOL:return 'proven'
    if lo>MATCH_TOL:return 'mismatch'
    return 'unmeasured'

# ---------- 数据装载 ----------
def _sums(db,lot):
    return {r['kind']:r['s'] for r in db.execute('SELECT kind,SUM(ml) s FROM paint_ledger WHERE lot_id=? GROUP BY kind',(lot,))}
def _physical(db,lot,available):
    return available-_sums(db,lot).get('draw',0.0)
def _free(db,lot,available):
    s=_sums(db,lot)
    return available-s.get('reserve',0.0)+s.get('release',0.0)
def _load_lots(db,geometry):
    lots={}
    for row in db.execute('SELECT * FROM pigment_lot ORDER BY id'):
        lots[row['id']]={'scatter':row['scatter'],'cost':row['cost_per_ml'],'pearl':row['pearl'],
                         'fam':_lot_family(db,row,geometry),'physical':_physical(db,row['id'],row['available_ml'])}
    return lots
def _required(db,o):
    req=set(json.loads(o['illuminants_json']))
    for rw in db.execute('SELECT escalated_json FROM paint_rework WHERE order_id=?',(o['id'],)):
        req|=set(json.loads(rw['escalated_json']))
    return sorted(req)
def _context(db,o):
    sample=db.execute('SELECT * FROM paint_sample WHERE vin=? AND panel=? ORDER BY measured_at DESC,id DESC LIMIT 1',
                      (o['vin'],o['panel'])).fetchone()
    if not sample:raise ValueError('paint_sample_missing')
    lots=_load_lots(db,o['geometry'])
    tf=_sample_family(db,sample,o['geometry'])
    panels=db.execute('SELECT * FROM spray_panel WHERE vin=? ORDER BY sprayed_at,id',(o['vin'],)).fetchall()
    fams={p['id']:_recipe_family(db,json.loads(p['recipe_json']),'spray_panel',p['id'],lots,o['geometry']) for p in panels}
    return sample,tf,lots,panels,fams
def _panel_per(o,sample,tf,p,fam,illuminants):
    """试喷板逐光源判定: 同光源拍摄的 rgb 原件是直接证据, 其余走光谱族."""
    per={}
    for e in illuminants:
        if (e==p['illuminant'] and o['geometry']==DEFAULT_GEOMETRY):
            match=json.loads(p['rgb_json'])==json.loads(sample['rgb_json'])
            per[e]=('proven' if match else 'mismatch',(0.0,0.0) if match else (1.0,1.0),'recorded_rgb')
        else:
            err=_err_interval(e,tf,fam)
            per[e]=(_verdict(err),err,'spectral')
    return per
def _cost(recipe_ml,lots):
    return sum(ml*(lots[lot]['cost'] if lot in lots else 1.0) for lot,ml in recipe_ml.items())

# ---------- 配方求解 ----------
def _compositions(n,k):
    if k==1:
        yield (n,);return
    for i in range(n+1):
        for rest in _compositions(n-i,k-1):yield (i,)+rest
def _solve(tf,lots,required,needed_bands,needed_ml):
    """在目标光源下求浓度网格上的最优配方; 数据不足返回 incomplete."""
    missing_target=[b for b in needed_bands if tf[b] is None]
    if missing_target:return {'status':'incomplete','missing_target':missing_target}
    target_pt={b:tf[b][0] for b in needed_bands}
    missing_lots={lid:[b for b in needed_bands if L['fam'][b] is None]
                  for lid,L in lots.items() if L['physical']>0}
    missing_lots={k:v for k,v in missing_lots.items() if v}
    usable=[lid for lid,L in lots.items() if L['physical']>0 and lid not in missing_lots]
    if not usable:return {'status':'incomplete','missing_lots':missing_lots}
    best=None
    for combo in _compositions(GRID,len(usable)):
        concs={lid:n/GRID for lid,n in zip(usable,combo) if n}
        spec={b:_predict(concs,lots,b) for b in needed_bands}
        errs={e:_err_point(e,target_pt,spec) for e in required}
        total=max(errs.values())
        cost=needed_ml*sum(c*lots[l]['cost'] for l,c in concs.items())
        key=(round(total,9),round(cost,9),sorted(concs.items()))
        if best is None or key<best[0]:best=(key,concs,errs)
    if best[0][0]>MATCH_TOL:return {'status':'infeasible','best_err':best[0][0]}
    return {'status':'ok','recipe':{l:c*needed_ml for l,c in best[1].items()},'err':best[2],
            'missing_lots':missing_lots}

# ---------- 库存 ----------
def _shortfall(db,recipe):
    lack={}
    for lot,ml in recipe.items():
        row=db.execute('SELECT available_ml FROM pigment_lot WHERE id=?',(lot,)).fetchone()
        if not row:raise ValueError('unknown_pigment_lot')
        free=_free(db,lot,row['available_ml'])
        if ml>free+1e-9:lack[lot]={'need':ml,'free':free}
    return lack
def _release(db,mix_id,moment,sid):
    rows=db.execute("""SELECT lot_id,SUM(CASE WHEN kind='reserve' THEN ml WHEN kind IN('release','draw') THEN -ml ELSE 0 END) active
                       FROM paint_ledger WHERE ref=? GROUP BY lot_id HAVING active>0""",(mix_id,)).fetchall()
    for r in rows:
        db.execute('INSERT INTO paint_ledger(lot_id,kind,ml,ref,at,source_id) VALUES(?,?,?,?,?,?)',
                   (r['lot_id'],'release',r['active'],mix_id,moment,sid))

# ---------- 工单 ----------
def _measurement_plan(o,sample,panels,fams,lots,needed_bands,tf):
    out=[]
    missing=[b for b in needed_bands if tf[b] is None]
    if missing:out.append({'subject_kind':'paint_sample','subject_id':sample['id'],'bands':missing,'geometry':o['geometry']})
    for p in panels:
        miss=[b for b in needed_bands if fams[p['id']][b] is None]
        if miss:out.append({'subject_kind':'spray_panel','subject_id':p['id'],'bands':miss,'geometry':o['geometry']})
    for lid in sorted(lots):
        L=lots[lid]
        if L['physical']<=0:continue
        miss=[b for b in needed_bands if L['fam'][b] is None]
        if miss:out.append({'subject_kind':'pigment_lot','subject_id':lid,'bands':miss,'geometry':o['geometry']})
    return out

# ---------- 事件入口 ----------
def order(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'paint_order',file)
        if repeated(db,'paint_order',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        illum=d['illuminants']
        if not illum or any(e not in ILLUMINANTS for e in illum):raise ValueError('unknown_illuminant')
        if d['needed_ml']<=0:raise ValueError('invalid_quantity')
        moment=at(d['at'])
        db.execute('INSERT INTO paint_order VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['vin'],d['panel'],d.get('kind','standard'),json.dumps(sorted(illum)),
                    d['needed_ml'],d.get('geometry',DEFAULT_GEOMETRY),moment,'open',sid))
        return {'id':d['id'],'status':'open'}

def measure(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'band_measurement',file)
        if repeated(db,'band_measurement',d['id'],sid):return {'id':d['id'],'replayed':True}
        kind=d['subject_kind']
        if kind not in SUBJECT_TABLE:raise ValueError('unknown_subject')
        table,tscol=SUBJECT_TABLE[kind]
        subj=db.execute(f'SELECT * FROM {table} WHERE id=?',(d['subject_id'],)).fetchone()
        if not subj:raise ValueError('unknown_subject')
        bands,values=d['bands'],d['values']
        if not bands or len(bands)!=len(values):raise ValueError('band_value_mismatch')
        if len(set(bands))!=len(bands):raise ValueError('duplicate_band')
        if any(b not in BANDS for b in bands):raise ValueError('unknown_band')
        if any(not 0.0<=v<=1.0 for v in values):raise ValueError('invalid_reflectance')
        moment=at(d['measured_at'])
        if tscol and subj[tscol] and moment<subj[tscol]:raise ValueError('measurement_before_subject')
        db.execute('INSERT INTO band_measurement VALUES(?,?,?,?,?,?,?,?,?)',
                   (d['id'],kind,d['subject_id'],json.dumps(bands),json.dumps(values),
                    d.get('geometry',DEFAULT_GEOMETRY),d.get('calibration',SHOP_CALIBRATION),moment,sid))
        return {'id':d['id'],'measured':bands}

def plan(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'paint_plan',file)
        if repeated(db,'mix_order',d['id'],sid):return {'id':d['id'],'replayed':True}
        o=db.execute('SELECT * FROM paint_order WHERE id=?',(d['order_id'],)).fetchone()
        if not o:raise ValueError('unknown_order')
        if o['status'] in ('drawn','sprayed'):raise ValueError('order_not_plannable')
        moment=at(d['at'])
        if moment<o['at']:raise ValueError('plan_before_order')
        required=_required(db,o)
        sample,tf,lots,panels,fams=_context(db,o)
        needed_bands=[b for b in BANDS if any(WEIGHTS[e][b]>0 for e in required)]
        # 1) 已试喷候选: 逐光源判定, 全部 proven 才可执行
        evals=[]
        for p in panels:
            per=_panel_per(o,sample,tf,p,fams[p['id']],required)
            recipe=_scale(json.loads(p['recipe_json']),o['needed_ml'])
            evals.append({'panel':p['id'],'recipe':recipe,'per':per,'cost':_cost(recipe,lots),
                          'usable':all(v[0]=='proven' for v in per.values())})
        proven=sorted((e for e in evals if e['usable']),key=lambda e:(e['cost'],e['panel']))
        # 2) 模型求解(无已证配方时)
        solved=None if proven else _solve(tf,lots,required,needed_bands,o['needed_ml'])
        # 3) 路由
        wos=[]
        if proven:
            status,recipe,basis='executable',proven[0]['recipe'],'measured_panel:'+proven[0]['panel']
        elif solved['status']=='ok':
            status,recipe,basis='pending_spray_test',solved['recipe'],'model'
            wos=[('spray_test',{'recipe':solved['recipe'],'verify':{'illuminants':required,'bands':needed_bands,'geometry':o['geometry']}})]
        elif solved['status']=='infeasible':
            status,recipe,basis='no_feasible_recipe',None,None
        else:
            status='pending_measurement';basis='provisional'
            day=[e for e in evals if e['per'].get('D65',('','',))[0]=='proven']
            day.sort(key=lambda e:(e['cost'],e['panel']))
            if day:recipe,basis=day[0]['recipe'],'provisional_panel:'+day[0]['panel']
            else:
                day_solved=_solve(tf,lots,['D65'],[b for b in BANDS if WEIGHTS['D65'][b]>0],o['needed_ml'])
                recipe=day_solved['recipe'] if day_solved['status']=='ok' else None
            wos=[('measure',{'measurements':_measurement_plan(o,sample,panels,fams,lots,needed_bands,tf)})]
        # 4) 旧调漆单作废, 释放其占用
        for old in db.execute("SELECT id FROM mix_order WHERE order_id=? AND status NOT IN('superseded','overturned')",(o['id'],)).fetchall():
            _release(db,old['id'],moment,sid)
            db.execute("UPDATE mix_order SET status='superseded' WHERE id=?",(old['id'],))
        db.execute("UPDATE work_order SET status='superseded' WHERE order_id=? AND status='open'",(o['id'],))
        # 5) 库存锁定: 同一毫升不得进入两张调漆单
        reserved=[]
        if recipe and status!='no_feasible_recipe':
            if _shortfall(db,recipe):status='awaiting_stock'
            else:
                for lot,ml in sorted(recipe.items()):
                    db.execute('INSERT INTO paint_ledger(lot_id,kind,ml,ref,at,source_id) VALUES(?,?,?,?,?,?)',
                               (lot,'reserve',ml,d['id'],moment,sid))
                    reserved.append({'lot':lot,'ml':ml})
        db.execute('INSERT INTO mix_order VALUES(?,?,?,?,?,?,?,?)',
                   (d['id'],o['id'],json.dumps(recipe,sort_keys=True) if recipe else None,basis,json.dumps(required),status,moment,sid))
        wo_rows=[]
        for i,(kind,detail) in enumerate(wos):
            wid=f'WO-{d["id"]}-{i+1}'
            db.execute('INSERT INTO work_order VALUES(?,?,?,?,?,?,?)',(wid,o['id'],d['id'],kind,json.dumps(detail,sort_keys=True),'open',sid))
            wo_rows.append({'id':wid,'kind':kind,**detail})
        db.execute('UPDATE paint_order SET status=? WHERE id=?',(status,o['id']))
        return {'id':d['id'],'mix_order':d['id'],'status':status,'recipe':recipe,'reserved':reserved,
                'work_orders':wo_rows,'required':required}

def draw(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'paint_draw',file)
        if repeated(db,'paint_draw',d['id'],sid):return {'id':d['id'],'replayed':True}
        m=db.execute('SELECT * FROM mix_order WHERE id=?',(d['mix_order_id'],)).fetchone()
        if not m:raise ValueError('unknown_mix_order')
        if m['status']!='executable':raise ValueError('mix_not_executable')
        moment=at(d['at'])
        if moment<m['at']:raise ValueError('draw_before_plan')
        recipe=json.loads(m['recipe_json']);drawn=[]
        for lot,ml in sorted(recipe.items()):
            active=db.execute("""SELECT SUM(CASE WHEN kind='reserve' THEN ml WHEN kind IN('release','draw') THEN -ml ELSE 0 END) a
                                 FROM paint_ledger WHERE ref=? AND lot_id=?""",(m['id'],lot)).fetchone()['a'] or 0.0
            if ml>active+1e-9:raise ValueError('reservation_missing')
            avail=db.execute('SELECT available_ml FROM pigment_lot WHERE id=?',(lot,)).fetchone()['available_ml']
            if ml>_physical(db,lot,avail)+1e-9:raise ValueError('stock_missing')
            db.execute('INSERT INTO paint_ledger(lot_id,kind,ml,ref,at,source_id) VALUES(?,?,?,?,?,?)',
                       (lot,'draw',ml,m['id'],moment,sid))
            drawn.append({'lot':lot,'ml':ml})
        db.execute('INSERT INTO paint_draw VALUES(?,?,?,?)',(d['id'],m['id'],moment,sid))
        db.execute("UPDATE mix_order SET status='drawn' WHERE id=?",(m['id'],))
        db.execute("UPDATE paint_order SET status='drawn' WHERE id=?",(m['order_id'],))
        return {'id':d['id'],'mix_order':m['id'],'drawn':drawn}

def spray(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'spray_event',file)
        if repeated(db,'spray_event',d['id'],sid):return {'id':d['id'],'replayed':True}
        m=db.execute('SELECT * FROM mix_order WHERE id=?',(d['mix_order_id'],)).fetchone()
        if not m:raise ValueError('unknown_mix_order')
        if m['status']!='drawn':raise ValueError('mix_not_drawn')
        o=db.execute('SELECT * FROM paint_order WHERE id=?',(m['order_id'],)).fetchone()
        moment=at(d['at'])
        dr=db.execute('SELECT at FROM paint_draw WHERE mix_order_id=?',(m['id'],)).fetchone()
        if moment<dr['at']:raise ValueError('spray_before_draw')
        db.execute('INSERT INTO spray_event VALUES(?,?,?,?,?,?,?)',(d['id'],m['id'],o['vin'],o['panel'],d['booth'],moment,sid))
        db.execute("UPDATE mix_order SET status='sprayed' WHERE id=?",(m['id'],))
        db.execute("UPDATE paint_order SET status='sprayed' WHERE id=?",(o['id'],))
        return {'id':d['id'],'status':'sprayed','booth':d['booth']}

def rework(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'paint_rework',file)
        if repeated(db,'paint_rework',d['id'],sid):return {'id':d['id'],'replayed':True}
        o=db.execute('SELECT * FROM paint_order WHERE id=?',(d['order_id'],)).fetchone()
        if not o:raise ValueError('unknown_order')
        if o['status']!='sprayed':raise ValueError('order_not_sprayed')
        meas=db.execute('SELECT * FROM band_measurement WHERE id=?',(d['measurement_id'],)).fetchone()
        if not meas:raise ValueError('unknown_measurement')
        moment=at(d['at'])
        if moment<meas['measured_at']:raise ValueError('rework_before_measurement')
        m=db.execute("SELECT * FROM mix_order WHERE order_id=? AND status='sprayed' ORDER BY at DESC,id DESC LIMIT 1",(o['id'],)).fetchone()
        # 触发测量的对象必须属于本订单: 实喷事件或本车原件
        kind,sid_=meas['subject_kind'],meas['subject_id']
        if kind=='spray_event':
            row=db.execute('SELECT m.order_id o FROM spray_event s JOIN mix_order m ON s.mix_order_id=m.id WHERE s.id=?',(sid_,)).fetchone()
            related=row and row['o']==o['id']
        elif kind in('paint_sample','spray_panel'):
            row=db.execute(f"SELECT vin FROM {SUBJECT_TABLE[kind][0]} WHERE id=?",(sid_,)).fetchone()
            related=row and row['vin']==o['vin']
        else:related=False
        if not related:raise ValueError('measurement_subject_mismatch')
        sample=db.execute('SELECT * FROM paint_sample WHERE vin=? AND panel=? ORDER BY measured_at DESC,id DESC LIMIT 1',
                          (o['vin'],o['panel'])).fetchone()
        tf=_sample_family(db,sample,o['geometry'])
        # 只凭实测证据判定: 测量对象身上直接测到的波段, 不用模型倒填
        cf={b:None for b in BANDS}
        for b,v in _measurements(db,meas['subject_kind'],meas['subject_id'],o['geometry']).items():cf[b]=(v,v)
        escalated=[e for e in sorted(ILLUMINANTS) if _err_interval(e,tf,cf)[0]>MATCH_TOL]
        if not escalated:raise ValueError('rework_unjustified')
        # 保留领料/实喷事实, 仅冲销库存: 释放未领预约, 已领未核销记 writeoff
        _release(db,m['id'],moment,sid)
        written=[]
        for r in db.execute("""SELECT lot_id,SUM(CASE WHEN kind='draw' THEN ml WHEN kind='writeoff' THEN -ml ELSE 0 END) open
                               FROM paint_ledger WHERE ref=? GROUP BY lot_id HAVING open>0""",(m['id'],)).fetchall():
            db.execute('INSERT INTO paint_ledger(lot_id,kind,ml,ref,at,source_id) VALUES(?,?,?,?,?,?)',
                       (r['lot_id'],'writeoff',r['open'],m['id'],moment,sid))
            written.append({'lot':r['lot_id'],'ml':r['open']})
        db.execute("UPDATE mix_order SET status='overturned' WHERE id=?",(m['id'],))
        db.execute('INSERT INTO paint_rework VALUES(?,?,?,?,?,?,?)',(d['id'],o['id'],m['id'],meas['id'],json.dumps(escalated),moment,sid))
        db.execute("UPDATE paint_order SET status='rework' WHERE id=?",(o['id'],))
        return {'id':d['id'],'mix_order':m['id'],'escalated':escalated,'written_off':written,'status':'rework'}

# ---------- 查询 ----------
def stock(path):
    with connect(path) as db:
        out={}
        for lot in db.execute('SELECT * FROM pigment_lot ORDER BY id'):
            s=_sums(db,lot['id'])
            reserved=s.get('reserve',0.0)-s.get('release',0.0)-s.get('draw',0.0)
            drawn=s.get('draw',0.0);written=s.get('writeoff',0.0)
            physical=lot['available_ml']-drawn
            out[lot['id']]={'available_ml':lot['available_ml'],'reserved_ml':round(reserved,9),
                            'drawn_ml':drawn,'written_off_ml':written,'in_use_ml':round(drawn-written,9),
                            'physical_ml':physical,'free_ml':round(physical-reserved,9)}
        return out

def order_status(path,order_id):
    with connect(path) as db:
        o=db.execute('SELECT * FROM paint_order WHERE id=?',(order_id,)).fetchone()
        if not o:raise ValueError('unknown_order')
        mixes=[dict(x) for x in db.execute('SELECT * FROM mix_order WHERE order_id=? ORDER BY at,id',(order_id,))]
        wos=[dict(x) for x in db.execute('SELECT * FROM work_order WHERE order_id=? ORDER BY id',(order_id,))]
        rws=[dict(x) for x in db.execute('SELECT * FROM paint_rework WHERE order_id=? ORDER BY at',(order_id,))]
        ledger=[dict(x) for x in db.execute('SELECT * FROM paint_ledger WHERE ref IN(SELECT id FROM mix_order WHERE order_id=?) ORDER BY id',(order_id,))]
        return {'order':dict(o),'required':_required(db,o),'mix_orders':mixes,'work_orders':wos,'reworks':rws,'ledger':ledger}

def candidates(path,order_id):
    """各候选配方在已证光源下的可用性, 以及区分候选所需的最少波段/角度."""
    with connect(path) as db:
        o=db.execute('SELECT * FROM paint_order WHERE id=?',(order_id,)).fetchone()
        if not o:raise ValueError('unknown_order')
        required=_required(db,o)
        sample,tf,lots,panels,fams=_context(db,o)
        rows=[];specs=[]
        for p in panels:
            per=_panel_per(o,sample,tf,p,fams[p['id']],sorted(ILLUMINANTS))
            usable_for=[e for e in sorted(ILLUMINANTS) if per[e][0]=='proven']
            if all(per[e][0]=='proven' for e in required):verdict='usable'
            elif any(per[e][0]=='mismatch' for e in required):verdict='rejected'
            else:verdict='needs_measurement'
            rows.append({'panel':p['id'],'recipe':_scale(json.loads(p['recipe_json']),o['needed_ml']),
                         'per_illuminant':{e:v[0] for e,v in per.items()},'usable_for':usable_for,'verdict':verdict})
            specs.append(fams[p['id']])
        needed_bands=[b for b in BANDS if any(WEIGHTS[e][b]>0 for e in required)]
        solved=_solve(tf,lots,required,needed_bands,o['needed_ml'])
        if solved['status']=='ok':
            concs=_concs(solved['recipe'])
            specs.append({b:(r,r) if (r:=_predict(concs,lots,b)) is not None else None for b in BANDS})
        return {'order_id':order_id,'required':required,'panels':rows,'solver':solved,
                'distinguishing':{'bands':_distinguishing(specs,required,tf),'geometry':o['geometry']}}

def _distinguishing(specs,required,tf):
    """最少补测波段: 目标或候选在相关波段未知 → 必须补测; 否则按最大分离度贪心."""
    bands=[b for b in BANDS if any(WEIGHTS[e][b]>0 for e in required)]
    unknown=[b for b in bands if tf.get(b) is None or any(s.get(b) is None for s in specs)]
    if unknown:return unknown
    pairs=[(i,j) for i in range(len(specs)) for j in range(i+1,len(specs))]
    remaining=set(pairs);chosen=[]
    while remaining:
        best,cover=None,set()
        for b in sorted(bands,key=lambda b:-max(abs(specs[i][b][0]-specs[j][b][0]) for i,j in remaining)):
            c={p for p in remaining if abs(specs[p[0]][b][0]-specs[p[1]][b][0])>MATCH_TOL}
            if len(c)>len(cover):best,cover=b,c
        if best is None:break
        chosen.append(best);remaining-=cover
    return chosen
