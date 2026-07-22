package com.kinderuber.llm;

import dev.langchain4j.model.ModelProvider;
import dev.langchain4j.model.chat.*;
import dev.langchain4j.model.chat.request.*;
import dev.langchain4j.model.chat.response.ChatResponse;
import java.util.Set;

/** One measurement per provider call, including tool rounds and rejected responses. */
public final class ManagedChatModel implements ChatModel {
    private final ChatModel delegate;
    private final PromptBudget budget;
    private final LlmMetrics metrics;
    private final String stage;
    public ManagedChatModel(ChatModel delegate, PromptBudget budget, LlmMetrics metrics, String stage) {
        this.delegate = delegate; this.budget = budget; this.metrics = metrics; this.stage = stage;
    }
    @Override public ChatResponse chat(ChatRequest request) {
        long start = System.nanoTime();
        ChatResponse response = null; RuntimeException failure = null;
        String model = request.modelName();
        if ((model == null || model.isBlank()) && delegate != null && delegate.defaultRequestParameters() != null)
            model = delegate.defaultRequestParameters().modelName();
        try {
            if (delegate == null) throw new IllegalStateException("model_unavailable");
            response = delegate.chat(budget.fit(request));
            CheckedChatModel.validate(response);
            return response;
        } catch (RuntimeException ex) { failure = ex; throw ex; }
        finally { metrics.call(stage, model, System.nanoTime() - start, response, failure); }
    }
    @Override public ChatRequestParameters defaultRequestParameters() { return delegate == null ? ChatRequestParameters.builder().build() : delegate.defaultRequestParameters(); }
    @Override public Set<Capability> supportedCapabilities() { return delegate == null ? Set.of() : delegate.supportedCapabilities(); }
    @Override public ModelProvider provider() { return delegate == null ? ModelProvider.OTHER : delegate.provider(); }
}
