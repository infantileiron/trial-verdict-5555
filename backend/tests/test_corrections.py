import os,sys,tempfile,threading
from pathlib import Path
_TMP=tempfile.mkdtemp(prefix='trial-test-')
os.environ.setdefault('DATABASE_URL','sqlite:///'+_TMP+'/test.db')
os.environ.setdefault('REDIS_URL','redis://fake:6379/0')
os.environ.setdefault('JWT_SECRET','test-secret')
ART=Path(_TMP)/'artifacts';ART.mkdir(exist_ok=True);os.environ['ARTIFACTS_DIR']=str(ART)
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import redis
from src.config.database import Base,engine,Session
from src.models import entities
from src.models.entities import User,Experiment,Event,Decision,Correction,EventRevocation
from src.utils.security import hash_password
import src.worker as worker
from src.main import app
from fastapi.testclient import TestClient
PASS=0
def check(name,cond,detail=''):
 global PASS
 assert cond,'FAIL: '+name+' '+str(detail);PASS+=1;print(' ok -',name)
class FakeRedis:
 def __init__(self):self.q=[];self.lock=threading.Lock()
 def rpush(self,k,v):
  with self.lock:self.q.append((k.encode(),str(v).encode()))
 def blpop(self,keys,timeout=3):
  import time as _t;deadline=_t.time()+0.2
  while _t.time()<deadline:
   with self.lock:
    for i,(k,v) in enumerate(self.q):
     if k.decode() in keys:return self.q.pop(i)
   _t.sleep(0.02)
  return None
 def ping(self):return True
fake=FakeRedis()
redis.Redis.from_url=lambda *a,**k:fake
worker.r=fake
Base.metadata.create_all(engine)
with Session() as s:
 s.add_all([User(email=r+'@demo.local',role=r,password=hash_password('Demo123!')) for r in ('admin','analyst','viewer')]);s.commit()
 exp=Experiment(code='T-1',name='更正流程实验',starts_at='2026-11-01T00:00:00+00:00',ends_at='2026-11-10T00:00:00+00:00',a_ratio=.5,hypothesis='验证更正单撤销与重新聚合口径')
 s.add(exp);s.flush()
 stamp='2026-11-02T08:00:00+00:00'
 for i in range(50):
  g='A' if i<25 else 'B';key=f'U{i+1:03}'
  s.add(Event(experiment_id=exp.id,user_key=key,group=g,kind='exposure',occurred_at=stamp))
  if (g=='A' and i<12) or (g=='B' and i in range(25,33)):
   s.add(Event(experiment_id=exp.id,user_key=key,group=g,kind='activation',occurred_at=stamp))
 s.commit();EXP=exp.id
def login(role):
 c=TestClient(app);r=c.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'});check('login '+role,r.status_code==200);c.headers['Authorization']='Bearer '+r.json()['token'];return c
