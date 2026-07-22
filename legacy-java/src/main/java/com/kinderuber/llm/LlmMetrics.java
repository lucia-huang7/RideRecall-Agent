package com.kinderuber.llm;

import dev.langchain4j.model.chat.response.ChatResponse;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tags;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.springframework.stereotype.Component;
import java.util.concurrent.TimeUnit;

@Component
public class LlmMetrics {
    private final MeterRegistry registry;
    public LlmMetrics(MeterRegistry registry) { this.registry = registry; }
    public static LlmMetrics isolated() { return new LlmMetrics(new SimpleMeterRegistry()); }

    public void call(String stage, String model, long elapsed, ChatResponse response, Throwable error) {
        String category = error == null ? "none" : category(error);
        Tags tags = Tags.of("stage", stage, "model", model == null || model.isBlank() ? "default" : model,
                "outcome", error == null ? "success" : "failed", "error", category);
        registry.counter("kinderuber_llm_calls", tags).increment();
        registry.timer("kinderuber_llm_duration", tags).record(elapsed, TimeUnit.NANOSECONDS);
        if (response != null && response.tokenUsage() != null) {
            var usage = response.tokenUsage();
            if (usage.inputTokenCount() != null) registry.counter("kinderuber_llm_tokens", tags.and("direction", "input")).increment(Math.max(0, usage.inputTokenCount()));
            if (usage.outputTokenCount() != null) registry.counter("kinderuber_llm_tokens", tags.and("direction", "output")).increment(Math.max(0, usage.outputTokenCount()));
        }
    }
    public void fallback(String stage, String reason) {
        registry.counter("kinderuber_llm_fallbacks", "stage", stage, "reason", reason).increment();
    }
    public static String category(Throwable error) {
        for (Throwable cause = error; cause != null; cause = cause.getCause()) {
            if (cause instanceof dev.langchain4j.exception.AuthenticationException) return "authentication";
            if (cause instanceof dev.langchain4j.exception.HttpException http) {
                if (http.statusCode() == 401 || http.statusCode() == 403) return "authentication";
                if (http.statusCode() == 429) return "rate_limit";
            }
            String message = cause.getMessage();
            if ("context_budget_exceeded".equals(message)) return "context_budget";
            if ("model_response_truncated".equals(message)) return "truncated";
            if ("model_response_incomplete".equals(message)) return "incomplete";
            if ("model_unavailable".equals(message)) return "unavailable";
            if (cause instanceof java.util.concurrent.TimeoutException || cause instanceof java.net.http.HttpTimeoutException
                    || "timeout".equals(message)) return "timeout";
            if (cause instanceof InterruptedException || "interrupted".equals(message)) return "cancelled";
        }
        return "provider_error";
    }
}
