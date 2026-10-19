from fastapi import APIRouter, HTTPException

from app.db.models import Team
from app.deps import CurrentUser, DbSession
from app.services.live_catalog import catalog, state, player_url

router=APIRouter(prefix='/api/live',tags=['live'])


@router.get('/state')
def live_state(db:DbSession,user:CurrentUser):
    return state(db,user.team_id)


@router.get('/catalog')
def read(db:DbSession,user:CurrentUser):
    data=catalog(db.get(Team,user.team_id))
    for item in data['channels']:
        item.pop('url',None)
    return {**data,**state(db,user.team_id)}


@router.get('/play/{channel_id}')
def play(channel_id:str,db:DbSession,user:CurrentUser):
    policy=state(db,user.team_id)
    if not policy['allowed']:
        raise HTTPException(409,policy['reason'])
    channel=next((item for item in catalog(db.get(Team,user.team_id))['channels'] if item['id']==channel_id),None)
    if channel is None:
        raise HTTPException(404,'未找到直播')
    if not channel['available']:
        raise HTTPException(422,channel['note'])
    try:
        channel['url']=player_url(channel)
    except Exception:
        raise HTTPException(502,'直播来源暂不可用，请稍后重试') from None
    # A generation job or the 23:00 boundary can intervene during manifest lookup.
    policy=state(db,user.team_id)
    if not policy['allowed']:raise HTTPException(409,policy['reason'])
    return {**channel,**policy}
