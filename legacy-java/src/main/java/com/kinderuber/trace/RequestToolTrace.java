package com.kinderuber.trace;

import java.util.List;

public record RequestToolTrace(
        String requestId,
        String timestamp,
        String endpoint,
        String userId,
        String conversationId,
        String intent,
        String intentGroup,
        String agentType,
        String primaryAgent,
        List<String> supportingAgents,
        List<String> toolsUsed,
        List<ToolCallTrace> toolCalls,
        boolean knowledgeUsed,
        boolean escalated,
        long latencyMs
) {
}
