import csv
from hashlib import sha256
from io import StringIO
import json

from app.problems import DocketError
from app.source.structure import PAYMENT_OPTIONAL, SCHEMAS

BOM = '\ufeff'


def scan(text,name):
    reader=csv.reader(StringIO(text.removeprefix(BOM),newline=''),strict=True)
    expected=SCHEMAS[name].split(',')
    optional=PAYMENT_OPTIONAL.get(name,())
    rows,isolated=[],[]
    repeats=submitted=0
    try:
        header=next(reader,None)
        if header is None:
            raise DocketError('SHEET_EMPTY',f'{name} 文件为空')
        if any(column not in expected+list(optional) for column in header) or any(
            column not in header for column in expected):
            raise DocketError('SCHEMA_BAD',f'{name} 的字段与定义不符')
        width=len(header)
        while True:
            line=reader.line_num+1
            cells=next(reader,None)
            if cells is None:
                break
            submitted+=1
            mark={'line':line,'source':name}
            if cells==header:
                repeats+=1
            elif not any(value.strip() for value in cells) or len(cells)!=width:
                checksum=sha256(json.dumps(cells,ensure_ascii=False).encode()).hexdigest()
                isolated.append(mark | {'code':'BLANK' if not any(value.strip() for value in cells) else 'WIDTH',
                                        'cells':len(cells),'row_sha256':checksum})
            else:
                fields=dict(zip(header,cells,strict=True))
                for column in optional:
                    fields[column]=fields.get(column,'').strip() or None
                rows.append(mark | {'fields':fields})
    except csv.Error:
        raise DocketError('CSV_BAD',f'{name} 第 {reader.line_num} 行 CSV 无法读取') from None
    return rows,{'source':name,'submitted':submitted,'parsed':len(rows),'headers':repeats,'isolation':isolated,
                 'state':'rows' if rows else 'no_usable_rows' if submitted else 'header_only'}
