package org.thehiddencanopy.haven;

import org.json.JSONObject;

/** Paired feature seam. The host resolves owner identity and every expiring grant. */
final class FeatureClient {
    static final String VERSION="haven-feature-ui-1";
    private final ProductTransport transport;
    private final String endpoint,credential;
    FeatureClient(ProductTransport transport,String endpoint,String credential) {
        this.transport=transport;this.endpoint=endpoint;this.credential=credential;
    }
    JSONObject catalog(String requestId) throws Exception {
        JSONObject result=transport.call(endpoint,credential,"haven.feature.catalog",new JSONObject().put("version",VERSION),requestId);
        if(result.optBoolean("ok") && !VERSION.equals(result.optString("contract")))throw new Exception("incompatible_feature_host");
        return result;
    }
    JSONObject call(String scope,String feature,String action,String command,JSONObject parameters,String requestId) throws Exception {
        if ((!"traceglass".equals(feature) && !"haven.ghost-teacher".equals(feature)) || scope==null || scope.isEmpty() || requestId==null || requestId.isEmpty())
            throw new IllegalArgumentException("invalid feature binding");
        JSONObject args=new JSONObject().put("version",VERSION).put("scope_id",scope).put("feature_id",feature)
            .put("action",action).put("command",command==null?JSONObject.NULL:command).put("parameters",parameters);
        JSONObject result=transport.call(endpoint,credential,"haven.feature.call",args,requestId);
        if (result.optBoolean("ok") && !VERSION.equals(result.optString("contract"))) throw new Exception("incompatible_feature_host");
        return result;
    }
}
