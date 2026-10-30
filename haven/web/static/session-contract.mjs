/** Provider-neutral session client. This client never creates a provider. */
export class SharedSessionClient {
  static version = "haven-session-1";
  constructor(transport) {
    if (!transport || typeof transport.call !== "function") throw new TypeError("transport required");
    this.transport = transport; this.operations = null; this.guarantees = null;
  }
  async call(operation, args, requestId, session = null) {
    if (typeof requestId !== "string" || !requestId) throw new TypeError("stable request ID required");
    if (operation !== "capabilities") {
      if (!this.operations || !this.operations.has(operation)) throw new Error("capability_unavailable");
      if (operation === "replay" && !this.guarantees.has("durable_replay")) throw new Error("capability_unavailable");
      if (["submit","cancel","decide"].includes(operation) && !this.guarantees.has("idempotent_commands")) throw new Error("capability_unavailable");
    }
    const result = await this.transport.call("haven.session." + operation, {...args, version: SharedSessionClient.version}, requestId);
    if (!result || result.ok !== true) throw new Error("session_unavailable_or_denied");
    if (result.contract !== SharedSessionClient.version || (session !== null && result.session_id !== session)) throw new Error("incompatible_session");
    return result;
  }
  async capabilities(requestId) {
    this.operations = this.guarantees = null;
    const result=await this.call("capabilities", {}, requestId);
    if (!Array.isArray(result.operations) || !Array.isArray(result.guarantees) || result.operations.some(x=>typeof x!=="string") || result.guarantees.some(x=>typeof x!=="string")) throw new Error("invalid_capabilities");
    this.operations=new Set(result.operations);this.guarantees=new Set(result.guarantees);return result;
  }
  sessions(requestId) { return this.call("sessions", {}, requestId); }
  snapshot(session, requestId) { return this.call("snapshot", {session_id: session}, requestId, session); }
  async replay(session, after, requestId) {
    if (!Number.isSafeInteger(after) || after < 0) throw new TypeError("invalid cursor");
    const result = await this.call("replay", {session_id: session, after, limit: 100}, requestId, session);
    if (!Array.isArray(result.events)) throw new Error("invalid_replay");
    let cursor = after;
    for (const event of result.events) {
      if (!event || !Number.isSafeInteger(event.seq) || event.seq !== cursor + 1) throw new Error("replay_gap");
      cursor = event.seq;
    }
    if (result.next_cursor !== cursor) throw new Error("invalid_cursor");
    return result;
  }
  commandStatus(session, commandId, requestId) { return this.call("command_status", {session_id: session, command_id: commandId}, requestId, session); }
  memory(session, requestId) { return this.call("memory", {session_id: session}, requestId, session); }
  submit(session, text, requestId) { return this.call("submit", {session_id: session, text}, requestId, session); }
  cancel(session, job, requestId) { return this.call("cancel", {session_id: session, job_id: job}, requestId, session); }
  decide(session, approval, revision, allow, requestId) { return this.call("decide", {session_id: session, approval_id: approval, revision, allow}, requestId, session); }
}
