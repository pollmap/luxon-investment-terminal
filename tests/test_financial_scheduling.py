from services.market_dashboard.authenticated_sources import order_financial_tasks

def test_missing_periods_advance_and_retry_keeps_one_in_five_visits():
 tasks=[{'query_key':'recent'},{'query_key':'older'},{'query_key':'oldest'}]
 progress={'recent':{'checked_at':'2026-10-04','attempts':1,'status':'error'}}
 assert [t['query_key'] for t in order_financial_tasks(tasks,progress)]==['older','oldest','recent']
 progress['recent']['attempts']=4
 assert order_financial_tasks(tasks,progress)[0]['query_key']=='recent'
 assert order_financial_tasks(tasks,progress,'oldest')[0]['query_key']=='oldest'

def test_legacy_checkpoints_work_and_keep_newest_order_within_each_group():
 tasks=[{'query_key':str(i)} for i in range(5)]
 progress={str(i):{'checked_at':'2026-10-03','status':'no_data'} for i in range(3)}
 assert [t['query_key'] for t in order_financial_tasks(tasks,progress)]==['3','4','0','1','2']
 assert [t['query_key'] for t in order_financial_tasks(tasks,{})]==['0','1','2','3','4']
