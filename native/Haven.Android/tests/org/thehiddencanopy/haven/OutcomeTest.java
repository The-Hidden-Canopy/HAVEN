package org.thehiddencanopy.haven;

public final class OutcomeTest {
    private static void expect(Outcome.Kind want, int http, boolean ok, String code, String status) {
        Outcome.Kind got=Outcome.classify(http,ok,code,status);
        if(got!=want) throw new AssertionError("expected " + want + " got " + got);
    }
    public static void main(String[] args) {
        expect(Outcome.Kind.PENDING,200,true,"","confirmation_required");
        expect(Outcome.Kind.REPORTED_EXECUTED,200,true,"","executed");
        expect(Outcome.Kind.CONFIRMATION_PROCESSED,200,true,"","confirmed");
        expect(Outcome.Kind.DENIED,200,true,"","denied");
        expect(Outcome.Kind.REJECTED,403,false,"external.scope_missing","");
        expect(Outcome.Kind.REJECTED,200,false,"","unknown request");
        expect(Outcome.Kind.UNCERTAIN,302,false,"","");
        expect(Outcome.Kind.UNCERTAIN,500,false,"outcome_uncertain","");
        expect(Outcome.Kind.UNCERTAIN,0,false,"","");
        expect(Outcome.Kind.UNCERTAIN,200,true,"","unrecognized");
        if(!Outcome.text(Outcome.Kind.CONFIRMATION_PROCESSED).contains("check HAVEN")) throw new AssertionError("confirmation is not proof of execution");
        if(!Outcome.text(Outcome.Kind.REPORTED_EXECUTED).contains("reported")) throw new AssertionError("execution must not claim independent verification");
        if (!PhonePresentation.serviceLabel("cover.close").equals("Request close")) throw new AssertionError("cover mapping");
        if (!PhonePresentation.serviceLabel("lock.unlock").isEmpty()) throw new AssertionError("unsupported action shown");
        String unknown = PhonePresentation.evidence("light",null,"unavailable","not supplied");
        if (!unknown.contains("Power: unknown") || !unknown.contains("Evidence: unavailable") || !unknown.contains("Observed: not supplied")) throw new AssertionError("unknown evidence misrepresented");
        if (!ProductRules.accepts("latch","latch","haven-product-ui-1")) throw new AssertionError("valid version refused");
        if (ProductRules.accepts("harbor","latch","haven-product-ui-1") || ProductRules.accepts("latch","latch","future")) throw new AssertionError("wrong adapter or version accepted");
        if (ProductRules.supports("latch","execute") || ProductRules.supports("harbor","unlock") || !ProductRules.supports("latch","propose_cancellation")) throw new AssertionError("product capability boundary");
        if (ThemeCatalog.IDS.length != 4 || !ThemeCatalog.validTheme("haven") || !ThemeCatalog.validTheme("canopy") || !ThemeCatalog.validTheme("ember") || !ThemeCatalog.validTheme("mono")) throw new AssertionError("theme identities");
        if (!"#081716".equals(ThemeCatalog.color("canopy",true,"HavenCanvasColor")) || !"#EA580C".equals(ThemeCatalog.color("ember",false,"HavenAccentColor"))) throw new AssertionError("desktop tokens");
        if (ThemeCatalog.color("unknown",false,"HavenCanvasColor") != null) throw new AssertionError("unknown theme");
        for (String theme : ThemeCatalog.IDS) for (boolean dark : new boolean[]{false,true}) if (ThemeCatalog.color(theme,dark,"HavenTextColor") == null) throw new AssertionError("missing palette");
        if (!SessionRules.accepts("shared","shared","haven-session-1") || SessionRules.accepts("shared","other","haven-session-1") || SessionRules.accepts("shared","shared","future")) throw new AssertionError("session contract validation");
        if (!SessionRules.next(2,3) || SessionRules.next(2,4) || SessionRules.next(2,2)) throw new AssertionError("event sequence gap");
        System.out.println("Outcome, session, product, theme, and presentation tests passed");
    }
}
