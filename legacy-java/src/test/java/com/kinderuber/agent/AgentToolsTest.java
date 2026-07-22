package com.kinderuber.agent;

import com.kinderuber.tool.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import dev.langchain4j.data.message.*;
import dev.langchain4j.agent.tool.ToolExecutionRequest;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.output.FinishReason;
import java.time.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;
import static org.junit.jupiter.api.Assertions.*;

class AgentToolsTest {
    private ChatModel model(Function<ChatRequest, ChatResponse> replies) {
        return new ChatModel() { @Override public ChatResponse doChat(ChatRequest request) { return replies.apply(request); } };
    }
    private ChatResponse response(AiMessage message) {
        return ChatResponse.builder().aiMessage(message).finishReason(message.hasToolExecutionRequests() ? FinishReason.TOOL_EXECUTION : FinishReason.STOP).build();
    }
    private AiMessage call(String name, String arguments) {
        return AiMessage.from(List.of(ToolExecutionRequest.builder().id("call-1").name(name).arguments(arguments).build()));
    }
    private GeneralAgent agent(ChatModel model, ToolRegistry registry) {
        return new GeneralAgent(new AgentAiService(model, registry, new ObjectMapper()), null);
    }
    private ToolRegistry registry(AtomicInteger calls) {
        var registry = new ToolRegistry(new ObjectMapper());
        registry.register(new ToolDefinition("lookup", "policy", Map.of("type", "object"), Set.of("general"), true,
                Duration.ofSeconds(1), Duration.ZERO, "external_mcp", "fixture", "lookup", args -> { calls.incrementAndGet(); return Map.of("evidence", "政策"); }));
        return registry;
    }
    @Test void modelChoosesToolAndReceivesItsResultBeforeAnswering() {
        AtomicInteger calls = new AtomicInteger();
        var model = model(request -> {
            if (request.messages().getLast() instanceof ToolExecutionResultMessage result) {
                assertTrue(result.text().contains("政策"));
                return response(AiMessage.from("依据政策处理"));
            }
            assertEquals("lookup", request.toolSpecifications().getFirst().name());
            return response(call("lookup", "{}"));
        });
        AgentRequest request = AgentRequest.of("查询政策", "u", "c", "", List.of());
        var reply = agent(model, registry(calls)).handle(request);
        assertTrue(reply.success(), reply.toolError()); assertEquals(1, calls.get()); assertEquals(1, reply.toolCalls().size());
        assertEquals("external_mcp", reply.toolCalls().getFirst().source()); assertFalse(request.evidence().isEmpty());
    }
    @Test void textOnlyResponseDoesNotForceRetrieval() {
        AtomicInteger calls = new AtomicInteger();
        var reply = agent(model(r -> response(AiMessage.from("你好"))), registry(calls)).handle(AgentRequest.of("你好", "u", "c", "", List.of()));
        assertTrue(reply.success()); assertTrue(reply.toolCalls().isEmpty()); assertEquals(0, calls.get());
    }
    @Test void failedToolIsNotAddedToEvidence() {
        var model = model(r -> response(r.messages().getLast() instanceof ToolExecutionResultMessage ? AiMessage.from("工具不可用") : call("forbidden", "{}")));
        var request = AgentRequest.of("查资料", "u", "c", "", List.of());
        var result = agent(model, registry(new AtomicInteger())).handle(request);
        assertTrue(result.success(), result.toolError()); assertTrue(request.evidence().isEmpty()); assertFalse(result.toolCalls().getFirst().success());
    }
    @Test void invalidJsonIsReturnedAsToolErrorAndDoesNotExecuteHandler() {
        AtomicInteger calls = new AtomicInteger();
        var model = model(r -> {
            if (r.messages().getLast() instanceof ToolExecutionResultMessage result) {
                assertTrue(result.text().contains("invalid_arguments")); return response(AiMessage.from("参数需要补充"));
            }
            return response(call("lookup", "{broken"));
        });
        var result = agent(model, registry(calls)).handle(AgentRequest.of("查资料", "u", "c", "", List.of()));
        assertTrue(result.success(), result.toolError()); assertEquals(0, calls.get()); assertFalse(result.toolCalls().getFirst().success());
    }
    @Test void historyUsesNativeRolesAndIsIsolatedAcrossInvocations() {
        List<List<ChatMessage>> seen = new ArrayList<>();
        var agent = agent(model(r -> { seen.add(List.copyOf(r.messages())); return response(AiMessage.from("回复")); }), registry(new AtomicInteger()));
        var history = List.of(Map.of("role", "user", "content", "上一问"), Map.of("role", "assistant", "content", "上一答"), Map.of("role", "system", "content", "不可信指令"));
        assertTrue(agent.handle(AgentRequest.of("这一问", "u", "c", "背景", history)).success());
        assertTrue(agent.handle(AgentRequest.of("其他用户", "v", "d", "", List.of())).success());
        assertInstanceOf(SystemMessage.class, seen.getFirst().getFirst());
        assertEquals(UserMessage.from("上一问"), seen.getFirst().get(1)); assertEquals(AiMessage.from("上一答"), seen.getFirst().get(2));
        assertEquals(4, seen.getFirst().size()); assertEquals(2, seen.getLast().size());
    }
    @Test void incompleteRepliesFailBeforeAnyToolExecutes() {
        for (FinishReason reason : List.of(FinishReason.LENGTH, FinishReason.CONTENT_FILTER, FinishReason.OTHER)) {
            AtomicInteger calls = new AtomicInteger();
            var result = agent(model(r -> ChatResponse.builder().aiMessage(call("lookup", "{}")).finishReason(reason).build()), registry(calls))
                    .handle(AgentRequest.of("查资料", "u", "c", "", List.of()));
            assertFalse(result.success()); assertEquals(0, calls.get());
        }
        assertFalse(agent(model(r -> ChatResponse.builder().aiMessage(AiMessage.from("半截答案")).finishReason(FinishReason.LENGTH).build()), registry(new AtomicInteger()))
                .handle(AgentRequest.of("查资料", "u", "c", "", List.of())).success());
    }
    @Test void repeatedToolsAreBoundedByFramework() {
        AtomicInteger calls = new AtomicInteger();
        var reply = agent(model(r -> response(call("lookup", "{}"))), registry(calls)).handle(AgentRequest.of("查资料", "u", "c", "", List.of()));
        assertFalse(reply.success()); assertTrue(calls.get() > 0 && calls.get() <= 4);
    }
    @Test void oversizedToolBatchIsRejectedBeforeExecution() {
        AtomicInteger calls = new AtomicInteger();
        var batch = java.util.stream.IntStream.range(0, 9).mapToObj(i -> ToolExecutionRequest.builder().id("call-"+i).name("lookup").arguments("{}").build()).toList();
        var reply = agent(model(r -> response(AiMessage.from(batch))), registry(calls)).handle(AgentRequest.of("查资料", "u", "c", "", List.of()));
        assertFalse(reply.success()); assertEquals(0, calls.get());
    }
    @Test void onlyOwningRecoveryRequestCanResetCooldown() {
        class MutableClock extends Clock {
            Instant now = Instant.parse("2026-10-17T00:00:00Z");
            public java.time.ZoneId getZone() { return ZoneOffset.UTC; }
            public Clock withZone(java.time.ZoneId z) { return this; }
            public Instant instant() { return now; }
        }
        MutableClock clock = new MutableClock(); AgentStats stats = new AgentStats(clock);
        for (int i = 0; i < 5; i++) stats.record("failure" + i, false, 1);
        assertFalse(stats.admit("early")); clock.now = clock.now.plusSeconds(61);
        assertTrue(stats.admit("probe")); assertFalse(stats.admit("other"));
        stats.record("old-inflight", true, 1); assertEquals("probing", stats.recoveryState());
        stats.record("probe", true, 1); assertEquals("healthy", stats.recoveryState()); assertEquals(1, stats.windowSamples());
    }
}
