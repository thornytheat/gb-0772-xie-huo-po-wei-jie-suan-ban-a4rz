from copy import deepcopy

import pytest

from app.archive.appeal import issue
from app.archive.canonical import digest
from app.billing.handover import split_stay
from app.problems import DocketError

OUT = '城北运营一队'
INC = '滨港停车服务'


def test_parties_only_recognize_due_from_their_window(handover):
    h = handover.settlement['handover']
    accounts = {a['party']: a for a in h['accounts']}
    # 交接点 00:20（UTC 16:20）：两家只认各自承管时段产生的应收
    assert accounts[OUT]['due_cents'] == 1200
    assert accounts[INC]['due_cents'] == 1040
    # 两家应收之和与原单应收严格相等
    assert h['due_total_cents'] == handover.settlement['money']['due_cents'] == 2240


def test_every_stay_split_conserves_original_due(handover):
    h = handover.settlement['handover']
    assert all(sum(row['split'].values()) == row['due_cents'] for row in h['stays'])
    # 首免与争议分钟同样按承管时段切分：首单 00:20 前整段（含 10 分钟首免、5 分钟争议）归出包方
    first = next(row for row in h['stays'] if row['due_cents'] == 700)
    assert first['split'] == {OUT: 700, INC: 0}
    crossing = next(row for row in h['stays'] if row['due_cents'] == 60)
    assert crossing['split'] == {OUT: 0, INC: 60}


def test_undirected_receipt_goes_whole_to_pending(handover):
    h = handover.settlement['handover']
    receipt_0009 = next(p for p in h['placements'] if p['receipt'] == '0009')
    # 没写给谁的部分付款回执：整笔待分，不替任何一家猜配
    assert (receipt_0009['code'], receipt_0009['recognized_cents'],
            receipt_0009['pending_cents'], receipt_0009['payee']) == ('UNDIRECTED', 0, 520, None)
    assert all(p['code'] != 'UNDIRECTED' or p['recognized_cents'] == 0 for p in h['placements'])


def test_directed_overflow_never_covers_other_party_debt(handover):
    h = handover.settlement['handover']
    overflow = next(p for p in h['placements'] if p['receipt'] == '0014')
    # 出包方在 0007 已认 850，剩余应收 350；0014 注明给出包方但付了 400
    assert (overflow['code'], overflow['recognized_cents'],
            overflow['pending_cents']) == ('DIRECTED_OVERFLOW', 350, 50)
    accounts = {a['party']: a for a in h['accounts']}
    # 任一家已认实收都不得超过其应收
    assert all(a['recognized_cents'] <= a['due_cents'] for a in h['accounts'])
    # 溢额留在待分池，不能拿去抵接包方的 140 分欠款
    assert accounts[INC]['outstanding_cents'] == 140
    assert accounts[OUT]['outstanding_cents'] == 0


def test_unknown_payee_and_orphan_receipts_are_traceable(handover):
    h = handover.settlement['handover']
    by_id = {p['receipt']: p for p in h['placements']}
    assert by_id['0013']['code'] == 'PAYEE_UNKNOWN' and by_id['0013']['pending_cents'] == 40
    assert by_id['0011']['code'] == 'ORPHAN_FUND' and by_id['0011']['pending_cents'] == 140


def test_fund_conservation_between_recognized_and_pending(handover):
    h = handover.settlement['handover']
    money = handover.settlement['money']
    assert h['recognized_total_cents'] == 2100 and h['pending_cents'] == 750
    # 已认实收 + 待分金额 = 全部有效回执（关联实收 + 游离回执）
    assert h['recognized_total_cents'] + h['pending_cents'] == money['paid_cents'] + money['orphan_cents']
    assert sum(a['recognized_cents'] for a in h['accounts']) == h['recognized_total_cents']


def test_rerun_produces_identical_allocation(client):
    first = client.post('/api/dockets', json={'sample': 'handover'}).json()
    second = client.post('/api/dockets', json={'sample': 'handover'}).json()
    # 同一批重跑复用同一编号，不产生第二笔划款
    assert first['key'] == second['key'] and second['reused'] is True
    record = client.get('/api/dockets/' + first['key']).json()
    assert client.get('/api/dockets/' + first['key']).json() == record


def test_appeal_carries_handover_invariants(client):
    key = client.post('/api/dockets', json={'sample': 'handover'}).json()['key']
    exported = client.get('/api/export/' + key).json()
    assert exported['evidence']['settlement']['handover']['pending_cents'] == 750
    issue(exported)  # 交接守恒校验通过


