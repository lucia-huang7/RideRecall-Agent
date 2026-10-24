package com.llmagentsystem.llm;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.agent.tool.*;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.chat.request.ChatRequest;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class PromptBudgetTest {
    private PromptBudget budget() {
        var config = new LlmAgentSystemProperties();
        config.getLlm().setMaxInputTokens(1600); config.getLlm().setMaxBackgroundTokens(256); config.getLlm().setMaxToolResultTokens(256);
        return new PromptBudget(config, new ObjectMapper());
    }
    @Test void dropsWholeOldTurnsButKeepsCurrentQuestionAndToolExchange() {
        var budget = budget();
        var call = ToolExecutionRequest.builder().id("id").name("lookup").arguments("{}").build();
        var active = List.<ChatMessage>of(UserMessage.from("最新问题"), AiMessage.from(List.of(call)), ToolExecutionResultMessage.from(call, "最新证据"));
        var messages = new ArrayList<ChatMessage>(List.of(SystemMessage.from("客服"), UserMessage.from("旧".repeat(1000)), AiMessage.from("旧回答")));
        messages.addAll(active);
        var fitted = budget.fit(ChatRequest.builder().messages(messages).build()).messages();
        assertEquals(4, fitted.size()); assertEquals(active, fitted.subList(1, fitted.size()));
        assertTrue(budget.estimateTokenCountInMessages(fitted) <= 1600);
    }
    @Test void activeQuestionAndToolSchemaCannotSilentlyDisappear() {
        var budget = budget();
        assertThrows(IllegalStateException.class, () -> budget.fit(ChatRequest.builder().messages(UserMessage.from("新".repeat(1000))).build()));
        var spec = ToolSpecification.builder().name("lookup").description("说明".repeat(1000)).build();
        assertThrows(IllegalStateException.class, () -> budget.fit(ChatRequest.builder().messages(UserMessage.from("问题")).toolSpecifications(spec).build()));
    }
    @Test void clippingKeepsUnicodeAndToolOutputIsValidExplicitlyTruncatedJson() throws Exception {
        var budget = budget(); var mapper = new ObjectMapper();
        String clipped = budget.truncate("😀你好".repeat(100), 100);
        assertTrue(budget.estimateTokenCountInText(clipped) <= 100); assertFalse(clipped.contains("\uFFFD"));
        String json = budget.toolResult(mapper.writeValueAsString(Map.of("results", "\\\"😀".repeat(500))));
        assertTrue(budget.estimateTokenCountInText(json) <= 256);
        assertTrue(mapper.readTree(json).path("truncated").asBoolean());
    }
}
