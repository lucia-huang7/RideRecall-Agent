package com.llmagentsystem.knowledge;

import com.llmagentsystem.util.Hashing;
import dev.langchain4j.data.document.Metadata;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.model.embedding.EmbeddingModel;
import dev.langchain4j.rag.content.ContentMetadata;
import dev.langchain4j.rag.content.retriever.EmbeddingStoreContentRetriever;
import dev.langchain4j.rag.query.Query;
import dev.langchain4j.store.embedding.EmbeddingStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;
import java.util.*;

/** Optional semantic branch of hybrid retrieval. The model and store must use the same vector space. */
@Component
public class SemanticKnowledgeIndex {
    private static final Logger log = LoggerFactory.getLogger(SemanticKnowledgeIndex.class);
    private final EmbeddingModel model;
    private boolean required;
    private final EmbeddingStore<TextSegment> store;
    private final com.llmagentsystem.llm.LlmMetrics metrics;
    private record State(long revision, Map<String, String> indexed, boolean ready) {}
    private volatile State state = new State(0, Map.of(), false);

    public SemanticKnowledgeIndex(ObjectProvider<EmbeddingModel> models, ObjectProvider<EmbeddingStore<TextSegment>> stores) {
        this(models, stores, com.llmagentsystem.llm.LlmMetrics.isolated());
    }
    public SemanticKnowledgeIndex(ObjectProvider<EmbeddingModel> models, ObjectProvider<EmbeddingStore<TextSegment>> stores, com.llmagentsystem.llm.LlmMetrics metrics) {
        this.model = models.getIfAvailable(); this.store = stores.getIfAvailable();
        this.metrics = metrics;
        if ((model == null) != (store == null))
            throw new IllegalArgumentException("Knowledge retrieval requires both EmbeddingModel and EmbeddingStore<TextSegment>");
    }

    @org.springframework.beans.factory.annotation.Autowired
    public SemanticKnowledgeIndex(com.llmagentsystem.storage.SemanticStorage storage, com.llmagentsystem.llm.LlmMetrics metrics) {
        model = storage.model(); store = storage.knowledge(); this.metrics = metrics; required = storage.required();
    }

    public void refresh(List<KnowledgeDocument> documents) { refresh(documents, state.revision() + 1); }

    public synchronized void refresh(List<KnowledgeDocument> documents, long revision) {
        if (model == null) return;
        Map<String, String> indexed = state.indexed();
        state = new State(revision, indexed, false);
        try {
            Map<String, String> next = new LinkedHashMap<>();
            documents.forEach(doc -> next.put(doc.id(), Hashing.sha256(doc.content())));
            List<KnowledgeDocument> changed = documents.stream().filter(doc -> !next.get(doc.id()).equals(indexed.get(doc.id()))).toList();
            for (int offset = 0; offset < changed.size(); offset += 32) {
                var batch = changed.subList(offset, Math.min(offset + 32, changed.size()));
                var segments = batch.stream().map(doc -> {
                    Map<String, Object> metadata = new HashMap<>(doc.metadata());
                    metadata.put("llmagentsystem_content_hash", next.get(doc.id()));
                    return TextSegment.from(doc.content(), Metadata.from(metadata));
                }).toList();
                var embeddings = model.embedAll(segments).content();
                if (embeddings.size() != batch.size()) throw new IllegalStateException("embedding_count_mismatch");
                var ids = batch.stream().map(KnowledgeDocument::id).toList();
                // EmbeddingStore does not guarantee that addAll with an existing ID is an upsert.
                store.removeAll(ids);
                store.addAll(ids, embeddings, segments);
            }
            var stale = indexed.keySet().stream().filter(id -> !next.containsKey(id)).toList();
            if (!stale.isEmpty()) store.removeAll(stale);
            state = new State(revision, Map.copyOf(next), true);
        } catch (Exception ex) {
            if (required) throw new IllegalStateException("Chroma knowledge index unavailable", ex);
            metrics.fallback("knowledge", "index_unavailable");
            log.warn("Semantic knowledge index unavailable; using local retrieval: {}", ex.getClass().getSimpleName());
        }
    }

    /** Null selects the local hash-vector branch; scores are never mixed across embedding models. */
    public Map<String, Double> scores(String query, int maxResults) { return scores(query, maxResults, state.revision()); }

    public Map<String, Double> scores(String query, int maxResults, long revision) {
        State snapshot = state;
        if (!snapshot.ready() || snapshot.revision() != revision) {
            if (required) throw new IllegalStateException("Chroma knowledge index is not ready");
            return null;
        }
        try {
            var retriever = EmbeddingStoreContentRetriever.builder().embeddingModel(model).embeddingStore(store)
                    .maxResults(maxResults).minScore(0.0).build();
            Map<String, Double> scores = new HashMap<>();
            for (var content : com.llmagentsystem.util.Tasks.within(java.time.Duration.ofSeconds(5), () -> retriever.retrieve(Query.from(query)))) {
                String id = (String) content.metadata().get(ContentMetadata.EMBEDDING_ID);
                String hash = content.textSegment().metadata().getString("llmagentsystem_content_hash");
                if (hash != null && hash.equals(snapshot.indexed().get(id))) {
                    double score = ((Number) content.metadata().get(ContentMetadata.SCORE)).doubleValue();
                    if (Double.isFinite(score)) scores.put(id, Math.max(0, score));
                }
            }
            return state == snapshot ? scores : null;
        } catch (Exception ex) {
            if (required) throw new IllegalStateException("Chroma knowledge query unavailable", ex);
            metrics.fallback("knowledge", "query_unavailable");
            log.warn("Semantic knowledge query failed; using local retrieval: {}", ex.getClass().getSimpleName());
            return null;
        }
    }
}
