package com.kinderuber.memory;

import java.util.*;

public final class ConversationState {
    private ConversationState() {}
    @SuppressWarnings("unchecked")
    public static Map<String, Object> merge(Map<String, Object> previous, Map<String, List<String>> entities, String group, String message, String requestId) {
        String domain = Set.of("billing", "query").contains(group) ? "commerce" : group;
        boolean neutral = Set.of("greeting", "feedback", "other", "complaint", "request", "escalation").contains(domain);
        Map<String, List<String>> old = previous.get("entities") instanceof Map<?, ?> value ? (Map<String, List<String>>) value : Map.of();
        boolean changedOrder = !entities.getOrDefault("order_id", List.of()).isEmpty() && !old.getOrDefault("order_id", List.of()).isEmpty() && !entities.get("order_id").equals(old.get("order_id"));
        boolean newTask = message.matches("(?s).*(?:(?:换个|新的|另一个)(?:问题|订单)|另一笔订单).*" );
        if (neutral) domain = !newTask ? String.valueOf(previous.getOrDefault("domain", entities.getOrDefault("order_id", List.of()).isEmpty() ? "general" : "commerce")) : (entities.getOrDefault("order_id", List.of()).isEmpty() ? "general" : "commerce");
        boolean keep = !newTask && !changedOrder && (neutral || domain.equals(previous.get("domain")));
        Map<String, List<String>> merged = new LinkedHashMap<>(keep ? old : Map.of());
        Map<String, Object> sources = new LinkedHashMap<>();
        if (keep && previous.get("sources") instanceof Map<?, ?> prior) prior.forEach((k,v) -> sources.put(String.valueOf(k), v));
        entities.forEach((key, value) -> {
            if (!value.isEmpty()) { merged.put(key, List.copyOf(value)); sources.put(key, requestId); }
            else merged.putIfAbsent(key, List.of());
        });
        return Map.of("domain", domain, "entities", merged, "sources", sources);
    }
}
