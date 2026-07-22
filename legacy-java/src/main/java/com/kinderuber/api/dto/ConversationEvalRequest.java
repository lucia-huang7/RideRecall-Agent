package com.kinderuber.api.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import jakarta.validation.constraints.*;

public record ConversationEvalRequest(
        @Size(max = 200) String userId,
        @JsonAlias("conv_id") @NotBlank @Size(max = 200) String conversationId,
        @Min(1) @Max(50) Integer maxTurns,
        @Size(max = 12000) String referenceAnswer,
        @Size(max = 20000) String evidence) {
    public int limit() { return maxTurns == null ? 20 : maxTurns; }
    public String userIdOrDefault() { return userId == null || userId.isBlank() ? "anonymous" : userId; }
}
