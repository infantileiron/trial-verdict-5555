/** @typedef {{id:number,code:string,name:string,starts_at:string,ends_at:string,a_ratio:number,hypothesis:string}} Experiment */
/** @typedef {{id:number,experiment_id:number,user_key:string,group:'A'|'B',kind:'exposure'|'activation',occurred_at:string,revoked?:boolean,revocation_reason?:string,correction_id?:number|null}} TrialEvent */
/** @typedef {{id:number,experiment_id:number,state:string,result:object,snapshot:object,rationale:string,action:string,published_at:string,correction_applied?:object[]|null,correction_note?:string}} Decision */
/** @typedef {{id:number,experiment_id:number,user_key:string,reason:string,revoke_kinds:string[],state:'pending'|'applying'|'effective'|'failed',error:string,counts_before:object|null,counts_after:object|null,affected_decisions:number[]|null,revoked_events:object[]}} Correction */
export const CONTRACT_VERSION='1.1';
