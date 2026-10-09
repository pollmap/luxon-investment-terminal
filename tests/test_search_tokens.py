from services.market_dashboard.models import matches_security,search_tokens

def test_name_and_alias_changes_do_not_reuse_old_tokens():
 search_tokens.cache_clear()
 row={'code':'005930','name':'삼성전자','aliases':['Samsung']}
 assert matches_security(row,'ㅅㅅ') and matches_security(row,'samsung')
 assert matches_security(row,'삼성 전자')
 changed={**row,'name':'다른회사','aliases':['NewAlias']}
 assert not matches_security(changed,'ㅅㅅ') and not matches_security(changed,'samsung')
 assert matches_security(changed,'newalias')
 assert search_tokens.cache_info().maxsize==16384
