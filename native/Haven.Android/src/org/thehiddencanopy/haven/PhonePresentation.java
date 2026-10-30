package org.thehiddencanopy.haven;

/** Presentation only: supported actions are still checked by HAVEN. */
final class PhonePresentation {
    static String serviceLabel(String service) {
        switch(service) {
            case "light.turn_on": case "switch.turn_on": case "fan.turn_on": return "Request turn on";
            case "light.turn_off": case "switch.turn_off": case "fan.turn_off": return "Request turn off";
            case "cover.open": return "Request open";
            case "cover.close": return "Request close";
            default: return "";
        }
    }
    static String evidence(String role, Boolean on, String status, String observed) {
        String power = on == null ? "unknown" : on ? "on" : "off";
        return role + " · Power: " + power + "\nEvidence: " + status + "\nObserved: " + observed;
    }
}
