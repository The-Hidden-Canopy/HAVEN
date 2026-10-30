"""Existing authenticated local transports share the provider-neutral wire schema."""
from .contract import CONTRACT, READ, SEND, CANCEL, APPROVE, AuthContext, Conflict, SessionDenied
from .gateway import SHAPES, SessionUnavailable, identifier, invoke_provider


class LocalSessionRoutes:
    def __init__(self, host, binding):
        self.host, self.binding = host, binding

    def call(self, params):
        try:
            if not isinstance(params, dict) or set(params) != {'tool','request_id','arguments'}:
                raise ValueError('invalid envelope')
            tool,request_id,args = params['tool'],params['request_id'],params['arguments']
            if not isinstance(tool,str) or not tool.startswith('haven.session.'):
                raise ValueError('unknown method')
            op=tool[len('haven.session.'):]
            if op not in SHAPES or not isinstance(args,dict) or set(args)!=SHAPES[op] or args.get('version')!=CONTRACT:
                raise ValueError('invalid envelope')
            identifier(request_id)
            sid=identifier(args['session_id']) if 'session_id' in args else None
            permission={'submit':SEND,'cancel':CANCEL,'decide':APPROVE}.get(op,READ)
            auth=AuthContext(self.host.director.household_id,self.host.identity.current_principal().actor_id,frozenset({permission}))
            if self.binding is None: raise SessionUnavailable('no provider configured')
            if not self.binding.allowed(auth,permission,sid): raise SessionDenied('session denied')
            value=invoke_provider(self.binding,auth,op,request_id,args,
                    lambda p,s: self.binding.allowed(auth,p,s))
            if not self.binding.allowed(auth,permission,sid):
                if op in {'submit','cancel','decide'}: return {'ok':False,'code':'outcome_uncertain'}
                raise SessionDenied('session revoked')
            return value
        except SessionDenied: return {'ok':False,'code':'access_denied'}
        except SessionUnavailable: return {'ok':False,'code':'session_provider_unavailable'}
        except Conflict: return {'ok':False,'code':'not_executed'}
        except (ValueError,KeyError,TypeError): return {'ok':False,'code':'invalid_or_unsupported'}
        except Exception: return {'ok':False,'code':'outcome_uncertain'}
