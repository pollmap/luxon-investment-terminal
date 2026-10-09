"""fixture_non_production: scoped stock proxy transformation and rollback guards."""
import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('api_reuse',Path(__file__).parents[1]/'ops/market/reuse_public_connections.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_reuses_api_only_and_does_not_change_freshness_or_websocket():
 source='''proxy_cache_path /fixture/cache keys_zone=fixture:10m;
server {
 server_name stocks.62-171-141-206.sslip.io;
 location ^~ /api/market/v1/ {
  proxy_cache_valid 200 1s;
  proxy_pass http://127.0.0.1:8211;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
 }
 location = /api/market/v1/stream {
  proxy_pass http://127.0.0.1:8211;
  proxy_set_header Connection "upgrade";
 }
}
'''
 new=m.upgraded_config(source)
 assert new.count('upstream korea_stocks_api_reuse_v1 {')==1
 assert 'proxy_cache_valid 200 1s;' in new
 assert 'proxy_pass http://korea_stocks_api_reuse_v1;' in new
 assert source[source.index(' location = /api/market/v1/stream'):]==new[new.index(' location = /api/market/v1/stream'):]
 assert source[:source.index(' location ^~ /api/market/v1/ {')] in new
 assert m.upgraded_config(new)==new


@pytest.mark.parametrize('change',[
 ('127.0.0.1:8211','127.0.0.1:9999'),
 ('proxy_set_header Connection "";','proxy_set_header Connection "close";'),
 ('proxy_http_version 1.1;','proxy_http_version 1.0;')])
def test_unexpected_routes_fail_before_any_mutation(change):
 source=' location ^~ /api/market/v1/ {\n proxy_pass http://127.0.0.1:8211;\n proxy_http_version 1.1;\n proxy_set_header Connection "";\n }'
 with pytest.raises(ValueError):m.upgraded_config(source.replace(*change))
