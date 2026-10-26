"""Layout contract for the Token and GPU product charts."""
from __future__ import annotations

import math
import re
import warnings
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pytest
from PIL import Image

from matplotlib.patheffects import SimpleLineShadow

from app.briefs.ai_compute_charts import (
    A100_COLOR, B200_COLOR, H100_COLOR, TOKEN_COLOR, gpu_chart, token_chart,
)
from app.services.image_delivery import MAX_BYTES, compose_image, encode_image


def _series(days, **columns):
    start = date(2025, 1, 1)
    rows = []
    for index in range(days):
        row = {'date': (start + timedelta(days=index)).isoformat()}
        for key, value in columns.items():
            row[key] = value(index) if callable(value) else value
        rows.append(row)
    return rows


@contextmanager
def _drawn(fn, rows, folder, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with patch('matplotlib.pyplot.close'):
            path = fn(rows, folder, **kwargs)
            figure = plt.gcf()
            figure.canvas.draw()
        yield figure, path, caught
    plt.close('all')


def _texts(figure):
    return [text.get_text() for text in figure.texts]


def test_long_history_keeps_a_thin_horizontal_axis_and_footer(tmp_path):
    rows = _series(220, token_price=lambda index: 1.1 + (index % 17) * 0.04)
    folder = tmp_path / 'token'
    folder.mkdir()
    with _drawn(token_chart, rows, folder, notes='单位以百万Token计。') as (figure, path, caught):
        axis = figure.axes[0]
        labels = [tick.get_text() for tick in axis.get_xticklabels() if tick.get_text()]
        renderer = figure.canvas.get_renderer()
        axis_box = axis.get_window_extent(renderer)
        label_boxes = [tick.get_window_extent(renderer) for tick in axis.get_xticklabels() if tick.get_text()]
        legend_box = figure.legends[0].get_window_extent(renderer)
        footer = [text for text in figure.texts if text.get_text().startswith('数据来源')][0]
        footer_box = footer.get_window_extent(renderer)

        assert len(figure.axes) == 1
        assert axis.get_yscale() == 'linear' and axis.get_ylim()[0] == 0
        assert not axis.texts
        assert 1 < len(labels) <= 7
        assert all(tick.get_rotation() == 0 for tick in axis.get_xticklabels())
        assert all(re.fullmatch(r'\d{4}/\d{1,2}', text) for text in labels)
        assert axis_box.y0 - min(box.y0 for box in label_boxes) < 18
        assert 0 < min(box.y0 for box in label_boxes) - legend_box.y1 < 24
        assert abs((legend_box.x0 + legend_box.x1) / 2 - (axis_box.x0 + axis_box.x1) / 2) < 8
        assert footer_box.y1 < legend_box.y0
        assert axis_box.height / figure.bbox.height > 0.62
        joined = '\n'.join(_texts(figure))
        assert '最近30' not in joined
        assert '美元/百万Token' in joined
        assert '数据来源：Silicon Data · LLM Token Expenditure Index' in joined
        assert '单位以百万Token计。' in joined
        assert '七日' in joined
        assert not any('Glyph' in str(item.message) for item in caught)
        assert path.name == 'AI算力CPI.png' and path.is_file()

        delivered = compose_image(path)
        content, suffix, media = encode_image(delivered)
        assert len(content) <= MAX_BYTES
        assert suffix == '.png' and media == 'image/png'
        with Image.open(path) as original:
            assert delivered.height > original.height
            assert delivered.width == original.width


def test_gpu_chart_is_one_panel_with_real_gaps_and_card_legend(tmp_path):
    def price(offset):
        return lambda index: None if index < offset else 2 + offset / 10 + index * 0.001

    rows = _series(400, a100=price(0), h100=price(0), b200=price(180))
    rows[4]['h100'] = None
    folder = tmp_path / 'gpu'
    folder.mkdir()
    with _drawn(gpu_chart, rows, folder) as (figure, path, caught):
        axis = figure.axes[0]
        by_label = {line.get_label(): line for line in axis.lines}
        expected = [mdates.date2num(date.fromisoformat(row['date'])) for row in rows]

        assert list(by_label) == ['A100价格', 'H100价格', 'B200价格']
        assert by_label['A100价格'].get_xdata(orig=False).tolist() == expected
        assert math.isnan(by_label['H100价格'].get_ydata()[4])
        assert all(math.isnan(value) for value in by_label['B200价格'].get_ydata()[:180])
        assert math.isfinite(by_label['B200价格'].get_ydata()[180])
        labels = [text.get_text() for text in figure.legends[0].get_texts()]
        assert labels == ['H100价格', 'B200价格', 'A100价格']
        joined = '\n'.join(_texts(figure))
        assert '美元/每块GPU每小时' in joined and '美元/GPU/小时' in joined
        assert '数据来源：Silicon Data · A100/H100/B200 NeoCloud' in joined
        assert axis.get_ylim()[0] == 0 and axis.get_yscale() == 'linear'
        assert len([tick.get_text() for tick in axis.get_xticklabels() if tick.get_text()]) <= 7
        assert not any('Glyph' in str(item.message) for item in caught)
        assert path.name == 'AI景气度指标.png'

        content, suffix, _media = encode_image(compose_image(path))
        assert suffix == '.png' and len(content) <= MAX_BYTES


def test_footer_uses_published_price_and_manual_source(tmp_path):
    rows = [
        {'date': '2026-10-07', 'token_price': '1.0000'},
        {'date': '2026-10-14', 'token_price': '0.9000', 'status': 'manual_entry',
         'manual_display': {'readings': [{'metric': 'token', 'date': '2026-10-14',
                                          'price': '0.90', 'week_change_pct': '-10'}]}},
    ]
    with _drawn(token_chart, rows, tmp_path) as (figure, _path, _caught):
        joined = '\n'.join(_texts(figure))
        assert '最新 0.90 美元/百万Token' in joined
        assert '七日 -10%' in joined
        assert '0.9000' not in joined
        assert '历史工作簿及本期编辑人工录入' in joined
        # A one-week span still uses the short horizontal form, not ISO dates.
        labels = [tick.get_text() for tick in figure.axes[0].get_xticklabels()]
        assert labels == ['10/7', '10/14']
        assert [tick.get_rotation() for tick in figure.axes[0].get_xticklabels()] == [0, 0]


def test_missing_week_baseline_is_omitted():
    rows = [{'date': '2026-10-14', 'a100': 1.5, 'h100': 2.8, 'b200': 6}]
    with patch('matplotlib.figure.Figure.savefig'), patch('matplotlib.pyplot.close'):
        gpu_chart(rows, Path('.'))
        joined = '\n'.join(_texts(plt.gcf()))
    plt.close('all')
    assert '七日' not in joined
    assert 'A100 1.50' in joined and 'B200 6.00' in joined


def _rgb(value):
    text = value.lstrip('#')
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def test_series_colors_follow_the_reference_not_the_old_palette(tmp_path):
    old = {'#5476DC', '#27A86C', '#E3A13D'}
    assert {H100_COLOR, B200_COLOR, A100_COLOR, TOKEN_COLOR}.isdisjoint(old)
    teal, purple, gold = _rgb(B200_COLOR), _rgb(H100_COLOR), _rgb(A100_COLOR)
    assert teal[1] > 170 and teal[2] > 170 and teal[0] < 90
    assert purple[2] > purple[0] > purple[1] and purple[2] > 160
    assert gold[0] > 220 and gold[1] > 180 and gold[2] < 110 and gold[1] > gold[0] * 0.78
    assert TOKEN_COLOR == B200_COLOR
    rows = _series(12, a100=1.4, h100=2.4, b200=5.2, token_price=1.2)
    folder = tmp_path / 'colors'
    folder.mkdir()
    with _drawn(gpu_chart, rows, folder) as (figure, _path, _caught):
        colors = {line.get_label(): line.get_color() for line in figure.axes[0].lines}
        assert colors == {'A100价格': A100_COLOR, 'H100价格': H100_COLOR, 'B200价格': B200_COLOR}
        assert any(isinstance(effect, SimpleLineShadow) for effect in figure.axes[0].lines[0].get_path_effects())
        swatches = {text.get_text(): handle.get_color()
                    for text, handle in zip(figure.legends[0].get_texts(), figure.legends[0].get_lines())}
        assert swatches == {'H100价格': H100_COLOR, 'B200价格': B200_COLOR, 'A100价格': A100_COLOR}
        assert all(not handle.get_path_effects() for handle in figure.legends[0].get_lines())
    with _drawn(token_chart, rows, folder) as (figure, _path, _caught):
        assert figure.axes[0].lines[0].get_color() == TOKEN_COLOR
        assert figure.legends[0].get_lines()[0].get_color() == TOKEN_COLOR


def test_token_history_cannot_be_empty(tmp_path):
    with pytest.raises(ValueError, match='Token历史为空'):
        token_chart([], tmp_path)
