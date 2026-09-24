package com.llmagentsystem.agent;

import com.llmagentsystem.intent.IntentCategory;
import com.llmagentsystem.intent.IntentResult;
import com.llmagentsystem.intent.UrgencyLevel;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public record AgentRequest(
        String message,
        String userId,
        String conversationId,
        String context,
        List<Map<String, String>> history,
        Map<String, List<String>> entities,
        IntentCategory intent,
        String intentGroup,
        UrgencyLevel urgency,
        double intentConfidence,
        String requestId,
        List<String> evidence,
        com.llmagentsystem.tool.ToolExecutionGuard operations
) {
    public AgentRequest(String message, String userId, String conversationId, String context, List<Map<String, String>> history,
                        Map<String, List<String>> entities, IntentCategory intent, String intentGroup, UrgencyLevel urgency,
                        double confidence, String requestId, List<String> evidence) {
        this(message, userId, conversationId, context, history, entities, intent, intentGroup, urgency, confidence, requestId,
                evidence, com.llmagentsystem.tool.ToolExecutionGuard.readOnly());
    }
    public AgentRequest withOperations(com.llmagentsystem.tool.ToolExecutionGuard guard) {
        return new AgentRequest(message, userId, conversationId, context, history, entities, intent, intentGroup, urgency,
                intentConfidence, requestId, evidence, guard);
    }
    public AgentRequest withIntent(IntentResult intentResult) {
        return new AgentRequest(
                message,
                userId,
                conversationId,
                context,
                history,
                intentResult.entities(),
                intentResult.intent(),
                intentResult.intentGroup(),
                intentResult.urgency(),
                intentResult.confidence(),
                requestId, evidence, operations
        );
    }

    public static AgentRequest of(String message, String userId, String conversationId, String context, List<Map<String, String>> history) {
        return of(message, userId, conversationId, context, history, null, newRequestId());
    }

    public static AgentRequest of(
            String message,
            String userId,
            String conversationId,
            String context,
            List<Map<String, String>> history,
            IntentResult intentResult
    ) {
        return of(message, userId, conversationId, context, history, intentResult, newRequestId());
    }

    public static AgentRequest of(
            String message,
            String userId,
            String conversationId,
            String context,
            List<Map<String, String>> history,
            IntentResult intentResult,
            String requestId
    ) {
        return new AgentRequest(
                message,
                userId,
                conversationId,
                context,
                history,
                intentResult == null ? Map.of() : intentResult.entities(),
                intentResult == null ? null : intentResult.intent(),
                intentResult == null ? null : intentResult.intentGroup(),
                intentResult == null ? null : intentResult.urgency(),
                intentResult == null ? 1.0 : intentResult.confidence(),
                requestId == null || requestId.isBlank() ? newRequestId() : requestId, new java.util.concurrent.CopyOnWriteArrayList<>()
        );
    }

    private static String newRequestId() {
        return UUID.randomUUID().toString();
    }
}
