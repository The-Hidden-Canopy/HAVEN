"""Explicitly granted paired-phone calls to reviewed local feature modules.

This adds no pairing/enrollment, runtime or generic IPC tunnel. The host must
provision expiring grants against the actual external binding and active scope.
"""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from threading import RLock

from haven.external_agents.domain import ExternalRequest, ExternalScope
from haven.external_agents.transport import hash_credential

VERSION='haven-feature-ui-1'
FEATURES={'traceglass':{'inspect','verify_package','export_package','capture'},
          'haven.ghost-teacher':{'catalog','validate','sessions','start','detail','evidence','diagnosis','step','pause','resume','export_status','export','dataset_catalog','dataset_profile','dataset_preview','dataset_review','dataset_export','contribution_status'}}
ACTIONS={'stage','install','enable','disable','uninstall','update','rollback','run','picker','entitlement','status'}
READ_COMMANDS={'catalog','validate','sessions','detail','evidence','diagnosis','export_status','dataset_catalog','dataset_profile','dataset_preview','contribution_status','inspect','verify_package','capture'}


class FeatureGateway:
    def __init__(self, *, workflows, path, clock=time.time):
        self.workflows=workflows;self.path=path;self.clock=clock;self.lock=RLock()
        with closing(sqlite3.connect(path)) as db,db:
            db.executescript('''CREATE TABLE IF NOT EXISTS feature_grants (
              binding TEXT NOT NULL,principal TEXT NOT NULL,scope TEXT NOT NULL,feature TEXT NOT NULL,
              operation TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(binding,principal,scope,feature,operation));
              CREATE TABLE IF NOT EXISTS feature_calls (
              binding TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,result TEXT,PRIMARY KEY(binding,id));''')

    def grant(self, *, binding_id, principal_id, scope_id, feature_id, operations, expires):
        """Host-only provisioning; nothing from a phone can register a grant."""
        if feature_id not in FEATURES or not isinstance(operations,(set,frozenset,list,tuple)) or not operations:
            raise ValueError('invalid feature grant')
        for op in operations:
            if op not in ACTIONS and op not in {'run.'+name for name in FEATURES[feature_id]}:
                raise ValueError('unknown feature operation')
        if isinstance(expires,bool) or not isinstance(expires,(int,float)) or not self.clock()<expires<float('inf'):
            raise ValueError('invalid grant expiry')
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            for op in operations:
                db.execute('INSERT OR REPLACE INTO feature_grants VALUES(?,?,?,?,?,?)',(binding_id,principal_id,scope_id,feature_id,op,expires))

    def revoke(self,binding_id):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.execute('DELETE FROM feature_grants WHERE binding=?',(binding_id,))

    def _admit(self,credential,subject,request_id):
        host=self.workflows.host
        connection=host._external_agents_store.connection_for_credential(hash_credential(credential))
        if connection is None: raise PermissionError('connection unavailable')
        admitted=host.external_agent_gateway.admit(ExternalRequest(connection_id=connection.connection_id,
            tool='haven.feature.call',required_scope=ExternalScope.ACTIONS_REQUEST,
            external_request_id=request_id,subject=subject,client_metadata={'transport':'android-https-v1'}))
        # Local workflows currently operate as one installed owner. A differently
        # bound phone can never inherit that owner's IPC identity.
        if admitted.binding is None or admitted.principal.actor_id!=host.identity.current_principal().actor_id:
            raise PermissionError('feature owner mapping unavailable')
        return admitted

    def _allowed(self,admitted,scope,feature,operation):
        if not self.workflows.authorize(admitted.principal.actor_id,scope,'features.run'): return False
        if operation.startswith('run.') or operation=='picker':
            try:
                if feature=='traceglass':
                    broker=getattr(self.workflows.host,'traceglass_broker',None)
                    if broker is None or broker.entitlement_status(principal_id=admitted.principal.actor_id,scope_id=scope)!='active':return False
                else:
                    bindings=self.workflows.ghost_teacher_bindings
                    if bindings is None or bindings.entitlement(admitted.principal.actor_id,scope)!='licensed':return False
            except Exception:return False
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT 1 FROM feature_grants WHERE binding=? AND principal=? AND scope=? AND feature=? AND operation=? AND expires>?',
                (admitted.binding.binding_id,admitted.principal.actor_id,scope,feature,operation,self.clock())).fetchone() is not None

    def call(self,credential,subject,tool,request_id,args):
        if tool=='haven.feature.catalog':
            if not isinstance(args,dict) or args!={'version':VERSION}: raise ValueError('invalid feature catalog request')
            admitted=self._admit(credential,subject,request_id)
            with self.lock,closing(sqlite3.connect(self.path)) as db:
                rows=db.execute('SELECT DISTINCT scope,feature FROM feature_grants WHERE binding=? AND principal=? AND expires>?',
                    (admitted.binding.binding_id,admitted.principal.actor_id,self.clock())).fetchall()
                items=[]
                for scope,feature in rows:
                    catalog=self.workflows.feature_catalog({'scope_id':scope})
                    release=next((r for r in catalog.get('features',[]) if r['feature_id']==feature),None)
                    if release is None:continue
                    operations=[op for op in sorted(ACTIONS|{'run.'+c for c in FEATURES[feature]}) if self._allowed(admitted,scope,feature,op)]
                    if operations:items.append({'scope_id':scope,'feature_id':feature,'label':release['display_name'],'operations':operations})
                return {'ok':True,'contract':VERSION,'items':items}
        if tool!='haven.feature.call' or not isinstance(args,dict) or set(args)!={'version','scope_id','feature_id','action','command','parameters'}:
            raise ValueError('invalid feature envelope')
        version,scope,feature,action,command,params=(args[k] for k in ('version','scope_id','feature_id','action','command','parameters'))
        if version!=VERSION or feature not in FEATURES or action not in ACTIONS or not isinstance(scope,str) or not isinstance(params,dict) or len(json.dumps(args))>32768:
            raise ValueError('invalid feature request')
        if (action=='run' and command not in FEATURES[feature]) or (action!='run' and command is not None):
            raise ValueError('unknown feature command')
        if set(params)&{'scope_id','principal_id','household_id','permission','authorized'}:
            raise ValueError('caller authority is forbidden')
        operation='run.'+command if action=='run' else action
        admitted=self._admit(credential,subject,request_id)
        with self.lock:
            if not self._allowed(admitted,scope,feature,operation): raise PermissionError('feature grant required')
            digest=hashlib.sha256(json.dumps(args,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            mutation=action not in {'picker','entitlement','status'} and not (action=='run' and command in READ_COMMANDS)
            if mutation:
                with closing(sqlite3.connect(self.path)) as db,db:
                    row=db.execute('SELECT digest,result FROM feature_calls WHERE binding=? AND id=?',(admitted.binding.binding_id,request_id)).fetchone()
                    if row:
                        if row[0]!=digest: raise ValueError('request ID reused')
                        # Do not replay an old private result after its source grant
                        # changed. A fresh read must inspect the current feature.
                        return {'ok':False,'contract':VERSION,'error':'already_recorded' if row[1] else 'outcome_uncertain'}
                    db.execute('INSERT INTO feature_calls VALUES(?,?,?,NULL)',(admitted.binding.binding_id,request_id,digest))
        if action in {'picker','entitlement'}:
            if feature!='traceglass': raise ValueError('operation unavailable')
            handler=self.workflows.handlers().get('traceglass.'+action)
            result=handler({'scope_id':scope,**params}) if handler else {'ok':False,'error':'service_unavailable'}
        elif action=='status':
            result=self.workflows.feature_catalog({'scope_id':scope})
            if result.get('ok'): result['features']=[r for r in result['features'] if r['feature_id']==feature]
        else:
            if action!='run' and set(params)-({'version'} if action=='stage' else set()): raise ValueError('invalid lifecycle request')
            result=self.workflows.feature_action({'scope_id':scope,'feature_id':feature,'action':action,
                **({'command':command,'parameters':params} if action=='run' else params)})
        with self.lock:
            try:
                current=self._admit(credential,subject,request_id)
                authorized=current.binding.binding_id==admitted.binding.binding_id and self._allowed(current,scope,feature,operation)
            except Exception: authorized=False
            if not authorized:
                if mutation: return {'ok':False,'error':'outcome_uncertain'}
                raise PermissionError('feature grant revoked')
            result={'contract':VERSION,**result}
            if mutation:
                with closing(sqlite3.connect(self.path)) as db,db:
                    db.execute('UPDATE feature_calls SET result=? WHERE binding=? AND id=?',(json.dumps(result),admitted.binding.binding_id,request_id))
            return result
