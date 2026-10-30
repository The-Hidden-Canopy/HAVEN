package org.thehiddencanopy.haven;

/** Classifies what the bridge actually reported; it does not infer a device consequence. */
final class Outcome {
    enum Kind { PENDING, REPORTED_EXECUTED, CONFIRMATION_PROCESSED, DENIED, REJECTED, UNCERTAIN }
    static Kind classify(int http, boolean ok, String code, String status) {
        if (http >= 500 || "outcome_uncertain".equals(code)) return Kind.UNCERTAIN;
        if (!ok) return (http >= 200 && http < 300) || (http >= 400 && http < 500) ? Kind.REJECTED : Kind.UNCERTAIN;
        if (http < 200 || http >= 300) return Kind.UNCERTAIN;
        if ("confirmation_required".equals(status)) return Kind.PENDING;
        if ("executed".equals(status)) return Kind.REPORTED_EXECUTED;
        if ("confirmed".equals(status)) return Kind.CONFIRMATION_PROCESSED;
        if ("denied".equals(status)) return Kind.DENIED;
        return Kind.UNCERTAIN;
    }
    static String text(Kind kind) {
        switch (kind) {
            case PENDING: return "Awaiting confirmation";
            case REPORTED_EXECUTED: return "HAVEN reported executed; check device evidence for the consequence";
            case CONFIRMATION_PROCESSED: return "Confirmation processed; check HAVEN and device evidence for the outcome";
            case DENIED: return "Denied";
            case REJECTED: return "Request rejected";
            default: return "Outcome uncertain; check HAVEN before another action";
        }
    }
}
