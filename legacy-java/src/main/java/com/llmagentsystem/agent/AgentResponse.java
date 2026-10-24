package com.llmagentsystem.agent;

public record AgentResponse(
        AgentType agentType,
        String content,
        boolean success,
        double confidence,
        long latencyMs,
        boolean escalate,
        String toolName,
        boolean toolSuccess,
        boolean toolCached,
        boolean toolReranked,
        String toolError,
        java.util.List<com.llmagentsystem.trace.ToolCallTrace> toolCalls
) {
    public AgentResponse(AgentType type, String content, boolean success, double confidence, long latency, boolean escalate,
                         String toolName, boolean toolSuccess, boolean cached, boolean reranked, String error) {
        this(type, content, success, confidence, latency, escalate, toolName, toolSuccess, cached, reranked, error, java.util.List.of());
    }
}
