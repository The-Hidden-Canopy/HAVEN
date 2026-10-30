package org.thehiddencanopy.haven;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import java.net.URL;
import java.text.DateFormat;
import java.util.Date;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.json.JSONArray;
import org.json.JSONObject;

/** Foreground-only client. Every command goes through the scoped HAVEN bridge. */
public final class MainActivity extends Activity {
    private SecureStore secrets;
    private ExecutorService worker;
    private BridgeClient client;
    private Future<?> task;
    private boolean foreground, dark, inFlight;
    private String themeId = "haven";
    private int generation;
    private String page = "Home", connectionMessage = "Not connected";
    private JSONObject world, productDescriptor, productData, sessionCapabilities;
    private String currentProduct="", productSection="", productMessage="Not checked";
    private JSONArray localFeatures;
    private JSONObject selectedFeature, featureOutput;
    private String featureMessage="Check for authorized local features.";
    private JSONArray sessionsList, sessionMemory, sessionEvents=new JSONArray();
    private JSONObject sessionSnapshot;
    private String sessionId="", sessionStatus="Check for a provider-backed HAVEN session.", sessionDraft="";
    private long sessionCursor;
    private EditText sessionInput;
    private LinearLayout root, content, nav;
    private EditText endpointField, tokenField;

    private SharedPreferences prefs() { return getPreferences(0); }
    private int dp(int n) { return (int)(n * getResources().getDisplayMetrics().density + .5f); }
    private int token(String name) {
        String hex = ThemeCatalog.color(themeId, dark, name);
        if (hex == null) hex = ThemeCatalog.color("haven", dark, name);
        return Color.parseColor(hex);
    }
    private int fg() { return token("HavenTextColor"); }
    private int muted() { return token("HavenMutedTextColor"); }
    private int bg() { return token("HavenCanvasColor"); }
    private int cardColor() { return token("HavenSurface1Color"); }
    private int accent() { return token("HavenAccentColor"); }
    private GradientDrawable rounded(int color) { GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(14)); return d; }
    private String value(JSONObject obj, String key, String fallback) { Object x = obj.opt(key); return x == null || x == JSONObject.NULL || String.valueOf(x).isEmpty() ? fallback : String.valueOf(x); }
    private JSONObject json(String text) { try { return new JSONObject(text); } catch (Exception e) { return new JSONObject(); } }
    private boolean has(String key) { return !prefs().getString(key, "").isEmpty(); }

    @Override public void onCreate(Bundle saved) {
        String mode = prefs().getString("appearance_mode", null);
        if (mode == null) {
            int oldMode = prefs().getInt("theme", 0);
            mode = oldMode == 1 ? "light" : oldMode == 2 ? "dark" : "system";
            prefs().edit().putString("appearance_mode", mode).remove("theme").apply();
        }
        themeId = prefs().getString("theme_id", "haven");
        dark = mode.equals("dark") || (!mode.equals("light") && (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES);
        setTheme(dark ? android.R.style.Theme_Material_NoActionBar : android.R.style.Theme_Material_Light_NoActionBar);
        super.onCreate(saved);
        if (saved != null) page = saved.getString("page", "Home");
        if (page.equals("Latch")) currentProduct="latch"; else if (page.equals("Harbor")) currentProduct="harbor";
        secrets = new SecureStore(this);
        getWindow().setStatusBarColor(bg()); getWindow().setNavigationBarColor(bg());
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(bg()); setContentView(root);
        LinearLayout header = new LinearLayout(this); header.setPadding(dp(20),dp(15),dp(20),dp(8)); header.setOrientation(LinearLayout.VERTICAL);
        TextView title = text("HAVEN", 24, fg(), true); header.addView(title); header.addView(text("Your household, clearly in view", 13, muted(), false)); root.addView(header);
        nav = new LinearLayout(this); nav.setPadding(dp(8),0,dp(8),dp(6)); root.addView(nav);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(dp(16),dp(8),dp(16),dp(32)); scroll.addView(content);
        render();
    }
    @Override protected void onSaveInstanceState(Bundle out) { out.putString("page", page); super.onSaveInstanceState(out); }
    @Override protected void onStart() { super.onStart(); foreground = true; worker = Executors.newSingleThreadExecutor(); client = new BridgeClient(); render(); }
    @Override protected void onStop() { foreground = false; inFlight = false; generation++; if (client != null) client.cancel(); if (task != null) task.cancel(true); if (worker != null) worker.shutdownNow(); world = null; localFeatures=null;selectedFeature=null;featureOutput=null;productDescriptor=null; productData=null; productMessage="Foreground paused; check availability again."; sessionCapabilities=null; sessionsList=null; sessionMemory=null; sessionSnapshot=null; sessionEvents=new JSONArray(); sessionId=""; sessionCursor=0; sessionStatus="Foreground paused. Reconnect to the existing host."; connectionMessage = "Foreground paused; refresh to reconnect."; super.onStop(); }
    private TextView text(String s, int size, int color, boolean bold) { TextView t = new TextView(this); t.setText(s); t.setTextSize(size); t.setTextColor(color); if (bold) t.setTypeface(null, Typeface.BOLD); return t; }
    private void addText(LinearLayout parent, String s, int size, int color, boolean bold) { TextView t = text(s,size,color,bold); t.setPadding(0,dp(4),0,dp(4)); parent.addView(t); }
    private LinearLayout card() { LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(dp(16),dp(12),dp(16),dp(12)); box.setBackground(rounded(cardColor())); LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1,-2); p.setMargins(0,0,0,dp(12)); content.addView(box,p); return box; }
    private Button button(LinearLayout parent, String label, Runnable action) { Button b = new Button(this); b.setText(label); b.setAllCaps(false); b.setTextColor(fg()); b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(token("HavenSurface2Color"))); b.setOnClickListener(v -> action.run()); parent.addView(b, new LinearLayout.LayoutParams(-1, -2)); return b; }
    private void render() {
        if (content == null) return;
        nav.removeAllViews();
        for (String name : new String[]{"Home", "Rooms", "Activity", "Plugins", "Settings"}) {
            Button b = new Button(this); b.setText(name); b.setAllCaps(false); b.setTextSize(12); b.setPadding(0,0,0,0); b.setTextColor((page.equals(name) || (name.equals("Plugins") && (page.equals("Latch") || page.equals("Harbor")))) ? accent() : fg()); b.setContentDescription(name + (page.equals(name) ? ", selected" : ""));
            b.setOnClickListener(v -> { page = name; productData=null; productDescriptor=null; render(); }); nav.addView(b, new LinearLayout.LayoutParams(0,dp(48),1));
        }
        content.removeAllViews();
        if (page.equals("Local features")) renderLocalFeatures(); else if (page.equals("Rooms")) renderRooms(); else if (page.equals("Activity")) renderActivity(); else if (page.equals("Settings")) renderSettings(); else if (page.equals("Session")) renderSession(); else if (page.equals("Plugins")) renderProducts(); else if (page.equals("Latch") || page.equals("Harbor")) renderProduct(); else renderHome();
    }
    private void heading(String title, String subtitle) { addText(content,title,24,fg(),true); addText(content,subtitle,14,muted(),false); }
    private void renderHome() {
        heading("Home", "Evidence and requests from your connected HAVEN");
        LinearLayout status = card(); addText(status,"Connection",18,fg(),true); addText(status,connectionMessage,14,muted(),false);
        if (!has("endpoint")) { addText(status,"Start by entering an HTTPS bridge and its credential in Settings.",14,fg(),false); button(status,"Set up connection",() -> { page="Settings"; render(); }); }
        else { button(status,"Refresh household", () -> readWorld()); button(status,"Connection settings", () -> { page="Settings"; render(); }); }
        showAttention();
        LinearLayout agent=card(); addText(agent,"HAVEN session",18,fg(),true); addText(agent,"Open an existing provider-backed host session.",14,muted(),false); button(agent,"Open session",()->{page="Session";render();});
        LinearLayout summary = card(); addText(summary,"Household",18,fg(),true);
        if (world == null) addText(summary,"No current evidence. Refresh when connected.",14,muted(),false);
        else { JSONArray rooms = world.optJSONArray("rooms"); addText(summary,(rooms == null ? 0 : rooms.length()) + " rooms in the last response",15,fg(),false); addText(summary,"Evidence is a snapshot, not a live stream. Open Rooms for observation times and status.",14,muted(),false); button(summary,"View rooms",() -> {page="Rooms";render();}); }
        LinearLayout more = card(); addText(more,"Coming later",18,fg(),true); addText(more,"People, Automations and Memory are not available in this Android slice. Agent access requires a compatible host.",14,muted(),false);
    }
    private void showAttention() {
        if (has("uncertain")) { LinearLayout c=card(); addText(c,"Outcome uncertain",18,fg(),true); JSONObject u=json(prefs().getString("uncertain","")); addText(c,value(u,"description","A request") + " may have completed. Check HAVEN before taking another action.",14,fg(),false); button(c,"Review activity",()->{page="Activity";render();}); }
        else if (has("pending")) { LinearLayout c=card(); addText(c,"Confirmation waiting",18,fg(),true); addText(c,"Review the request in Activity. It may expire.",14,fg(),false); button(c,"Review request",()->{page="Activity";render();}); }
    }
    private void renderRooms() {
        heading("Rooms", "Device state is observed evidence, not a guarantee");
        LinearLayout top=card(); addText(top,connectionMessage,14,muted(),false); button(top,"Refresh evidence",()->readWorld());
        if (world == null) { LinearLayout c=card(); addText(c,"No household evidence yet. Connect and refresh.",15,fg(),false); return; }
        JSONArray rooms=world.optJSONArray("rooms"); if (rooms == null || rooms.length()==0) { LinearLayout c=card(); addText(c,"No rooms were returned.",15,muted(),false); return; }
        for (int i=0;i<rooms.length();i++) { JSONObject room=rooms.optJSONObject(i); if(room==null) continue; LinearLayout c=card(); addText(c,value(room,"name",value(room,"id","Room")),19,fg(),true); JSONArray devices=room.optJSONArray("devices"); if(devices==null || devices.length()==0) addText(c,"No device evidence in this room.",14,muted(),false); else for(int j=0;j<devices.length();j++){JSONObject d=devices.optJSONObject(j); if(d!=null) renderDevice(c,d);} }
    }
    private void renderDevice(LinearLayout c, JSONObject d) {
        String id=value(d,"id",""); String role=value(d,"role","Device");
        addText(c, id.isEmpty()?role:id.replace('_',' '),16,fg(),true);
        String status=value(d,"status","not supplied"), observed=value(d,"observed_at","not supplied");
        Boolean power = !d.has("is_on") || d.isNull("is_on") || !(d.opt("is_on") instanceof Boolean) ? null : d.optBoolean("is_on");
        addText(c,PhonePresentation.evidence(role,power,status,observed),13,muted(),false);
        if (!d.isNull("brightness_pct") && d.has("brightness_pct")) addText(c,"Brightness: " + d.optInt("brightness_pct") + "%",13,muted(),false);
        JSONArray services=d.optJSONArray("phone_services");
        if(id.isEmpty() || services==null || services.length()==0) {addText(c,"No supported phone action advertised.",13,muted(),false); return;}
        for(int n=0;n<services.length();n++){String service=services.optString(n,""); String label=PhonePresentation.serviceLabel(service); if(label.isEmpty()) continue; button(c,label,()->prepareAction(id,service,label));}
    }
    private void prepareAction(String id, String service, String label) {
        if(has("uncertain") || has("pending")) {new AlertDialog.Builder(this).setTitle("Review Activity first").setMessage("Resolve the existing pending or uncertain request before sending another action.").setPositiveButton("Open Activity",(d,w)->{page="Activity";render();}).setNegativeButton("Cancel",null).show(); return;}
        new AlertDialog.Builder(this).setTitle(label).setMessage(id.replace('_',' ') + "\n\nHAVEN will check authority and may require confirmation.").setNegativeButton("Cancel",null).setPositiveButton("Send request",(d,w)-> { try { JSONObject args=new JSONObject().put("device_id",id).put("service",service); invoke("haven.action.request",args,true,id + "  -  " + service); } catch(Exception ignored) {} }).show();
    }
    private void renderActivity() {
        heading("Activity", "Local request history; HAVEN remains the source of truth");
        if(has("uncertain")) {JSONObject u=json(prefs().getString("uncertain","")); LinearLayout c=card(); addText(c,"Outcome uncertain",18,fg(),true); addText(c,value(u,"description","Action") + "\nRequest: " + value(u,"id","unknown") + "\nCheck HAVEN and device evidence. An interrupted request may have completed.",14,fg(),false); button(c,"I checked HAVEN",()->new AlertDialog.Builder(this).setTitle("Clear local warning?").setMessage("This only clears the local warning and pending card; it does not cancel or verify an action in HAVEN.").setNegativeButton("Keep warning",null).setPositiveButton("Clear local warning",(d,w)->{prefs().edit().remove("uncertain").remove("pending").commit(); render();}).show());}
        else if(has("pending")) { JSONObject p=json(prefs().getString("pending","")); LinearLayout c=card(); addText(c,"Confirmation requested",18,fg(),true); addText(c,value(p,"description","Device action") + "\nExpires: " + value(p,"expires_at","not supplied") + "\nThis request may have expired; HAVEN decides whether it remains valid.",14,fg(),false); button(c,"Confirm",()->confirmPending(p,true)); button(c,"Deny",()->confirmPending(p,false)); button(c,"Remove local pending card",()->new AlertDialog.Builder(this).setTitle("Remove this card?").setMessage("This does not cancel or deny the request in HAVEN. Check HAVEN if you are unsure of its status.").setNegativeButton("Keep",null).setPositiveButton("Remove card",(d,w)->{prefs().edit().remove("pending").commit();render();}).show()); }
        LinearLayout c=card(); addText(c,"On this device",18,fg(),true);
        try { JSONArray events=new JSONArray(prefs().getString("activity","[]")); if(events.length()==0) addText(c,"No actions recorded here yet.",14,muted(),false); for(int i=events.length()-1;i>=0;i--){JSONObject e=events.optJSONObject(i); if(e!=null) addText(c,value(e,"time","") + "\n" + value(e,"description","Action") + "\n" + value(e,"result","Unknown"),14,fg(),false);} } catch(Exception e){addText(c,"Local activity could not be read.",14,muted(),false);}
        addText(c,"This is not a complete household audit or a device verification log.",13,muted(),false);
    }
    private void confirmPending(JSONObject p, boolean confirm) {
        String id=value(p,"request_id",""); if(id.isEmpty()) return;
        new AlertDialog.Builder(this).setTitle(confirm ? "Confirm request?" : "Deny request?").setMessage(value(p,"description","Device action") + "\nHAVEN will recheck authority and evidence.").setNegativeButton("Cancel",null).setPositiveButton(confirm?"Confirm":"Deny",(d,w)->{try {invoke(confirm?"haven.action.confirm":"haven.action.deny",new JSONObject().put("request_id",id),true,(confirm?"Confirm  -  ":"Deny  -  ")+value(p,"description","action"));}catch(Exception ignored) {}}).show();
    }
    private EditText input(LinearLayout parent,String hint,String text,boolean secret) { EditText e=new EditText(this); e.setSingleLine(true); e.setHint(hint); e.setText(text); if(secret)e.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD); parent.addView(e); return e; }
    private void renderSession() {
        heading("HAVEN session", "One host session across your interfaces");
        LinearLayout status=card();addText(status,sessionStatus,14,fg(),false);
        button(status,"Check provider availability",()->sessionCall("capabilities",null,null,0,false));
        if(sessionHas("sessions"))button(status,"Find existing sessions",()->sessionCall("sessions",null,null,0,false));
        addText(status,"A connected provider runs on its host. Closing this screen does not cancel a job. This phone creates no provider or separate memory.",13,muted(),false);
        if(sessionsList!=null)for(int i=0;i<sessionsList.length();i++){JSONObject item=sessionsList.optJSONObject(i);if(item==null)continue;String sid=value(item,"session_id","");if(!sid.isEmpty())button(status,"Open session " + sid,()->{if(inFlight){sessionStatus="Wait for the current request to finish.";render();return;}sessionId=sid;sessionCursor=0;sessionEvents=new JSONArray();sessionMemory=null;sessionSnapshot=null;sessionCall("snapshot",null,null,0,false);});}
        if(sessionId.isEmpty())return;
        if(has("uncertain")){JSONObject pending=json(prefs().getString("uncertain",""));String sid=value(pending,"session_id",""),command=value(pending,"id","");if(sid.equals(sessionId) && value(pending,"session_endpoint","").equals(prefs().getString("endpoint","")) && !command.isEmpty() && sessionHas("command_status")){LinearLayout review=card();addText(review,"Uncertain session request",18,fg(),true);addText(review,"Check the host's durable request record before dismissing the warning.",14,muted(),false);button(review,"Check request status",()->sessionCall("command_status",command,null,0,false));}}
        LinearLayout header=card();addText(header,"Session " + sessionId,17,fg(),true);if(sessionHas("snapshot"))button(header,"Refresh jobs & approvals",()->sessionCall("snapshot",null,null,0,false));if(sessionHas("replay"))button(header,"Replay conversation",()->sessionCall("replay",null,null,0,false));if(sessionHas("memory"))button(header,"Load provider memory",()->sessionCall("memory",null,null,0,false));
        LinearLayout conversation=card();addText(conversation,"Conversation",18,fg(),true);
        if(sessionEvents.length()==0)addText(conversation,"No replayed messages yet.",14,muted(),false);
        for(int i=0;i<sessionEvents.length();i++){JSONObject event=sessionEvents.optJSONObject(i);if(event==null || !"message".equals(value(event,"kind","")))continue;JSONObject data=event.optJSONObject("payload");if(data!=null)addText(conversation,value(data,"sender","agent") + ": " + value(data,"text",""),14,fg(),false);}
        if(sessionHas("submit")){sessionInput=input(conversation,"Message to the connected provider",sessionDraft,false);button(conversation,"Send message",()->{sessionDraft=sessionInput.getText().toString();if(!sessionDraft.trim().isEmpty())sessionCall("submit",sessionDraft,null,0,true);});}
        if(sessionMemory!=null){LinearLayout m=card();addText(m,"Session memory",18,fg(),true);if(sessionMemory.length()==0)addText(m,"No memory returned.",14,muted(),false);for(int i=0;i<sessionMemory.length();i++){JSONObject item=sessionMemory.optJSONObject(i);if(item!=null)addText(m,value(item,"text","Memory details unavailable"),14,fg(),false);}}
        if(sessionSnapshot==null)return;
        JSONArray jobs=sessionSnapshot.optJSONArray("jobs");LinearLayout jobCard=card();addText(jobCard,"Jobs",18,fg(),true);if(jobs==null || jobs.length()==0)addText(jobCard,"No jobs returned.",14,muted(),false);
        if(jobs!=null)for(int i=0;i<jobs.length();i++){JSONObject job=jobs.optJSONObject(i);if(job==null)continue;String jid=value(job,"job_id",""),state=value(job,"state","unknown");addText(jobCard,jid + "  -  " + state,14,fg(),false);if(sessionHas("cancel") && !jid.isEmpty() && (state.equals("queued")||state.equals("running")||state.equals("interrupted")))button(jobCard,"Request cancellation",()->new AlertDialog.Builder(this).setTitle("Request cancellation?").setMessage("HAVEN may already be executing this job. A request to cancel is not proof it stopped.").setNegativeButton("Back",null).setPositiveButton("Request",(d,w)->sessionCall("cancel",jid,null,0,true)).show());}
        JSONArray approvals=sessionSnapshot.optJSONArray("approvals");LinearLayout approvalCard=card();addText(approvalCard,"Approvals",18,fg(),true);if(approvals==null || approvals.length()==0)addText(approvalCard,"No approvals returned.",14,muted(),false);
        if(approvals!=null)for(int i=0;i<approvals.length();i++){JSONObject approval=approvals.optJSONObject(i);if(approval==null)continue;String aid=value(approval,"approval_id",""),state=value(approval,"status","unknown"),description=value(approval,"description","");int revision=approval.optInt("revision",-1);addText(approvalCard,(description.isEmpty()?"Approval details unavailable":description) + "\n" + state + "  -  expires " + value(approval,"expires_at","unknown"),14,fg(),false);if(sessionHas("decide") && !aid.isEmpty() && !description.isEmpty() && revision>=0 && state.equals("pending")){button(approvalCard,"Review approval",()->new AlertDialog.Builder(this).setTitle("Review approval").setMessage(description + "\nHAVEN must recheck its authority, expiry and revision.").setNegativeButton("Back",null).setNeutralButton("Deny",(d,w)->sessionCall("decide",aid,"deny",revision,true)).setPositiveButton("Approve",(d,w)->sessionCall("decide",aid,"allow",revision,true)).show());}}
    }
    private boolean sessionHas(String operation) {
        if(sessionCapabilities==null)return false;
        JSONArray ops=sessionCapabilities.optJSONArray("operations"), guarantees=sessionCapabilities.optJSONArray("guarantees");
        boolean listed=false,durable=false,idempotent=false;
        if(ops!=null)for(int i=0;i<ops.length();i++)if(operation.equals(ops.optString(i)))listed=true;
        if(guarantees!=null)for(int i=0;i<guarantees.length();i++){String g=guarantees.optString(i);if(g.equals("durable_replay"))durable=true;if(g.equals("idempotent_commands"))idempotent=true;}
        return SessionRules.permitted(operation,listed,durable,idempotent);
    }
    private void sessionCall(String operation,String arg,String decision,int revision,boolean mutation) {
        if(!foreground || inFlight){sessionStatus="Wait for the current request to finish.";render();return;}
        if(!operation.equals("capabilities") && !sessionHas(operation)){sessionStatus="Provider capability unavailable.";render();return;}
        if(mutation && (has("uncertain") || has("pending"))){page="Activity";render();return;}
        String endpoint=prefs().getString("endpoint",""), credential;
        try{JSONObject saved=json(secrets.load());credential=saved.optString("token","");if(!endpoint.equals(saved.optString("endpoint",""))||credential.isEmpty())throw new Exception();}catch(Exception e){sessionStatus="Set up the host connection in Settings.";render();return;}
        if(!operation.equals("sessions") && !operation.equals("capabilities") && sessionId.isEmpty()){sessionStatus="Choose an existing session first.";render();return;}
        String id=BridgeClient.newRequestId(), session=sessionId; long cursor=sessionCursor;
        if(mutation){try{JSONObject u=new JSONObject().put("id",id).put("session_id",session).put("session_endpoint",endpoint).put("description","Session " + operation + "  -  " + session);if(!prefs().edit().putString("uncertain",u.toString()).commit()){sessionStatus="Could not record request; nothing sent.";render();return;}}catch(Exception e){return;}}
        SessionClient api=new SessionClient(client,endpoint,credential);int turn=++generation;inFlight=true;sessionStatus="Contacting the session host - ";render();
        task=worker.submit(()->{JSONObject response=null;int failure=0;String failureCode="";try{switch(operation){case "capabilities":response=api.capabilities(id);break;case "sessions":response=api.sessions(id);break;case "snapshot":response=api.snapshot(session,id);break;case "replay":response=api.replay(session,cursor,id);break;case "memory":response=api.memory(session,id);break;case "command_status":response=api.commandStatus(session,arg,id);break;case "submit":response=api.submit(session,arg,id);break;case "cancel":response=api.cancel(session,arg,id);break;case "decide":response=api.decide(session,arg,revision,"allow".equals(decision),id);break;default:throw new Exception();}}catch(SessionClient.SessionFailure e){failure=e.http;failureCode=e.code;}catch(Exception ignored){}final JSONObject result=response;final int error=failure;final String code=failureCode;runOnUiThread(()->{if(!foreground || turn!=generation)return;inFlight=false;if(mutation)sessionMutationResult(operation,result,error,code);else if(page.equals("Session") && (operation.equals("capabilities") || operation.equals("sessions") || session.equals(sessionId)))sessionReadResult(operation,result,error);render();});});
    }
    private void sessionReadResult(String operation,JSONObject r,int error) {
        if(r==null){sessionCapabilities=null;sessionsList=null;sessionMemory=null;sessionSnapshot=null;sessionEvents=new JSONArray();sessionCursor=0;sessionStatus=error==401 || error==403?"Session access denied. Check the host permission.":"Session provider unavailable. Connect a compatible host.";return;}
        if(operation.equals("capabilities")){sessionCapabilities=r;sessionStatus="Provider capabilities received. Availability is not an authorization grant.";return;}
        if(operation.equals("sessions")){sessionsList=r.optJSONArray("sessions");sessionId="";sessionMemory=null;sessionEvents=new JSONArray();sessionCursor=0;sessionStatus=sessionsList==null?"Incompatible session list.":"Choose an existing session.";return;}
        if(operation.equals("snapshot")){sessionSnapshot=r;sessionStatus="Session connected. Replay conversation to view messages.";return;}
        if(operation.equals("command_status")){String state=value(r,"state","unknown");JSONObject recorded=r.optJSONObject("result");sessionStatus=state.equals("recorded") && recorded!=null ? "Host recorded: " + value(recorded,"state",value(recorded,"status","result recorded")) + ". Review before dismissing the warning." : "Host has no recorded result yet; outcome remains uncertain.";return;}
        if(operation.equals("memory")){sessionMemory=r.optJSONArray("items");sessionStatus=sessionMemory==null?"Incompatible memory response.":"Session memory received.";return;}
        JSONArray events=r.optJSONArray("events");if(events==null){sessionStatus="Incompatible replay response.";return;}
        JSONArray combined=new JSONArray();for(int i=0;i<sessionEvents.length();i++)combined.put(sessionEvents.opt(i));long last=sessionCursor;
        for(int i=0;i<events.length();i++){JSONObject e=events.optJSONObject(i);if(e==null){sessionStatus="Invalid replay; reconnect.";return;}long seq=e.optLong("seq",-1);if(!SessionRules.next(last,seq)){sessionEvents=new JSONArray();sessionCursor=0;sessionStatus="Replay gap. Reconnect and replay from the beginning.";return;}combined.put(e);last=seq;}
        if(r.optLong("next_cursor",-1)!=last){sessionEvents=new JSONArray();sessionCursor=0;sessionStatus="Invalid replay cursor. Reconnect and replay.";return;}
        JSONArray trimmed=new JSONArray();for(int i=Math.max(0,combined.length()-300);i<combined.length();i++)trimmed.put(combined.opt(i));sessionEvents=trimmed;sessionCursor=last;sessionStatus="Conversation replayed through event " + last + ".";
    }
    private void sessionMutationResult(String operation,JSONObject r,int error,String code) {
        boolean valid=r!=null;
        if(valid && operation.equals("submit"))valid=!value(r,"job_id","").isEmpty() && !value(r,"state","").isEmpty();
        if(valid && operation.equals("cancel"))valid=!value(r,"job_id","").isEmpty() && !value(r,"state","").isEmpty();
        if(valid && operation.equals("decide"))valid=!value(r,"approval_id","").isEmpty() && !value(r,"status","").isEmpty();
        boolean rejected=r==null && (error==400 || error==401 || error==403 || error==409) && (code.equals("not_executed") || code.equals("access_denied") || code.equals("stale_approval"));
        if(valid || rejected){if(!prefs().edit().remove("uncertain").commit()){sessionStatus="Could not record response; check the host.";page="Activity";return;}}
        if(valid){if(operation.equals("submit"))sessionDraft="";sessionSnapshot=null;sessionStatus=operation.equals("cancel")?"Cancellation request received; check job status.":operation.equals("decide")?"Decision returned; refresh approval and job status.":"Message accepted; refresh jobs and replay.";}
        else if(rejected)sessionStatus="Request denied or stale. Refresh the shared session.";
        else {sessionStatus="Outcome uncertain. Check the host before retrying.";page="Activity";}
    }

    private void renderProducts() {
        heading("Plugins", "Product integrations run through their own authority boundaries");
        LinearLayout l=card(); addText(l,"Latch",18,fg(),true); addText(l,"Commitments, access reviews and cancellation proposals.",14,muted(),false); button(l,"Open Latch",()->openProduct("latch"));
        LinearLayout h=card(); addText(h,"Harbor",18,fg(),true); addText(h,"Vault metadata and requests when a supported Harbor owner connection is available.",14,muted(),false); button(h,"Open Harbor",()->openProduct("harbor"));
        LinearLayout f=card(); addText(f,"Local features",18,fg(),true); button(f,"Open authorized local features",()->{page="Local features";selectedFeature=null;featureOutput=null;render();});
        LinearLayout x=card(); addText(x,"Other extensions",18,fg(),true); addText(x,"HAVEN also has providers, intelligence services, feature modules and export consumers. A catalog listing alone is not a connected product integration.",14,muted(),false);
    }
    private boolean featureAllows(String operation) {
        JSONArray ops=selectedFeature==null?null:selectedFeature.optJSONArray("operations");
        if(ops!=null)for(int i=0;i<ops.length();i++)if(operation.equals(ops.optString(i)))return true;
        return false;
    }
    private void renderLocalFeatures() {
        heading("Local features","Authorized interfaces to the paired local host");
        LinearLayout state=card();addText(state,featureMessage,14,fg(),false);
        button(state,"Refresh authorized features",()->featureCall("catalog",null,new JSONObject(),false));
        if(localFeatures!=null)for(int i=0;i<localFeatures.length();i++){
            final JSONObject row=localFeatures.optJSONObject(i);if(row==null)continue;
            button(state,value(row,"label","Local feature"),()->{selectedFeature=row;featureOutput=null;render();});
        }
        if(selectedFeature==null)return;
        LinearLayout controls=card();addText(controls,value(selectedFeature,"label","Local feature"),18,fg(),true);
        for(String action:new String[]{"stage","install","enable","disable","status"})if(featureAllows(action)){
            final String op=action;
            button(controls,op.equals("stage")?"Stage reviewed release":op,()->{
                JSONObject parameters=new JSONObject();try{if(op.equals("stage"))parameters.put("version","0.1.0");}catch(Exception ignored){}
                if(op.equals("status")){featureCall(op,null,parameters,false);return;}
                new AlertDialog.Builder(this).setTitle("Change local feature?").setMessage("Request " + op + " through HAVEN's current policy.").setNegativeButton("Cancel",null).setPositiveButton("Request",(d,w)->featureCall(op,null,parameters,true)).show();
            });
        }
        for(String command:new String[]{"catalog","sessions","dataset_catalog"})if(featureAllows("run."+command)){
            final String op=command;
            button(controls,command.replace('_',' '),()->{JSONObject params=new JSONObject();try{params.put("request",new JSONObject());}catch(Exception ignored){}featureCall("run",op,params,false);});
        }
        if(featureAllows("entitlement"))button(controls,"Check entitlement",()->featureCall("entitlement",null,new JSONObject(),false));
        if(featureAllows("picker"))button(controls,"Choose an authorized run",()->{JSONObject params=new JSONObject();try{params.put("kind","run").put("offset",0).put("limit",25);}catch(Exception ignored){}featureCall("picker",null,params,false);});
        if(featureOutput!=null){
            JSONArray items=featureOutput.optJSONArray("items");
            if(items!=null && featureAllows("run.inspect"))for(int i=0;i<items.length();i++){
                final JSONObject item=items.optJSONObject(i);if(item==null)continue;
                button(controls,"Inspect " + value(item,"label","run"),()->{JSONObject params=new JSONObject();try{params.put("contract","traceglass.request.v1").put("artifact",item.getString("handle")).put("offset",0).put("limit",25);}catch(Exception ignored){}featureCall("run","inspect",params,false);});
            }
            addText(controls,featureOutput.toString(),14,fg(),false);
        }
    }
    private void featureCall(String action,String command,JSONObject parameters,boolean mutation) {
        if(!foreground || inFlight)return;
        if(mutation && (has("uncertain") || has("pending"))){page="Activity";render();return;}
        String endpoint=prefs().getString("endpoint",""),credential;
        try{JSONObject saved=json(secrets.load());credential=saved.getString("token");if(!endpoint.equals(saved.getString("endpoint")))throw new Exception();}catch(Exception e){featureMessage="Set up the existing paired connection in Settings.";render();return;}
        final String requestId=BridgeClient.newRequestId();
        final JSONObject selection=selectedFeature;
        if(!action.equals("catalog") && selection==null)return;
        if(mutation){try{if(!prefs().edit().putString("uncertain",new JSONObject().put("id",requestId).put("description","Local feature " + action).toString()).commit())return;}catch(Exception e){return;}}
        final int turn=++generation;inFlight=true;featureMessage="Contacting the local feature host.";featureOutput=null;render();
        final FeatureClient api=new FeatureClient(client,endpoint,credential);
        task=worker.submit(()->{JSONObject response=null;try{response=action.equals("catalog")?api.catalog(requestId):api.call(selection.getString("scope_id"),selection.getString("feature_id"),action,command,parameters,requestId);}catch(Exception ignored){}
            final JSONObject result=response;runOnUiThread(()->{if(!foreground || turn!=generation)return;inFlight=false;
                boolean success=result!=null && result.optBoolean("ok");int http=result==null?0:result.optInt("http_status");
                if(mutation && (success || http==400 || http==401 || http==403))prefs().edit().remove("uncertain").commit();
                featureMessage=success?"Host response received.":mutation?"Outcome uncertain or denied. Inspect current feature state before retrying.":"Feature unavailable or access denied.";
                if(success && page.equals("Local features")){if(action.equals("catalog")){localFeatures=result.optJSONArray("items");selectedFeature=null;featureOutput=null;}else if(selectedFeature==selection)featureOutput=result;}
                render();});});
    }
    private void openProduct(String id) {currentProduct=id; productDescriptor=null; productData=null; productSection=""; productMessage="Not checked"; page=id.equals("latch")?"Latch":"Harbor";render();}
    private boolean productHas(String capability) {
        if(productDescriptor==null || !ProductRules.supports(currentProduct,capability) || !"configured".equals(value(productDescriptor,"availability",""))) return false;
        JSONArray caps=productDescriptor.optJSONArray("capabilities"); if(caps==null)return false; for(int i=0;i<caps.length();i++) if(capability.equals(caps.optString(i)))return true; return false;
    }
    private void renderProduct() {
        heading(currentProduct.equals("latch")?"Latch":"Harbor", currentProduct.equals("latch")?"Commitments and access reviews":"Metadata only  -  Harbor retains authority");
        LinearLayout status=card(); addText(status,productMessage,14,fg(),false); button(status,"Check availability",()->productCall("haven.product.describe",null,null,false)); button(status,"All plugins",()->{page="Plugins";productData=null;render();});
        if(currentProduct.equals("latch")) {
            LinearLayout navCard=card(); addText(navCard,"Latch",18,fg(),true);
            if(productHas("commitments"))button(navCard,"Commitments & renewals",()->productCall("haven.product.read","commitments",null,false));
            if(productHas("access"))button(navCard,"Access grants",()->productCall("haven.product.read","access",null,false));
            if(productHas("reviews"))button(navCard,"Review proposals",()->productCall("haven.product.read","reviews",null,false));
            if(productHas("plans"))button(navCard,"Action plans",()->productCall("haven.product.read","plans",null,false));
            addText(navCard,"A cancellation proposal is not authorization, execution or verification. Continue those steps in Latch.",13,muted(),false);
            button(navCard,"Latch handoff help",()->new AlertDialog.Builder(this).setTitle("Continue in Latch").setMessage("Open a supported Latch client and review the plan there. An Android deep link is not configured.").setPositiveButton("OK",null).show());
            renderLatchData();
        } else {
            LinearLayout navCard=card(); addText(navCard,"Harbor",18,fg(),true);
            if(productHas("metadata"))button(navCard,"Refresh metadata",()->productCall("haven.product.read","metadata",null,false));
            addText(navCard,"Verification and unlock are not available here. Use Harbor's supported owner flow; Android device unlock does not grant Harbor access.",14,muted(),false);
            button(navCard,"Verification handoff help",()->new AlertDialog.Builder(this).setTitle("Verify in Harbor").setMessage("Continue with a supported Harbor owner client. No Android verification transport or automatic handoff is configured.").setPositiveButton("OK",null).show());
            renderHarborData();
        }
    }
    private void renderLatchData() {
        if(productData==null)return;
        if(productSection.equals("evidence") || productSection.equals("commitment_detail")) {LinearLayout c=card(); addText(c,productSection.equals("evidence")?"Evidence details":"Commitment details",18,fg(),true); JSONObject item=productData.optJSONObject("item"); addText(c,item==null?"Details unavailable.":item.toString(),14,fg(),false); return;}
        JSONArray items=productData.optJSONArray("items"); if(items==null){LinearLayout c=card();addText(c,"This response is unavailable or incompatible.",14,muted(),false);return;}
        if(items.length()==0){LinearLayout c=card();addText(c,"No items returned.",14,muted(),false);return;}
        for(int i=0;i<items.length();i++){JSONObject item=items.optJSONObject(i);if(item==null)continue;LinearLayout c=card();
            if(productSection.equals("commitments")) {
                String id=value(item,"commitment_id",""); addText(c,value(item,"provider","Commitment"),18,fg(),true); addText(c,value(item,"kind","Commitment") + "  -  Next event: " + value(item,"next_event_at","not supplied") + "\n" + value(item,"message",""),14,muted(),false);
                if(!id.isEmpty() && productHas("commitment_detail"))button(c,"View details",()->productCall("haven.product.read","commitment_detail",id,false));
                if(!id.isEmpty() && productHas("propose_cancellation"))button(c,"Propose cancellation",()->new AlertDialog.Builder(this).setTitle("Propose cancellation?").setMessage("Latch will create or return a proposal. It will not authorize, execute or verify a cancellation here.").setNegativeButton("Cancel",null).setPositiveButton("Propose",(d,w)->productCall("haven.product.propose","propose_cancellation",id,true)).show());
            } else if(productSection.equals("access")) {
                JSONObject who=item.optJSONObject("grantee"), resource=item.optJSONObject("resource"); addText(c,"Access grant",18,fg(),true); addText(c,"Grantee: " + (who==null?"unknown":value(who,"id","unknown")) + "\nResource: " + (resource==null?"unknown":value(resource,"id","unknown")) + "\nStale: " + value(item,"stale","unknown") + "\nLast used: " + value(item,"last_used_at","not supplied"),14,muted(),false);
            } else if(productSection.equals("reviews")) {
                addText(c,"Review proposal",18,fg(),true); addText(c,"Status: " + value(item,"status","unknown") + "\n" + value(item,"summary",""),14,muted(),false); JSONArray evidence=item.optJSONArray("evidence"); if(evidence!=null)for(int j=0;j<evidence.length();j++){JSONObject e=evidence.optJSONObject(j);if(e==null)continue;String eid=value(e,"id",value(e,"evidence_id",""));addText(c,"Evidence: " + value(e,"source",value(e,"source_connector","unknown source")) + "  -  " + value(e,"freshness","unknown freshness") + "\nObserved: " + value(e,"acquired_at","not supplied"),13,muted(),false);if(!eid.isEmpty() && productHas("evidence"))button(c,"Evidence details",()->productCall("haven.product.read","evidence",eid,false));}
            } else {addText(c,"Action plan",18,fg(),true);addText(c,"Status: " + value(item,"status","unknown") + "\nPlan: " + value(item,"plan_id","not supplied"),14,muted(),false);}
        }
    }
    private void renderHarborData() {
        if(productData==null)return; String state=value(productData,"state","unavailable"); LinearLayout s=card(); addText(s,"Harbor: " + state,18,fg(),true);
        if(!"unlocked".equals(state)){addText(s,"Metadata is not available. Complete any required verification in Harbor.",14,muted(),false);return;}
        JSONObject snap=productData.optJSONObject("snapshot"); if(snap==null){addText(s,"Metadata response unavailable.",14,muted(),false);return;}
        harborRows(snap,"objects","Vault objects",new String[]{"display_label","object_type"});
        harborRows(snap,"requests","Request audit  -  not a pending approval queue",new String[]{"caller_app_id","purpose","operation","decision"});
        harborRows(snap,"app_grants","Applications & grants",new String[]{"display_name","status","trust_level"});
        harborRows(snap,"history","Activity",new String[]{"operation","decision","created_at"});
    }
    private void harborRows(JSONObject snapshot,String key,String title,String[] fields) {
        LinearLayout c=card();addText(c,title,17,fg(),true);JSONArray items=snapshot.optJSONArray(key);if(items==null){addText(c,"Unavailable",14,muted(),false);return;}if(items.length()==0){addText(c,"None returned",14,muted(),false);return;}
        for(int i=0;i<items.length();i++){JSONObject item=items.optJSONObject(i);if(item==null)continue;StringBuilder line=new StringBuilder();for(String field:fields){String v=value(item,field,"");if(!v.isEmpty()){if(line.length()>0)line.append("  -  ");line.append(v);}}addText(c,line.length()==0?"Metadata unavailable":line.toString(),14,fg(),false);}
    }
    private void productCall(String tool,String section,String itemId,boolean mutation) {
        if(!foreground || inFlight){productMessage="Wait for the current request to finish.";render();return;}
        if(mutation && (has("uncertain") || has("pending"))){page="Activity";render();return;}
        String url=prefs().getString("endpoint",""), credential;
        try{JSONObject saved=json(secrets.load());credential=saved.optString("token","");if(!url.equals(saved.optString("endpoint","")) || credential.isEmpty())throw new Exception();}catch(Exception e){productMessage="Set up a connection in Settings.";render();return;}
        final String id=BridgeClient.newRequestId(), product=currentProduct; JSONObject args=new JSONObject();
        try{args.put("id",product).put("version",ProductRules.VERSION);if(section!=null)args.put("section",section);if(itemId!=null)args.put("item_id",itemId);if(mutation && !prefs().edit().putString("uncertain",new JSONObject().put("id",id).put("description","Latch cancellation proposal  -  " + itemId).toString()).commit()){productMessage="Could not record request; no proposal sent.";render();return;}}catch(Exception e){return;}
        final int turn=++generation; final ProductTransport transport=client; inFlight=true;productMessage="Contacting integration - ";productData=null;render();
        task=worker.submit(()->{JSONObject response=null;try{response=transport.call(url,credential,tool,args,id);}catch(Exception ignored){}final JSONObject result=response;runOnUiThread(()->{if(!foreground || turn!=generation)return;inFlight=false;if(mutation){handleProductProposal(result,itemId);}else if(currentProduct.equals(product) && (page.equals("Latch") || page.equals("Harbor"))){handleProductRead(result,product,section);}render();});});
    }
    private void handleProductRead(JSONObject result,String product,String section) {
        if(result==null || result.optInt("http_status",0)!=200 || !result.optBoolean("ok",false)){productDescriptor=null;productData=null;productMessage=result!=null && result.optInt("http_status")==403 ? "Product access denied. An owner must grant access." : "Integration unavailable or disconnected. Check Settings and the product host.";return;}
        JSONObject descriptor=result.optJSONObject("descriptor");
        if(descriptor==null || !ProductRules.accepts(product,value(descriptor,"id",""),value(descriptor,"version",""))){productDescriptor=null;productData=null;productMessage="Integration version is incompatible.";return;}
        productDescriptor=descriptor; productMessage="Status: " + value(descriptor,"availability","unavailable") + "  -  " + ProductRules.VERSION;productSection=section==null?"":section;productData=result.optJSONObject("data");
    }
    private void handleProductProposal(JSONObject result,String itemId) {
        int http=result==null?0:result.optInt("http_status",0); JSONObject data=result==null?null:result.optJSONObject("data"); JSONObject plan=data==null?null:data.optJSONObject("item");
        boolean definitive=http==200 && result.optBoolean("ok",false) && plan!=null && !value(plan,"status","").isEmpty();
        boolean rejected=result!=null && (http==400 || http==401 || http==403) && result.opt("ok") instanceof Boolean && !result.optBoolean("ok",true);
        String detail=definitive?"Latch reports plan status: " + value(plan,"status","unknown") + "  -  Plan: " + value(plan,"plan_id","not supplied") + ". Authorization, execution and verification remain in Latch.":rejected?"Proposal rejected or not authorized.":"Proposal outcome uncertain; check Latch before trying again.";
        JSONArray events;try{events=new JSONArray(prefs().getString("activity","[]"));}catch(Exception e){events=new JSONArray();}JSONArray trimmed=new JSONArray();for(int i=Math.max(0,events.length()-19);i<events.length();i++)trimmed.put(events.opt(i));try{trimmed.put(new JSONObject().put("time",DateFormat.getDateTimeInstance().format(new Date())).put("description","Latch cancellation proposal  -  " + itemId).put("result",detail));}catch(Exception ignored){}
        SharedPreferences.Editor edit=prefs().edit().putString("activity",trimmed.toString());if(definitive || rejected)edit.remove("uncertain");if(!edit.commit())connectionMessage="Could not save response; check Latch.";else connectionMessage=detail;productData=null;page="Activity";
    }

    private void renderSettings() {
        heading("Settings & help", "Connect to a configured HAVEN bridge");
        LinearLayout c=card(); addText(c,"Appearance",18,fg(),true);
        addText(c,"Theme",14,muted(),false);
        java.util.ArrayList<String> names=new java.util.ArrayList<>(); for(String name:ThemeCatalog.NAMES)names.add(name);
        int selected=-1; for(int i=0;i<ThemeCatalog.IDS.length;i++)if(ThemeCatalog.IDS[i].equals(themeId))selected=i;
        if(selected<0){names.add("Unavailable theme: " + themeId);selected=names.size()-1;}
        Spinner themes=new Spinner(this); themes.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names)); themes.setSelection(selected);
        themes.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){public void onNothingSelected(android.widget.AdapterView<?> p){} public void onItemSelected(android.widget.AdapterView<?> p,View v,int i,long id){if(i<ThemeCatalog.IDS.length && !ThemeCatalog.IDS[i].equals(themeId)){prefs().edit().putString("theme_id",ThemeCatalog.IDS[i]).apply();recreate();}}});c.addView(themes);
        if(!ThemeCatalog.validTheme(themeId))addText(c,"The saved theme is unavailable on this device. Haven colors are shown temporarily; the saved choice is retained.",13,muted(),false);
        addText(c,"Mode",14,muted(),false);
        String savedMode=prefs().getString("appearance_mode","system"); int modeIndex=-1;
        java.util.ArrayList<String> modeNames=new java.util.ArrayList<>(); for(int i=0;i<ThemeCatalog.MODES.length;i++){String m=ThemeCatalog.MODES[i];modeNames.add(m.substring(0,1).toUpperCase()+m.substring(1));if(m.equals(savedMode))modeIndex=i;}
        if(modeIndex<0){modeNames.add("Unavailable mode: " + savedMode);modeIndex=modeNames.size()-1;}
        Spinner modes=new Spinner(this); modes.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,modeNames)); modes.setSelection(modeIndex);
        modes.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){public void onNothingSelected(android.widget.AdapterView<?> p){} public void onItemSelected(android.widget.AdapterView<?> p,View v,int i,long id){String old=prefs().getString("appearance_mode","system");if(i<ThemeCatalog.MODES.length && !ThemeCatalog.MODES[i].equals(old)){prefs().edit().putString("appearance_mode",ThemeCatalog.MODES[i]).apply();recreate();}}});c.addView(modes);
        c=card(); addText(c,"1. Get a bridge connection",18,fg(),true); addText(c,"Use an explicitly configured HTTPS bridge and credential. Demo mode is synthetic; live household pairing is not yet available.",14,muted(),false);
        addText(c,"2. Enter connection details",18,fg(),true); endpointField=input(c,"https://host/v1/tool",prefs().getString("endpoint",""),false); String saved=""; try{saved=json(secrets.load()).optString("token", "");}catch(Exception e){connectionMessage="Saved credential unavailable. Enter it again.";} tokenField=input(c,"Bridge credential",saved,true);
        button(c,"Save and test connection",()->saveConnection()); button(c,"Forget connection",()->new AlertDialog.Builder(this).setTitle("Forget connection?").setMessage("This removes the credential from this device. It does not revoke the connection in HAVEN or cancel pending requests.").setNegativeButton("Cancel",null).setPositiveButton("Forget",(d,w)->forgetConnection()).show());
        LinearLayout help=card(); addText(help,"3. If connection fails",18,fg(),true); addText(help,"Check that the bridge is running, the HTTPS address and credential are correct, and Android trusts the matching certificate. Then test again. Access denied means HAVEN did not grant the requested scope.\n\nOnly foreground requests run. No action is retried automatically. The app does not yet pair a live household or provide chat, People, Automations, or Memory.",14,muted(),false);
    }
    private void saveConnection() {
        if (inFlight) { connectionMessage="Wait for the current request to finish."; return; }
        final String url=endpointField.getText().toString().trim(), token=tokenField.getText().toString();
        try { URL parsed=new URL(url); if(!"https".equalsIgnoreCase(parsed.getProtocol()) || parsed.getHost().isEmpty() || parsed.getUserInfo()!=null || parsed.getQuery()!=null || parsed.getRef()!=null || !"/v1/tool".equals(parsed.getPath()) || token.trim().isEmpty()) throw new IllegalArgumentException("Enter an HTTPS /v1/tool URL and a credential."); }
        catch(Exception e){new AlertDialog.Builder(this).setMessage("Enter a valid HTTPS /v1/tool URL and a credential.").setPositiveButton("OK",null).show();return;}
        String old=""; try{old=json(secrets.load()).optString("token", "");}catch(Exception ignored){}
        boolean changed=!url.equals(prefs().getString("endpoint","")) || !token.equals(old);
        Runnable save=()->{try{secrets.save(new JSONObject().put("endpoint",url).put("token",token).toString()); if(!prefs().edit().putString("endpoint",url).remove("pending").commit())throw new Exception(); world=null; connectionMessage="Connection saved; testing - "; page="Home";render();readWorld();}catch(Exception e){connectionMessage="Could not save connection securely."; render();}};
        if(changed && has("pending")) new AlertDialog.Builder(this).setTitle("Change connection?").setMessage("Changing connections removes the local pending card but does not cancel the request in HAVEN. Check the original HAVEN instance.").setNegativeButton("Cancel",null).setPositiveButton("Change",(d,w)->save.run()).show(); else if(!changed) {page="Home";render();readWorld();} else save.run();
    }
    private void forgetConnection() { if (inFlight) { connectionMessage="Wait for the current request to finish."; return; } try { secrets.clear(); prefs().edit().remove("endpoint").remove("pending").commit(); world=null; productDescriptor=null; productData=null; sessionCapabilities=null; sessionsList=null; sessionSnapshot=null; sessionMemory=null; sessionEvents=new JSONArray(); sessionId=""; sessionCursor=0; connectionMessage="Connection forgotten on this device."; page="Home";render(); } catch(Exception e){connectionMessage="Could not forget connection.";render();} }
    private void readWorld() { invoke("haven.world.get",new JSONObject(),false,"Refresh household"); }
    private void invoke(String tool, JSONObject args, boolean mutation, String description) {
        if(!foreground || inFlight) {connectionMessage="A request is already running.";render();return;}
        String url=prefs().getString("endpoint", ""), credential; try{JSONObject stored=json(secrets.load()); credential=stored.optString("token", ""); if(!url.equals(stored.optString("endpoint", ""))) throw new Exception("Connection mismatch");}catch(Exception e){connectionMessage="Saved connection unavailable. Re-enter it in Settings.";render();return;}
        if(url.isEmpty() || credential.isEmpty()){connectionMessage="Set up a connection in Settings.";render();return;}
        if(mutation && has("uncertain")){page="Activity";render();return;}
        final String id=BridgeClient.newRequestId();
        if(mutation){try{JSONObject u=new JSONObject().put("id",id).put("description",description); if(!prefs().edit().putString("uncertain",u.toString()).commit()) {connectionMessage="Could not record request safely; no action sent.";render();return;}}catch(Exception e){return;}}
        final int turn=++generation; final BridgeClient transport=client; inFlight=true; connectionMessage="Contacting HAVEN..."; render();
        task=worker.submit(()->{JSONObject response=null; try{response=transport.call(url,credential,tool,args,id);}catch(Exception ignored){} final JSONObject result=response; runOnUiThread(()->{if(!foreground || turn!=generation)return; inFlight=false; if(!mutation) handleWorld(result); else handleAction(tool,description,result); render();});});
    }
    private void handleWorld(JSONObject r) {
        if(r!=null && r.optInt("http_status",0)==200 && r.optBoolean("ok",false) && r.optJSONObject("state")!=null) { world=r.optJSONObject("state"); connectionMessage="Connected  -  evidence received " + DateFormat.getTimeInstance(DateFormat.SHORT).format(new Date()); }
        else {world=null; connectionMessage=r!=null && (r.optInt("http_status")==401 || r.optInt("http_status")==403) ? "Access denied. The credential may be invalid, expired, revoked, or missing a scope. Check Settings and ask the owner about pairing." : "Could not connect. Check address, certificate and network in Settings.";}
    }
    private void handleAction(String tool,String description,JSONObject r) {
        int http=r==null?0:r.optInt("http_status",0); if (r != null && !(r.opt("ok") instanceof Boolean)) http=0; boolean ok=r!=null && r.optBoolean("ok",false); String code=r==null?"":value(r,"code",""); String status=r==null?"":value(r,"status","");
        Outcome.Kind kind=Outcome.classify(http,ok,code,status); JSONObject pending=null;
        if(kind==Outcome.Kind.PENDING){String req=value(r,"request_id",""); if(req.isEmpty() || !"haven.action.request".equals(tool)) kind=Outcome.Kind.UNCERTAIN; else try{pending=new JSONObject().put("request_id",req).put("expires_at",value(r,"expires_at","not supplied")).put("description",description);}catch(Exception e){kind=Outcome.Kind.UNCERTAIN;}}
        String detail=Outcome.text(kind); if(r!=null && !value(r,"receipt_id","").isEmpty()) detail += "  -  Receipt: " + value(r,"receipt_id",""); if(r!=null && kind==Outcome.Kind.REJECTED) detail += ": " + value(r,"error",value(r,"code","not authorized or invalid")); if(r!=null && kind==Outcome.Kind.DENIED) detail += "  -  " + value(r,"reason","HAVEN denied the request");
        JSONArray events; try{events=new JSONArray(prefs().getString("activity","[]"));}catch(Exception e){events=new JSONArray();}
        JSONArray trimmed=new JSONArray(); for(int i=Math.max(0,events.length()-19);i<events.length();i++) trimmed.put(events.opt(i)); try{trimmed.put(new JSONObject().put("time",DateFormat.getDateTimeInstance().format(new Date())).put("description",description).put("result",detail));}catch(Exception ignored){}
        SharedPreferences.Editor edit=prefs().edit().putString("activity",trimmed.toString());
        if(kind!=Outcome.Kind.UNCERTAIN) edit.remove("uncertain");
        if(pending!=null) edit.putString("pending",pending.toString());
        else if(("haven.action.confirm".equals(tool)||"haven.action.deny".equals(tool)) && (kind==Outcome.Kind.CONFIRMATION_PROCESSED || kind==Outcome.Kind.DENIED)) edit.remove("pending");
        if(!edit.commit()) connectionMessage="Could not save response; check HAVEN before another action."; else connectionMessage=detail;
        world=null; page="Activity";
    }
}
