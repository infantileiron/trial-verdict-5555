import os,json
from datetime import timezone
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Experiment,Event,Decision,Audit,Correction,EventRevocation
from src.schemas.business import Login,ExperimentInput,EventInput,RunInput,RationaleInput,PublishInput,CorrectionInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump,utc
from src.controllers.business import experiment,decision,validate_event,snapshot,publish
from src.services.corrections import now,revoked_event_ids,ensure_revocable,apply_correction,correction_detail
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/experiments')
def experiments(u=Depends(current),s=Depends(db)):
 out=[]
 for e in s.query(Experiment).order_by(Experiment.id.desc()):
  gone=revoked_event_ids(s,e.id)
  valid=s.query(Event.id).filter_by(experiment_id=e.id,kind='exposure')
  if gone:valid=valid.filter(~Event.id.in_(gone))
  out.append({**dump(e),'exposures':valid.count(),'raw_exposures':s.query(Event).filter_by(experiment_id=e.id,kind='exposure').count()})
 return out
@router.post('/experiments')
def create_experiment(data:ExperimentInput,u=Depends(editor),s=Depends(db)):
 d=data.model_dump();d['starts_at']=utc(data.starts_at);d['ends_at']=utc(data.ends_at);r=Experiment(**d);s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'实验编号已存在')
 audit(s,u,'建立实验 '+r.code);return dump(r)
@router.put('/experiments/{i}')
def edit_experiment(i:int,data:ExperimentInput,u=Depends(editor),s=Depends(db)):
 r=experiment(s,i)
 if s.query(Event).filter_by(experiment_id=i).first():raise HTTPException(409,'已有事件，禁止事后修改实验定义；请建立新实验')
 d=data.model_dump();d['starts_at']=utc(data.starts_at);d['ends_at']=utc(data.ends_at)
 for k,v in d.items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'实验编号已存在')
 audit(s,u,'修订实验 '+str(i));return dump(r)
@router.get('/events')
def events(experiment_id:int|None=None,u=Depends(current),s=Depends(db)):
 q=s.query(Event)
 if experiment_id:q=q.filter_by(experiment_id=experiment_id)
 rows=q.order_by(Event.id.desc()).all()
 rev_map={rv.event_id:rv for rv in s.query(EventRevocation).all()}
 return [{**dump(e),'revoked':e.id in rev_map,'revocation_reason':rev_map[e.id].reason if e.id in rev_map else '',
          'correction_id':rev_map[e.id].correction_id if e.id in rev_map else None} for e in rows]
@router.post('/events')
def create_event(data:EventInput,u=Depends(editor),s=Depends(db)):
 stamp=validate_event(s,data);d=data.model_dump();d['occurred_at']=stamp;r=Event(**d);s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'该用户事件已存在')
 audit(s,u,'采集事件 '+str(r.id));return dump(r)
@router.get('/decisions')
def decisions(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Decision).order_by(Decision.id.desc())]
@router.post('/decisions')
def run(data:RunInput,u=Depends(editor),s=Depends(db)):
 exp=experiment(s,data.experiment_id)
 if s.query(Decision).filter(Decision.state.in_(['queued','running'])).count()>=10:raise HTTPException(429,'队列已满，请等待任务完成')
 r=Decision(experiment_id=exp.id,snapshot=snapshot(s,exp));s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('trial:decisions',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，恢复后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交实验裁决 '+str(r.id));return dump(r)
@router.get('/decisions/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(decision(s,i))
@router.put('/decisions/{i}/rationale')
def rationale(i:int,data:RationaleInput,u=Depends(editor),s=Depends(db)):
 r=decision(s,i)
 if r.state=='blocked':raise HTTPException(409,'该裁决证据已被更正单撤销，禁止继续流转')
 if r.published_at:raise HTTPException(409,'已发布裁决不可覆盖，请重新创建裁决')
 if r.state!='done':raise HTTPException(409,'计算完成后再填写裁决理由')
 r.rationale=data.rationale;s.commit();audit(s,u,'保存裁决理由 '+str(i));return dump(r)
@router.post('/decisions/{i}/publish')
def release(i:int,data:PublishInput,u=Depends(admin),s=Depends(db)):
 r=publish(s,decision(s,i),data.action,u);audit(s,u,'发布裁决 '+str(i)+' '+data.action);return r
@router.get('/decisions/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=decision(s,i)
 if r.state!='done':raise HTTPException(409,'尚无成功结果')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="trial-verdict-{i}.json"'})
@router.get('/corrections')
def corrections(u=Depends(current),s=Depends(db)):
 exp_map={e.id:e.code for e in s.query(Experiment).all()}
 return [{**dump(c),'experiment_code':exp_map.get(c.experiment_id,'')} for c in s.query(Correction).order_by(Correction.id.desc()).all()]
@router.post('/corrections')
def create_correction(data:CorrectionInput,u=Depends(editor),s=Depends(db)):
 exp,kinds=ensure_revocable(s,data)
 c=Correction(experiment_id=exp.id,user_key=data.user_key,reason=data.reason,revoke_kinds=kinds,
              state='pending',created_by=u.email,created_at=now(),attempts=0)
 s.add(c);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('trial:corrections',c.id)
 except redis.RedisError:c.state='failed';c.error='队列暂不可用，可稍后重试';s.commit();raise HTTPException(503,c.error)
 audit(s,u,'提交更正单 '+str(c.id)+' 参与者 '+c.user_key);return correction_detail(s,c)
@router.get('/corrections/{i}')
def correction(i:int,u=Depends(current),s=Depends(db)):
 c=s.get(Correction,i)
 if not c:raise HTTPException(404,'更正单不存在')
 return correction_detail(s,c)
@router.post('/corrections/{i}/retry')
def retry_correction(i:int,u=Depends(editor),s=Depends(db)):
 c=s.get(Correction,i)
 if not c:raise HTTPException(404,'更正单不存在')
 if c.state=='effective':raise HTTPException(409,'更正单已生效，无需重试')
 if c.state in ('pending','applying'):raise HTTPException(409,'更正单仍在处理中，请稍后刷新')
 c.state='pending';c.error='';s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('trial:corrections',c.id)
 except redis.RedisError:c.state='failed';c.error='队列暂不可用，可稍后重试';s.commit();raise HTTPException(503,c.error)
 audit(s,u,'重试更正单 '+str(c.id));return correction_detail(s,c)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
