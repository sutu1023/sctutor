"""Shared half-open date windows and privacy-scoped, spreadsheet-safe exports."""
import csv
import io
import math
import time
from dataclasses import dataclass

from .config import UserError

DAY = 86400


@dataclass(frozen=True)
class Window:
    start: float
    end: float

    @classmethod
    def make(cls, start=None, end=None, now=None):
        now = time.time() if now is None else now
        if (start is None) != (end is None):
            raise UserError("请同时指定开始和结束时间。")
        start, end = (now - 7 * DAY, now) if start is None else (start, end)
        if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end or end-start > 366*DAY:
            raise UserError("日期范围须先后有序且不超过366天。")
        return cls(start, end)


def csv_text(rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        safe = []
        for value in row:
            text = "暂无数据" if value is None else str(value)
            text = ''.join(c for c in text if ord(c) >= 32 or c in '\n\t')
            if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\n')):
                text = "'" + text
            safe.append(text)
        writer.writerow(safe)
    return '\ufeff' + buffer.getvalue()


def export_rows(data, kind):
    s = data['summary'] if kind == 'report' else data
    rows = [['SC-Tutor', kind], ['开始（UTC时间戳，包含）', s['start']],
            ['结束（UTC时间戳，不包含）', s['end']], ['生成时间', time.time()],
            ['说明', '掌握为当前快照，不是所选期间历史状态；仅本机演示记录'], ['指标', '值']]
    fields = ('mastered', 'total', 'first_correct', 'attempted', 'accuracy', 'review_concepts') if kind == 'report' else (
        'questions', 'ended', 'unfinished', 'failed_questions', 'average_end_level', 'l4_questions', 'l4_rate',
        'checks', 'rejected', 'check_errors', 'rejection_rate', 'unattributed')
    rows.extend([field, s[field]] for field in fields)
    if kind == 'report':
        rows.append(['知识点', '当前概率', '独立证据数', '最近练习', '期间提问', '复习建议'])
        rows.extend([x['name'], x['probability'], x['observations'], x['last_practiced'], x['asked_this_week'], x['reason']] for x in data['concepts'])
    else:
        rows.append(['疑难知识点', '未解决问题数'])
        rows.extend([x['name'], x['count']] for x in data['difficulties'])
        rows.append(['检查时间', '级别', '程序类别'])
        rows.extend([x['created'], x['level'], x['category']] for x in data['recent_checks'])
    return rows
