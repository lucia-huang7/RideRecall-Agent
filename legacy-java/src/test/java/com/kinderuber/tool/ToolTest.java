package com.kinderuber.tool;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class ToolTest {
    @Test void nullableArgumentsReachHandlerAndDoNotTripCircuit() {
        var registry = new ToolRegistry(new ObjectMapper()); var calls = new AtomicInteger();
        registry.register(new ToolDefinition("nullable", "Nullable", Map.of("type", "object", "properties", Map.of("query", Map.of("type", List.of("string", "null")))),
                Set.of("general"), true, Duration.ofSeconds(1), Duration.ZERO, "local", "", "", args -> {
                    assertTrue(args.containsKey("query")); assertNull(args.get("query"));
                    assertThrows(UnsupportedOperationException.class, () -> args.put("query", "changed"));
                    calls.incrementAndGet(); return "ok";
                }));
        Map<String, Object> args = new HashMap<>(); args.put("query", null);
        for (int i = 0; i < 6; i++) assertTrue(registry.call("nullable", args, "general").success());
        assertEquals(6, calls.get());
    }
    @Test void rerankingMetadataSurvivesCacheAndTraceAdapter() {
        var mapper = new ObjectMapper(); var registry = new ToolRegistry(mapper);
        registry.register(new ToolDefinition("ranked", "Ranked", Map.of("type", "object"), Set.of("general"), true, Duration.ofSeconds(1), Duration.ofSeconds(30), "local", "", "",
                args -> Map.of("results", List.of("政策"), "reranked", true)));
        assertTrue(registry.call("ranked", Map.of(), "general").reranked());
        var cached = registry.call("ranked", Map.of(), "general"); assertTrue(cached.cached()); assertTrue(cached.reranked());
        var traces = new ArrayList<com.kinderuber.trace.ToolCallTrace>();
        var provider = new RegistryToolProvider(registry, mapper, "general", new ArrayList<>(), traces, com.kinderuber.llm.PromptBudget.defaults());
        provider.execute(dev.langchain4j.agent.tool.ToolExecutionRequest.builder().id("id").name("ranked").arguments("{}").build());
        assertTrue(traces.getFirst().reranked()); assertTrue(traces.getFirst().cached());
    }
    @Test void unsupportedRootConstraintsAreRejectedBeforePublishingTool() {
        var registry = new ToolRegistry(new ObjectMapper());
        for (String keyword : List.of("anyOf", "oneOf", "allOf", "minProperties", "$ref")) {
            var schema = new HashMap<String, Object>(); schema.put("type", "object"); schema.put(keyword, List.of(Map.of("required", List.of("id"))));
            var invalid = new ToolDefinition("lookup", "Lookup", schema, Set.of("general"), true, Duration.ofSeconds(1), Duration.ZERO, "local", "", "", args -> args);
            var error = assertThrows(IllegalArgumentException.class, () -> registry.register(invalid));
            assertTrue(error.getMessage().contains(keyword)); assertTrue(registry.available("general").isEmpty());
        }
    }
    @Test void staleSuccessCannotCloseNewCircuitAndOnlyOneProbeRuns() {
        CircuitBreaker breaker = new CircuitBreaker(1, Duration.ZERO);
        var late = breaker.acquire(); var failed = breaker.acquire();
        breaker.failure(failed); breaker.success(late);
        assertEquals(CircuitState.OPEN, breaker.state());
        var probe = breaker.acquire(); assertNotNull(probe); assertNull(breaker.acquire());
        breaker.failure(probe); assertEquals(CircuitState.OPEN, breaker.state());
        var recovery = breaker.acquire(); breaker.success(recovery); assertEquals(CircuitState.CLOSED, breaker.state());
    }
    private ToolDefinition tool(AtomicInteger count, boolean fail) {
        return new ToolDefinition("lookup", "Lookup", Map.of("type", "object", "properties", Map.of("query", Map.of("type", "string", "minLength", 1)), "required", List.of("query"), "additionalProperties", false),
                Set.of("technical"), true, Duration.ofSeconds(1), Duration.ofSeconds(30), "local", "", "", args -> { count.incrementAndGet(); if (fail) throw new IllegalStateException("private credential"); return args; });
    }
    @Test void validatesSchemaAndScopeBeforeExecutionAndCachesSuccessOnly() {
        ToolRegistry registry = new ToolRegistry(new ObjectMapper()); AtomicInteger count = new AtomicInteger(); registry.register(tool(count, false));
        assertFalse(registry.call("lookup", Map.of(), "technical").success());
        assertFalse(registry.call("lookup", Map.of("query", 42), "technical").success());
        assertFalse(registry.call("lookup", Map.of("query", "x"), "billing").success());
        assertEquals(0, count.get());
        assertTrue(registry.call("lookup", Map.of("query", "x"), "technical").success());
        assertTrue(registry.call("lookup", Map.of("query", "x"), "technical").cached()); assertEquals(1, count.get());
        registry.register(tool(count, true));
        var first = registry.call("lookup", Map.of("query", "x"), "technical");
        assertFalse(first.success()); assertFalse(first.error().contains("credential"));
        assertFalse(registry.call("lookup", Map.of("query", "x"), "technical").cached()); assertEquals(3, count.get());
    }
    @Test void forkSeparatesStatsAndCaches() {
        ToolRegistry registry = new ToolRegistry(new ObjectMapper()); AtomicInteger count = new AtomicInteger(); registry.register(tool(count, false));
        registry.fork().call("lookup", Map.of("query", "x"), "technical");
        assertFalse(registry.call("lookup", Map.of("query", "x"), "technical").cached()); assertEquals(2, count.get());
    }
    @Test void timesOutAndDoesNotCacheLateResult() {
        ToolRegistry registry = new ToolRegistry(new ObjectMapper()); AtomicInteger count = new AtomicInteger();
        registry.register(new ToolDefinition("slow", "Slow", Map.of("type", "object"), Set.of("general"), true, Duration.ofMillis(20), Duration.ofSeconds(30), "local", "", "", args -> { count.incrementAndGet(); Thread.sleep(200); return "late"; }));
        assertFalse(registry.call("slow", Map.of(), "general").success());
        assertFalse(registry.call("slow", Map.of(), "general").cached()); assertEquals(2, count.get());
    }
}
