package com.llmagentsystem.tool;

import java.time.Duration;
import java.util.Map;
import java.util.Set;

public record ToolDefinition(String name, String description, Map<String, Object> inputSchema,
                             Set<String> agents, boolean readOnly, Duration timeout, Duration cacheTtl,
                             String source, String server, String remoteName, Handler handler, ContextHandler contextHandler) {
    public ToolDefinition(String name, String description, Map<String, Object> schema, Set<String> agents, boolean readOnly,
                          Duration timeout, Duration cacheTtl, String source, String server, String remoteName, Handler handler) {
        this(name, description, schema, agents, readOnly, timeout, cacheTtl, source, server, remoteName, handler, null);
    }
    public static ToolDefinition contextual(String name, String description, Map<String, Object> schema,
                                             Set<String> agents, ContextHandler handler) {
        return new ToolDefinition(name, description, schema, agents, true, Duration.ofSeconds(2), Duration.ZERO,
                "local", "", "", null, handler);
    }
    @FunctionalInterface public interface Handler { Object call(Map<String, Object> arguments) throws Exception; }
    @FunctionalInterface public interface ContextHandler {
        Object call(com.llmagentsystem.agent.AgentRequest request, Map<String, Object> arguments) throws Exception;
    }
}
