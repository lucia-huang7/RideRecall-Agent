package com.kinderuber.api.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;

@Schema(description = "智能客服对话请求")
public record ChatRequest(
        @Schema(description = "用户消息", example = "我想申请退款，订单号是 #12345")
        @NotBlank @jakarta.validation.constraints.Size(max = 12000) String message,
        @Schema(description = "用户 ID，未传时默认为 anonymous", example = "u1001")
        @jakarta.validation.constraints.Size(max = 256) String userId,
        @Schema(description = "会话 ID，未传时服务端自动生成", example = "conv-001")
        @JsonProperty("conv_id")
        @JsonAlias({"conv_id", "conversation_id"})
        @jakarta.validation.constraints.Size(max = 256) String conversationId,
        @jakarta.validation.constraints.Size(min = 1, max = 128) String clientRequestId
) {
    public String userIdOrDefault() {
        return userId == null || userId.isBlank() ? "anonymous" : userId;
    }
}
