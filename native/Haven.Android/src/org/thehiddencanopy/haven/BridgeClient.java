package org.thehiddencanopy.haven;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** One request, no redirects and no automatic retries of mutations. */
final class BridgeClient implements ProductTransport {
    private HttpsURLConnection active;
    private boolean cancelled;
    static String newRequestId() { return UUID.randomUUID().toString(); }
    public JSONObject call(String endpoint, String token, String tool, JSONObject arguments, String requestId) throws Exception {
        URL url = new URL(endpoint);
        if (!"https".equalsIgnoreCase(url.getProtocol()) || url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null || !"/v1/tool".equals(url.getPath())) throw new IllegalArgumentException("Use an HTTPS bridge URL ending in /v1/tool");
        HttpsURLConnection connection = (HttpsURLConnection) url.openConnection();
        synchronized (this) {
            if (cancelled || Thread.currentThread().isInterrupted()) { connection.disconnect(); throw new java.io.IOException("Cancelled"); }
            active = connection;
        }
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000); connection.setReadTimeout(12000);
            connection.setRequestMethod("POST"); connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Content-Type", "application/json");
            byte[] payload = new JSONObject().put("tool", tool).put("request_id", requestId).put("arguments", arguments).toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(payload.length);
            ensureActive();
            try (OutputStream out = connection.getOutputStream()) { ensureActive(); out.write(payload); }
            ensureActive();
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            if (stream == null) throw new Exception("Bridge returned HTTP " + status);
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            try (InputStream in = stream) {
                byte[] buffer = new byte[4096]; int n;
                while ((n = in.read(buffer)) != -1) { if (data.size() + n > 1048576) throw new Exception("Response too large"); data.write(buffer, 0, n); }
            }
            JSONObject result = new JSONObject(data.toString("UTF-8"));
            result.put("http_status", status);
            return result;
        } finally { synchronized (this) { if (active == connection) active = null; } connection.disconnect(); }
    }
    private synchronized void ensureActive() throws java.io.IOException {
        if (cancelled || Thread.currentThread().isInterrupted()) throw new java.io.IOException("Cancelled");
    }
    public synchronized void cancel() { cancelled = true; if (active != null) active.disconnect(); }
}
