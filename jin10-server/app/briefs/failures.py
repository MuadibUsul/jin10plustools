"""Stable production failure details without changing legacy task states."""
import json
from pathlib import Path


class SourceUnavailable(ValueError):
    code = 'source_unavailable'
    retryable = True

    def __init__(self, message, *, source=None):
        super().__init__(message)
        self.source = source


class SourceContractError(ValueError):
    code = 'source_contract_error'
    retryable = False

    def __init__(self, message, *, source=None):
        super().__init__(message)
        self.source = source


class MissingObservations(ValueError):
    retryable = True

    def __init__(self, message, *, source, missing_dates, latest_date=None, publication_checked=False):
        super().__init__(message)
        self.source = source
        self.missing_dates = sorted(set(missing_dates))
        self.latest_date = latest_date
        self.code = ('awaiting_source' if publication_checked and (latest_date is None or
                     max(self.missing_dates) > latest_date) else 'required_data_missing')


def failure_details(error, report=None):
    report = report or {}
    message = str(error or report.get('message') or '')[:500]
    code = report.get('code')
    if not code:
        if any(word in message for word in ('稿件已更新', '生成期间稿件已更新')):
            code = 'version_conflict'
        elif any(word in message for word in ('尚未更新', '尚未发布')):
            code = 'awaiting_source'
        elif any(word in message for word in ('缺少', '缺失', '历史不足')):
            code = 'required_data_missing'
        elif any(word in message.lower() for word in ('timeout', 'http ', '超时', '连接', 'acquisition failed')):
            code = 'source_unavailable'
        else:
            code = 'execution_failed'
    actions = {
        'awaiting_source': '等待来源发布后重试，核对实际数据日',
        'required_data_missing': '核查原始归档和必要交易日，补齐经核验数据后重试',
        'source_unavailable': '检查来源连通性及最近抓取记录后重试',
        'source_contract_error': '对照原始响应、解析规则和字段单位，定位采集程序或来源格式变化',
        'version_conflict': '打开最新稿件，核对后重新操作',
        'execution_failed': '查看本次任务过程及服务端日志，定位失败步骤',
    }
    return {'code': code, 'message': message, 'source': report.get('source'),
            'missing_dates': report.get('missing_dates', []),
            'latest_date': report.get('latest_date'),
            'retryable': report.get('retryable', code != 'version_conflict'),
            'action': actions.get(code, actions['execution_failed'])}


def job_failure(job, root):
    if not job.error:
        return None
    path = Path(root) / 'runs' / str(job.id) / 'error.json'
    try:
        report = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size <= 64000 else {}
    except (OSError, ValueError):
        report = {}
    # A previous attempt must never supply diagnostics for a later error.
    if report.get('message') != job.error:
        report = {}
    return failure_details(job.error, report)
