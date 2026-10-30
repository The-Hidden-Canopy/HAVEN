package org.thehiddencanopy.haven;

/** Fail-closed checks for the host-owned agent contract; never creates an agent. */
final class SessionRules {
    static final String VERSION="haven-session-1";
    static boolean accepts(String expectedSession,String responseSession,String version) {
        return VERSION.equals(version) && (expectedSession == null || expectedSession.equals(responseSession));
    }
    static boolean permitted(String operation, boolean listed, boolean durableReplay, boolean idempotentCommands) {
        if (!listed) return false;
        if ("replay".equals(operation) && !durableReplay) return false;
        if (("submit".equals(operation) || "cancel".equals(operation) || "decide".equals(operation)) && !idempotentCommands) return false;
        return true;
    }
    static boolean next(long last,long next) { return next == last + 1; }
}
