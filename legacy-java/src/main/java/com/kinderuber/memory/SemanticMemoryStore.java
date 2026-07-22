package com.kinderuber.memory;

import com.kinderuber.storage.SemanticStorage;
import com.kinderuber.util.Hashing;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.document.Metadata;
import dev.langchain4j.data.embedding.Embedding;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.store.embedding.*;
import dev.langchain4j.store.embedding.filter.Filter;
import org.springframework.stereotype.Component;
import java.time.Instant;
import java.util.*;
import java.util.function.UnaryOperator;
import static dev.langchain4j.store.embedding.filter.MetadataFilterBuilder.metadataKey;

@Component
public class SemanticMemoryStore {
    private final SemanticStorage storage;
    private final ObjectMapper mapper;
    private final ConversationStore conversations;
    private volatile Embedding profileVector;
    public SemanticMemoryStore(SemanticStorage storage, ObjectMapper mapper, ConversationStore conversations) {
        this.storage = storage; this.mapper = mapper; this.conversations = conversations;
    }
    public boolean enabled() { return storage.enabled(); }
    private Filter user(String user) { return metadataKey("user_scope").isEqualTo(Hashing.key(user)); }
    private List<EmbeddingMatch<TextSegment>> search(EmbeddingStore<TextSegment> store, Embedding vector, Filter filter, int limit) {
        return store.search(EmbeddingSearchRequest.builder().queryEmbedding(vector).filter(filter).maxResults(limit).minScore(0.0).build()).matches();
    }
    public void remember(String user, String conv, String summary, Instant timestamp) {
        String id = Hashing.key(user, conv, timestamp.toString(), summary);
        Filter identity = metadataKey("entry_key").isEqualTo(id);
        var vector = storage.model().embed(summary).content();
        if (!search(storage.episodic(), vector, identity, 1).isEmpty()) return;
        var metadata = Metadata.from(Map.of("user_scope", Hashing.key(user), "conversation_scope", Hashing.key(user, conv), "entry_key", id));
        storage.episodic().addAll(List.of(id), List.of(vector), List.of(TextSegment.from(summary, metadata)));
    }
    public List<String> recall(String user, String conv, String query) {
        if (query == null || query.isBlank()) return List.of();
        var vector = storage.model().embed(query).content();
        var scoped = user(user).and(metadataKey("conversation_scope").isEqualTo(Hashing.key(user, conv)));
        LinkedHashSet<String> results = new LinkedHashSet<>();
        search(storage.episodic(), vector, scoped, 5).forEach(m -> results.add(m.embedded().text()));
        if (results.size() < 5) search(storage.episodic(), vector, user(user), 5).forEach(m -> results.add(m.embedded().text()));
        return results.stream().limit(5).toList();
    }
    private Embedding profileVector() {
        var vector = profileVector;
        if (vector == null) synchronized (this) {
            if (profileVector == null) profileVector = storage.model().embed("user profile").content();
            vector = profileVector;
        }
        return vector;
    }
    public Map<String, Object> profile(String user) {
        return search(storage.profiles(), profileVector(), user(user), 100).stream()
                .max(Comparator.comparingLong(m -> m.embedded().metadata().getLong("updated_at")))
                .map(m -> {
                    try { return mapper.<Map<String, Object>>readValue(m.embedded().text(), new TypeReference<>() {}); }
                    catch (Exception ex) { throw new IllegalStateException("Invalid stored profile", ex); }
                }).orElse(Map.of());
    }
    /** Serialize profile merges across instances and append before removing the old version. */
    public synchronized Map<String, Object> updateProfile(String user, UnaryOperator<Map<String, Object>> merge) {
        if (conversations == null) return writeProfile(user, merge);
        try (var lease = conversations.acquireProfile(user)) { lease.assertValid(); return writeProfile(user, merge); }
    }
    private Map<String, Object> writeProfile(String user, UnaryOperator<Map<String, Object>> merge) {
        Map<String, Object> previous = profile(user), next = merge.apply(previous);
        if (next.equals(previous)) return previous;
        String version = UUID.randomUUID().toString();
        var metadata = Metadata.from(Map.of("user_scope", Hashing.key(user), "updated_at", System.currentTimeMillis(), "version", version));
        try {
            storage.profiles().addAll(List.of(version), List.of(profileVector()), List.of(TextSegment.from(mapper.writeValueAsString(next), metadata)));
            storage.profiles().removeAll(user(user).and(metadataKey("version").isNotEqualTo(version)));
            return next;
        } catch (Exception ex) { throw new IllegalStateException("Profile persistence failed", ex); }
    }
}