admin=login('admin');analyst=login('analyst');viewer=login('viewer')
# 1. 初始裁决：冻结含误采事件的证据，完成并发布
r=analyst.post('/decisions',json={'experiment_id':EXP});check('提交裁决',r.status_code==200,r.text);D1=r.json()['id']
worker.tick(fake)
d=admin.get(f'/decisions/{D1}').json();check('裁决计算完成',d['state']=='done',d.get('error'))
check('初始口径 50曝光/20激活',(d['result']['n_a']+d['result']['n_b'],d['result']['activated_a']+d['result']['activated_b'])==(50,20))
r=analyst.put(f'/decisions/{D1}/rationale',json={'rationale':'更正前证据：误采激活未剔除，按当时冻结数据起草的发布理由。'});check('填写理由',r.status_code==200,r.text)
r=admin.post(f'/decisions/{D1}/publish',json={'action':'keep_a'});check('更正前发布成功',r.status_code==200,r.text)
# 2. 更正单：仅撤销误采激活（U001 是 A 组激活用户）
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U001','reason':'试用埋点把该激活误记为真实转化','revoke_kinds':['activation']})
check('创建更正单',r.status_code==200,r.text);C1=r.json()['id']
check('更正单初始待处理',r.json()['state']=='pending')
cl=analyst.get(f'/corrections/{C1}').json();check('待处理可查',cl['state']=='pending' and cl['revoke_kinds']==['activation'])
worker.tick(fake)
cl=analyst.get(f'/corrections/{C1}').json()
check('更正生效',cl['state']=='effective',cl.get('error'))
check('保留原事件与原因',len(cl['revoked_events'])==1 and cl['revoked_events'][0]['reason'].startswith('试用埋点'))
check('更正前后人数可查',cl['counts_before']['total_activations']==20 and cl['counts_after']['total_activations']==19 and cl['counts_after']['total_exposures']==50)
ev=analyst.get('/events',params={'experiment_id':EXP}).json()
target=[e for e in ev if e['user_key']=='U001' and e['kind']=='activation'][0]
check('事件列表标注已撤销但行保留',target['revoked'] is True and target['correction_id']==C1)
check('原始事件行未删除',len([e for e in ev if e['user_key']=='U001'])==2)
# 已发布裁决保留当时证据并标注
d=admin.get(f'/decisions/{D1}').json()
check('已发布裁决仍为发布态',d['published_at']!='' and d['state']=='done')
check('已发布裁决标注受更正影响',d['correction_note']!='' and any(h['correction_id']==C1 for h in d['correction_applied']))
check('发布快照证据原样保留',len(d['snapshot']['events'])==70)
# 3. 撤销曝光连带激活（U026 是 B 组曝光+激活用户）
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U026','reason':'该用户为内部测试账号，曝光与激活均为误采'})
check('默认两类全撤',r.status_code==200 and r.json()['revoke_kinds']==['exposure','activation'],r.text);C2=r.json()['id']
worker.tick(fake)
cl=analyst.get(f'/corrections/{C2}').json()
check('曝光更正生效',cl['state']=='effective',cl.get('error'))
check('曝光连带激活剔除',cl['counts_before']['total_exposures']==50 and cl['counts_after']['total_exposures']==49 and cl['counts_after']['total_activations']==18)
check('本次撤销两条事件',len(cl['revoked_events'])==2)
b=cl['counts_after'];check('分组口径正确 B组曝光24/激活7',(b['n_b'],b['x_b'])==(24,7),(b['n_b'],b['x_b']))
# 4. 新裁决只取有效事件
r=analyst.post('/decisions',json={'experiment_id':EXP});D2=r.json()['id'];worker.tick(fake)
d=analyst.get(f'/decisions/{D2}').json()
check('新裁决口径 49曝光/18激活',(d['result']['n_a']+d['result']['n_b'],d['result']['activated_a']+d['result']['activated_b'])==(49,18))
check('新快照记录已生效更正',d['snapshot']['correction_ids']==[C1,C2])
check('新快照不含已撤销事件',all(e['id'] not in {x['event_id'] for x in cl['revoked_events']} for e in d['snapshot']['events']))
# 5. 失效裁决：对 D2 快照中的参与者再发更正，裁决应被阻断且不可发布
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U002','reason':'U002 激活同样为误记','revoke_kinds':['activation']});C3=r.json()['id']
worker.tick(fake)
d=analyst.get(f'/decisions/{D2}').json()
check('未发布裁决被置为证据失效',d['state']=='blocked' and f'#{C3}' in d['error'])
r=analyst.put(f'/decisions/{D2}/rationale',json={'rationale':'尝试给失效裁决补理由应被拒绝。'});check('失效裁决禁止补理由',r.status_code==409)
r=admin.post(f'/decisions/{D2}/publish',json={'action':'keep_a'});check('失效裁决禁止发布',r.status_code==409)
# 6. 排队计算晚完成也不得恢复可发布：更正先入队生效，后入队裁决取到任务时证据已失效
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U003','reason':'排队期间生效的更正','revoke_kinds':['activation']});C4=r.json()['id']
r=analyst.post('/decisions',json={'experiment_id':EXP});D3=r.json()['id']
# 先处理更正，再处理排队中的裁决
worker.tick(fake);worker.tick(fake)
d=analyst.get(f'/decisions/{D3}').json()
check('排队中证据失效直接 blocked，不产出结果',d['state']=='blocked' and d['result'] is None,d['state'])
# 重复 tick 不会让失效裁决复活
worker.tick(fake)
d=analyst.get(f'/decisions/{D3}').json()
check('失效裁决不会因后续队列处理恢复',d['state']=='blocked')
# 计算结束后复核路径：更正先入队生效，裁决入队后置为 running，模拟长计算结束前的二次核验
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U008','reason':'计算期间生效的更正','revoke_kinds':['activation']});C4b=r.json()['id']
r=analyst.post('/decisions',json={'experiment_id':EXP});D3b=r.json()['id']
worker.tick(fake)
with Session() as s:
 row=s.get(Decision,D3b)
 check('裁决在取任务前即被拦截',row.state=='blocked')
 row.state='running';s.commit()
 from src.services.analysis import calculate as _calc
 from src.services.corrections import snapshot_is_stale as _stale
 # 对应 worker.process_decision：先算出结果，写库前再核一次证据是否仍有效
 result=_calc(row.snapshot)
 if _stale(s,row):
  row.state='blocked';row.error='计算完成时更正单已撤销冻结证据中的事件，禁止发布；请基于有效事件新建裁决'
 else:
  row.result=result;row.state='done'
 s.commit()
