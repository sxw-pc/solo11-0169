"""逐次通行核销：把稀疏定位、路网版本、标签交接、闸口签名与收费回执分开计量。

设计原则（与客户车辆维修/保修责任完全隔离）：

* 定位点只说明"在这段时间从 A 到 B 是可能的"，不说明走了哪条路。
  对长缺口枚举所有满足时间/路网约束的候选路径，绝不把最快路径当成唯一事实。
* OCR 牌照字符串是文本，不是实车见证；未签名回执只能挂争议，不能向车主计费。
* 每张回执在重叠租约、借还交接前后只能归属一次；身份链优先看标签交接与闸口签名。
* 决策与调整分开记账：已收客户款、门店已付/已退事实永不改写，只追加补收/退款/争议。
"""
import hashlib
import json
from datetime import datetime
from math import ceil

from .store import at, connect, repeated, source

PLAN_ACTUAL = 'actual-toll'
PLAN_FIXED = 'fixed-daily'
PLANS = (PLAN_ACTUAL, PLAN_FIXED)

DEFAULT_SLACK_S = 180          # 停靠/拥堵容差，不让"最短时间"变成唯一解
DEFAULT_MAX_GAP_S = 12 * 3600  # 超过该缺口不再假装能完成地图匹配
SNAP_TOLERANCE_M = 40          # accuracy_m 之外允许的吸附半径
SIGNATURE_TOLERANCE_S = 90
MAX_CANDIDATE_PATHS = 32
MAX_PATH_DEPTH = 8

STATUS_PROVABLE = 'provable'
STATUS_DISPUTED = 'disputed'
STATUS_NOT_ATTRIBUTABLE = 'not_attributable'


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _dt(value):
    if value.endswith('Z'):
        return datetime.fromisoformat(value[:-1] + '+00:00')
    return datetime.fromisoformat(value)


def _seconds(a, b):
    return int((_dt(b) - _dt(a)).total_seconds())


