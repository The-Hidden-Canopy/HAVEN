package org.thehiddencanopy.haven;

/** Version and operation vocabulary shared by product screens. */
final class ProductRules {
    static final String VERSION = "haven-product-ui-1";
    static boolean accepts(String requested, String returned, String version) {
        return ("latch".equals(requested) || "harbor".equals(requested)) && requested.equals(returned) && VERSION.equals(version);
    }
    static boolean supports(String product, String capability) {
        if ("latch".equals(product)) return capability.equals("commitments") || capability.equals("access") || capability.equals("reviews") || capability.equals("plans") || capability.equals("evidence") || capability.equals("commitment_detail") || capability.equals("propose_cancellation");
        return "harbor".equals(product) && "metadata".equals(capability);
    }
}
