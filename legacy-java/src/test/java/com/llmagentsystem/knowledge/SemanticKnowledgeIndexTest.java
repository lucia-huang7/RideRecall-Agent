package com.llmagentsystem.knowledge;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.embedding.Embedding;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.model.embedding.EmbeddingModel;
import dev.langchain4j.model.output.Response;
import dev.langchain4j.store.embedding.EmbeddingStore;
import dev.langchain4j.store.embedding.inmemory.InMemoryEmbeddingStore;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.support.DefaultListableBeanFactory;
import org.springframework.core.ResolvableType;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.jupiter.api.Assertions.*;

class SemanticKnowledgeIndexTest {
    @TempDir Path directory;
    private SemanticKnowledgeIndex index(EmbeddingModel model, EmbeddingStore<TextSegment> store) {
        var beans = new DefaultListableBeanFactory();
        if (model != null) beans.registerSingleton("embeddingModel", model);
        if (store != null) {
            var definition = new org.springframework.beans.factory.support.RootBeanDefinition();
            definition.setTargetType(ResolvableType.forClassWithGenerics(EmbeddingStore.class, TextSegment.class));
            definition.setInstanceSupplier(() -> store); beans.registerBeanDefinition("embeddingStore", definition);
        }
        return new SemanticKnowledgeIndex(beans.getBeanProvider(EmbeddingModel.class),
                beans.getBeanProvider(ResolvableType.forClassWithGenerics(EmbeddingStore.class, TextSegment.class)));
    }

    @Test void slowQueryDoesNotBlockAnotherQueryOrDocumentReplacement() throws Exception {
        var entered = new java.util.concurrent.CountDownLatch(1); var release = new java.util.concurrent.CountDownLatch(1);
        EmbeddingModel model = segments -> {
            if (segments.getFirst().text().contains("first")) {
                entered.countDown();
                try { if (!release.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new IllegalStateException("fixture_timeout"); }
                catch (InterruptedException ex) { Thread.currentThread().interrupt(); throw new IllegalStateException(ex); }
            }
            return Response.from(segments.stream().map(s -> Embedding.from(new float[]{1, 0})).toList());
        };
        var properties = new LlmAgentSystemProperties(); properties.getStorage().setKnowledgePath(directory.resolve("parallel.json").toString());
        var service = new KnowledgeBaseService(properties, index(model, new InMemoryEmbeddingStore<>()), new ObjectMapper());
        service.addDocuments(List.of(Map.of("title", "退款", "content", "退款旧政策")));
        try (var executor = java.util.concurrent.Executors.newVirtualThreadPerTaskExecutor()) {
            var first = executor.submit(() -> service.search("退款first", 1));
            assertTrue(entered.await(3, java.util.concurrent.TimeUnit.SECONDS));
            try {
                var second = executor.submit(() -> service.search("退款second", 1));
                assertEquals("退款旧政策", second.get(3, java.util.concurrent.TimeUnit.SECONDS).getFirst().content());
                var update = executor.submit(() -> service.addDocuments(List.of(Map.of("title", "退款", "content", "退款新政策"))));
                assertEquals(1, update.get(3, java.util.concurrent.TimeUnit.SECONDS));
            } finally { release.countDown(); }
            assertEquals("退款旧政策", first.get(3, java.util.concurrent.TimeUnit.SECONDS).getFirst().content());
            assertEquals("退款新政策", service.search("退款", 1).getFirst().content());
        }
    }

    @Test void modelAndStoreMustBeConfiguredTogether() {
        assertThrows(IllegalArgumentException.class, () -> index(null, new InMemoryEmbeddingStore<>()));
        assertNotNull(index(null, null));
    }

    @Test void semanticRecallActuallyAffectsSearchAndReimportDeletesStaleVectors() {
        var store = new InMemoryEmbeddingStore<TextSegment>();
        // Deliberately no lexical overlap between query and policy, so a semantic match is required.
        EmbeddingModel model = segments -> Response.from(segments.stream()
                .map(s -> Embedding.from(s.text().contains("退款") || s.text().contains("资金返还") ? new float[]{1, 0} : new float[]{0, 1})).toList());
        var index = index(model, store);
        var properties = new LlmAgentSystemProperties(); properties.getStorage().setKnowledgePath(directory.resolve("kb.json").toString());
        properties.getRag().setBm25Weight(0); properties.getRag().setVectorWeight(1);
        var service = new KnowledgeBaseService(properties, index, new ObjectMapper());
        service.addDocuments(List.of(Map.of("title", "规则", "content", "资金返还需审核。".repeat(200))));
        assertTrue(store.size() > 1);
        assertTrue(service.search("退款", 1).getFirst().content().contains("资金返还"));
        service.addDocuments(List.of(Map.of("title", "规则", "content", "资金返还需审核。")));
        assertEquals(1, store.size()); assertEquals(1, index.scores("退款", 20).size());
    }

    @Test void failedIndexOrQueryFallsBackWithoutWritingHashVectorsIntoSemanticStore() {
        var failing = new AtomicBoolean(false);
        EmbeddingModel model = segments -> {
            if (failing.get()) throw new IllegalStateException("fixture_unavailable");
            return Response.from(segments.stream().map(s -> Embedding.from(new float[]{1, 0})).toList());
        };
        var store = new InMemoryEmbeddingStore<TextSegment>(); var index = index(model, store);
        var properties = new LlmAgentSystemProperties(); properties.getStorage().setKnowledgePath(directory.resolve("kb.json").toString());
        var service = new KnowledgeBaseService(properties, index, new ObjectMapper());
        service.addDocuments(List.of(Map.of("title", "退款", "content", "退款需审核")));
        failing.set(true);
        assertNull(index.scores("退款", 20)); assertFalse(service.search("退款", 1).isEmpty());
        service.addDocuments(List.of(Map.of("title", "退款", "content", "退款新政策")));
        assertNull(index.scores("退款", 20)); assertEquals("退款新政策", service.search("退款", 1).getFirst().content());
        assertEquals(1, store.size());
    }
}
