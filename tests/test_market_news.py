from services.market_dashboard.news import normalize_news
import pytest

def test_headlines_preserve_time_and_entities_without_article_body():
    item={'mobileNewsUrl':'https://n.news.naver.com/mnews/article/001/0001234',
          'title':'반도체 &amp; 실적','datetime':'202610031418','officeName':'뉴스', 'body':'DO NOT RETURN'}
    rows=normalize_news([{'items':[item,item]}])
    assert len(rows)==1
    assert rows[0]['title']=='반도체 & 실적'
    assert rows[0]['published_at']=='2026-10-03T14:18:00+09:00'
    assert 'body' not in rows[0]

def test_reject_external_or_script_links_and_bad_shape():
    assert normalize_news([{'items':[{'mobileNewsUrl':'javascript:alert(1)','title':'x'},
        {'mobileNewsUrl':'https://n.news.naver.com.evil.test/mnews/article/1/2','title':'x'}]}])==[]
    with pytest.raises(ValueError): normalize_news({'error':'changed contract'})

def test_unknown_timestamp_is_not_collection_time():
    rows=normalize_news([{'items':[{'mobileNewsUrl':'https://n.news.naver.com/mnews/article/1/2','title':'x','datetime':'bad'}]}])
    assert rows[0]['published_at'] is None
