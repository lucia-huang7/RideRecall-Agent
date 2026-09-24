package com.llmagentsystem.integration;

import com.llmagentsystem.tool.*;
import com.fasterxml.jackson.databind.*;
import io.modelcontextprotocol.client.*;
import io.modelcontextprotocol.client.transport.*;
import io.modelcontextprotocol.spec.*;
import io.modelcontextprotocol.json.jackson2.JacksonMcpJsonMapper;
import jakarta.annotation.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import java.net.URI;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class StandardMcpClient {
    private final ToolRegistry registry;
    private final Environment environment;
    private final ObjectMapper wire = new ObjectMapper();
    private final Path configPath;
    private final boolean enabled;
    private final Map<String, Connection> connections = new ConcurrentHashMap<>();
    private static final class Connection {
        final JsonNode config;
        volatile McpSyncClient client;
        volatile String error = "", status = "disconnected";
        volatile Set<String> tools = Set.of();
        Connection(JsonNode config) { this.config = config; }
    }
    public StandardMcpClient(ToolRegistry registry, Environment environment,
            @Value("${MCP_SERVERS_CONFIG:config/mcp_servers.json}") String path,
            @Value("${MCP_ENABLED:true}") boolean enabled) {
        this.registry = registry; this.environment = environment; this.configPath = Path.of(path); this.enabled = enabled;
    }
    @PostConstruct public void load() throws Exception {
        if (!enabled || !Files.exists(configPath)) return;
        JsonNode servers = wire.readTree(configPath.toFile()).path("servers");
        if (!servers.isArray() || servers.size() > 16) throw new IllegalArgumentException("MCP servers 必须是至多 16 项的数组");
        for (JsonNode config : servers) {
            if (!config.path("enabled").asBoolean(true)) continue;
            String name = config.path("name").asText();
            if (!name.matches("[A-Za-z0-9_-]{1,24}") || connections.containsKey(name)) throw new IllegalArgumentException("Invalid MCP server name");
            if (!config.path("allowed_tools").isArray() || config.path("allowed_tools").isEmpty()
                    || !config.path("agents").isArray() || config.path("agents").isEmpty()
                    || strings(config.path("agents")).stream().anyMatch(a -> !Set.of("general", "technical", "billing", "escalation").contains(a))
                    || config.path("timeout_s").asDouble(25) < 1 || config.path("timeout_s").asDouble(25) > 120
                    || config.path("cache_ttl").asDouble(300) < 0 || config.path("cache_ttl").asDouble(300) > 3600
                    || config.path("max_result_chars").asInt(12000) < 256 || config.path("max_result_chars").asInt(12000) > 50000)
                throw new IllegalArgumentException("MCP 白名单、Agent、超时、缓存或输出上限配置无效");
            String transport = config.path("transport").asText("streamable_http");
            if (transport.equals("streamable_http")) {
                URI uri = URI.create(config.path("url").asText());
                if (!Set.of("http", "https").contains(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null) throw new IllegalArgumentException("Invalid MCP URL");
            } else if (!transport.equals("stdio") || config.path("command").asText().isBlank()) throw new IllegalArgumentException("Invalid MCP transport/command");
            connections.put(name, new Connection(config));
        }
    }
    @Scheduled(initialDelay = 1000, fixedDelay = 30000)
    public void reconnect() {
        connections.forEach((name, connection) -> {
            synchronized (connection) {
                if (connection.client != null) return;
                McpSyncClient client = null;
                try {
                    McpClientTransport transport;
                    JsonNode config = connection.config;
                    if (config.path("transport").asText("streamable_http").equals("stdio")) {
                        var params = ServerParameters.builder(config.path("command").asText()).args(strings(config.path("args")));
                        config.path("env").fields().forEachRemaining(e -> params.addEnvVar(e.getKey(), e.getValue().asText()));
                        for (String variable : strings(config.path("env_vars"))) {
                            String value = environment.getProperty(variable);
                            if (value != null) params.addEnvVar(variable, value);
                        }
                        transport = new StdioClientTransport(params.build(), new JacksonMcpJsonMapper(wire));
                    } else if (config.path("transport").asText("streamable_http").equals("streamable_http")) {
                        URI uri = URI.create(config.path("url").asText());
                        if (!Set.of("http", "https").contains(uri.getScheme())) throw new IllegalArgumentException("Invalid MCP URL");
                        var builder = HttpClientStreamableHttpTransport.builder(uri.getScheme() + "://" + uri.getRawAuthority())
                                .endpoint((uri.getRawPath().isEmpty() ? "/mcp" : uri.getRawPath()) + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery()))
                                .connectTimeout(Duration.ofSeconds(5)).jsonMapper(new JacksonMcpJsonMapper(wire));
                        config.path("headers_env").fields().forEachRemaining(e -> {
                            String value = environment.getProperty(e.getValue().asText());
                            if (value != null) builder.customizeRequest(r -> r.header(e.getKey(), value));
                        });
                        transport = builder.build();
                    } else throw new IllegalArgumentException("Unsupported MCP transport");
                    client = McpClient.sync(transport).requestTimeout(Duration.ofMillis((long) (config.path("timeout_s").asDouble(25) * 1000)))
                            .initializationTimeout(Duration.ofSeconds(10)).toolsChangeConsumer(tools -> discover(name, connection)).build();
                    client.initialize();
                    connection.client = client;
                    discover(name, connection);
                    connection.status = "connected";
                    connection.error = "";
                } catch (Exception ex) {
                    if (client != null) try { client.close(); } catch (Exception ignored) {}
                    disconnected(connection, ex);
                }
            }
        });
    }
    private void discover(String name, Connection connection) {
        McpSyncClient client = connection.client;
        if (client == null) return;
        List<McpSchema.Tool> tools = new ArrayList<>();
        String cursor = null;
        Set<String> cursors = new HashSet<>();
        do {
            var page = cursor == null ? client.listTools() : client.listTools(cursor);
            tools.addAll(page.tools());
            cursor = page.nextCursor();
            if (cursor != null && !cursors.add(cursor)) throw new IllegalStateException("Repeated MCP cursor");
        } while (cursor != null && !cursor.isEmpty());
        Set<String> next = new HashSet<>();
        Set<String> allowed = new HashSet<>(strings(connection.config.path("allowed_tools")));
        if (!connection.config.path("read_only").asBoolean(true) && !registry.writesEnabled()) {
            connection.tools.forEach(registry::unregister); connection.tools = Set.of(); return;
        }
        for (var tool : tools) {
            if (!allowed.contains(tool.name()) || (connection.config.path("read_only").asBoolean(true) && !safeReadOnly(tool))) continue;
            boolean readOnly = safeReadOnly(tool);
            String alias = alias(name, tool.name());
            Map<String, Object> schema = new LinkedHashMap<>();
            var input = tool.inputSchema();
            schema.put("type", input.type()); schema.put("properties", input.properties());
            if (input.required() != null) schema.put("required", input.required());
            if (input.additionalProperties() != null) schema.put("additionalProperties", input.additionalProperties());
            if (input.defs() != null) schema.put("$defs", input.defs());
            if (input.definitions() != null) schema.put("definitions", input.definitions());
            registry.register(new ToolDefinition(alias, tool.description() == null ? tool.name() : tool.description(), schema,
                    Set.copyOf(strings(connection.config.path("agents"))), readOnly, Duration.ofMillis((long) (connection.config.path("timeout_s").asDouble(25) * 1000)),
                    readOnly ? Duration.ofMillis((long) (connection.config.path("cache_ttl").asDouble(300) * 1000)) : Duration.ZERO, "external_mcp", name, tool.name(), args -> {
                        try {
                            var result = client.callTool(new McpSchema.CallToolRequest(tool.name(), args));
                            if (Boolean.TRUE.equals(result.isError())) throw new IllegalStateException("remote_tool_error");
                            Map<String, Object> payload = new LinkedHashMap<>();
                            payload.put("isError", false); payload.put("server", name); payload.put("tool", tool.name());
                            payload.put("content", result.content());
                            if (result.structuredContent() != null) payload.put("structuredContent", result.structuredContent());
                            return bounded(payload, connection.config.path("max_result_chars").asInt(12000));
                        } catch (Exception ex) {
                            // Remote tool errors are normal protocol replies; transport errors trigger reconnection.
                            if (!"remote_tool_error".equals(ex.getMessage())) {
                                synchronized (connection) { disconnected(connection, ex); try { client.close(); } catch (Exception ignored) {} }
                            }
                            throw ex;
                        }
                    }));
            next.add(alias);
        }
        connection.tools.stream().filter(t -> !next.contains(t)).forEach(registry::unregister);
        connection.tools = Set.copyOf(next);
    }
    static boolean safeReadOnly(McpSchema.Tool tool) {
        var annotations = tool.annotations();
        return annotations != null && Boolean.TRUE.equals(annotations.readOnlyHint())
                && !Boolean.TRUE.equals(annotations.destructiveHint());
    }
    static String alias(String server, String tool) {
        String name = "mcp_" + server + "_" + tool.replaceAll("[^A-Za-z0-9_-]", "_");
        return name.length() <= 64 ? name : name.substring(0, 47) + "_" + com.llmagentsystem.util.Hashing.key(server, tool).substring(0, 16);
    }
    public Object bounded(Map<String, Object> value, int maximum) throws Exception {
        int limit = Math.max(256, maximum);
        String json = wire.writeValueAsString(value);
        if (json.length() <= limit) return value;
        // Keep the result valid JSON and retain small structured outputs independently of long text blocks.
        Map<String, Object> trimmed = new LinkedHashMap<>();
        trimmed.put("isError", false); trimmed.put("truncated", true);
        trimmed.put("server", value.get("server")); trimmed.put("tool", value.get("tool"));
        Object structured = value.get("structuredContent");
        if (structured != null && wire.writeValueAsString(structured).length() < limit / 2) trimmed.put("structuredContent", structured);
        int budget = Math.max(0, limit - wire.writeValueAsString(trimmed).length() - 100);
        String text = json.substring(0, Math.min(json.length(), budget / 2));
        trimmed.put("content", List.of(Map.of("type", "text", "text", text)));
        while (wire.writeValueAsString(trimmed).length() > limit && !text.isEmpty()) {
            text = text.substring(0, text.length() / 2);
            trimmed.put("content", List.of(Map.of("type", "text", "text", text)));
        }
        return trimmed;
    }
    private void disconnected(Connection connection, Exception ex) {
        connection.tools.forEach(registry::unregister); connection.tools = Set.of(); connection.client = null;
        connection.status = "disconnected"; connection.error = ex.getClass().getSimpleName();
    }
    private List<String> strings(JsonNode array) { List<String> result = new ArrayList<>(); array.forEach(n -> result.add(n.asText())); return result; }
    public Map<String, Object> summary() {
        List<Map<String, Object>> items = new ArrayList<>();
        connections.forEach((name, c) -> items.add(Map.of("name", name, "status", c.status, "error", c.error, "tools", c.tools)));
        return Map.of("enabled", enabled, "servers", items, "tools", registry.available("admin").stream().filter(t -> t.source().equals("external_mcp")).map(ToolDefinition::name).toList());
    }
    @PreDestroy public void close() { connections.values().forEach(c -> { if (c.client != null) try { c.client.close(); } catch (Exception ignored) {} }); }
}
