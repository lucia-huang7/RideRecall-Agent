package com.kinderuber.llm;

import com.kinderuber.config.KinderUberProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.TokenCountEstimator;
import dev.langchain4j.model.chat.request.ChatRequest;
import org.springframework.stereotype.Component;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Network-free conservative estimate: one UTF-8 byte per token, plus protocol overhead. */
@Component
public class PromptBudget implements TokenCountEstimator {
    private final KinderUberProperties.Llm config;
    private final ObjectMapper mapper;
    public PromptBudget(KinderUberProperties properties, ObjectMapper mapper) {
        this.config = properties.getLlm(); this.mapper = mapper;
        if (config.getMaxInputTokens() < 1024 || config.getMaxBackgroundTokens() < 128
                || config.getMaxToolResultTokens() < 128
                || config.getMaxBackgroundTokens() + config.getMaxToolResultTokens() >= config.getMaxInputTokens())
            throw new IllegalArgumentException("Invalid LLM input/background/tool token budgets");
    }
    public static PromptBudget defaults() { return new PromptBudget(new KinderUberProperties(), new ObjectMapper()); }
    public int backgroundLimit() { return config.getMaxBackgroundTokens(); }

    @Override public int estimateTokenCountInText(String text) {
        return text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length;
    }
    @Override public int estimateTokenCountInMessage(ChatMessage message) {
        String text = switch (message) {
            case SystemMessage m -> m.text();
            case UserMessage m -> m.hasSingleText() ? m.singleText() : m.toString();
            case AiMessage m -> Objects.toString(m.text(), "") + (m.hasToolExecutionRequests() ? m.toolExecutionRequests().toString() : "");
            case ToolExecutionResultMessage m -> m.id() + m.toolName() + m.text();
            default -> message.toString();
        };
        return estimateTokenCountInText(text) + 32;
    }
    @Override public int estimateTokenCountInMessages(Iterable<ChatMessage> messages) {
        long count = 0;
        for (var message : messages) count += estimateTokenCountInMessage(message);
        return (int) Math.min(Integer.MAX_VALUE, count);
    }

    public String truncate(String text, int limit) {
        if (text == null || limit <= 0) return "";
        if (estimateTokenCountInText(text) <= limit) return text;
        String suffix = "\n[内容已截断]";
        int remaining = Math.max(0, limit - estimateTokenCountInText(suffix)), end = 0;
        for (int i = 0; i < text.length();) {
            int cp = text.codePointAt(i);
            int bytes = cp <= 0x7f ? 1 : cp <= 0x7ff ? 2 : cp <= 0xffff ? 3 : 4;
            if (bytes > remaining) break;
            remaining -= bytes; i += Character.charCount(cp); end = i;
        }
        return text.substring(0, end) + (limit >= estimateTokenCountInText(suffix) ? suffix : "");
    }

    public String toolResult(String json) {
        int limit = config.getMaxToolResultTokens();
        if (estimateTokenCountInText(json) <= limit) return json;
        try {
            int excerptLimit = limit / 2;
            while (true) {
                String bounded = mapper.writeValueAsString(Map.of("truncated", true, "excerpt", truncate(json, excerptLimit),
                        "notice", "资料已截断，请缩小查询范围"));
                if (estimateTokenCountInText(bounded) <= limit) return bounded;
                excerptLimit /= 2;
                if (excerptLimit == 0) throw new IllegalStateException("tool_result_budget_exceeded");
            }
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) { throw new IllegalStateException(ex); }
    }

    public String agentPrompt(com.kinderuber.agent.AgentRequest request) {
        int entityLimit = backgroundLimit() / 4;
        return "[背景信息]\n" + truncate(request.context(), backgroundLimit() - entityLimit)
                + "\n[结构化实体]\n" + truncate(Objects.toString(request.entities(), ""), entityLimit)
                + "\n[用户问题]\n" + request.message();
    }

    /** Remove whole old turns; never truncate the active user question or split a tool/result exchange. */
    public ChatRequest fit(ChatRequest request) {
        List<ChatMessage> messages = new ArrayList<>(request.messages());
        long schemaCost = request.toolSpecifications() == null ? 0 : request.toolSpecifications().stream()
                .mapToLong(tool -> estimateTokenCountInText(tool.toString()) + 128L).sum();
        while (schemaCost + estimateTokenCountInMessages(messages) > config.getMaxInputTokens()) {
            int activeUser = -1;
            for (int i = messages.size() - 1; i >= 0; i--) if (messages.get(i) instanceof UserMessage) { activeUser = i; break; }
            int first = 0;
            while (first < messages.size() && messages.get(first) instanceof SystemMessage) first++;
            if (first >= activeUser) throw new IllegalStateException("context_budget_exceeded");
            int next = first + 1;
            while (next < activeUser && !(messages.get(next) instanceof UserMessage)) next++;
            messages.subList(first, next).clear();
        }
        return request.toBuilder().messages(messages).build();
    }
}
