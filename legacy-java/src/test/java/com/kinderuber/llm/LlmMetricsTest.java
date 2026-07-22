package com.kinderuber.llm;

import com.kinderuber.config.KinderUberProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.output.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.support.StaticListableBeanFactory;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class LlmMetricsTest {
    @Test void recordsProviderUsageEvenWhenResponseIsRejectedAsTruncated() {
        var registry = new SimpleMeterRegistry(); var metrics = new LlmMetrics(registry);
        var raw = new ChatModel() {
            @Override public ChatResponse doChat(ChatRequest request) {
                return ChatResponse.builder().aiMessage(AiMessage.from("半截答案")).finishReason(FinishReason.LENGTH).tokenUsage(new TokenUsage(12, 7)).build();
            }
        };
        var model = new ManagedChatModel(raw, PromptBudget.defaults(), metrics, "agent");
        assertThrows(IllegalStateException.class, () -> model.chat(ChatRequest.builder().messages(UserMessage.from("问题")).build()));
        assertEquals(1, registry.get("kinderuber_llm_calls").tag("error", "truncated").counter().count());
        assertEquals(12, registry.get("kinderuber_llm_tokens").tag("direction", "input").counter().count());
        assertEquals(7, registry.get("kinderuber_llm_tokens").tag("direction", "output").counter().count());
        assertEquals(1, registry.get("kinderuber_llm_duration").timer().count());
    }
    @Test void oversizedPromptsAreRejectedBeforeProviderInvocation() {
        var config = new KinderUberProperties(); config.getLlm().setMaxInputTokens(1600);
        config.getLlm().setMaxBackgroundTokens(256); config.getLlm().setMaxToolResultTokens(256);
        var registry = new SimpleMeterRegistry(); var calls = new AtomicInteger();
        var raw = new ChatModel() { @Override public ChatResponse doChat(ChatRequest request) { calls.incrementAndGet(); throw new AssertionError("provider must not run"); } };
        var model = new ManagedChatModel(raw, new PromptBudget(config, new ObjectMapper()), new LlmMetrics(registry), "agent");
        assertThrows(IllegalStateException.class, () -> model.chat(ChatRequest.builder().messages(UserMessage.from("长".repeat(1000))).build()));
        assertEquals(0, calls.get()); assertEquals(1, registry.get("kinderuber_llm_calls").tag("error", "context_budget").counter().count());
    }
    @Test void unavailableModelAndPlainTextFallbackHaveSeparateMetrics() {
        var registry = new SimpleMeterRegistry(); var metrics = new LlmMetrics(registry); var config = new KinderUberProperties();
        var gateway = new LangChain4jLlmGateway(new StaticListableBeanFactory().getBeanProvider(ChatModel.class), config, PromptBudget.defaults(), metrics);
        assertFalse(gateway.chat("", "问题", 0, 512).isBlank());
        assertEquals(1, registry.get("kinderuber_llm_calls").tag("error", "unavailable").counter().count());
        assertEquals(1, registry.get("kinderuber_llm_fallbacks").tag("reason", "unavailable").counter().count());
        assertNull(registry.find("kinderuber_llm_tokens").counter());
    }
}