def test_tampered_handover_split_is_rejected(client):
    key = client.post('/api/dockets', json={'sample': 'handover'}).json()['key']
    doc = client.get('/api/export/' + key).json()
    doc['evidence']['settlement']['handover']['stays'][0]['split'][INC] += 10
    doc['manifest']['evidence_sha256'] = digest(doc['evidence'])
    with pytest.raises(DocketError) as err:
        issue(doc)
    assert err.value.code == 'HANDOVER_SPLIT_UNBALANCED'


def test_over_claim_is_rejected_at_signing(client):
    key = client.post('/api/dockets', json={'sample': 'handover'}).json()['key']
    doc = client.get('/api/export/' + key).json()
    doc['evidence']['settlement']['handover']['accounts'][0]['recognized_cents'] += 1
    doc['manifest']['evidence_sha256'] = digest(doc['evidence'])
    with pytest.raises(DocketError) as err:
        issue(doc)
    assert err.value.code == 'HANDOVER_OVER_CLAIM'


def test_fund_unbalance_is_rejected_at_signing(client):
    key = client.post('/api/dockets', json={'sample': 'handover'}).json()['key']
    doc = client.get('/api/export/' + key).json()
    doc['evidence']['settlement']['handover']['pending_cents'] += 1
    doc['manifest']['evidence_sha256'] = digest(doc['evidence'])
    with pytest.raises(DocketError) as err:
        issue(doc)
    assert err.value.code == 'HANDOVER_FUND_UNBALANCED'


def test_no_handover_keeps_legacy_behavior(dirty):
    assert dirty.settlement['handover'] is None
    assert dirty.settlement['money'] == {'due_cents': 2240, 'paid_cents': 2310,
                                         'difference_cents': 70, 'orphan_cents': 140}


def test_split_stay_minute_boundaries(handover):
    stay = next(s for s in handover.pricing['stays'] if s['due_cents'] == 700)
    parts = split_stay(stay, handover.survey.handover)
    # 停靠 23:40–00:25，交接 00:20：出包方 40 分钟、接包方 5 分钟（争议）
    assert sum(g['minutes'] for g in parts[OUT]['segments']) == 40
    assert sum(g['minutes'] for g in parts[INC]['segments']) == 5
    assert parts[OUT]['cents'] + parts[INC]['cents'] == stay['due_cents']


def test_handover_validation_rejects_bad_window():
    from app.source.structure import Survey
    base = {'name': '区', 'start': '2026-11-30T23:30+08:00',
            'end': '2026-12-01T01:30+08:00', 'berths': ['B-01']}
    bad = deepcopy(base) | {'handover': {'at': '2026-11-30T23:30+08:00',
                                         'outgoing': '甲', 'incoming': '乙'}}
    with pytest.raises(DocketError) as err:
        Survey.build(bad)
    assert err.value.code == 'HANDOVER_BAD'
    same = deepcopy(base) | {'handover': {'at': '2026-12-01T00:00+08:00',
                                          'outgoing': '同一家', 'incoming': '同一家'}}
    with pytest.raises(DocketError) as err:
        Survey.build(same)
    assert err.value.code == 'HANDOVER_BAD'


def test_payee_column_optional_for_legacy_sheets(dirty):
    # 旧样例没有 payee 列，回执仍可正常关联，payee 为空
    receipts = [r for line in dirty.settlement['lines'] for r in line['receipts']]
    assert receipts and all(r.get('payee') is None for r in receipts)


def test_missing_payee_column_under_handover_claims_nothing(client):
    import csv
    import io
    from app.source.packet import bundle as load_sample
    files = load_sample('handover')
    rows = list(csv.reader(io.StringIO(files['payments'].removeprefix('﻿'))))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(rows[0][:5])
    writer.writerows(row[:5] for row in rows[1:])
    files['payments'] = buf.getvalue()
    key = client.post('/api/dockets', json={'bundle': files}).json()['key']
    handover_view = client.get('/api/dockets/' + key).json()['evidence']['settlement']['handover']
    # 没有任何回执注明收方：两家都不得猜配，已认实收为零，全部有效金额进待分池
    assert all(a['recognized_cents'] == 0 for a in handover_view['accounts'])
    assert handover_view['pending_cents'] == 2850
    assert set(p['code'] for p in handover_view['placements']) <= {'UNDIRECTED', 'ORPHAN_FUND'}
