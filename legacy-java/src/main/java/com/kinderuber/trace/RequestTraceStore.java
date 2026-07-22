package com.kinderuber.trace;

import com.kinderuber.util.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import java.time.*;
import java.util.*;

@Service
public class RequestTraceStore {
    private static final int MAX_SIZE = 200;
    private final LinkedHashMap<String, RequestToolTrace> traces = new LinkedHashMap<>();
    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;
    public RequestTraceStore() { redis = null; mapper = null; }
    @Autowired public RequestTraceStore(StringRedisTemplate redis, ObjectMapper mapper) { this.redis = redis; this.mapper = mapper; }
    public synchronized void record(RequestToolTrace trace) {
        if (trace == null || trace.requestId() == null || trace.requestId().isBlank()) return;
        RequestToolTrace clean = new RequestToolTrace(trace.requestId(), trace.timestamp() == null ? Instant.now().toString() : trace.timestamp(), trace.endpoint(),
                Hashing.key(trace.userId()), Hashing.key(trace.conversationId()), trace.intent(), trace.intentGroup(), trace.agentType(), trace.primaryAgent(),
                trace.supportingAgents(), trace.toolsUsed(), trace.toolCalls().stream().map(t -> new ToolCallTrace(t.toolName(), t.success(), t.fallbackUsed(), t.cached(), t.reranked(), t.latencyMs(), redact(t.error()), t.source(), t.server(), t.remoteName())).toList(),
                trace.knowledgeUsed(), trace.escalated(), trace.latencyMs());
        traces.remove(clean.requestId()); traces.put(clean.requestId(), clean);
        while (traces.size() > MAX_SIZE) traces.remove(traces.keySet().iterator().next());
        if (redis != null) try {
            String lua = "redis.call('set',KEYS[1],ARGV[1],'EX',604800); redis.call('lrem',KEYS[2],0,ARGV[2]); redis.call('lpush',KEYS[2],ARGV[2]); redis.call('ltrim',KEYS[2],0,199); redis.call('expire',KEYS[2],604800); return 1";
            redis.execute(new DefaultRedisScript<>(lua, Long.class), List.of("trace:" + clean.requestId(), "trace:recent"), mapper.writeValueAsString(clean), clean.requestId());
        } catch (Exception ignored) { /* Traces remain available in memory if Redis is temporarily unavailable. */ }
    }
    private String redact(String text) {
        if (text == null) return "";
        String clean = text.replaceAll("(?i)(?:bearer\\s+|sk-)[A-Za-z0-9._-]+", "[REDACTED]")
                .replaceAll("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[EMAIL]")
                .replaceAll("(?<!\\d)1[3-9]\\d{9}(?!\\d)", "[PHONE]");
        return clean.substring(0, Math.min(256, clean.length()));
    }
    public synchronized Optional<RequestToolTrace> find(String id) {
        if (redis != null) try {
            String raw = redis.opsForValue().get("trace:" + id);
            if (raw != null) return Optional.of(mapper.readValue(raw, RequestToolTrace.class));
        } catch (Exception ignored) {}
        return Optional.ofNullable(traces.get(id));
    }
    public synchronized List<RequestToolTrace> recent(int limit) {
        int count = Math.max(0, Math.min(MAX_SIZE, limit));
        if (count == 0) return List.of();
        if (redis != null) try {
            List<String> ids = redis.opsForList().range("trace:recent", 0, count - 1);
            if (ids != null && !ids.isEmpty()) return ids.stream().map(this::find).flatMap(Optional::stream).toList();
        } catch (Exception ignored) {}
        return new ArrayList<>(traces.values()).reversed().stream().limit(count).toList();
    }
    public synchronized void clear() { traces.clear(); }
}
