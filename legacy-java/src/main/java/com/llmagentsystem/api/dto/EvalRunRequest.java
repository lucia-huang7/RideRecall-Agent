package com.llmagentsystem.api.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.List;
import java.util.Map;

public record EvalRunRequest(
        @Valid @Size(max = 200) List<IntentCase> intentCases,
        @Valid @Size(max = 100) List<DialogCase> dialogCases) {
    public record IntentCase(@NotBlank @Size(max = 12000) String message, @NotBlank String expectedIntent,
                             List<Map<String, String>> history, Map<String, Object> context) {
        public IntentCase(String message, String expected) { this(message, expected, List.of(), Map.of()); }
    }
    public record DialogCase(@Size(max = 12000) String question, @Size(max = 50) List<@NotBlank @Size(max = 12000) String> turns,
                             String userId, @JsonAlias("conv_id") String conversationId, @Size(max = 12000) String referenceAnswer, @Size(max = 20000) String evidence,
                             @Pattern(regexp = "general|technical|billing|escalation") String expectedAgent, String expectedIntent,
                             @Size(max = 20) List<String> requiredTools, Map<String, List<String>> expectedEntities) {
        public DialogCase(String question, List<String> turns, String user, String conv) { this(question, turns, user, conv, null, null, null, null, List.of(), Map.of()); }
        @AssertTrue(message = "对话用例需要 question 或非空 turns")
        public boolean isContentPresent() { return (question != null && !question.isBlank()) || (turns != null && !turns.isEmpty()); }
    }
}
