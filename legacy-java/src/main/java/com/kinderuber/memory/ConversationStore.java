package com.kinderuber.memory;

import com.kinderuber.api.dto.ChatResponse;
import com.kinderuber.util.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import jakarta.annotation.PreDestroy;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;

@Service
public class ConversationStore {
    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;
    private final ScheduledExecutorService renewals = Executors.newSingleThreadScheduledExecutor(r -> { Thread t = new Thread(r, "conversation-leases"); t.setDaemon(true); return t; });
    private static final DefaultRedisScript<Long> RENEW = new DefaultRedisScript<>("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('pexpire',KEYS[1],ARGV[2]) end return 0", Long.class);
    private static final DefaultRedisScript<Long> RELEASE = new DefaultRedisScript<>("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0", Long.class);
    private static final DefaultRedisScript<Long> COMMIT = new DefaultRedisScript<>("""
        if redis.call('get',KEYS[1]) ~= ARGV[1] then return 0 end
        if ARGV[7] == '1' then
          if redis.call('exists',KEYS[5]) == 0 then
            local old=redis.call('lrange',KEYS[2],0,197)
            for i=#old,1,-1 do redis.call('lpush',KEYS[5],old[i]) end
            redis.call('set',KEYS[6],tostring(#old))
            if #old > 0 then redis.call('set',KEYS[7],'1','EX',ARGV[5]) end
          end
          redis.call('lpush',KEYS[2],ARGV[2],ARGV[3])
          redis.call('expire',KEYS[2],ARGV[5])
          redis.call('set',KEYS[4],ARGV[6],'EX',ARGV[5])
          redis.call('lpush',KEYS[5],ARGV[2],ARGV[3])
          redis.call('ltrim',KEYS[5],0,199)
          redis.call('expire',KEYS[5],ARGV[5])
          redis.call('incrby',KEYS[6],2)
          redis.call('expire',KEYS[6],ARGV[5])
          if redis.call('exists',KEYS[7]) == 1 then redis.call('expire',KEYS[7],ARGV[5]) end
        end
        redis.call('set',KEYS[3],ARGV[4],'EX',86400)
        return 1
        """, Long.class);
    public ConversationStore(StringRedisTemplate redis, ObjectMapper mapper) { this.redis = redis; this.mapper = mapper; }
    public static String scope(String user, String conversation) { return Hashing.key(user, conversation); }
    public Lease acquire(String user, String conversation) {
        return acquireKey("conversation_lock:" + scope(user, conversation));
    }
    public Lease acquireProfile(String user) { return acquireKey("profile_lock:" + Hashing.key(user)); }
    private Lease acquireKey(String key) {
        String token = UUID.randomUUID().toString();
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        try {
            while (!Boolean.TRUE.equals(redis.opsForValue().setIfAbsent(key, token, Duration.ofSeconds(15)))) {
                if (System.nanoTime() >= deadline) throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "会话繁忙，请使用相同请求 ID 重试");
                Thread.sleep(50);
            }
            return new Lease(key, token);
        } catch (InterruptedException ex) { Thread.currentThread().interrupt(); throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "请求已取消"); }
        catch (org.springframework.dao.DataAccessException ex) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "会话存储不可用"); }
    }
    public final class Lease implements AutoCloseable {
        private final String key, token;
        private volatile boolean lost;
        private final ScheduledFuture<?> renewal;
        Lease(String key, String token) {
            this.key = key; this.token = token;
            renewal = renewals.scheduleAtFixedRate(() -> {
                try { if (!Long.valueOf(1).equals(redis.execute(RENEW, List.of(key), token, "15000"))) lost = true; }
                catch (Exception ex) { lost = true; }
            }, 4, 4, TimeUnit.SECONDS);
        }
        public void assertValid() {
            if (lost || Thread.currentThread().isInterrupted()) throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "会话锁已失效，请重试");
        }
        public void close() { renewal.cancel(false); try { redis.execute(RELEASE, List.of(key), token); } catch (Exception ignored) {} }
    }
    public record Receipt(String message, ChatResponse response) {}
    public Receipt receipt(String user, String conv, String clientId) {
        try {
            String raw = redis.opsForValue().get(receiptKey(user, conv, clientId));
            return raw == null ? null : mapper.readValue(raw, Receipt.class);
        } catch (Exception ex) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "请求回执读取失败"); }
    }
    public Map<String, Object> state(String user, String conv) {
        try {
            String raw = redis.opsForValue().get("state:" + scope(user, conv));
            return raw == null ? Map.of() : mapper.readValue(raw, new com.fasterxml.jackson.core.type.TypeReference<>() {});
        } catch (Exception ex) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "会话状态读取失败"); }
    }
    public void commit(Lease lease, String user, String conv, String clientId, String message, ChatResponse reply, Map<String, Object> state, long ttl) {
        commit(lease, user, conv, clientId, message, reply, state, ttl, List.of(), "");
    }
    public void commit(Lease lease, String user, String conv, String clientId, String message, ChatResponse reply, Map<String, Object> state, long ttl,
                       List<String> evidence, String context) {
        lease.assertValid();
        try {
            String userJson = mapper.writeValueAsString(new ConversationMessage(MessageRole.USER, message, Instant.now(), Map.of("request_id", Objects.toString(reply.requestId(), ""))));
            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("request_id", reply.requestId()); metadata.put("intent", reply.intent()); metadata.put("agent_type", reply.agentType());
            metadata.put("status", reply.status()); metadata.put("tools_used", reply.toolsUsed());
            metadata.put("evidence", evidence); metadata.put("context", context);
            String assistantJson = mapper.writeValueAsString(new ConversationMessage(MessageRole.ASSISTANT, reply.response(), Instant.now(), metadata));
            Long saved = redis.execute(COMMIT, List.of(lease.key, "wm:" + scope(user, conv), receiptKey(user, conv, clientId), "state:" + scope(user, conv),
                            "history:" + scope(user, conv), "history_count:" + scope(user, conv), "history_legacy:" + scope(user, conv)),
                    lease.token, userJson, assistantJson, mapper.writeValueAsString(new Receipt(message, reply)), Long.toString(ttl), mapper.writeValueAsString(state), reply.status() != null && reply.status().equals("failed") ? "0" : "1");
            if (!Long.valueOf(1).equals(saved)) throw new IllegalStateException("lease_lost");
        } catch (Exception ex) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "本轮提交失败，请使用相同请求 ID 重试"); }
    }
    public record Transcript(List<ConversationMessage> messages, String source, boolean truncated) {}
    /** One read-only Redis snapshot; independent of the working-memory compression process. */
    @SuppressWarnings("unchecked")
    public Transcript transcript(String user, String conv) {
        try {
            String lua = """
                    local rows=redis.call('lrange',KEYS[1],0,199)
                    local source='conversation_history'
                    if redis.call('exists',KEYS[4]) == 1 then source='conversation_history_with_legacy' end
                    local total=redis.call('get',KEYS[2]) or '0'
                    if #rows == 0 then
                      rows=redis.call('lrange',KEYS[3],0,199)
                      source='working_memory_legacy'
                      total=tostring(redis.call('llen',KEYS[3]))
                    end
                    local result={source,total}
                    for _,row in ipairs(rows) do table.insert(result,row) end
                    return result
                    """;
            List<String> raw = redis.execute(new DefaultRedisScript<>(lua, List.class),
                    List.of("history:" + scope(user, conv), "history_count:" + scope(user, conv), "wm:" + scope(user, conv), "history_legacy:" + scope(user, conv)));
            if (raw == null || raw.size() <= 2) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "当前用户的会话记录不存在或已过期");
            List<ConversationMessage> messages = new ArrayList<>();
            for (int i = raw.size() - 1; i >= 2; i--) messages.add(mapper.readValue(raw.get(i), ConversationMessage.class));
            return new Transcript(List.copyOf(messages), raw.getFirst(),
                    !raw.getFirst().equals("conversation_history") || Long.parseLong(raw.get(1)) > messages.size());
        } catch (ResponseStatusException ex) { throw ex; }
        catch (Exception ex) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "会话记录读取失败，请稍后重试"); }
    }
    private String receiptKey(String user, String conv, String client) { return "receipt:" + scope(user, conv) + ":" + Hashing.key(client); }
    @PreDestroy public void close() { renewals.shutdownNow(); }
}
