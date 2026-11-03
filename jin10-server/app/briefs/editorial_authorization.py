"""Team-scoped adoption of an exact style-pack version, without global approval."""


def effective_mode(team_settings,configured,explicit=None):
    if explicit is not None:return explicit
    if configured=='off':return 'off'
    choice=(team_settings or {}).get('brief_editorial') or {}
    return 'active' if choice.get('mode')=='sample' else configured


def review_basis(pack,category,team_settings):
    review=pack.get('review') or {}
    if pack.get('status')=='approved' and review.get('approved_by') and review.get('reviewed_at'):
        return 'reviewed_pack'
    choice=(team_settings or {}).get('brief_editorial') or {}
    authorization=(choice.get('packs') or {}).get(category) or {}
    if (choice.get('mode')=='sample' and (authorization.get('digest')==pack.get('digest')
            or authorization.get('content_digest')==content_digest(pack))
            and authorization.get('authorized_by') and authorization.get('authorized_at')
            and authorization.get('source') in ('explicit_user_instruction','team_admin_choice')):
        return 'team_authorized_pack'
    return None


def content_digest(pack):
    from .sample_story import sha
    return sha({k:v for k,v in pack.items() if k not in ('digest','review','status')})


def resolve_pack(category,team_settings):
    """Keep the last adopted pack active while a changed pack awaits adoption."""
    from .editorial_suite import load_pack,_checked_pack
    current=load_pack(category)
    if review_basis(current,category,team_settings):return current,None
    saved=(((team_settings or {}).get('brief_editorial') or {}).get('packs') or {}).get(category) or {}
    snapshot=saved.get('snapshot')
    if snapshot:
        if category=='gpu_rental':
            from .gpu_editorial import _checked_pack as checked
            previous=checked(snapshot)
        else:previous=_checked_pack(category,snapshot)
        if review_basis(previous,category,team_settings):return previous,current['version']
    raise ValueError('sample_pack_not_adopted')


def adoption(user_id,*,source='team_admin_choice'):
    from .editorial_suite import SUPPORTED_CATEGORIES,load_pack
    from app.db.models import utcnow
    now=utcnow().isoformat();packs={}
    for category in SUPPORTED_CATEGORIES:
        pack=load_pack(category)
        packs[category]={'digest':pack['digest'],'content_digest':content_digest(pack),
            'version':pack['version'],'snapshot':pack,'authorized_by':str(user_id),
            'authorized_at':now,'source':source}
    return {'mode':'sample','writer':'sample_story_v2','authorized_at':now,'source':source,'packs':packs}
