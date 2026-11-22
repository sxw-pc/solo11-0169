import json,hashlib
from .store import connect,source,repeated,at

ROUTES=('payoff','assumption')
DOC_KINDS=('lien_release','title_revoke','title_correct','transfer_receipt')

def archive(path,file):
    """厂家生产档案：把车架号/发动机号锚定到首次上牌产权，构成实车身份的根。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'factory_archive',file)
        if repeated(db,'factory_archive',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM title_record WHERE id=?',(d['title_id'],)).fetchone():raise ValueError('unknown_title')
        db.execute('INSERT INTO factory_archive VALUES(?,?,?,?,?,?)',(d['id'],d['frame_serial'],d['engine_serial'],d['title_id'],at(d['issued_at']),sid))
        return {'id':d['id'],'status':'recorded'}

def registry(path,file):
    """登记机关文书：留置解除回执、吊销/更正、外部转移签收。只采证据，不改写既有交割。"""
    with connect(path) as db,db:
        sid,d,_=source(db,'registry_doc',file)
        if repeated(db,'registry_doc',d['id'],sid):return {'id':d['id'],'replayed':True}
        if d['kind'] not in DOC_KINDS:raise ValueError('unknown_registry_doc')
        if not db.execute('SELECT 1 FROM title_record WHERE id=?',(d['title_id'],)).fetchone():raise ValueError('unknown_title')
        if d['kind']=='lien_release':
            if not db.execute('SELECT 1 FROM lien_record WHERE id=? AND title_id=?',(d['lien_id'],d['title_id'])).fetchone():raise ValueError('unknown_lien')
        if d['kind']=='transfer_receipt' and (not d.get('delivery_id') or not d.get('receipt_no')):raise ValueError('receipt_incomplete')
        db.execute('INSERT INTO registry_doc VALUES(?,?,?,?,?,?,?,?,?)',(d['id'],d['kind'],d['jurisdiction'],d['title_id'],d.get('lien_id'),d.get('delivery_id'),d.get('receipt_no'),at(d['at']),sid))
        return {'id':d['id'],'status':'recorded'}

def _graph(db,vin):
    """实体—产权—债权图：先由车架/发动机/厂家档案辨别实体，再判有效产权与留置；同号异车只留冲突候选，绝不按名字合并。"""
    v=db.execute('SELECT * FROM vehicle WHERE vin=?',(vin,)).fetchone()
    if not v:raise ValueError('unknown_vehicle')
    identity=None;conflicts=[]
    for s in db.execute('SELECT * FROM frame_scan WHERE vin=? ORDER BY scanned_at,id',(vin,)):
        key={'frame_serial':s['frame_serial'],'engine_serial':s['engine_serial']}
        if identity is None:identity=key
        elif identity!=key:conflicts.append({'kind':'frame_scan_mismatch','scan_id':s['id'],**key,'reason':'车架/发动机号前后不一致，疑似车架更换或 OCR 误读，保留为独立身份候选'})
    archives=[]
    if identity:archives=[dict(x) for x in db.execute('SELECT * FROM factory_archive WHERE frame_serial=? AND engine_serial=? ORDER BY issued_at,id',(identity['frame_serial'],identity['engine_serial']))]
    bound=[]
    for a in archives:
        t=db.execute('SELECT * FROM title_record WHERE id=?',(a['title_id'],)).fetchone()
        if not t:continue
        t=dict(t);t['revoked']=bool(db.execute("SELECT 1 FROM registry_doc WHERE kind='title_revoke' AND title_id=?",(t['id'],)).fetchone())
        if t['vin']!=vin:conflicts.append({'kind':'vin_mismatch','title_id':t['id'],'registry_vin':t['vin'],'reason':'厂家链锚定的产权 VIN 拼写与实车不同，疑似 OCR 误读或跨地迁入，仍以车架为锚'})
        bound.append(t)
    bound_ids={t['id'] for t in bound}
    for t in db.execute('SELECT * FROM title_record WHERE vin=? ORDER BY registered_at,id',(vin,)):
        if t['id'] not in bound_ids:conflicts.append({'kind':'shared_vin_unbound','title_id':t['id'],'jurisdiction':t['jurisdiction'],'registered_at':t['registered_at'],'reason':'VIN/车主拼写相同但无厂家首次上牌链连到实扫车架/发动机，疑似套牌登记，不合并'})
    claims=[]
    for t in bound:
        for l in db.execute('SELECT * FROM lien_record WHERE title_id=? ORDER BY id',(t['id'],)):
            rel=db.execute("SELECT id FROM registry_doc WHERE kind='lien_release' AND lien_id=? ORDER BY at LIMIT 1",(l['id'],)).fetchone()
            claims.append({'lien_id':l['id'],'title_id':t['id'],'creditor':l['creditor'],'amount_cents':l['amount_cents'],
                           'open':l['released_at'] is None and rel is None,'released_by':rel['id'] if rel else l['released_at']})
    active=[t for t in bound if not t['revoked'] and t['status']=='active']
    if identity is None or not archives:status='pending'
    elif len(active)!=1:status='conflict'
    else:status='verified'
    return {'vin':vin,'owner':v['owner'],'identity':identity,'status':status,
            'title_id':active[0]['id'] if len(active)==1 else None,
            'bound_titles':[t['id'] for t in bound],'claims':claims,'conflicts':conflicts}

def graph(path,vin):
    with connect(path) as db:return _graph(db,vin)

def _offer(g,route):
    """对同一实体与同一原始留置事实，按交割路线给出可刊登说明、买方权利与解除/承接待办。"""
    liens=[c for c in g['claims'] if c['open'] and c['title_id']==g['title_id']]
    discharged=[c['lien_id'] for c in g['claims'] if not c['open'] and c['title_id']==g['title_id']]
    if g['status']!='verified':
        return 'pending',{'headline':'pending_verification','statement':'待核：实车身份与产权链尚未确认，暂不可挂牌','buyer_rights':None,'todos':['await_factory_or_registry_evidence'],'conflicts':g['conflicts']}
    if route=='payoff' and liens:
        total=sum(c['amount_cents'] for c in liens);creditors=sorted({c['creditor'] for c in liens})
        return 'pending',{'headline':'lien_payoff_pending','statement':'待核：存在未解除留置，卖方结清并取得登记机关解除回执后方可标清洁产权','buyer_rights':None,
                          'todos':[f'settle {total} cents to {c}' for c in creditors]+['obtain registry lien release receipt'],'open_liens':liens}
    if route=='assumption' and liens:
        creditors=sorted({c['creditor'] for c in liens})
        return 'verified',{'headline':'with_lien','statement':'带留置权：买方取得受限产权，'+'、'.join(creditors)+' 债权随车保留','buyer_rights':'restricted_title',
                           'creditor_signoff':creditors,'todos':['tripartite_assumption_agreement'],'proceeds_to':'seller_subject_to_creditor_claim',
                           'delivery_docs':['title_certificate','tripartite_assumption'],'assumed_liens':liens}
    text='清洁产权：卖方已结清留置并取得登记机关解除回执，买方不承接任何留置' if discharged else '清洁产权：登记链无未解除留置，买方不承接任何留置'
    return 'verified',{'headline':'clean_title','statement':text,'buyer_rights':'unencumbered_title','creditor_signoff':None,'todos':[],
                       'proceeds_to':'seller','delivery_docs':['title_certificate','lien_release_receipt'],'liens_discharged':discharged}

def prepare(path,vin,route,at_time):
    """生成一版拍卖物权说明（待核/已核版本各自留存）；已签卖或已交割的实体车不可再上架。"""
    if route not in ROUTES:raise ValueError('unknown_route')
    moment=at(at_time)
    with connect(path) as db,db:
        raw=json.dumps({'vin':vin,'route':route,'at':moment},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        g=_graph(db,vin)
        seq=db.execute('SELECT COUNT(*) c FROM listing WHERE vin=? AND route=?',(vin,route)).fetchone()['c']+1
        lid=f'LS-{vin}-{route}-{seq}'
        old=db.execute('SELECT id FROM source WHERE sha=?',(digest,)).fetchone()
        if old:
            prior=db.execute('SELECT * FROM listing WHERE id=?',(lid,)).fetchone()
            if prior:return {'id':lid,'status':prior['status'],'replayed':True}
        if g['identity']:
            sold=db.execute("SELECT d.id FROM delivery d JOIN listing l ON d.listing_id=l.id WHERE l.frame_serial=? AND d.status IN('signed','closed') LIMIT 1",(g['identity']['frame_serial'],)).fetchone()
            if sold:raise ValueError('entity_already_sold')
        sid=old['id'] if old else db.execute("INSERT INTO source(kind,sha,raw) VALUES('listing',?,?)",(digest,raw)).lastrowid
        status,stmt=_offer(g,route)
        db.execute('INSERT INTO listing VALUES(?,?,?,?,?,?,?,?,?)',(lid,vin,g['identity']['frame_serial'] if g['identity'] else None,g['title_id'],route,status,json.dumps(stmt,ensure_ascii=False,sort_keys=True),moment,sid))
        return {'id':lid,'status':status,'statement':stmt}

def sign(path,listing_id,buyer,price_cents,at_time,assumption_doc=None):
    """签署买卖：只对已核版本生效；同一实体车已签/已交割即阻断重复售卖。"""
    moment=at(at_time)
    with connect(path) as db,db:
        l=db.execute('SELECT * FROM listing WHERE id=?',(listing_id,)).fetchone()
        if not l:raise ValueError('unknown_listing')
        did=f'DL-{listing_id}'
        raw=json.dumps({'listing_id':listing_id,'buyer':buyer,'price_cents':price_cents,'at':moment,'assumption_doc':assumption_doc},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        old=db.execute('SELECT id FROM source WHERE sha=?',(digest,)).fetchone()
        if old:
            prior=db.execute('SELECT * FROM delivery WHERE id=?',(did,)).fetchone()
            if prior:return {'id':did,'status':prior['status'],'replayed':True}
        if l['status']!='verified':raise ValueError('listing_not_verified')
        if not isinstance(price_cents,int) or isinstance(price_cents,bool) or price_cents<=0:raise ValueError('invalid_money')
        if l['route']=='assumption' and not assumption_doc:raise ValueError('assumption_doc_required')
        clash=db.execute("SELECT d.id FROM delivery d JOIN listing l2 ON d.listing_id=l2.id WHERE l2.frame_serial=? AND d.status IN('signed','closed') LIMIT 1",(l['frame_serial'],)).fetchone()
        if clash:raise ValueError('duplicate_sale_blocked')
        sid=old['id'] if old else db.execute("INSERT INTO source(kind,sha,raw) VALUES('delivery_sign',?,?)",(digest,raw)).lastrowid
        db.execute('INSERT INTO delivery VALUES(?,?,?,?,?,?,?,?,?,?)',(did,listing_id,buyer,price_cents,l['route'],assumption_doc,'signed',moment,None,sid))
        return {'id':did,'status':'signed'}

def recover(path,receipt_no):
    """崩溃恢复：按登记机关外部签收号续写本地交割，时间以签收文书时点为准；幂等。"""
    with connect(path) as db,db:
        doc=db.execute("SELECT * FROM registry_doc WHERE kind='transfer_receipt' AND receipt_no=?",(receipt_no,)).fetchone()
        if not doc:raise ValueError('unknown_receipt')
        d=db.execute('SELECT * FROM delivery WHERE id=?',(doc['delivery_id'],)).fetchone()
        if not d:raise ValueError('unknown_delivery')
        raw=json.dumps({'receipt_no':receipt_no},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        old=db.execute('SELECT id FROM source WHERE sha=?',(digest,)).fetchone()
        if old and d['status']=='closed':return {'id':d['id'],'status':'closed','replayed':True}
        if d['status']!='signed':raise ValueError('delivery_not_signed')
        sid=old['id'] if old else db.execute("INSERT INTO source(kind,sha,raw) VALUES('delivery_close',?,?)",(digest,raw)).lastrowid
        db.execute("UPDATE delivery SET status='closed',closed_at=? WHERE id=?",(doc['at'],d['id']))
        return {'id':d['id'],'status':'closed','closed_at':doc['at']}

def statement(path,vin):
    """拍卖资料分版本返回：待核、已核、在途交割与已交割各自分离。"""
    with connect(path) as db:
        g=_graph(db,vin)
        listings=[]
        for l in db.execute('SELECT * FROM listing WHERE vin=? ORDER BY id',(vin,)):
            row=dict(l);row['statement']=json.loads(row['statement']);listings.append(row)
        deliveries=[dict(x) for x in db.execute('SELECT d.* FROM delivery d JOIN listing l ON d.listing_id=l.id WHERE l.vin=? ORDER BY d.id',(vin,))]
        return {'vin':vin,'entity':g,
                'pending':[l for l in listings if l['status']=='pending'],
                'verified':[l for l in listings if l['status']=='verified'],
                'in_transit':[d for d in deliveries if d['status']=='signed'],
                'delivered':[d for d in deliveries if d['status']=='closed']}
