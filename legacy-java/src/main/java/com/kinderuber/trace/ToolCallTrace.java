package com.kinderuber.trace;

public record ToolCallTrace(String toolName, boolean success, boolean fallbackUsed, boolean cached,
                            boolean reranked, long latencyMs, String error, String source, String server, String remoteName) {
    public ToolCallTrace(String name, boolean success, boolean fallback, boolean cached, boolean reranked, long latency, String error) {
        this(name, success, fallback, cached, reranked, latency, error, "local", "", "");
    }
}
