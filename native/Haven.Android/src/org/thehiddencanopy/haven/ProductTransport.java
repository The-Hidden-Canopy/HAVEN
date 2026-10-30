package org.thehiddencanopy.haven;

import org.json.JSONObject;

/** Authenticated host transport; it does not confer a product grant. */
interface ProductTransport {
    JSONObject call(String endpoint, String token, String tool, JSONObject arguments, String requestId) throws Exception;
    void cancel();
}
