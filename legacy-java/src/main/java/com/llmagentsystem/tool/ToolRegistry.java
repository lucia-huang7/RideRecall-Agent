package com.llmagentsystem.tool;

import com.llmagentsystem.util.Hashing;
import com.llmagentsystem.util.Tasks;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import org.springframework.stereotype.Service;
import java.time.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class ToolRegistry {
    private final ObjectMapper mapper;
    private final boolean writesEnabled;
    private final Map<String, Entry> entries = new ConcurrentHashMap<>();
    private static final class Entry {
        final ToolDefinition tool;
        final Schema schema;
        final CircuitBreaker circuit = new CircuitBreaker(5, Duration.ofSeconds(60));
        final ToolStats stats = new ToolStats();
        final Map<String, Cached> cache = new ConcurrentHashMap<>();
        Entry(ToolDefinition tool, Schema schema) { this.tool = tool; this.schema = schema; }
    }
    private record Cached(Object value, boolean reranked, Instant expires) {}
    public ToolRegistry(ObjectMapper mapper) { this(mapper, false); }
    public ToolRegistry(ObjectMapper mapper, boolean writesEnabled) { this.mapper = mapper; this.writesEnabled = writesEnabled; }
    @org.springframework.beans.factory.annotation.Autowired
    public ToolRegistry(ObjectMapper mapper, org.springframework.core.env.Environment env) { this(mapper, env.getProperty("MCP_WRITE_ENABLED", Boolean.class, false)); }
    public boolean writesEnabled() { return writesEnabled; }
    public void register(ToolDefinition tool) {
        if (tool.name() == null || !tool.name().matches("[A-Za-z0-9_-]{1,64}")) throw new IllegalArgumentException("Invalid tool name");
        if (!tool.readOnly() && !tool.cacheTtl().isZero()) throw new IllegalArgumentException("Write tools must not cache results");
        if (tool.contextHandler() != null && !tool.cacheTtl().isZero()) throw new IllegalArgumentException("Request-context tools must not share cached results");
        if (tool.handler() == null && tool.contextHandler() == null) throw new IllegalArgumentException("Tool requires a handler");
        // Validate compatibility before publishing; unsupported constraints must not disappear in a model request.
        com.llmagentsystem.llm.ToolSpecifications.from(tool, mapper);
        entries.put(tool.name(), new Entry(tool, SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12).getSchema(mapper.valueToTree(tool.inputSchema()))));
    }
    public void unregister(String name) { entries.remove(name); }
    public List<ToolDefinition> available(String agent) {
        return entries.values().stream().map(e -> e.tool).filter(t -> (t.readOnly() || writesEnabled) && (agent.equals("admin") || t.agents().contains(agent))).sorted(Comparator.comparing(ToolDefinition::name)).toList();
    }
    public ToolResult<Object> call(String name, Map<String, Object> arguments, String agent) {
        return call(name, arguments, agent, null);
    }
    public ToolResult<Object> call(String name, Map<String, Object> arguments, String agent, com.llmagentsystem.agent.AgentRequest request) {
        Entry entry = entries.get(name);
        long started = System.nanoTime();
        if (entry == null || (!entry.tool.readOnly() && (!writesEnabled || request == null || !request.operations().permitsWrites())) || (!agent.equals("admin") && !entry.tool.agents().contains(agent))) return failed(name, "tool_not_allowed", started);
        if (entry.tool.contextHandler() != null && request == null) return failed(name, "request_context_required", started);
        if (!entry.schema.validate(mapper.valueToTree(arguments)).isEmpty()) return failed(name, "invalid_arguments", started);
        String key;
        try {
            key = Hashing.sha256(mapper.writer().with(com.fasterxml.jackson.databind.SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS).writeValueAsString(arguments));
        } catch (Exception ex) { return failed(name, "invalid_arguments", started); }
        Cached cached = entry.cache.get(key);
        if (cached != null && Instant.now().isBefore(cached.expires)) return new ToolResult<>(true, cached.value, name, null, true, 0, cached.reranked);
        CircuitBreaker.Permit permit = entry.circuit.acquire();
        if (permit == null) return failed(name, "circuit_open", started);
        try {
            if (!entry.tool.readOnly()) request.operations().beforeWrite(name, arguments);
            Map<String, Object> snapshot = Collections.unmodifiableMap(new LinkedHashMap<>(arguments));
            Object value = Tasks.within(entry.tool.timeout(), () -> entry.tool.contextHandler() == null
                    ? entry.tool.handler().call(snapshot) : entry.tool.contextHandler().call(request, snapshot));
            if (value instanceof Map<?, ?> map && (Boolean.TRUE.equals(map.get("isError")) || Boolean.FALSE.equals(map.get("success"))))
                throw new IllegalStateException("tool_reported_error");
            boolean reranked = value instanceof Map<?, ?> map && Boolean.TRUE.equals(map.get("reranked"));
            entry.circuit.success(permit);
            entry.stats.record(true, elapsed(started));
            if (entry.cache.size() >= 500) entry.cache.clear();
            if (!entry.tool.cacheTtl().isZero()) entry.cache.put(key, new Cached(value, reranked, Instant.now().plus(entry.tool.cacheTtl())));
            return new ToolResult<>(true, value, name, null, false, elapsed(started), reranked);
        } catch (Exception ex) {
            entry.circuit.failure(permit);
            entry.stats.record(false, elapsed(started));
            return failed(name, ex.getMessage() != null && ex.getMessage().equals("timeout") ? "timeout" : "tool_execution_failed", started);
        }
    }
    private ToolResult<Object> failed(String name, String error, long start) { return new ToolResult<>(false, Map.of(), name, error, false, elapsed(start), false); }
    private long elapsed(long start) { return (System.nanoTime() - start) / 1_000_000; }
    public ToolRegistry fork() {
        ToolRegistry copy = new ToolRegistry(mapper);
        entries.values().stream().filter(e -> e.tool.readOnly()).forEach(e -> copy.register(e.tool));
        return copy;
    }
    public Map<String, Object> stats() {
        Map<String, Object> result = new TreeMap<>();
        entries.forEach((name, e) -> result.put(name, Map.of("total", e.stats.total(), "success_rate", e.stats.successRate(), "avg_latency_ms", e.stats.avgLatencyMs(), "circuit_state", e.circuit.state().name().toLowerCase())));
        return result;
    }
    public Map<String, Object> descriptor(String name) {
        Entry e = entries.get(name);
        return e == null ? Map.of() : Map.of("source", e.tool.source(), "server", e.tool.server(), "remote_name", e.tool.remoteName());
    }
}
