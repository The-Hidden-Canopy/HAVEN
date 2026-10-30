package org.thehiddencanopy.haven;

import org.json.JSONObject;

/** Client of one host-owned session. No local agent, memory, or automatic retry. */
final class SessionClient {
    private final ProductTransport transport;
    private final String endpoint, credential;
    SessionClient(ProductTransport transport,String endpoint,String credential) {this.transport=transport;this.endpoint=endpoint;this.credential=credential;}
    private JSONObject invoke(String tool,JSONObject args,String requestId,String session) throws Exception {
        args.put("version",SessionRules.VERSION);
        JSONObject response=transport.call(endpoint,credential,tool,args,requestId);
        int http=response.optInt("http_status",0);
        if(http!=200 || !response.optBoolean("ok",false)) throw new SessionFailure(http,response.optString("code","unavailable"));
        String returned=response.optString("session_id","");
        if(!SessionRules.accepts(session,returned,response.optString("contract",""))) throw new SessionFailure(0,"incompatible_session");
        return response;
    }
    JSONObject capabilities(String id) throws Exception {return invoke("haven.session.capabilities",new JSONObject(),id,null);}
    JSONObject sessions(String id) throws Exception {return invoke("haven.session.sessions",new JSONObject(),id,null);}
    JSONObject snapshot(String session,String id) throws Exception {return invoke("haven.session.snapshot",new JSONObject().put("session_id",session),id,session);}
    JSONObject replay(String session,long cursor,String id) throws Exception {return invoke("haven.session.replay",new JSONObject().put("session_id",session).put("after",cursor).put("limit",100),id,session);}
    JSONObject commandStatus(String session,String command,String id) throws Exception {return invoke("haven.session.command_status",new JSONObject().put("session_id",session).put("command_id",command),id,session);}
    JSONObject memory(String session,String id) throws Exception {return invoke("haven.session.memory",new JSONObject().put("session_id",session),id,session);}
    JSONObject submit(String session,String text,String id) throws Exception {return invoke("haven.session.submit",new JSONObject().put("session_id",session).put("text",text),id,session);}
    JSONObject cancel(String session,String job,String id) throws Exception {return invoke("haven.session.cancel",new JSONObject().put("session_id",session).put("job_id",job),id,session);}
    JSONObject decide(String session,String approval,int revision,boolean allow,String id) throws Exception {return invoke("haven.session.decide",new JSONObject().put("session_id",session).put("approval_id",approval).put("revision",revision).put("allow",allow),id,session);}
    static final class SessionFailure extends Exception {
        final int http; final String code;
        SessionFailure(int http,String code){super(code);this.http=http;this.code=code;}
    }
}
