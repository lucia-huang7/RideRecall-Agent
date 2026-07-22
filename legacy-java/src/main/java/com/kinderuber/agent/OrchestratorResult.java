package com.kinderuber.agent;

import com.kinderuber.intent.IntentCategory;
import com.kinderuber.trace.ToolCallTrace;

import java.util.List;

public record OrchestratorResult(
        String requestId,
        String response,
        AgentType agentType,
        IntentCategory intent,
        boolean escalated,
        long latencyMs,
        List<AgentType> agentTypes,
        AgentType primaryAgent,
        List<AgentType> supportingAgents,
        List<String> toolsUsed,
        List<ToolCallTrace> toolCalls,
        String routingReason,
        double routingConfidence,
        String status, boolean retryable, String errorCode
) {
    public OrchestratorResult(String requestId, String response, AgentType type, IntentCategory intent, boolean escalated, long latency,
            List<AgentType> types, AgentType primary, List<AgentType> supporting, List<String> tools, List<ToolCallTrace> calls, String reason, double confidence) {
        this(requestId, response, type, intent, escalated, latency, types, primary, supporting, tools, calls, reason, confidence, "success", false, null);
    }
}
