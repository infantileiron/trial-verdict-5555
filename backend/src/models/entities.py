from sqlalchemy import Column,Integer,String,Float,JSON,Text,UniqueConstraint,ForeignKey
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Experiment(Base):
 __tablename__='experiments'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True);name=Column(String(120));starts_at=Column(String(40));ends_at=Column(String(40));a_ratio=Column(Float);hypothesis=Column(Text)
class Event(Base):
 __tablename__='events'
 __table_args__=(UniqueConstraint('experiment_id','user_key','kind',name='uq_exp_user_kind'),)
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));user_key=Column(String(80));group=Column(String(1));kind=Column(String(20));occurred_at=Column(String(40))
class Correction(Base):
 __tablename__='corrections'
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));user_key=Column(String(80));reason=Column(Text);revoke_kinds=Column(JSON)
 state=Column(String(20),default='pending');error=Column(Text,default='');created_by=Column(String(100),default='')
 created_at=Column(String(40));applied_at=Column(String(40),default='');attempts=Column(Integer,default=0)
 counts_before=Column(JSON,nullable=True);counts_after=Column(JSON,nullable=True);affected_decisions=Column(JSON,nullable=True)
class EventRevocation(Base):
 __tablename__='event_revocations'
 __table_args__=(UniqueConstraint('event_id',name='uq_revoked_event'),)
 id=Column(Integer,primary_key=True);event_id=Column(Integer,ForeignKey('events.id'));correction_id=Column(Integer,ForeignKey('corrections.id'));reason=Column(Text);created_at=Column(String(40))
class Decision(Base):
 __tablename__='decisions'
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));state=Column(String(20),default='queued');snapshot=Column(JSON);result=Column(JSON,nullable=True);error=Column(Text,default='');rationale=Column(Text,default='');action=Column(String(30),default='');published_by=Column(String(100),default='');published_at=Column(String(40),default='')
 correction_applied=Column(JSON,nullable=True);correction_note=Column(Text,default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
