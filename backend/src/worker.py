import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Decision,Correction
from src.services.analysis import calculate
from src.services.corrections import apply_correction,snapshot_is_stale
r=redis.Redis.from_url(os.environ['REDIS_URL'])
ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/artifacts'))
def process_decision(s,row_id):
 row=s.get(Decision,int(row_id))
 if not row or row.state!='queued':return
 if snapshot_is_stale(s,row):
  row.state='blocked';row.error='排队期间更正单已撤销冻结证据中的事件，禁止发布；请基于有效事件新建裁决';s.commit();return
 row.state='running';s.commit()
 try:
  result=calculate(row.snapshot)
  # 长计算结束后再核一次：生效更正不得因排队计算晚完成而恢复可发布
  if snapshot_is_stale(s,row):
   row.state='blocked';row.error='计算完成时更正单已撤销冻结证据中的事件，禁止发布；请基于有效事件新建裁决'
  else:
   row.result=result;row.state='done'
   (ARTIFACTS/('trial-verdict-'+str(row.id)+'.json')).write_text(json.dumps(row.result,ensure_ascii=False))
 except Exception as e:row.state='failed';row.error=str(e)
 s.commit()
def process_correction(s,row_id):
 apply_correction(s,int(row_id))
def recover():
 with Session() as recovery:
  for pending in recovery.query(Decision).filter(Decision.state.in_(['queued','running'])).all():
   pending.state='queued';r.rpush('trial:decisions',pending.id)
  for pending in recovery.query(Correction).filter(Correction.state.in_(['pending','applying'])).all():
   pending.state='queued';r.rpush('trial:corrections',pending.id)
  recovery.commit()
def tick(client):
 item=client.blpop(['trial:decisions','trial:corrections'],timeout=3)
 if not item:return
 queue,row_id=item[0].decode(),item[1]
 with Session() as s:
  try:
   if queue=='trial:decisions':process_decision(s,row_id)
   else:process_correction(s,row_id)
  except Exception:logging.exception('job failed %s %s',queue,row_id)
def main():
 recover()
 while True:
  Path('/tmp/heartbeat').touch()
  try:tick(r)
  except Exception:logging.exception('worker');time.sleep(2)
if __name__=='__main__':main()
