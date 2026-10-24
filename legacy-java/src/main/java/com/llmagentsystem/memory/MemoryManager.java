package com.llmagentsystem.memory;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.llmagentsystem.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class MemoryManager {

    private static final Logger log = LoggerFactory.getLogger(MemoryManager.class);

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final LlmAgentSystemProperties properties;
    private final LlmGateway llmGateway;
    private final SemanticMemoryStore semantic;
    private final com.llmagentsystem.llm.PromptBudget budget;
    private final List<EpisodicEntry> episodicStore = new CopyOnWriteArrayList<>();
    private final Map<String, Map<String, Object>> profileStore = new ConcurrentHashMap<>();

    public MemoryManager(StringRedisTemplate redisTemplate, ObjectMapper objectMapper, LlmAgentSystemProperties properties, LlmGateway llmGateway) {
        this(redisTemplate, objectMapper, properties, llmGateway, null);
    }
    @org.springframework.beans.factory.annotation.Autowired
    public MemoryManager(StringRedisTemplate redisTemplate, ObjectMapper objectMapper, LlmAgentSystemProperties properties, LlmGateway llmGateway, SemanticMemoryStore semantic) {
        this.semantic = semantic;
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.llmGateway = llmGateway;
        this.budget = new com.llmagentsystem.llm.PromptBudget(properties, objectMapper);
    }

    @PostConstruct
    public void init() {
        loadPersistentMemory();
        if (semantic != null && semantic.enabled()) {
            for (var entry : episodicStore) semantic.remember(entry.userId(), entry.conversationId(), entry.summary(), entry.timestamp());
            profileStore.forEach((user, profile) -> semantic.updateProfile(user, current -> current.isEmpty() ? profile : current));
        }
    }

    public MemoryContext getContext(String userId, String conversationId, String query) {
        List<ConversationMessage> recent = getWorkingMemory(userId, conversationId);
        List<String> relevantHistory = semantic != null && semantic.enabled() ? semantic.recall(userId, conversationId, query) : searchEpisodic(userId, conversationId, query);
        Map<String, Object> profile = semantic != null && semantic.enabled() ? semantic.profile(userId) : profileStore.getOrDefault(userId, Map.of());
        String summary = safeRedisGet(summaryKey(userId, conversationId));
        return new MemoryContext(recent, relevantHistory, profile, summary == null ? "" : summary);
    }

    public void addMessage(String userId, String conversationId, MessageRole role, String content) {
        ConversationMessage message = new ConversationMessage(role, safe(content), Instant.now(), Map.of());
        String key = wmKey(userId, conversationId);
        try {
            redisTemplate.opsForList().leftPush(key, objectMapper.writeValueAsString(message));
            redisTemplate.expire(key, Duration.ofSeconds(properties.getMemory().getTtlSeconds()));
            Long size = redisTemplate.opsForList().size(key);
            if (size != null && size >= properties.getMemory().getCompressAt()) {
                compress(userId, conversationId);
            }
        } catch (Exception ex) {
            log.warn("Failed to write working memory: {}", ex.getMessage());
        }
    }

    public void updateProfile(String userId, String conversationId) {
        List<ConversationMessage> messages = getWorkingMemory(userId, conversationId);
        if (messages.isEmpty()) {
            return;
        }
        String text = tail(messages, 10).stream()
                .map(m -> m.role().name().toLowerCase() + ": " + m.content())
                .reduce((a, b) -> a + "\n" + b)
                .orElse("");
        String prompt = """
                从以下客服对话中提炼用户画像，返回 JSON：
                %s
                格式: {"preferences":["..."],"entities":{"产品":[],"问题类型":[]}}
                """.formatted(text);
        try {
            String raw = com.llmagentsystem.util.Tasks.within(Duration.ofSeconds(10), () -> llmGateway.chatRequired("", prompt, 0.0, 512));
            Map<String, Object> extracted = readProfile(raw);
            if (semantic != null && semantic.enabled()) semantic.updateProfile(userId, previous -> mergeProfile(previous, extracted));
            else { profileStore.compute(userId, (ignored, previous) -> mergeProfile(previous, extracted)); persistMemory(); }
        } catch (Exception ex) {
            log.warn("Profile update failed: {}", ex.getMessage());
        }
    }

    public List<ConversationMessage> getWorkingMemory(String userId, String conversationId) {
        return readMessages(userId, conversationId, properties.getMemory().getWorkingMax() - 1);
    }

    private List<ConversationMessage> readMessages(String userId, String conversationId, long end) {
        try {
            String key = wmKey(userId, conversationId);
            List<String> raw = redisTemplate.opsForList().range(key, 0, end);
            if (raw == null) {
                return List.of();
            }
            List<ConversationMessage> messages = new ArrayList<>();
            for (int i = raw.size() - 1; i >= 0; i--) {
                messages.add(objectMapper.readValue(raw.get(i), ConversationMessage.class));
            }
            return messages;
        } catch (Exception ex) {
            throw new IllegalStateException("工作记忆读取失败", ex);
        }
    }

    public void maintain(String userId, String conversationId) {
        try { compress(userId, conversationId); updateProfile(userId, conversationId); }
        catch (Exception ex) { log.warn("Memory maintenance failed: {}", ex.getClass().getSimpleName()); }
    }

    private void compress(String userId, String conversationId) {
        List<ConversationMessage> messages = readMessages(userId, conversationId, -1);
        if (messages.size() < properties.getMemory().getCompressAt()) {
            return;
        }
        int keep = Math.min(5, messages.size());
        int batchEnd = 0, batchCost = 0;
        while (batchEnd < messages.size() - keep) {
            int cost = budget.estimateTokenCountInText(messages.get(batchEnd).content()) + 32;
            if (batchEnd > 0 && batchCost + cost > properties.getLlm().getMaxInputTokens() / 3) break;
            batchCost += cost; batchEnd++;
        }
        List<ConversationMessage> oldMessages = messages.subList(0, batchEnd);
        String text = oldMessages.stream()
                .map(m -> m.role().name().toLowerCase() + ": " + m.content())
                .reduce((a, b) -> a + "\n" + b)
                .orElse("");
        String summary;
        String previousSummary;
        try {
            previousSummary = redisTemplate.opsForValue().get(summaryKey(userId, conversationId));
            String prior = previousSummary == null ? "" : previousSummary;
            summary = com.llmagentsystem.util.Tasks.within(Duration.ofSeconds(10), () -> llmGateway.chatRequired("",
                    "用 2-3 句话合并总结已有摘要和新增对话，保留有效事实、用户约束和未解决问题，去除重复信息。\n[已有摘要]\n" + prior + "\n[新增对话]\n" + text, 0.0, 384));
            if (summary == null || summary.isBlank()) throw new IllegalStateException("empty_summary");
            summary = budget.truncate(summary, 1500);
        } catch (Exception ex) {
            log.warn("Skipping memory compression; summary unavailable: {}", ex.getClass().getSimpleName());
            return;
        }
        String key = wmKey(userId, conversationId);
        try {
            // Compare the entire captured suffix, then remove only those old messages.
            List<String> args = new ArrayList<>();
            args.add(summary);
            args.add(Long.toString(properties.getMemory().getTtlSeconds()));
            args.add(Integer.toString(oldMessages.size()));
            args.add(previousSummary == null ? "" : previousSummary);
            for (int i = oldMessages.size() - 1; i >= 0; i--) args.add(objectMapper.writeValueAsString(oldMessages.get(i)));
            String lua = """
                local n=tonumber(ARGV[3])
                if (redis.call('get',KEYS[2]) or '') ~= ARGV[4] then return 0 end
                local current=redis.call('lrange',KEYS[1],-n,-1)
                if #current ~= n then return 0 end
                for i=1,n do if current[i] ~= ARGV[i+4] then return 0 end end
                local length=redis.call('llen',KEYS[1])
                redis.call('ltrim',KEYS[1],0,length-n-1)
                redis.call('set',KEYS[2],ARGV[1],'EX',ARGV[2])
                return 1
                """;
            Long committed = redisTemplate.execute(new org.springframework.data.redis.core.script.DefaultRedisScript<>(lua, Long.class),
                    List.of(key, summaryKey(userId, conversationId)), args.toArray());
            if (Long.valueOf(1).equals(committed)) {
                Instant timestamp = Instant.now();
                if (semantic != null && semantic.enabled()) semantic.remember(userId, conversationId, summary, timestamp);
                else { episodicStore.add(new EpisodicEntry(userId, conversationId, summary, text, timestamp, embed(summary))); persistMemory(); }
            }
        } catch (Exception ex) { log.warn("Memory compression failed: {}", ex.getClass().getSimpleName()); }
    }

    private List<String> searchEpisodic(String userId, String conversationId, String query) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        double[] queryVec = embed(query);
        return episodicStore.stream()
                .filter(e -> e.userId().equals(userId))
                .sorted(Comparator.comparing((EpisodicEntry e) -> !e.conversationId().equals(conversationId))
                        .thenComparing(Comparator.comparingDouble((EpisodicEntry e) -> cosine(queryVec, e.embedding())).reversed()))
                .limit(5)
                .map(EpisodicEntry::summary).distinct()
                .toList();
    }

    private String safeRedisGet(String key) {
        try {
            return redisTemplate.opsForValue().get(key);
        } catch (Exception ex) {
            return null;
        }
    }

    private List<ConversationMessage> tail(List<ConversationMessage> messages, int n) {
        return messages.subList(Math.max(0, messages.size() - n), messages.size());
    }

    private Map<String, Object> readProfile(String raw) throws Exception {
        if (raw == null) throw new IllegalArgumentException("missing_profile");
        int start = raw.indexOf('{');
        int end = raw.lastIndexOf('}');
        if (start < 0 || end <= start) throw new IllegalArgumentException("invalid_profile_json");
        var node = objectMapper.readTree(raw.substring(start, end + 1));
        if (!node.isObject() || !node.path("preferences").isArray() || !node.path("entities").isObject())
            throw new IllegalArgumentException("invalid_profile_shape");
        for (var preference : node.path("preferences")) if (!preference.isTextual()) throw new IllegalArgumentException("invalid_profile_preference");
        for (var values : node.path("entities")) {
            if (!values.isArray()) throw new IllegalArgumentException("invalid_profile_entities");
            for (var value : values) if (!value.isTextual()) throw new IllegalArgumentException("invalid_profile_entity");
        }
        return objectMapper.convertValue(node, new TypeReference<>() {});
    }

    private Map<String, Object> mergeProfile(Map<String, Object> previous, Map<String, Object> extracted) {
        Map<String, Object> merged = new java.util.LinkedHashMap<>(previous == null ? Map.of() : previous);
        merged.put("preferences", mergeValues(merged.get("preferences"), extracted.get("preferences")));
        Map<String, Object> entities = new java.util.LinkedHashMap<>();
        if (merged.get("entities") instanceof Map<?, ?> old) old.forEach((k, v) -> entities.put(k.toString(), v));
        if (extracted.get("entities") instanceof Map<?, ?> added)
            added.forEach((k, v) -> entities.put(k.toString(), mergeValues(entities.get(k.toString()), v)));
        while (entities.size() > 12) entities.remove(entities.keySet().iterator().next());
        merged.put("entities", entities);
        return Map.copyOf(merged);
    }

    private List<String> mergeValues(Object previous, Object added) {
        var values = new java.util.LinkedHashSet<String>();
        for (Object source : new Object[]{previous, added}) {
            if (source instanceof List<?> list) for (Object value : list)
                if (value instanceof String text && !text.isBlank()) values.add(budget.truncate(text, 128));
        }
        while (values.size() > 12) values.remove(values.iterator().next());
        return List.copyOf(values);
    }

    private String wmKey(String userId, String conversationId) {
        return "wm:" + ConversationStore.scope(userId, conversationId);
    }

    private String summaryKey(String userId, String conversationId) {
        return "summary:" + ConversationStore.scope(userId, conversationId);
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }

    private double[] embed(String text) {
        double[] vector = new double[256];
        String normalized = text == null ? "" : text.toLowerCase();
        for (int n = 1; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                String gram = normalized.substring(i, i + n);
                int hash = gram.hashCode();
                int idx = Math.floorMod(hash, vector.length);
                vector[idx] += (hash & 1) == 0 ? 1.0 : -1.0;
            }
        }
        return vector;
    }

    private double cosine(double[] a, double[] b) {
        double dot = 0.0;
        double na = 0.0;
        double nb = 0.0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }
        return na == 0 || nb == 0 ? 0.0 : dot / (Math.sqrt(na) * Math.sqrt(nb));
    }

    private void loadPersistentMemory() {
        Path path = Path.of(properties.getStorage().getMemoryPath());
        if (!Files.exists(path)) {
            return;
        }
        try {
            StoredMemory stored = objectMapper.readValue(path.toFile(), StoredMemory.class);
            if (stored.episodic() != null) {
                stored.episodic().forEach(entry -> {
                    if (entry.summary() != null && !entry.summary().isBlank()) {
                        episodicStore.add(new EpisodicEntry(
                                safe(entry.userId()),
                                safe(entry.conversationId()),
                                safe(entry.summary()),
                                safe(entry.fullText()),
                                entry.timestamp() == null ? Instant.now() : entry.timestamp(),
                                embed(entry.summary())
                        ));
                    }
                });
            }
            if (stored.profiles() != null) {
                profileStore.putAll(stored.profiles());
            }
            log.info("Loaded persisted memory: episodic={}, profiles={}", episodicStore.size(), profileStore.size());
        } catch (Exception ex) {
            throw new IllegalStateException("长期记忆文件损坏或无法读取，请先恢复数据文件：" + path, ex);
        }
    }

    private synchronized void persistMemory() {
        Path path = Path.of(properties.getStorage().getMemoryPath());
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            List<StoredEpisodicEntry> episodic = episodicStore.stream()
                    .map(entry -> new StoredEpisodicEntry(entry.userId(), entry.conversationId(), entry.summary(), entry.fullText(), entry.timestamp()))
                    .toList();
            com.llmagentsystem.util.AtomicJson.write(objectMapper, path, new StoredMemory(episodic, Map.copyOf(profileStore)));
        } catch (Exception ex) {
            log.warn("Failed to persist memory store {}: {}", path, ex.getMessage());
        }
    }

    private record EpisodicEntry(String userId, String conversationId, String summary, String fullText, Instant timestamp, double[] embedding) {
    }

    private record StoredMemory(List<StoredEpisodicEntry> episodic, Map<String, Map<String, Object>> profiles) {
        private StoredMemory {
            episodic = episodic == null ? List.of() : episodic;
            profiles = profiles == null ? Map.of() : profiles;
        }
    }

    private record StoredEpisodicEntry(String userId, String conversationId, String summary, String fullText, Instant timestamp) {
    }
}