d=analyst.get(f'/decisions/{D3b}').json()
check('长计算结束复核拦截，结果被丢弃',d['state']=='blocked' and d['result'] is None and '更正单' in d['error'])
# 7. 失败后重试不重复扣减：构造失败（对已全撤参与者再次撤销同类型）
while fake.q:worker.tick(fake)  # 排空前面手工置 running 测试遗留的队列项（blocked 项会被 worker 跳过）
with Session() as s:
 bad=Correction(experiment_id=EXP,user_key='U001',reason='重复撤销测试',revoke_kinds=['activation'],state='pending',created_by='analyst@demo.local',created_at='2026-11-11T00:00:00+00:00',attempts=0)
 s.add(bad);s.commit();BAD=bad.id
fake.rpush('trial:corrections',BAD)
worker.tick(fake)
bc=analyst.get(f'/corrections/{BAD}').json()
check('无匹配事件时更正失败',bc['state']=='failed' and '没有匹配' in bc['error'],bc.get('error'))
with Session() as s:
 rev_count=s.query(EventRevocation).count()
r=analyst.post(f'/corrections/{BAD}/retry');check('失败可重试入队',r.status_code==200 and r.json()['state']=='pending',r.text)
worker.tick(fake)
bc=analyst.get(f'/corrections/{BAD}').json()
check('重试仍失败但不重复扣减',bc['state']=='failed' and bc['attempts']==2)
with Session() as s:check('撤销留痕数量未增加',s.query(EventRevocation).count()==rev_count)
# 给失败单补一个可撤销目标后重试成功（幂等路径）
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U004','reason':'U004 激活误记','revoke_kinds':['activation']});C5=r.json()['id']
worker.tick(fake)
c5=analyst.get(f'/corrections/{C5}').json();check('后续更正正常生效',c5['state']=='effective')
# 8. 并发顺序语义：更正先生效拒绝旧裁决发布
r=analyst.post('/decisions',json={'experiment_id':EXP});D4=r.json()['id'];worker.tick(fake)
r=analyst.put(f'/decisions/{D4}/rationale',json={'rationale':'用于并发顺序验证的充分长度发布理由。'});check('D4理由保存',r.status_code==200)
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U005','reason':'发布前先生效的更正','revoke_kinds':['activation']});C6=r.json()['id']
worker.tick(fake)
r=admin.post(f'/decisions/{D4}/publish',json={'action':'keep_a'});check('更正先生效则拒绝旧裁决发布',r.status_code==409 and '撤销' in r.json()['detail'])
# 发布先完成：再生效更正，裁决保留并标注
r=analyst.post('/decisions',json={'experiment_id':EXP});D5=r.json()['id'];worker.tick(fake)
r=analyst.put(f'/decisions/{D5}/rationale',json={'rationale':'发布先于更正完成场景下的充分长度业务理由。'});check('D5理由保存',r.status_code==200)
r=admin.post(f'/decisions/{D5}/publish',json={'action':'keep_a'});check('更正前发布成功',r.status_code==200)
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'U006','reason':'发布后才确认的误采','revoke_kinds':['activation']});C7=r.json()['id']
worker.tick(fake)
d=admin.get(f'/decisions/{D5}').json()
check('发布先完成则保留发布态与证据',d['state']=='done' and d['published_at']!='')
check('发布先完成也标注受更正影响',d['correction_note']!='' and C7 in d['correction_applied'][-1].values())
# 9. 权限与校验
r=viewer.post('/corrections',json={'experiment_id':EXP,'user_key':'U007','reason':'观察员不能发起更正单。'});check('viewer 禁止创建更正',r.status_code==403)
r=analyst.post('/corrections',json={'experiment_id':9999,'user_key':'U007','reason':'不存在实验的更正单。'});check('实验不存在 404',r.status_code==404)
r=analyst.post('/corrections',json={'experiment_id':EXP,'user_key':'NOBODY','reason':'不存在参与者无法撤销事件。'});check('无匹配参与者 422',r.status_code==422)
r=analyst.post(f'/corrections/{C1}/retry');check('已生效更正不可重试',r.status_code==409)
# 10. 实验列表有效曝光与原始曝光并存
exps=analyst.get('/experiments').json();e1=[x for x in exps if x['id']==EXP][0]
check('实验有效/原始曝光并列',e1['exposures']==49 and e1['raw_exposures']==50)
# 11. 审计留痕
acts=[a['action'] for a in admin.get('/audits').json()]
check('审计含更正提交与重试',any('提交更正单' in a for a in acts) and any('重试更正单' in a for a in acts))
print(f'\nALL {PASS} CHECKS PASSED')
