package com.llmagentsystem.storage;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.llmagentsystem.memory.SemanticMemoryStore;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.document.Metadata;
import dev.langchain4j.data.embedding.Embedding;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.store.embedding.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;
import java.nio.file.Path;
import java.time.Instant;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class SemanticStorageTest {
    @TempDir Path dir;
    @Test void bundledModelActuallyEmbedsAndLocalStoreSurvivesRestart() {
        var env=new MockEnvironment().withProperty("SEMANTIC_STORAGE_MODE","local").withProperty("SEMANTIC_LOCAL_DIR",dir.toString());
        var storage=new SemanticStorage(env,new LlmAgentSystemProperties());
        var refund=storage.model().embed("How can I get my purchase refunded?").content();
        assertEquals(384,refund.dimension());
        storage.knowledge().add(refund,TextSegment.from("Return and refund policy"));
        storage.knowledge().add(storage.model().embed("Tomorrow is sunny and warm.").content(),TextSegment.from("Weather forecast"));
        var reopened=new PersistentEmbeddingStore(dir.resolve("llmagentsystem_java_minilm_v1_knowledge.json"));
        var results=reopened.search(EmbeddingSearchRequest.builder().queryEmbedding(storage.model().embed("I want my money back").content()).maxResults(1).build());
        assertEquals("Return and refund policy",results.matches().getFirst().embedded().text());
        var memory=new SemanticMemoryStore(storage,new ObjectMapper(),null);
        memory.remember("alice","current","My payment failed",Instant.parse("2026-01-01T00:00:00Z"));
        memory.remember("alice","other","My refund request",Instant.parse("2026-01-02T00:00:00Z"));
        memory.remember("bob","current","private bob refund",Instant.now());
        assertEquals("My payment failed",memory.recall("alice","current","refund").getFirst());
        assertFalse(memory.recall("alice","current","refund").contains("private bob refund"));
        memory.updateProfile("alice",p->Map.of("preferences",List.of("中文")));
        memory.updateProfile("alice",p->{assertEquals(List.of("中文"),p.get("preferences"));return Map.of("preferences",List.of("中文","简洁"));});
        var restored=new SemanticStorage(storage.model(),storage.knowledge(),storage.episodic(),new PersistentEmbeddingStore(dir.resolve("llmagentsystem_java_minilm_v1_profiles.json")));
        assertEquals(List.of("中文","简洁"),new SemanticMemoryStore(restored,new ObjectMapper(),null).profile("alice").get("preferences"));
        assertTrue(memory.profile("bob").isEmpty());
    }
    @Test void productionRejectsLocalAndDisabledStorage() {
        for(String mode:List.of("local","disabled")) assertThrows(IllegalArgumentException.class,()->new SemanticStorage(new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_ENV","production").withProperty("SEMANTIC_STORAGE_MODE",mode),new LlmAgentSystemProperties()));
    }
    @Test @org.junit.jupiter.api.condition.EnabledIfSystemProperty(named="test.chroma.url",matches="https?://.*")
    void officialChromaAdapterPersistsAndFiltersActualRemoteCollections() {
        String prefix="llmagentsystem_qa_"+UUID.randomUUID().toString().replace("-","").substring(0,12);
        var env=new MockEnvironment().withProperty("SEMANTIC_STORAGE_MODE","chroma").withProperty("CHROMA_BASE_URL",System.getProperty("test.chroma.url")).withProperty("CHROMA_COLLECTION_PREFIX",prefix);
        var storage=new SemanticStorage(env,new LlmAgentSystemProperties());
        try {
            var vector=storage.model().embed("Refund policy").content();storage.knowledge().add(vector,TextSegment.from("Remote refund policy"));
            var reopened=new SemanticStorage(env,new LlmAgentSystemProperties());
            assertEquals("Remote refund policy",reopened.knowledge().search(EmbeddingSearchRequest.builder().queryEmbedding(vector).maxResults(1).build()).matches().getFirst().embedded().text());
            var memory=new SemanticMemoryStore(storage,new ObjectMapper(),null);
            memory.remember("alice","current","Alice current payment",Instant.now());memory.remember("alice","other","Alice refund history",Instant.now());memory.remember("bob","current","Private bob history",Instant.now());
            assertEquals("Alice current payment",memory.recall("alice","current","refund").getFirst());assertFalse(memory.recall("alice","current","refund").contains("Private bob history"));
            memory.updateProfile("alice",p->Map.of("preferences",List.of("中文")));
            memory.updateProfile("alice",p->Map.of("preferences",List.of("简洁")));
            assertEquals(List.of("简洁"),new SemanticMemoryStore(reopened,new ObjectMapper(),null).profile("alice").get("preferences"));assertTrue(memory.profile("bob").isEmpty());
        } finally { storage.knowledge().removeAll();storage.episodic().removeAll();storage.profiles().removeAll(); }
    }

}
