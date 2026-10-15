import importlib.util
from pathlib import Path


def test_test_site_prefix_isolates_navigation_api_and_login(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'deploy' / 'prefix_web.py'
    spec = importlib.util.spec_from_file_location('prefix_web', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'portal.html').write_text('<body><a href="/daily/">日报</a><script src="/portal.js"></script>', encoding='utf-8')
    (tmp_path / 'workspace.js').write_text("const token = 'plus.token'; fetch(path, options); api('/api/auth/me'); const source='https://example.com/file';", encoding='utf-8')
    (tmp_path / 'briefs.js').write_text("import {} from '/workspace.js'; api(`/api/briefs/${id}`);", encoding='utf-8')
    module.prefix_web(tmp_path, '/test')
    page = (tmp_path / 'portal.html').read_text(encoding='utf-8')
    assert 'href="/test/daily/"' in page and 'src="/test/portal.js"' in page
    assert '独立测试站' in page
    workspace = (tmp_path / 'workspace.js').read_text(encoding='utf-8')
    assert 'plus.test.token' in workspace and "api('/test/api/auth/me')" in workspace
    assert "fetch(path.startsWith('/test/') ? path : '/test' + path," in workspace
    assert 'https://example.com/file' in workspace
    brief = (tmp_path / 'briefs.js').read_text(encoding='utf-8')
    assert "from '/test/workspace.js'" in brief and '`/test/api/briefs/${id}`' in brief
