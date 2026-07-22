package com.kinderuber.tool;

import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;
import java.time.Duration;
import java.util.*;

@Component
public class KnowledgeToolRegistration {
    private final ToolRegistry registry;
    private final KnowledgeToolManager knowledge;
    public KnowledgeToolRegistration(ToolRegistry registry, KnowledgeToolManager knowledge) { this.registry = registry; this.knowledge = knowledge; }
    @PostConstruct public void register() {
        registry.register(new ToolDefinition("search_knowledge_base", "搜索本地客服知识库，获取订单、退款、账单和技术支持政策。", Map.of(
                "type", "object", "properties", Map.of("query", Map.of("type", "string", "minLength", 1, "maxLength", 2000), "top_k", Map.of("type", "integer", "minimum", 1, "maximum", 20)), "required", List.of("query"), "additionalProperties", false),
                Set.of("general", "technical", "billing", "escalation"), true, Duration.ofSeconds(15), Duration.ZERO, "local", "", "", args -> {
                    var result = knowledge.searchWithRewrite((String) args.get("query"), ((Number) args.getOrDefault("top_k", 3)).intValue());
                    if (!result.success()) throw new IllegalStateException("knowledge_unavailable");
                    return Map.of("results", result.data(), "reranked", result.reranked());
                }));
    }
}
