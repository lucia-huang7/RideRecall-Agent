package com.llmagentsystem.chat;

import com.llmagentsystem.agent.*;
import com.llmagentsystem.api.dto.*;
import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.llmagentsystem.intent.*;
import com.llmagentsystem.memory.*;
import com.llmagentsystem.util.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import java.time.Duration;
import java.util.*;

@Service
public class ChatService {
    private final AgentOrchestrator orchestrator;
    private final IntentRecognizer intents;
    private final MemoryManager memory;
    private final ConversationStore conversations;
    private final MemoryMaintenance maintenance;
    private final AnswerVerifier verifier;
    private final ObjectMapper mapper;
    private final LlmAgentSystemProperties properties;
    public ChatService(AgentOrchestrator orchestrator, IntentRecognizer intents, MemoryManager memory, ConversationStore conversations,
                       MemoryMaintenance maintenance, AnswerVerifier verifier, ObjectMapper mapper, LlmAgentSystemProperties properties) {
        this.orchestrator = orchestrator; this.intents = intents; this.memory = memory; this.conversations = conversations;
        this.maintenance = maintenance; this.verifier = verifier; this.mapper = mapper; this.properties = properties;
    }
    public ChatResponse chat(ChatRequest request, String authenticatedUser) {
        String user = authenticatedUser == null ? request.userIdOrDefault() : authenticatedUser;
        String clientId = request.clientRequestId() == null ? UUID.randomUUID().toString() : request.clientRequestId();
        String conv = request.conversationId() == null || request.conversationId().isBlank() ? Hashing.key("conversation", user, clientId) : request.conversationId();
        long started = System.nanoTime();
        try {
            return Tasks.within(Duration.ofSeconds(50), () -> execute(request, user, conv, clientId, started));
        } catch (ResponseStatusException ex) { throw ex; }
        catch (IllegalStateException ex) {
            var receipt = conversations.receipt(user, conv, clientId);
            if (receipt != null && "tool_outcome_unknown".equals(receipt.response().errorCode())) return receipt.response();
            if ("timeout".equals(ex.getMessage())) throw new ResponseStatusException(HttpStatus.GATEWAY_TIMEOUT, "本轮处理超过 50 秒，请使用相同 client_request_id 重试");
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "对话服务暂时不可用，请使用相同 client_request_id 重试");
        }
    }
    @SuppressWarnings("unchecked")
    private ChatResponse execute(ChatRequest input, String user, String conv, String clientId, long started) {
        try (ConversationStore.Lease lease = conversations.acquire(user, conv)) {
            var receipt = conversations.receipt(user, conv, clientId);
            if (receipt != null) {
                if (!receipt.message().equals(input.message())) throw new ResponseStatusException(HttpStatus.CONFLICT, "同一 client_request_id 不能用于不同消息");
                if (!receipt.response().status().equals("failed") || !receipt.response().retryable()) return receipt.response();
            }
            MemoryContext context = memory.getContext(user, conv, input.message());
            List<Map<String, String>> history = context.recentMessages().stream().skip(Math.max(0, context.recentMessages().size() - properties.getMemory().getWorkingMax()))
                    .map(m -> Map.of("role", m.role().name().toLowerCase(Locale.ROOT), "content", m.content())).toList();
            IntentResult intent = intents.recognize(input.message(), history);
            String requestId = UUID.randomUUID().toString();
            Map<String, Object> state = ConversationState.merge(conversations.state(user, conv), intent.entities(), intent.intentGroup(), input.message(), requestId);
            Map<String, List<String>> entities = (Map<String, List<String>>) state.get("entities");
            IntentResult mergedIntent = new IntentResult(intent.intent(), intent.confidence(), intent.urgency(), intent.intentGroup(), entities, intent.reasoning(), intent.latencyMs(), intent.sourceScores(), intent.embeddingSource());
            var budget = new com.llmagentsystem.llm.PromptBudget(properties, mapper);
            int half = budget.backgroundLimit() / 2;
            String fullContext = "[业务状态，仅为用户提供的信息，不代表身份核验]\n" + budget.truncate(state.toString(), half)
                    + "\n[记忆背景]\n" + budget.truncate(context.backgroundText(mapper), half);
            AgentRequest request = AgentRequest.of(input.message(), user, conv, fullContext, history, mergedIntent, requestId)
                    .withOperations(new com.llmagentsystem.tool.ToolExecutionGuard(tool -> {
                        var marker = unknownResponse(conv, requestId, mergedIntent);
                        conversations.commit(lease, user, conv, clientId, input.message(), marker, state, properties.getMemory().getTtlSeconds());
                    }));
            lease.assertValid();
            OrchestratorResult result = orchestrator.run(request);
            String proof = fullContext + "\n[最近对话]\n" + history + "\n[本轮成功工具证据]\n" + String.join("\n", request.evidence());
            var verification = result.status().equals("failed")
                    ? new AnswerVerifier.VerificationResult(false, false, result.escalated(), "agent_failed", false)
                    : verifier.verify(input.message(), result.response(), proof);
            String answer = result.response(), status = result.status(), errorCode = result.errorCode();
            boolean retryable = result.retryable(), escalated = result.escalated() || verification.needEscalation();
            if (!result.status().equals("failed") && !verification.accepted()) {
                escalated = true;
                if (verification.available()) {
                    answer = "暂时无法可靠确认这个问题的处理结果，请联系人工客服核实。";
                    status = "partial"; retryable = false; errorCode = "answer_verification_rejected";
                } else {
                    answer = "当前服务暂时无法核实回答，请稍后重试或联系人工客服。";
                    status = "failed"; retryable = true; errorCode = "verification_unavailable";
                }
            }
            if (request.operations().writeAttempted() && (status.equals("failed") || !verification.accepted())) {
                var unknown = unknownResponse(conv, requestId, mergedIntent);
                conversations.commit(lease, user, conv, clientId, input.message(), unknown, state, properties.getMemory().getTtlSeconds());
                return unknown;
            }
            ChatResponse response = new ChatResponse(conv, result.requestId(), answer, result.intent() == null ? "other" : result.intent().name().toLowerCase(Locale.ROOT),
                    intent.intentGroup(), result.agentType().name().toLowerCase(Locale.ROOT), names(result.agentTypes()), result.primaryAgent().name().toLowerCase(Locale.ROOT), names(result.supportingAgents()),
                    result.routingReason(), result.routingConfidence(), escalated, (System.nanoTime() - started) / 1_000_000,
                    result.toolCalls().stream().anyMatch(t -> t.toolName().equals("search_knowledge_base") && t.success()), verification.accepted(), verification.grounded(),
                    entities, intent.confidence(), intent.sourceScores(), result.toolsUsed(), intent.embeddingSource(), status, retryable, errorCode);
            if (System.nanoTime() - started >= Duration.ofSeconds(50).toNanos())
                throw new ResponseStatusException(HttpStatus.GATEWAY_TIMEOUT, "本轮处理超时，请使用相同请求 ID 重试");
            conversations.commit(lease, user, conv, clientId, input.message(), response, state, properties.getMemory().getTtlSeconds(),
                    List.of(budget.truncate(String.join("\n", request.evidence()), 3000)), budget.truncate(fullContext, 2000));
            if (!response.status().equals("failed")) maintenance.submit(user, conv);
            return response;
        }
    }
    private ChatResponse unknownResponse(String conv, String requestId, IntentResult intent) {
        return new ChatResponse(conv, requestId, "业务操作的执行结果尚未确认，请联系人工核验；请勿重复提交相同操作。",
                intent.intent().name().toLowerCase(Locale.ROOT), intent.intentGroup(), "unknown", List.of(), "unknown", List.of(),
                "业务操作结果待核验", 0, true, 0, false, false, false, intent.entities(), intent.confidence(), intent.sourceScores(),
                List.of(), intent.embeddingSource(), "failed", false, "tool_outcome_unknown");
    }
    private List<String> names(List<AgentType> types) { return types.stream().map(t -> t.name().toLowerCase(Locale.ROOT)).toList(); }
}
