"""Only human ratings for identical prose may enter the editorial scorecard."""
from copy import deepcopy
import json
from pathlib import Path
import runpy

import pytest


score = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/evaluate_gpu_editorial.py'))[
    'incorporate_reviews']


def collection():
    return [{'review_id': 'one', 'A': {'title': '标题A', 'body': '正文A'},
             'B': {'title': '标题B', 'body': '正文B'},
             'human_review': {'preferred': None, 'A_readiness': None,
                              'B_readiness': None, 'reason': None}}]


def apply(tmp_path, edit):
    review = collection()
    submitted = {'pack_digest': 'pack-one', 'cases': deepcopy(review)}
    edit(submitted)
    path = tmp_path / 'ratings.json'
    path.write_text(json.dumps(submitted), encoding='utf-8')
    report = {'pack_digest': 'pack-one', 'summary': {'style_accepted': None}}
    key = [{'review_id': 'one', 'mapping': {'A': 'candidate', 'B': 'baseline'}}]
    score(path, report, review, key)
    return report


def test_empty_reviews_cannot_be_counted_as_editorial_acceptance(tmp_path):
    report = apply(tmp_path, lambda data: None)
    assert report['summary']['human_direct_acceptance_rate'] is None
    assert report['summary']['human_candidate_rated_count'] == 0
    assert report['summary']['style_accepted'] is None


def test_only_completed_matching_reviews_are_scored_without_approving_pack(tmp_path):
    def edit(data):
        data['cases'][0]['human_review'].update(preferred='A', A_readiness='direct',
                                               B_readiness='rewrite', reason='具体卡种对照更清楚')
    report = apply(tmp_path, edit)
    assert report['summary']['human_candidate_rated_count'] == 1
    assert report['summary']['human_direct_acceptance_rate'] == 1
    assert report['summary']['human_review_counts']['baseline'] == {'rewrite': 1}
    assert report['summary']['style_accepted'] is None


@pytest.mark.parametrize('edit', [
    lambda data: data.update(pack_digest='an-older-pack'),
    lambda data: data['cases'][0]['A'].update(body='已经修改的正文'),
    lambda data: data['cases'][0]['human_review'].update(A_readiness='assumed-good'),
])
def test_stale_or_invalid_reviews_are_rejected(tmp_path, edit):
    with pytest.raises(ValueError):
        apply(tmp_path, edit)
