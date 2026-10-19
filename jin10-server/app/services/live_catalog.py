"""Curated public single-player URLs; never embed a multi-player directory page."""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from app.briefs.policy import CHINA
from app.db.models import BriefJob


def now():
    return datetime.now(CHINA)


def catalog(team):
    data=json.loads((Path(__file__).parents[1]/'assets/live-catalog.json').read_text(encoding='utf-8'))
    saved=team.settings or {}
    for channel in data['channels']:
        if channel['id'] in saved.get('live_urls',{}):
            channel['url']=saved['live_urls'][channel['id']]
            channel['note']='团队自定义播放器' if channel['url'] else '已停用'
            channel.pop('select_variant',None)
        channel['available']=bool(channel['url'])
        url=urlsplit(channel['url'])
        channel['access']='youtube' if url.hostname in ('www.youtube.com','youtube.com','youtu.be','www.youtube-nocookie.com') else 'direct' if url.path.lower().endswith('.m3u8') else 'embedded'
    if saved.get('live_url'):
        data['channels'].append(dict(id='custom',category='markets',name='自定义直播',url=saved['live_url'],source_url=saved['live_url'],note='团队自定义播放器',available=True))
    return data


def player_url(channel):
    """Smithsonian advertises dead low-resolution variants; select its live HD rendition.

    Resolve the public master afresh rather than storing its short-lived session URL.
    This is metadata only: no video is fetched or activated on the server.
    """
    from urllib.parse import urljoin
    import httpx
    url=channel['url']
    if channel.get('select_variant')!='first':return url
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('nzp-wowza01.si.edu','nzp-wowza02.si.edu'):
        raise ValueError('直播来源配置不合法')
    with httpx.Client(timeout=8,follow_redirects=False) as client:
        with client.stream('GET',url) as response:
            response.raise_for_status();raw=b''
            for chunk in response.iter_bytes():
                raw+=chunk
                if len(raw)>64000:raise ValueError('播放器清单过大')
    text=raw.decode('utf-8-sig')
    if not text.startswith('#EXTM3U'):raise ValueError('直播清单暂不可用')
    variant=next((line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')),None)
    resolved=urljoin(url,variant or '')
    target=urlsplit(resolved)
    if target.scheme!='https' or target.netloc!=parsed.netloc or not target.path.endswith('.m3u8'):
        raise ValueError('直播清单暂不可用')
    return resolved


def state(db,team_id):
    stamp=now()
    busy=db.scalar(select(BriefJob.id).where(BriefJob.team_id==team_id,
        BriefJob.issue_date==stamp.date(),BriefJob.status.in_(['queued','running','waiting'])).limit(1)) is not None
    return dict(server_time=stamp.isoformat(),allowed=8<=stamp.hour<23 and not busy,
        busy=busy,closes_at=stamp.replace(hour=23,minute=0,second=0,microsecond=0).isoformat(),
        reason='快讯正在生成，已返回处理动态' if busy else '' if 8<=stamp.hour<23 else '直播开放时间：北京时间 8:00—23:00')
