from datetime import date

import httpx
import pytest

from app.briefs.public_compute import fetch_visible, ORIGIN


def page(metric, stamp, price, change, *, up=True, unit=None):
    heading = {'token': 'LLM Token Expenditure Index Family',
               'a100': 'Silicon Data A100 Rental Price Index',
               'h100': 'Silicon Data H100 Rental Price Index',
               'b200': 'Silicon Data B200 Rental Price Index'}[metric]
    unit = unit or ('USD / M tokens' if metric == 'token' else 'USD / GPU-hour')
    rotation = 'rotate-180' if up else ''
    return f'''<html><h1>{heading}</h1><div><p>As of {stamp}</p>
      <div><p>{price}</p><p>{unit}</p></div>
      <div><svg class="{rotation}"><path d="M4 12 0.5 6.8h2.3V0h2.4v6.8h2.3Z"/></svg>
      <p>{change}% (7D)</p></div></div></html>'''


def client_for(pages, robots='User-agent: *\nAllow: /\n', code=200):
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text=robots)
        name = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(code, text=pages[name], headers={'content-type': 'text/html'})
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=ORIGIN)


def test_visible_token_precision_and_direction_are_exactly_what_page_shows():
    pages = {'llm-token-expenditure-index': page('token', 'Oct 20, 2026', '0.96', '4.1', up=False)}
    with client_for(pages) as client:
        result = fetch_visible('token_cost', today=date(2026, 10, 22), client=client)
    assert result['data_date'] == '2026-10-20'
    assert result['readings'][0]['price'] == '0.96'
    assert result['readings'][0]['precision'] == 2
    assert result['readings'][0]['week_change_pct'] == '-4.1'


def test_gpu_must_have_three_cards_on_one_day():
    pages = {metric: page(metric, 'Oct 21, 2026', price, change)
             for metric, price, change in [('a100', '1.55', '0.6'),
                                           ('h100', '2.86', '10.0'), ('b200', '6.14', '6.6')]}
    with client_for(pages) as client:
        result = fetch_visible('gpu_rental', today=date(2026, 10, 22), client=client)
    assert [(r['metric'], r['price']) for r in result['readings']] == [
        ('a100', '1.55'), ('h100', '2.86'), ('b200', '6.14')]
    pages['b200'] = page('b200', 'Oct 20, 2026', '6.14', '6.6')
    with client_for(pages) as client, pytest.raises(ValueError, match='不一致'):
        fetch_visible('gpu_rental', today=date(2026, 10, 22), client=client)


def test_robots_and_rejection_are_terminal():
    pages = {'llm-token-expenditure-index': page('token', 'Oct 20, 2026', '0.96', '4.1')}
    with client_for(pages, robots='User-agent: *\nDisallow: /products/silicon-index/') as client:
        with pytest.raises(ValueError, match='robots'):
            fetch_visible('token_cost', today=date(2026, 10, 22), client=client)
    with client_for(pages, code=429) as client:
        with pytest.raises(ValueError, match='拒绝'):
            fetch_visible('token_cost', today=date(2026, 10, 22), client=client)


def test_wrong_unit_or_unknown_direction_must_fail():
    wrong = page('token', 'Oct 20, 2026', '0.96', '4.1', unit='USD / GPU-hour')
    with client_for({'llm-token-expenditure-index': wrong}) as client:
        with pytest.raises(ValueError, match='单位'):
            fetch_visible('token_cost', today=date(2026, 10, 22), client=client)
    wrong = page('token', 'Oct 20, 2026', '0.96', '4.1').replace('M4 12 0.5 6.8', 'M0 0')
    with client_for({'llm-token-expenditure-index': wrong}) as client:
        with pytest.raises(ValueError, match='方向'):
            fetch_visible('token_cost', today=date(2026, 10, 22), client=client)
