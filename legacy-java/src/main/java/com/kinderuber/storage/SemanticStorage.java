package com.kinderuber.storage;

import com.kinderuber.config.KinderUberProperties;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.model.embedding.EmbeddingModel;
import dev.langchain4j.model.embedding.onnx.allminilml6v2.AllMiniLmL6V2EmbeddingModel;
import dev.langchain4j.store.embedding.EmbeddingStore;
import dev.langchain4j.store.embedding.chroma.*;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import java.nio.file.Path;
import java.time.Duration;
import java.util.*;

/** Bundled real embedding model and separate collections; no remote embedding credentials required. */
@Component
public class SemanticStorage {
    private final String mode;
    private final EmbeddingModel model;
    private final EmbeddingStore<TextSegment> knowledge, episodic, profiles;
    @org.springframework.beans.factory.annotation.Autowired
    public SemanticStorage(Environment env, KinderUberProperties properties) {
        boolean production = env.getProperty("KINDER_UBER_ENV", "development").equalsIgnoreCase("production");
        mode = env.getProperty("kinderuber.semantic.mode", env.getProperty("SEMANTIC_STORAGE_MODE", production ? "chroma" : "local")).toLowerCase(Locale.ROOT);
        if (!Set.of("local", "chroma", "disabled").contains(mode) || (production && !mode.equals("chroma")))
            throw new IllegalArgumentException("Semantic mode must be local/chroma/disabled; production requires chroma");
        if (mode.equals("disabled")) { model = null; knowledge = episodic = profiles = null; return; }
        model = new AllMiniLmL6V2EmbeddingModel();
        String prefix = env.getProperty("CHROMA_COLLECTION_PREFIX", "kinderuber_java_minilm_v1");
        if (!prefix.matches("[a-zA-Z0-9_]{3,45}")) throw new IllegalArgumentException("Invalid Chroma collection prefix");
        knowledge = store("knowledge", prefix, env, properties);
        episodic = store("episodic", prefix, env, properties);
        profiles = store("profiles", prefix, env, properties);
    }
    public SemanticStorage(EmbeddingModel model, EmbeddingStore<TextSegment> knowledge, EmbeddingStore<TextSegment> episodic, EmbeddingStore<TextSegment> profiles) {
        mode = "test"; this.model = model; this.knowledge = knowledge; this.episodic = episodic; this.profiles = profiles;
    }
    private EmbeddingStore<TextSegment> store(String suffix, String prefix, Environment env, KinderUberProperties properties) {
        if (mode.equals("local")) {
            Path root = Path.of(env.getProperty("SEMANTIC_LOCAL_DIR", properties.getStorage().getDataDir() + "/vectors"));
            return new PersistentEmbeddingStore(root.resolve(prefix + "_" + suffix + ".json"));
        }
        return ChromaEmbeddingStore.builder().baseUrl(env.getProperty("CHROMA_BASE_URL", "http://localhost:8002"))
                .apiVersion(ChromaApiVersion.valueOf(env.getProperty("CHROMA_API_VERSION", "V2")))
                .tenantName(env.getProperty("CHROMA_TENANT", "default_tenant"))
                .databaseName(env.getProperty("CHROMA_DATABASE", "default_database"))
                .collectionName(prefix + "_" + suffix).timeout(Duration.ofSeconds(5)).build();
    }
    public EmbeddingModel model() { return model; }
    public EmbeddingStore<TextSegment> knowledge() { return knowledge; }
    public EmbeddingStore<TextSegment> episodic() { return episodic; }
    public EmbeddingStore<TextSegment> profiles() { return profiles; }
    public boolean enabled() { return model != null; }
    public boolean required() { return mode.equals("chroma"); }
    public Map<String, Object> summary() { return Map.of("mode", mode, "enabled", enabled(), "model", enabled() ? "all-MiniLM-L6-v2" : "disabled", "dimension", enabled() ? 384 : 0); }
}