def _j(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _load(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _cents(value, field='cents'):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('invalid_%s' % field)
    return value


def _positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('invalid_%s' % field)
    return value


def _accuracy(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('invalid_accuracy_m')
    return value


def _norm_plan(value):
    if value in (PLAN_ACTUAL, 'actual', 'per-toll', 'per_toll'):
        return PLAN_ACTUAL
    if value in (PLAN_FIXED, 'fixed', 'fixed-package', 'daily-package'):
        return PLAN_FIXED
    raise ValueError('unknown_plan')


def _source_payload(db, kind, payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    old = db.execute('SELECT id,kind FROM source WHERE sha=?', (digest,)).fetchone()
    if old:
        if old['kind'] != kind:
            raise ValueError('source_kind_conflict')
        return old['id'], payload, True
    sid = db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',
                     (kind, digest, raw)).lastrowid
    return sid, payload, False


def _agreement(db, agreement_id):
    row = db.execute('SELECT * FROM loan_agreement WHERE id=?', (agreement_id,)).fetchone()
    if not row:
        raise ValueError('unknown_agreement')
    return row


def _effective_end(db, ag, moment):
    ends = [ag['due_at'], moment]
    ret = db.execute('SELECT returned_at FROM loan_return WHERE agreement_id=? '
                     'ORDER BY returned_at LIMIT 1', (ag['id'],)).fetchone()
    if ret:
        ends.append(ret['returned_at'])
    return min(ends, key=_dt)


def _is_active(db, ag):
    superseder = db.execute('SELECT 1 FROM loan_agreement WHERE supersedes=?',
                            (ag['id'],)).fetchone()
    return superseder is None


def _within(db, ag, moment, include_end=True):
    end = _effective_end(db, ag, moment)
    lo = _dt(ag['started_at']) <= _dt(moment)
    hi = _dt(moment) <= _dt(end) if include_end else _dt(moment) < _dt(end)
    return lo and hi


# ---------------------------------------------------------------------------
# 证据录入
# ---------------------------------------------------------------------------
def agreement(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'loan_agreement', file)
        if repeated(db, 'loan_agreement', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        vehicle = db.execute('SELECT * FROM vehicle WHERE vin=?',
                             (d['customer_vin'],)).fetchone()
        if not vehicle:
            raise ValueError('unknown_customer_vehicle')
        if not db.execute('SELECT 1 FROM loaner WHERE id=?',
                          (d['loaner_id'],)).fetchone():
            raise ValueError('unknown_loaner')
        if not d.get('borrower'):
            raise ValueError('borrower_missing')
        plan = _norm_plan(d['plan'])
        version = _positive_int(d.get('version', 1), 'version')
        daily = d.get('daily_cents')
        if plan == PLAN_FIXED:
            daily = _cents(daily, 'daily_cents')
        else:
            if daily is not None:
                raise ValueError('daily_cents_not_applicable')
            daily = None
        start, due = at(d['started_at']), at(d['due_at'])
        if _dt(due) <= _dt(start):
            raise ValueError('invalid_agreement_window')
        supersedes = d.get('supersedes')
        if supersedes:
            prior = db.execute('SELECT * FROM loan_agreement WHERE id=?',
                               (supersedes,)).fetchone()
            if not prior:
                raise ValueError('unknown_superseded_agreement')
            if prior['customer_vin'] != d['customer_vin'] or prior['loaner_id'] != d['loaner_id']:
                raise ValueError('superseded_agreement_mismatch')
            if supersedes == d['id']:
                raise ValueError('invalid_supersedes')
        db.execute('INSERT INTO loan_agreement VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (d['id'], d['customer_vin'], d['loaner_id'], d['borrower'],
                    start, due, plan, version, daily, supersedes, sid))
        return {'id': d['id'], 'plan': plan, 'version': version, 'replayed': False}


def fix(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'trip_fix', file)
        if repeated(db, 'trip_fix', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        ag = _agreement(db, d['agreement_id'])
        moment = at(d['observed_at'])
        accuracy = _accuracy(d.get('accuracy_m', 0))
        if not _within(db, ag, moment):
            raise ValueError('fix_outside_agreement')
        if not isinstance(d.get('x_m'), int) or not isinstance(d.get('y_m'), int):
            raise ValueError('invalid_fix_position')
        db.execute('INSERT INTO trip_fix VALUES(?,?,?,?,?,?,?)',
                   (d['id'], d['agreement_id'], moment, d['x_m'], d['y_m'], accuracy, sid))
        return {'id': d['id'], 'replayed': False}


def toll(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'toll_receipt', file)
        if repeated(db, 'toll_receipt', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not d.get('plate') or not d.get('gate'):
            raise ValueError('toll_identity_missing')
        cents = _cents(d['cents'])
        db.execute('INSERT INTO toll_receipt VALUES(?,?,?,?,?,?)',
                   (d['id'], d['plate'], d['gate'], at(d['passed_at']), cents, sid))
        return {'id': d['id'], 'replayed': False}


def fix_batch(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'fix_batch', file)
        if repeated(db, 'fix_batch', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        ag = _agreement(db, d['agreement_id'])
        points = d.get('fixes') or d.get('points') or []
        if not isinstance(points, list) or not points:
            raise ValueError('fix_batch_empty')
        seen = set()
        cleaned = []
        for p in points:
            pid = p.get('id')
            if not pid or pid in seen:
                raise ValueError('duplicate_fix_id')
            seen.add(pid)
            moment = at(p['observed_at'])
            if not _within(db, ag, moment):
                raise ValueError('fix_outside_agreement')
            if not isinstance(p.get('x_m'), int) or not isinstance(p.get('y_m'), int):
                raise ValueError('invalid_fix_position')
            cleaned.append((pid, moment, p['x_m'], p['y_m'],
                            _accuracy(p.get('accuracy_m', 0)), p.get('tag')))
        db.execute('INSERT INTO fix_batch VALUES(?,?,?)', (d['id'], d['agreement_id'], sid))
        for pid, moment, x, y, acc, _tag in cleaned:
            old = db.execute('SELECT source_id FROM fix_batch_item WHERE id=?',
                             (pid,)).fetchone()
            if old:
                raise ValueError('event_id_conflict')
            db.execute('INSERT INTO fix_batch_item VALUES(?,?,?,?,?,?,?,?)',
                       (pid, d['id'], d['agreement_id'], moment, x, y, acc, sid))
        return {'id': d['id'], 'fixes': len(cleaned), 'replayed': False}


def road_network(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'road_network', file)
        if repeated(db, 'road_network', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        version = _positive_int(d.get('version'), 'version')
        nodes, edges, closures = d.get('nodes'), d.get('edges'), d.get('closures', [])
        if not isinstance(nodes, dict) or not nodes:
            raise ValueError('network_nodes_missing')
        for key, node in nodes.items():
            if not isinstance(node.get('x_m'), int) or not isinstance(node.get('y_m'), int):
                raise ValueError('invalid_node_position')
        if not isinstance(edges, list) or not edges:
            raise ValueError('network_edges_missing')
        for edge in edges:
            if not edge.get('id') or edge.get('from') not in nodes or edge.get('to') not in nodes:
                raise ValueError('invalid_edge')
            seconds = edge.get('seconds', (edge.get('minutes') or 0) * 60)
            if not isinstance(seconds, int) or seconds <= 0:
                raise ValueError('invalid_edge_duration')
            if edge.get('kind') not in ('toll', 'free', 'surface'):
                raise ValueError('invalid_edge_kind')
        if not isinstance(closures, list):
            raise ValueError('invalid_closures')
        for c in closures:
            if not c.get('edge_id') and not c.get('gate'):
                raise ValueError('invalid_closure')
            at(c['from_at']); at(c['to_at'])
        db.execute('INSERT INTO road_network VALUES(?,?,?,?,?,?)',
                   (d['id'], version, _j(nodes), _j(edges), _j(closures), sid))
        return {'id': d['id'], 'version': version, 'replayed': False}


def tag_handover(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'tag_handover', file)
        if repeated(db, 'tag_handover', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        loaner = db.execute('SELECT * FROM loaner WHERE id=?', (d['loaner_id'],)).fetchone()
        if not loaner:
            raise ValueError('unknown_loaner')
        if d.get('plate') and d['plate'] != loaner['plate']:
            raise ValueError('handover_plate_mismatch')
        if d.get('agreement_id'):
            ag = _agreement(db, d['agreement_id'])
            if ag['loaner_id'] != d['loaner_id']:
                raise ValueError('handover_agreement_mismatch')
        if not d.get('tag'):
            raise ValueError('tag_missing')
        start = at(d['from_at'])
        end = at(d['to_at']) if d.get('to_at') else None
        if end and _dt(end) <= _dt(start):
            raise ValueError('invalid_handover_window')
        db.execute('INSERT INTO tag_handover VALUES(?,?,?,?,?,?,?,?,?)',
                   (d['id'], d['loaner_id'], d.get('plate') or loaner['plate'], d['tag'],
                    d.get('borrower'), d.get('agreement_id'), start, end, sid))
        return {'id': d['id'], 'tag': d['tag'], 'replayed': False}


def gate_signature(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'gate_signature', file)
        if repeated(db, 'gate_signature', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not d.get('gate') or not d.get('plate') or not d.get('tag'):
            raise ValueError('signature_identity_missing')
        if not d.get('signature'):
            raise ValueError('signature_missing')
        db.execute('INSERT INTO gate_signature VALUES(?,?,?,?,?,?,?)',
                   (d['id'], d['gate'], d['plate'], d['tag'], at(d['passed_at']),
                    d['signature'], sid))
        return {'id': d['id'], 'replayed': False}


def ocr_read(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'ocr_read', file)
        if repeated(db, 'ocr_read', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not db.execute('SELECT 1 FROM toll_receipt WHERE id=?',
                          (d['receipt_id'],)).fetchone():
            raise ValueError('unknown_toll_receipt')
        if not d.get('plate_text'):
            raise ValueError('ocr_plate_missing')
        conf = d.get('confidence', 0)
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
            raise ValueError('invalid_ocr_confidence')
        image_sha = d.get('image_sha')
        if image_sha is not None and (not isinstance(image_sha, str) or len(image_sha) < 16):
            raise ValueError('invalid_ocr_image_sha')
        db.execute('INSERT INTO ocr_read VALUES(?,?,?,?,?,?,?)',
                   (d['id'], d['receipt_id'], d['plate_text'], float(conf),
                    1 if d.get('signed') else 0, image_sha, sid))
        return {'id': d['id'], 'signed': bool(d.get('signed')), 'replayed': False}


def plate_appeal(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'plate_appeal', file)
        if repeated(db, 'plate_appeal', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not d.get('plate') or not d.get('reason'):
            raise ValueError('appeal_incomplete')
        db.execute('INSERT INTO plate_appeal VALUES(?,?,?,?,?)',
                   (d['id'], d['plate'], d['reason'], at(d['at']), sid))
        return {'id': d['id'], 'replayed': False}


def loan_return(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'loan_return', file)
        if repeated(db, 'loan_return', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        ag = _agreement(db, d['agreement_id'])
        moment = at(d['returned_at'])
        if _dt(moment) < _dt(ag['started_at']):
            raise ValueError('return_before_handover')
        if db.execute('SELECT 1 FROM loan_return WHERE agreement_id=?',
                      (d['agreement_id'],)).fetchone():
            raise ValueError('return_already_recorded')
        db.execute('INSERT INTO loan_return VALUES(?,?,?,?)',
                   (d['id'], d['agreement_id'], moment, sid))
        return {'id': d['id'], 'returned_at': moment, 'replayed': False}


def customer_payment(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'customer_payment', file)
        if repeated(db, 'customer_payment', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        _agreement(db, d['agreement_id'])
        if d.get('receipt_id') and not db.execute(
                'SELECT 1 FROM toll_receipt WHERE id=?', (d['receipt_id'],)).fetchone():
            raise ValueError('unknown_toll_receipt')
        db.execute('INSERT INTO customer_payment VALUES(?,?,?,?,?,?)',
                   (d['id'], d['agreement_id'], d.get('receipt_id'),
                    _cents(d['cents'], 'cents'), at(d['at']), sid))
        return {'id': d['id'], 'replayed': False}


def toll_payment(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'toll_payment', file)
        if repeated(db, 'toll_payment', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not db.execute('SELECT 1 FROM toll_receipt WHERE id=?',
                          (d['receipt_id'],)).fetchone():
            raise ValueError('unknown_toll_receipt')
        if d.get('kind') not in ('shop-paid', 'authority-charged', 'refund'):
            raise ValueError('invalid_toll_payment_kind')
        db.execute('INSERT INTO toll_payment VALUES(?,?,?,?,?,?)',
                   (d['id'], d['receipt_id'], d['kind'], _cents(d['cents'], 'cents'),
                    at(d['at']), sid))
        return {'id': d['id'], 'replayed': False}


# ---------------------------------------------------------------------------
# 证据读取
# ---------------------------------------------------------------------------
def _lineage(db, agreement_id):
    """租约版本链：当前协议 + 它取代的旧版本（旧版本的轨迹与交接仍有效）。"""
    ids, seen, cursor = [agreement_id], {agreement_id}, agreement_id
    while cursor:
        row = db.execute('SELECT supersedes FROM loan_agreement WHERE id=?',
                         (cursor,)).fetchone()
        cursor = row['supersedes'] if row else None
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
        ids.append(cursor)
    return ids


def _fix_points(db, agreement_id):
    ids = _lineage(db, agreement_id)
    marks = ','.join('?' * len(ids))
    rows = {r['id']: dict(r) for r in db.execute(
        'SELECT id,agreement_id,observed_at,x_m,y_m,accuracy_m FROM trip_fix '
        'WHERE agreement_id IN (%s)' % marks, ids)}
    for r in db.execute(
            'SELECT id,agreement_id,observed_at,x_m,y_m,accuracy_m FROM fix_batch_item '
            'WHERE agreement_id IN (%s)' % marks, ids):
        rows.setdefault(r['id'], dict(r))
    points = sorted(rows.values(), key=lambda p: (p['observed_at'], p['id']))
    return [{'id': p['id'], 'at': p['observed_at'], 'x': p['x_m'],
             'y': p['y_m'], 'accuracy_m': p['accuracy_m']} for p in points]


def _agreements(db):
    return [dict(r) for r in db.execute('SELECT * FROM loan_agreement ORDER BY id')]


def _loaner(db, loaner_id):
    return db.execute('SELECT * FROM loaner WHERE id=?', (loaner_id,)).fetchone()


def _network(db):
    row = db.execute('SELECT * FROM road_network ORDER BY version DESC, id DESC LIMIT 1').fetchone()
    if not row:
        return None
    closures = []
    for r in db.execute('SELECT * FROM road_network ORDER BY version, id'):
        closures.extend(_load(r['closures']) or [])
    return {'id': row['id'], 'version': row['version'],
            'nodes': _norm_nodes(_load(row['nodes'])),
            'edges': _load(row['edges']), 'closures': closures}


def _norm_nodes(nodes):
    return {nid: {'x': n['x_m'], 'y': n['y_m'], 'id': nid,
                  **{k: v for k, v in n.items() if k not in ('x_m', 'y_m')}}
            for nid, n in nodes.items()}


def _handovers(db, loaner_id):
    return [dict(r) for r in db.execute(
        'SELECT * FROM tag_handover WHERE loaner_id=? ORDER BY id', (loaner_id,))]


def _signatures(db):
    return [dict(r) for r in db.execute('SELECT * FROM gate_signature ORDER BY id')]


def _ocr(db, receipt_id):
    return [dict(r) for r in db.execute(
        'SELECT * FROM ocr_read WHERE receipt_id=? ORDER BY id', (receipt_id,))]


def _appeals(db, plate):
    return [dict(r) for r in db.execute(
        'SELECT * FROM plate_appeal WHERE plate=? ORDER BY id', (plate,))]


# ---------------------------------------------------------------------------
# 路网路径枚举
# ---------------------------------------------------------------------------
def _distance(a, b):
    return ((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2) ** 0.5


def _snap(nodes, point):
    tolerance = point['accuracy_m'] + SNAP_TOLERANCE_M
    return sorted(nid for nid, node in nodes.items()
                  if _distance(node, point) <= tolerance)


def _overlaps(a_from, a_to, b_from, b_to):
    return _dt(a_from) < _dt(b_to) and _dt(b_from) < _dt(a_to)


def _edge_unavailable(edge, gap_start, gap_end, closures):
    for closure in closures:
        same_edge = closure.get('edge_id') and closure['edge_id'] == edge['id']
        same_gate = closure.get('gate') and edge.get('gate') and closure['gate'] == edge['gate']
        if (same_edge or same_gate) and _overlaps(
                closure['from_at'], closure['to_at'], gap_start, gap_end):
            return True
    return False


def _enumerate_paths(nodes, edges, starts, ends, max_seconds, unavailable):
    adjacency = {}
    for edge in edges:
        if edge['id'] in unavailable:
            continue
        adjacency.setdefault(edge['from'], []).append(edge)
        if edge.get('bidirectional', True):
            reverse = {**edge, 'from': edge['to'], 'to': edge['from']}
            adjacency.setdefault(reverse['from'], []).append(reverse)
    end_set = set(ends)
    results = []
    truncated = False

    def walk(node, visited, used, seconds):
        nonlocal truncated
        if len(results) >= MAX_CANDIDATE_PATHS:
            truncated = True
            return
        if node in end_set and used:
            results.append({'edges': [e['id'] for e in used],
                            'seconds': seconds,
                            'uses_toll': any(e.get('kind') == 'toll' for e in used),
                            'gates': sorted({e['gate'] for e in used if e.get('gate')})})
        if len(used) >= MAX_PATH_DEPTH:
            return
        for edge in adjacency.get(node, []):
            if edge['to'] in visited:
                continue
            cost = edge.get('seconds', 0)
            if seconds + cost > max_seconds:
                continue
            walk(edge['to'], visited | {edge['to']}, used + [edge],
                 seconds + cost)

    # 起点==终点时，零长度路径本身也是一条候选（不能假装只有绕行一种可能）
    if set(starts) & end_set:
        results.append({'edges': [], 'seconds': 0, 'uses_toll': False, 'gates': []})
    for start in starts:
        walk(start, {start}, [], 0)
    unique = {tuple(p['edges']): p for p in results}
    return list(unique.values())[:MAX_CANDIDATE_PATHS], truncated


def _path_evidence(network, receipt, points):
    """返回 (support, candidates, segment, note)。"""
    moment = receipt['passed_at']
    before = [p for p in points if _dt(p['at']) <= _dt(moment)]
    after = [p for p in points if _dt(p['at']) >= _dt(moment)]
    if not before or not after:
        return 'none', [], (before[-1:] + after[:1]), 'no_bracketing_fix'
    a, b = before[-1], after[0]
    if a['id'] == b['id']:
        # 只有恰在收费时刻的一条定位：没有两个端点，无法确定任何路线
        return 'none', [], [a], 'single_coincident_fix'
    gap = _seconds(a['at'], b['at'])
    segment = [a, b]
    if gap < 0:
        return 'none', [], segment, 'invalid_fix_order'
    if gap > DEFAULT_MAX_GAP_S:
        return 'gap-too-long', [], segment, 'gap_exceeds_matching_horizon'
    if not network:
        return 'unknown', [], segment, 'road_network_missing'
    starts = _snap(network['nodes'], a)
    ends = _snap(network['nodes'], b)
    if not starts or not ends:
        return 'no-snap', [], segment, 'fix_not_on_network'
    unavailable = set()
    for edge in network['edges']:
        if _edge_unavailable(edge, a['at'], b['at'], network['closures']):
            unavailable.add(edge['id'])
    candidates, truncated = _enumerate_paths(
        network['nodes'], network['edges'], starts, ends,
        gap + DEFAULT_SLACK_S, unavailable)
    if not candidates:
        return 'no-route', [], segment, 'no_path_within_time_and_closures'
    for candidate in candidates:
        candidate['uses_receipt_gate'] = receipt['gate'] in candidate['gates']
    gate = [c for c in candidates if c['uses_receipt_gate']]
    if len(gate) == len(candidates):
        support = 'toll-only'
    elif not gate:
        support = 'no-toll-path'
    else:
        support = 'ambiguous'
    note = 'candidate_paths_truncated' if truncated else ''
    return support, candidates, segment, note


def _gate_closed(network, receipt, segment):
    if not network:
        return False
    start = segment[0]['at'] if segment else receipt['passed_at']
    end = segment[-1]['at'] if len(segment) > 1 else receipt['passed_at']
    for closure in network['closures']:
        if closure.get('gate'):
            if closure['gate'] != receipt['gate']:
                continue
        elif closure.get('edge_id'):
            which = next((e for e in network['edges']
                          if e['id'] == closure['edge_id']), None)
            if not which or which.get('gate') != receipt['gate']:
                continue
        else:
            continue
        if _overlaps(closure['from_at'], closure['to_at'], start, end):
            return True
    return False


# ---------------------------------------------------------------------------
# 身份链
# ---------------------------------------------------------------------------
def _identity(db, receipt, agreement):
    loaner = _loaner(db, agreement['loaner_id'])
    plate = loaner['plate'] if loaner else None
    passed = receipt['passed_at']
    lineage = set(_lineage(db, agreement['id']))
    handovers = [h for h in _handovers(db, agreement['loaner_id'])
                 if _dt(h['from_at']) <= _dt(passed)
                 and (h['to_at'] is None or _dt(passed) <= _dt(h['to_at']))]
    linked = [h for h in handovers
              if h['agreement_id'] in (None, *lineage)
              and (h['borrower'] in (None, agreement['borrower']))]
    linked_tags = {h['tag'] for h in linked}
    chain = [{'kind': 'tag_handover', 'id': h['id'], 'tag': h['tag'],
              'from_at': h['from_at'], 'to_at': h['to_at']} for h in linked]

    re_lent = []
    if linked_tags:
        for h in db.execute('SELECT * FROM tag_handover ORDER BY id'):
            if h['tag'] in linked_tags and h['loaner_id'] != agreement['loaner_id'] \
                    and _dt(h['from_at']) <= _dt(passed) \
                    and (h['to_at'] is None or _dt(passed) <= _dt(h['to_at'])):
                re_lent.append(dict(h))

    ocr_rows = _ocr(db, receipt['id'])
    chain.extend({'kind': 'ocr', 'id': o['id'], 'plate_text': o['plate_text'],
                  'confidence': o['confidence'], 'signed': bool(o['signed'])}
                 for o in ocr_rows)
    plate_mismatch = any(o['plate_text'] != plate for o in ocr_rows)
    appeals = _appeals(db, receipt['plate']) + (_appeals(db, plate) if plate else [])
    chain.extend({'kind': 'plate_appeal', 'id': a['id'], 'plate': a['plate'],
                  'reason': a['reason']} for a in appeals)

    signature = None
    for sig in _signatures(db):
        if sig['gate'] != receipt['gate']:
            continue
        if abs(_seconds(sig['passed_at'], passed)) > SIGNATURE_TOLERANCE_S:
            continue
        chain.append({'kind': 'gate_signature', 'id': sig['id'], 'tag': sig['tag'],
                      'plate': sig['plate'], 'passed_at': sig['passed_at']})
        if sig['plate'] == plate and (not linked_tags or sig['tag'] in linked_tags):
            signature = sig
            break

    if re_lent:
        trust, reason = 'contested', 'tag_lent_to_another_vehicle'
    elif signature and not plate_mismatch:
        trust, reason = 'trusted-gate', 'signed_gate_matches_vehicle'
    elif plate_mismatch:
        trust, reason = 'contested', 'ocr_plate_mismatch'
    elif appeals:
        trust, reason = 'contested', 'plate_misread_appeal'
    elif linked_tags:
        trust, reason = 'tag-handover', 'tag_with_borrower_but_no_gate_witness'
    elif ocr_rows:
        trust, reason = 'ocr-only', 'unsigned_plate_text_only'
    elif plate and receipt['plate'] == plate:
        trust, reason = 'plate-only', 'receipt_plate_matches_vehicle_no_witness'
    else:
        trust, reason = 'unverified', 'no_identity_evidence'
    return {'trust': trust, 'reason': reason, 'plate': plate,
            'linked_tags': sorted(linked_tags), 're_lent': [h['id'] for h in re_lent],
            'chain': chain, 'signature_id': signature['id'] if signature else None}


# ---------------------------------------------------------------------------
# 协议归属与决策
# ---------------------------------------------------------------------------
def _priority(db, agreement):
    active = 0 if _is_active(db, agreement) else 1
    length = _seconds(agreement['started_at'], agreement['due_at'])
    return (active, -agreement['version'], length, agreement['id'])


def _agreement_handover(db, agreement, moment):
    lineage = set(_lineage(db, agreement['id']))
    for handover in _handovers(db, agreement['loaner_id']):
        if handover['agreement_id'] not in lineage:
            continue
        if _dt(handover['from_at']) <= _dt(moment) and (
                handover['to_at'] is None or _dt(moment) <= _dt(handover['to_at'])):
            return handover
    return None


def _has_bracketing_fix(db, agreement, moment):
    points = _fix_points(db, agreement['id'])
    return any(_dt(p['at']) <= _dt(moment) for p in points) and \
        any(_dt(p['at']) >= _dt(moment) for p in points)


def _assignment(db, receipt):
    candidates = [a for a in _agreements(db) if _within(db, a, receipt['passed_at'])]
    if not candidates:
        return None

    def key(ag):
        loaner = _loaner(db, ag['loaner_id'])
        plate_match = 0 if loaner and loaner['plate'] == receipt['plate'] else 1
        handover_match = 0 if _agreement_handover(db, ag, receipt['passed_at']) else 1
        fix_match = 0 if _has_bracketing_fix(db, ag, receipt['passed_at']) else 1
        return (0 if _is_active(db, ag) else 1, handover_match, fix_match,
                plate_match) + _priority(db, ag)

    return sorted(candidates, key=key)[0]


def _required(trust, support, closed, status):
    if status == STATUS_PROVABLE:
        return []
    if closed:
        return ['closure_clearance_or_alternate_gate_record']
    if status == STATUS_NOT_ATTRIBUTABLE:
        if support in ('none', 'no-snap', 'gap-too-long'):
            return ['bracketing_gps_fix_with_accuracy', 'authenticated_gate_signature']
        return ['authenticated_gate_signature', 'tag_handover_for_passage']
    # disputed
    if trust != 'trusted-gate':
        return ['authenticated_gate_signature', 'tag_handover_for_passage']
    return ['gate_passage_confirmation_from_operator']


def _decide_receipt(db, receipt, agreement, network):
    if not agreement:
        return {'subject_id': receipt['id'], 'subject_kind': 'receipt',
                'receipt_id': receipt['id'], 'agreement_id': None,
                'status': STATUS_NOT_ATTRIBUTABLE, 'reason': 'outside_all_agreements',
                'customer_cents': 0, 'shop_verify_cents': 0,
                'trust': 'none', 'path_support': 'unknown', 'candidate_paths': [],
                'trajectory': [], 'identity_chain': [],
                'required_evidence': ['loan_agreement_covering_passage'], 'days': 0}
    points = _fix_points(db, agreement['id'])
    support, candidates, segment, note = _path_evidence(network, receipt, points)
    identity = _identity(db, receipt, agreement)
    closed = _gate_closed(network, receipt, segment)
    trust = identity['trust']
    cents = receipt['cents']

    if agreement['plan'] == PLAN_FIXED:
        status = STATUS_DISPUTED
        reason = 'fixed_package_covers_passage_verify_with_authority'
        customer, verify = 0, cents
    elif trust != 'trusted-gate' and support in (
            'none', 'gap-too-long', 'no-snap', 'no-route', 'unknown'):
        status, reason = STATUS_NOT_ATTRIBUTABLE, 'insufficient_bracketing_or_network_evidence'
        customer, verify = 0, 0
    elif trust == 'trusted-gate' and not closed:
        if support == 'no-toll-path':
            status, reason = STATUS_DISPUTED, 'gps_timing_conflicts_with_signed_gate'
            customer, verify = 0, cents
        else:
            status, reason = STATUS_PROVABLE, identity['reason']
            customer, verify = cents, 0
    elif trust == 'trusted-gate' and closed:
        status, reason = STATUS_DISPUTED, 'signed_gate_conflicts_with_closure'
        customer, verify = 0, cents
    elif trust in ('ocr-only', 'tag-handover', 'contested', 'plate-only'):
        if support == 'no-toll-path':
            reason = 'gps_only_shows_free_route_receipt_disputed'
        else:
            reason = identity['reason']
        status = STATUS_DISPUTED
        customer, verify = 0, cents
    else:
        status, reason = STATUS_NOT_ATTRIBUTABLE, identity['reason']
        customer, verify = 0, 0
    required = _required(trust, support, closed, status)
    if note:
        reason = '%s;%s' % (reason, note)
    return {'subject_id': receipt['id'], 'subject_kind': 'receipt',
            'receipt_id': receipt['id'], 'agreement_id': agreement['id'],
            'status': status, 'reason': reason,
            'customer_cents': customer, 'shop_verify_cents': verify,
            'trust': trust, 'path_support': support,
            'candidate_paths': candidates, 'trajectory': segment,
            'identity_chain': identity['chain'], 'required_evidence': required,
            'days': 0}


def _usage_dates(db, agreement, receipts_by_agreement):
    end = _effective_end(db, agreement, '9999-12-31T23:59:59Z')
    dates = {p['at'][:10] for p in _fix_points(db, agreement['id'])
             if _dt(p['at']) <= _dt(end)}
    dates |= {r['passed_at'][:10] for r in receipts_by_agreement.get(agreement['id'], [])
              if _dt(r['passed_at']) <= _dt(end)}
    return dates


def _decide_package(db, agreement, dates):
    days = len(dates)
    if not days or agreement['plan'] != PLAN_FIXED:
        return None
    return {'subject_id': 'PKG:%s' % agreement['id'], 'subject_kind': 'package',
            'receipt_id': None, 'agreement_id': agreement['id'],
            'status': STATUS_PROVABLE, 'reason': 'fixed_daily_package',
            'customer_cents': agreement['daily_cents'] * days,
            'shop_verify_cents': 0, 'trust': 'contract', 'path_support': 'not_required',
            'candidate_paths': [], 'trajectory': [], 'identity_chain': [],
            'required_evidence': [], 'days': days}


def reconcile(path, at_time=None):
    """计算当前证据下的逐笔决策，不落库。"""
    with connect(path) as db:
        moment = at_time or _default_at(db)
        at(moment)
        return _reconcile(db, moment)


def _reconcile(db, moment):
    network = _network(db)
    receipts = [dict(r) for r in db.execute(
        'SELECT * FROM toll_receipt ORDER BY passed_at,id')]
    agreements = _agreements(db)
    receipts_by_agreement = {}
    decisions = []
    for receipt in receipts:
        agreement = _assignment(db, receipt)
        decision = _decide_receipt(db, receipt, agreement, network)
        decisions.append(decision)
        if agreement:
            receipts_by_agreement.setdefault(agreement['id'], []).append(receipt)
    claimed_dates = set()
    for agreement in sorted(agreements, key=lambda a: _priority(db, a)):
        dates = _usage_dates(db, agreement, receipts_by_agreement)
        fresh = {d for d in dates if d not in claimed_dates}
        if agreement['plan'] == PLAN_FIXED and fresh:
            claimed_dates |= fresh
        package = _decide_package(db, agreement, fresh)
        if package:
            decisions.append(package)
    return _summary(moment, decisions)


def _summary(moment, decisions):
    receipts = [d for d in decisions if d['subject_kind'] == 'receipt']
    packages = [d for d in decisions if d['subject_kind'] == 'package']
    agreements = {}
    for d in receipts + packages:
        ag = d['agreement_id']
        if not ag:
            continue
        bucket = agreements.setdefault(ag, {
            'agreement_id': ag, 'customer_receivable_cents': 0,
            'disputed_cents': 0, 'not_attributable_cents': 0, 'package_days': 0})
        bucket['customer_receivable_cents'] += d['customer_cents']
        if d['status'] == STATUS_DISPUTED:
            bucket['disputed_cents'] += d['shop_verify_cents']
        if d['status'] == STATUS_NOT_ATTRIBUTABLE:
            bucket['not_attributable_cents'] += d['shop_verify_cents']
        if d['subject_kind'] == 'package':
            bucket['package_days'] += d['days']
    return {'at': moment, 'receipts': receipts, 'packages': packages,
            'agreements': [agreements[k] for k in sorted(agreements)],
            'totals': {'customer_receivable_cents': sum(
                           d['customer_cents'] for d in receipts + packages),
                       'disputed_cents': sum(d['shop_verify_cents'] for d in decisions
                                             if d['status'] == STATUS_DISPUTED),
                       'not_attributable_cents': sum(d['shop_verify_cents'] for d in decisions
                                                     if d['status'] == STATUS_NOT_ATTRIBUTABLE)}}


# ---------------------------------------------------------------------------
# 结算、调整与对账
# ---------------------------------------------------------------------------
def _default_at(db):
    stamps = []
    for table, column in (('toll_receipt', 'passed_at'), ('trip_fix', 'observed_at'),
                          ('fix_batch_item', 'observed_at'), ('loan_return', 'returned_at'),
                          ('customer_payment', 'at'), ('toll_payment', 'at')):
        row = db.execute('SELECT MAX(%s) AS m FROM %s' % (column, table)).fetchone()
        if row and row['m']:
            stamps.append(row['m'])
    return max(stamps) if stamps else '1970-01-01T00:00:00Z'


def _prior_decisions(db):
    """每个 subject 的最近一次已落库决策。"""
    latest = {}
    for row in db.execute('SELECT * FROM toll_decision ORDER BY revision, row_id'):
        latest[row['subject_id']] = dict(row)
    return latest


def _persist(db, moment, decisions, sid):
    prior = _prior_decisions(db)
    stored = []
    for decision in decisions:
        previous = prior.get(decision['subject_id'])
        revision = (previous['revision'] + 1) if previous else 1
        db.execute(
            'INSERT INTO toll_decision(subject_id,subject_kind,receipt_id,agreement_id,'
            'revision,status,reason,customer_cents,shop_verify_cents,trust,path_support,'
            'candidate_paths,trajectory,identity_chain,required_evidence,days,at,source_id) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (decision['subject_id'], decision['subject_kind'], decision['receipt_id'],
             decision['agreement_id'], revision, decision['status'], decision['reason'],
             decision['customer_cents'], decision['shop_verify_cents'], decision['trust'],
             decision['path_support'], _j(decision['candidate_paths']),
             _j(decision['trajectory']), _j(decision['identity_chain']),
             _j(decision['required_evidence']), decision['days'], moment, sid))
        stored.append(decision)
    return stored


def _evidence_digest(db):
    rows = [(r['kind'], r['sha']) for r in db.execute(
        "SELECT kind,sha FROM source WHERE kind NOT IN ('toll_settlement') ORDER BY id")]
    return hashlib.sha256(_j(rows).encode()).hexdigest()


def settle(path, at_time):
    """按给定时刻的可见证据重新决策，只追加修订与调整，不改写旧记录。"""
    moment = at(at_time)
    with connect(path) as db, db:
        sid, _, _ = _source_payload(
            db, 'toll_settlement', {'at': moment, 'evidence': _evidence_digest(db)})
        existing = db.execute(
            'SELECT 1 FROM toll_decision WHERE at=? AND source_id=? LIMIT 1',
            (moment, sid)).fetchone()
        if existing:
            return _ledger(db, moment, replayed=True)
        current = _reconcile(db, moment)
        prior = _prior_decisions(db)
        decisions = current['receipts'] + current['packages']
        for decision in decisions:
            previous = prior.get(decision['subject_id'])
            old_customer = previous['customer_cents'] if previous else 0
            delta = decision['customer_cents'] - old_customer
            if delta > 0:
                db.execute(
                    'INSERT INTO toll_adjustment(agreement_id,subject_id,kind,cents,reason,at,source_id) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (decision['agreement_id'], decision['subject_id'], 'top-up', delta,
                     'late_evidence' if previous else 'initial_assessment', moment, sid))
            elif delta < 0:
                db.execute(
                    'INSERT INTO toll_adjustment(agreement_id,subject_id,kind,cents,reason,at,source_id) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (decision['agreement_id'], decision['subject_id'], 'refund', -delta,
                     'evidence_changed', moment, sid))
            if decision['status'] == STATUS_DISPUTED and decision['shop_verify_cents']:
                db.execute(
                    'INSERT INTO toll_adjustment(agreement_id,subject_id,kind,cents,reason,at,source_id) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (decision['agreement_id'], decision['subject_id'], 'dispute',
                     decision['shop_verify_cents'], decision['reason'], moment, sid))
        _persist(db, moment, decisions, sid)
        return _ledger(db, moment, replayed=False)


def _ledger(db, moment, replayed):
    latest = _prior_decisions(db)
    agreements = {}
    for row in latest.values():
        agreement_id = row['agreement_id']
        bucket = agreements.setdefault(agreement_id, {
            'agreement_id': agreement_id, 'customer_receivable_cents': 0,
            'disputed_cents': 0, 'not_attributable_cents': 0,
            'package_days': 0, 'customer_paid_cents': 0,
            'customer_outstanding_cents': 0, 'receipts': []})
        bucket['customer_receivable_cents'] += row['customer_cents']
        if row['status'] == STATUS_DISPUTED:
            bucket['disputed_cents'] += row['shop_verify_cents']
        if row['status'] == STATUS_NOT_ATTRIBUTABLE:
            bucket['not_attributable_cents'] += row['shop_verify_cents']
        if row['subject_kind'] == 'package':
            bucket['package_days'] += row['days']
        if row['subject_kind'] == 'receipt':
            bucket['receipts'].append(_decision_out(row))
    for payment in db.execute('SELECT * FROM customer_payment'):
        bucket = agreements.get(payment['agreement_id'])
        if bucket:
            bucket['customer_paid_cents'] += payment['cents']
    for bucket in agreements.values():
        bucket['customer_outstanding_cents'] = (
            bucket['customer_receivable_cents'] - bucket['customer_paid_cents'])
        bucket['receipts'].sort(key=lambda r: r['subject_id'])
    receipt_agreement = {row['receipt_id']: row['agreement_id']
                         for row in latest.values() if row['receipt_id']}
    shop_facts, shop_paid = [], 0
    for payment in db.execute('SELECT * FROM toll_payment ORDER BY id'):
        agreement_id = receipt_agreement.get(payment['receipt_id'])
        shop_facts.append({'id': payment['id'], 'receipt_id': payment['receipt_id'],
                           'agreement_id': agreement_id, 'kind': payment['kind'],
                           'cents': payment['cents'], 'at': payment['at']})
        if payment['kind'] in ('shop-paid', 'authority-charged'):
            shop_paid += payment['cents']
        elif payment['kind'] == 'refund':
            shop_paid -= payment['cents']
    adjustments = [dict(r) for r in db.execute(
        'SELECT * FROM toll_adjustment ORDER BY row_id')]
    return {'at': moment, 'replayed': replayed,
            'agreements': [agreements[k] for k in sorted(
                agreements, key=lambda k: (k is None, k or ''))],
            'adjustments': adjustments, 'toll_payments': shop_facts,
            'totals': {'customer_receivable_cents': sum(
                           b['customer_receivable_cents'] for b in agreements.values()),
                       'disputed_cents': sum(b['disputed_cents'] for b in agreements.values()),
                       'not_attributable_cents': sum(
                           b['not_attributable_cents'] for b in agreements.values()),
                       'customer_paid_cents': sum(
                           b['customer_paid_cents'] for b in agreements.values()),
                       'shop_net_paid_cents': shop_paid}}


def _decision_out(row):
    return {'subject_id': row['subject_id'], 'subject_kind': row['subject_kind'],
            'receipt_id': row['receipt_id'], 'agreement_id': row['agreement_id'],
            'revision': row['revision'], 'status': row['status'], 'reason': row['reason'],
            'customer_cents': row['customer_cents'],
            'shop_verify_cents': row['shop_verify_cents'], 'trust': row['trust'],
            'path_support': row['path_support'],
            'candidate_paths': _load(row['candidate_paths']) or [],
            'trajectory': _load(row['trajectory']) or [],
            'identity_chain': _load(row['identity_chain']) or [],
            'required_evidence': _load(row['required_evidence']) or [],
            'days': row['days'], 'at': row['at']}


def ledger(path):
    with connect(path) as db:
        return _ledger(db, _default_at(db), replayed=False)


def explain(path, subject_id=None):
    """每笔收费的轨迹段、身份链与可信度；不足时列出最小补充证据。"""
    with connect(path) as db:
        latest = _prior_decisions(db)
        rows = [_decision_out(r) for r in latest.values()]
        if subject_id:
            rows = [r for r in rows if r['subject_id'] == subject_id]
        elif not rows:
            fallback = reconcile(path, _default_at(db))
            rows = fallback['receipts'] + fallback['packages']
        return {'decisions': sorted(rows, key=lambda r: (r['subject_kind'] != 'receipt',
                                                         r['subject_id']))}


# 所有可导入证据类型 -> 录入函数，供 CLI 与 audit 共用。
IMPORTERS = {
    'loan_agreement': agreement,
    'trip_fix': fix,
    'toll_receipt': toll,
    'fix_batch': fix_batch,
    'road_network': road_network,
    'tag_handover': tag_handover,
    'gate_signature': gate_signature,
    'ocr_read': ocr_read,
    'plate_appeal': plate_appeal,
    'loan_return': loan_return,
    'customer_payment': customer_payment,
    'toll_payment': toll_payment,
}
