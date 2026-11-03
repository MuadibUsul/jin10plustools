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
    if (choice.get('mode')=='sample' and authorization.get('digest')==pack.get('digest')
            and authorization.get('authorized_by') and authorization.get('authorized_at')
            and authorization.get('source')=='explicit_user_instruction'):
        return 'team_authorized_pack'
    return None
