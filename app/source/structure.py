from dataclasses import dataclass
from datetime import datetime
import re

from app.curb.timeaxis import M, minute
from app.problems import DocketError

SCHEMAS={
    'sensors':'event,berth,plate,sensor,start,end',
    'bookings':'reservation,berth,plate,effective,start,end,state',
    'payments':'receipt,berth,plate,arrival,cents',
}
# 承包商交接为可选项：启用时回执可带 payee 列注明收方
PAYMENT_OPTIONAL = {'payments': ('payee',)}
PARTY_PATTERN = re.compile(r'[A-Za-z0-9一-鿿._-]{1,30}')


@dataclass(frozen=True)
class Survey:
    name: str
    start: datetime
    end: datetime
    berths: tuple[str,...]
    handover: dict|None = None

    @classmethod
    def build(cls, obj):
        try:
            start,end=minute(obj['start']),minute(obj['end'])
            name=obj['name']
            ids=obj['berths']
            if (not isinstance(name,str) or not name.strip() or len(name)>70 or not start<end<=start+2880*M
                or not isinstance(ids,list) or not 1<=len(ids)<=12 or any(
                    not isinstance(x,str) or not re.fullmatch(r'[A-Z0-9-]{1,20}',x) for x in ids)
                or len(set(ids))!=len(ids)):
                raise ValueError
        except (KeyError,TypeError,ValueError):
            raise DocketError('SURVEY_BAD','清算区间或泊位列表错误') from None
        handover=cls._handover(obj.get('handover'),start,end)
        return cls(name,start,end,tuple(ids),handover)

    @staticmethod
    def _handover(raw,start,end):
        if raw is None:
            return None
        try:
            at=minute(raw['at'])
            outgoing,incoming=raw['outgoing'],raw['incoming']
            if not (start < at < end
                    and isinstance(outgoing,str) and isinstance(incoming,str)
                    and PARTY_PATTERN.fullmatch(outgoing) and PARTY_PATTERN.fullmatch(incoming)
                    and outgoing != incoming):
                raise ValueError
        except (KeyError,TypeError,ValueError,AttributeError):
            raise DocketError('HANDOVER_BAD','交接时刻或承包商名称错误') from None
        return {'at':at,'outgoing':outgoing,'incoming':incoming}
