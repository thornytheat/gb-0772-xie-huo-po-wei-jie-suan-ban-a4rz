from app.curb.timeaxis import M, minute

# 回执分账结论
DIRECTED = 'DIRECTED'                    # 注明收方且未超过其应收，全额认给该方
DIRECTED_OVERFLOW = 'DIRECTED_OVERFLOW'  # 注明收方但超过其应收，仅认应收额，溢额待分
UNDIRECTED = 'UNDIRECTED'                # 未注明收方，整笔进待分池
ORPHAN_FUND = 'ORPHAN_FUND'              # 未关联停靠的游离回执，未注明收方，整笔待分
PAYEE_UNKNOWN = 'PAYEE_UNKNOWN'          # 收方不是交接两家，无法认领


def split_stay(stay, handover):
    """按交接时刻切分一次停靠的收费分段。

    分段是 [start,end) 的分钟序列，逐分钟归给当时承管的承包商；首免与
    争议分钟同样按所在时段归属。两家分段金额之和等于原单应收。
    """
    outgoing, incoming = handover['outgoing'], handover['incoming']
    cut_at = handover['at']
    parts = {party: {'cents': 0, 'minutes': 0, 'segments': []}
             for party in (outgoing, incoming)}
    for segment in stay['segments']:
        begin, end = minute(segment['start']), minute(segment['end'])
        cuts = (
            (outgoing, begin, min(end, cut_at)),
            (incoming, max(begin, cut_at), end),
        )
        for party, part_begin, part_end in cuts:
            minutes = int((part_end - part_begin) / M) if part_end > part_begin else 0
            if minutes <= 0:
                continue
            cents = minutes * segment['rate_cents']
            bucket = parts[party]
            bucket['cents'] += cents
            bucket['minutes'] += minutes
            bucket['segments'].append({
                'code': segment['code'], 'minutes': minutes,
                'rate_cents': segment['rate_cents'], 'cents': cents,
                'day': segment['day'],
                'start': part_begin.isoformat(), 'end': part_end.isoformat(),
            })
    return parts


def handover_account(settlement, handover, priced_stays):
    """生成两家承包商的交接分账。

    守恒关系：两家应收之和 = 原单应收；已认实收 + 待分金额 = 全部有效
    回执金额。定向给一家但超过其应收的溢额留在待分池，不得用于抵扣
    另一家欠款；未注明收方的回执整笔进待分池，不替任何一家猜配。
    纯函数确定性计算，同批输入重跑得到同一笔分配，不重复划款。
    """
    if not handover:
        return None
    parties = (handover['outgoing'], handover['incoming'])
    stays_by_id = {stay['stay']: stay for stay in priced_stays}
    due = {party: 0 for party in parties}
    per_stay = []
    for line in settlement['lines']:
        stay = stays_by_id[line['stay']]
        parts = split_stay(stay, handover)
        for party in parties:
            due[party] += parts[party]['cents']
        per_stay.append({
            'stay': line['stay'], 'berth': line['berth'], 'arrival': line['arrival'],
            'due_cents': line['due_cents'],
            'split': {party: parts[party]['cents'] for party in parties},
            'segments': {party: parts[party]['segments'] for party in parties},
        })

    recognized = {party: 0 for party in parties}
    pending = 0
    placements = []

    def place(receipt, orphan):
        nonlocal pending
        party = receipt.get('payee')
        total = receipt['cents']
        if party is not None and party not in parties:
            pending += total
            code = PAYEE_UNKNOWN
            accepted = 0
        elif party is None:
            pending += total
            code = ORPHAN_FUND if orphan else UNDIRECTED
            accepted = 0
        else:
            headroom = max(0, due[party] - recognized[party])
            accepted = min(total, headroom)
            overflow = total - accepted
            pending += overflow
            recognized[party] += accepted
            code = DIRECTED if overflow == 0 else DIRECTED_OVERFLOW
        placements.append({
            'receipt': receipt['fields']['receipt'], 'line': receipt['line'],
            'payee': party, 'cents': total, 'recognized_cents': accepted,
            'pending_cents': total - accepted, 'code': code,
            'orphan': orphan,
        })

    for line in settlement['lines']:
        for receipt in line['receipts']:
            place(receipt, orphan=False)
    for receipt in settlement['orphans']:
        place(receipt, orphan=True)

    return {
        'at': handover['at'].isoformat(),
        'parties': list(parties),
        'accounts': [{
            'party': party,
            'due_cents': due[party],
            'recognized_cents': recognized[party],
            'outstanding_cents': due[party] - recognized[party],
        } for party in parties],
        'pending_cents': pending,
        'due_total_cents': sum(due.values()),
        'recognized_total_cents': sum(recognized.values()),
        'placements': placements,
        'stays': per_stay,
    }
