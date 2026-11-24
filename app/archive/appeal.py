from app.archive.canonical import digest
from app.problems import DocketError


def issue(packet):
    evidence=packet['evidence']
    if digest(evidence)!=packet['manifest']['evidence_sha256']:
        raise DocketError('DIGEST_BAD','证据内容与留档摘要不同',409)
    account=evidence['settlement']
    for row in account['berths']:
        remainder=row['scheduled']-sum(row[key] for key in ('vacant','disputed','free','base','upgrade'))
        if remainder:
            raise DocketError('BERTH_UNBALANCED','泊位分钟勾稽未平',409)
    money=account['money']
    if money['paid_cents']-money['due_cents']!=money['difference_cents'] or any(
        r['paid_cents']-r['due_cents']!=r['difference_cents'] for r in account['lines']
    ):
        raise DocketError('MONEY_UNBALANCED','收费差价勾稽未平',409)
    handover=account.get('handover')
    if handover:
        _check_handover(handover,money)
    return {'document':'泊位理赔证据','minutes_balance':0,'sha256':digest(packet),**packet}


def _check_handover(handover,money):
    # 两家应收之和必须与原单应收相等（首免/争议跨交接同样按承管时段切分）
    if sum(a['due_cents'] for a in handover['accounts']) != money['due_cents']:
        raise DocketError('HANDOVER_DUE_UNBALANCED','交接两家应收之和与原单不符',409)
    # 每单切分金额之和必须等于该单原应收
    if any(sum(row['split'].values()) != row['due_cents'] for row in handover['stays']):
        raise DocketError('HANDOVER_SPLIT_UNBALANCED','逐单交接切分未守恒',409)
    recognized=sum(a['recognized_cents'] for a in handover['accounts'])
    # 任何一家的已认实收都不得超过其应收（定向超额不得抵另一家欠款）
    if any(a['recognized_cents'] > a['due_cents'] for a in handover['accounts']):
        raise DocketError('HANDOVER_OVER_CLAIM','承包商认领超过其应收',409)
    # 已认实收 + 待分金额 = 全部有效回执（关联实收 + 游离回执）
    if recognized + handover['pending_cents'] != money['paid_cents'] + money['orphan_cents']:
        raise DocketError('HANDOVER_FUND_UNBALANCED','交接认领与待分金额未守恒',409)
    if recognized != handover['recognized_total_cents']:
        raise DocketError('HANDOVER_FUND_UNBALANCED','交接认领合计不一致',409)
