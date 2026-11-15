-- 更正单（误采事件撤销）schema，由 SQLAlchemy metadata 在 src/models/entities.py 创建。
-- corrections: 按实验参与者撤销误采事件的更正单，状态 pending -> applying -> effective/failed，可重试。
--   revoke_kinds 为本次请求撤销的事件类型；revoke_kinds 含 exposure 时级联撤销该参与者激活。
--   counts_before/counts_after 仅存口径人数（不含用户列表），affected_decisions 存受影响裁决 id。
-- event_revocations: 撤销留痕，原 events 行保留不删；event_id 唯一，保证重试不重复扣减。
-- decisions 增补:
--   state 增加 blocked（更正生效冻结证据失效，禁止发布，不会再被排队计算恢复）。
--   correction_applied(JSON)/correction_note: 已发布裁决保留当时证据并标注已受更正影响。
-- 新裁决快照只取 event_revocations 之外的有效事件，并在 snapshot.correction_ids 记录已生效更正。
CREATE TABLE corrections (
 id INTEGER PRIMARY KEY AUTO_INCREMENT,
 experiment_id INTEGER NOT NULL,
 user_key VARCHAR(80) NOT NULL,
 reason TEXT NOT NULL,
 revoke_kinds JSON NOT NULL,
 state VARCHAR(20) NOT NULL DEFAULT 'pending',
 error TEXT,
 created_by VARCHAR(100),
 created_at VARCHAR(40),
 applied_at VARCHAR(40),
 attempts INTEGER DEFAULT 0,
 counts_before JSON,
 counts_after JSON,
 affected_decisions JSON,
 CONSTRAINT fk_corrections_experiment FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
CREATE TABLE event_revocations (
 id INTEGER PRIMARY KEY AUTO_INCREMENT,
 event_id INTEGER NOT NULL,
 correction_id INTEGER NOT NULL,
 reason TEXT NOT NULL,
 created_at VARCHAR(40),
 CONSTRAINT uq_revoked_event UNIQUE (event_id),
 CONSTRAINT fk_revocation_event FOREIGN KEY (event_id) REFERENCES events(id),
 CONSTRAINT fk_revocation_correction FOREIGN KEY (correction_id) REFERENCES corrections(id)
);
ALTER TABLE decisions ADD COLUMN correction_applied JSON NULL;
ALTER TABLE decisions ADD COLUMN correction_note VARCHAR(1000) NOT NULL DEFAULT '';
