from datetime import datetime,timezone
from fastapi import HTTPException
from src.models.entities import Experiment,Event,EventRevocation,Decision,Correction
from src.services.business import dump
def now():return datetime.now(timezone.utc).isoformat()
def revoked_event_ids(s,experiment_id=None):
 q=s.query(EventRevocation.event_id)
 if experiment_id is not None:
  q=q.join(Event,Event.id==EventRevocation.event_id).filter(Event.experiment_id==experiment_id)
 return {row[0] for row in q.all()}
def valid_events(s,experiment_id):
 gone=revoked_event_ids(s,experiment_id)
 return s.query(Event).filter_by(experiment_id=experiment_id).filter(~Event.id.in_(gone or [-1])).order_by(Event.id).all()
def aggregate(s,experiment_id):
 exposed={'A':set(),'B':set()};activated={'A':set(),'B':set()}
 for e in valid_events(s,experiment_id):
  if e.kind=='exposure':exposed[e.group].add(e.user_key)
  elif e.kind=='activation' and e.user_key in exposed[e.group]:activated[e.group].add(e.user_key)
 n_a,n_b=len(exposed['A']),len(exposed['B']);x_a,x_b=len(activated['A']),len(activated['B'])
 return {'n_a':n_a,'n_b':n_b,'x_a':x_a,'x_b':x_b,'total_exposures':n_a+n_b,'total_activations':x_a+x_b,
         'exposed_users':{'A':sorted(exposed['A']),'B':sorted(exposed['B'])},
         'activated_users':{'A':sorted(activated['A']),'B':sorted(activated['B'])}}
def build_snapshot(s,exp):
 events=[dump(e) for e in valid_events(s,exp.id)]
 correction_ids=[row[0] for row in s.query(Correction.id).filter_by(experiment_id=exp.id,state='effective').order_by(Correction.id).all()]
 return {'experiment':dump(exp),'events':events,'correction_ids':correction_ids}
def snapshot_is_stale(s,decision):
 if not decision.snapshot:return False
 gone=revoked_event_ids(s,decision.experiment_id)
 return bool(gone and {e['id'] for e in decision.snapshot['events']}&gone)
def apply_correction(s,correction_id):
 c=s.query(Correction).filter_by(id=correction_id).with_for_update().first()
 if not c:return
 if c.state=='effective':return
 c.attempts=(c.attempts or 0)+1;c.state='applying';c.error='';s.commit()
 try:
  s.query(Experiment).filter_by(id=c.experiment_id).with_for_update().first()
  already=revoked_event_ids(s,c.experiment_id)
  targets=s.query(Event).filter_by(experiment_id=c.experiment_id,user_key=c.user_key).filter(Event.kind.in_(c.revoke_kinds)).filter(~Event.id.in_(already or [-1])).all()
  if not targets:raise RuntimeError('该实验参与者没有匹配的有效事件可撤销')
  before=aggregate(s,c.experiment_id)
  target_ids={e.id for e in targets}
  # 撤销曝光时，连同该参与者仍有效的激活一并剔除
  cascade=s.query(Event).filter_by(experiment_id=c.experiment_id,user_key=c.user_key,kind='activation').filter(~Event.id.in_(already or [-1])).all()
  if 'exposure' in c.revoke_kinds:target_ids|={e.id for e in cascade}
  stamp=now()
  for eid in sorted(target_ids):
   s.add(EventRevocation(event_id=eid,correction_id=c.id,reason=c.reason,created_at=stamp))
  s.flush()
  after=aggregate(s,c.experiment_id)
  affected=[]
  for d in s.query(Decision).filter_by(experiment_id=c.experiment_id).with_for_update().all():
   snap_ids={e['id'] for e in (d.snapshot or {}).get('events',[])}
   if not (snap_ids&target_ids):continue
   note={'correction_id':c.id,'at':stamp,'user_key':c.user_key,'revoked_event_ids':sorted(target_ids),'reason':c.reason}
   history=list(d.correction_applied or [])
   if not any(h.get('correction_id')==c.id for h in history):history.append(note)
   d.correction_applied=history;d.correction_note=f"已受更正单 #{c.id} 影响：{c.reason}"
   affected.append(d.id)
   if d.state in ('queued','running','done') and not d.published_at:
    d.state='blocked';d.error=f"更正单 #{c.id} 生效，冻结证据中的事件已撤销，禁止发布；请基于有效事件新建裁决"
  c.counts_before={k:v for k,v in before.items() if not k.endswith('_users')}
  c.counts_after={k:v for k,v in after.items() if not k.endswith('_users')}
  c.affected_decisions=affected;c.state='effective';c.applied_at=stamp;c.error=''
  s.commit()
 except Exception as exc:
  s.rollback()
  failed=s.get(Correction,correction_id)
  if failed and failed.state!='effective':
   failed.state='failed';failed.error=str(exc);s.commit()
  raise
def correction_detail(s,c):
 revoked=s.query(EventRevocation,Event).join(Event,Event.id==EventRevocation.event_id).filter(EventRevocation.correction_id==c.id).order_by(Event.id).all()
 exp=s.get(Experiment,c.experiment_id)
 return {**dump(c),'experiment_code':exp.code if exp else '',
         'revoked_events':[{'event_id':e.id,'kind':e.kind,'group':e.group,'user_key':e.user_key,'occurred_at':e.occurred_at,'reason':rv.reason} for rv,e in revoked]}
def ensure_revocable(s,data):
 exp=s.get(Experiment,data.experiment_id)
 if not exp:raise HTTPException(404,'实验不存在')
 kinds=data.revoke_kinds or ['exposure','activation']
 if any(k not in ('exposure','activation') for k in kinds):raise HTTPException(422,'可撤销事件类型只能是 exposure / activation')
 gone=revoked_event_ids(s,exp.id)
 q=s.query(Event).filter_by(experiment_id=exp.id,user_key=data.user_key).filter(Event.kind.in_(kinds))
 if gone:q=q.filter(~Event.id.in_(gone))
 if not q.first():raise HTTPException(422,'该实验参与者没有匹配的有效事件可撤销（可能已被其他更正单撤销）')
 return exp,kinds
