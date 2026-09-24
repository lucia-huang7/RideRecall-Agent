package com.llmagentsystem.llm;

import dev.langchain4j.model.ModelProvider;
import dev.langchain4j.model.chat.Capability;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.request.ChatRequestParameters;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.output.FinishReason;
import java.util.Set;

/** Rejects incomplete replies before AI Services can execute tools or persist an answer. */
public final class CheckedChatModel implements ChatModel {
    private final ChatModel delegate;

    public CheckedChatModel(ChatModel delegate) { this.delegate = delegate; }

    @Override public ChatResponse chat(ChatRequest request) {
        ChatResponse response = delegate.chat(request);
        validate(response);
        return response;
    }

    public static void validate(ChatResponse response) {
        if (response == null || response.aiMessage() == null) throw new IllegalStateException("empty_model_response");
        FinishReason reason = response.finishReason();
        if (reason == FinishReason.LENGTH) throw new IllegalStateException("model_response_truncated");
        if (reason == FinishReason.CONTENT_FILTER || reason == FinishReason.OTHER)
            throw new IllegalStateException("model_response_incomplete");
        var message = response.aiMessage();
        if (message.hasToolExecutionRequests()) {
            if (message.toolExecutionRequests().size() > 8) throw new IllegalStateException("tool_call_limit");
            if (reason != null && reason != FinishReason.TOOL_EXECUTION)
                throw new IllegalStateException("invalid_tool_response");
        } else if (reason == FinishReason.TOOL_EXECUTION || message.text() == null || message.text().isBlank()) {
            throw new IllegalStateException("empty_model_response");
        }
    }

    @Override public ChatRequestParameters defaultRequestParameters() { return delegate.defaultRequestParameters(); }
    @Override public Set<Capability> supportedCapabilities() { return delegate.supportedCapabilities(); }
    @Override public ModelProvider provider() { return delegate.provider(); }
}
