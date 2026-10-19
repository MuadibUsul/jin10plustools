from app.services.copy_dates import copy_dates

def test_copy_dates_preserves_source_urls_and_is_idempotent():
    text='截至2026-10-16 09:15，参考2026-01-02；https://example.com/2026-10-16'
    expected='截至2026年10月16日 9:15，参考2026年1月2日；https://example.com/2026-10-16'
    assert copy_dates(text)==expected
    assert copy_dates(expected)==expected
    assert copy_dates('2026-99-99')=='2026-99-99'
