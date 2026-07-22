package com.kinderuber.storage;

import com.kinderuber.util.AtomicJson;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.data.embedding.Embedding;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.store.embedding.*;
import dev.langchain4j.store.embedding.filter.Filter;
import dev.langchain4j.store.embedding.inmemory.InMemoryEmbeddingStore;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;

/** Development store: persist a complete new snapshot before publishing it to readers. */
public final class PersistentEmbeddingStore implements EmbeddingStore<TextSegment> {
    private final Path path;
    private final ObjectMapper mapper = new ObjectMapper();
    private volatile InMemoryEmbeddingStore<TextSegment> delegate;
    public PersistentEmbeddingStore(Path path) {
        this.path = path;
        try { delegate = Files.exists(path) ? InMemoryEmbeddingStore.fromJson(Files.readString(path)) : new InMemoryEmbeddingStore<>(); }
        catch (Exception ex) { throw new IllegalStateException("Cannot read semantic store " + path, ex); }
    }
    private synchronized void mutate(Consumer<InMemoryEmbeddingStore<TextSegment>> action) {
        var next = InMemoryEmbeddingStore.fromJson(delegate.serializeToJson());
        action.accept(next);
        try { AtomicJson.write(mapper, path, mapper.readTree(next.serializeToJson())); }
        catch (Exception ex) { throw new IllegalStateException("Cannot persist semantic store " + path, ex); }
        delegate = next;
    }
    public String add(Embedding embedding) { String id = UUID.randomUUID().toString(); add(id, embedding); return id; }
    public void add(String id, Embedding embedding) { mutate(s -> s.add(id, embedding)); }
    public String add(Embedding embedding, TextSegment segment) { String id = UUID.randomUUID().toString(); addAll(List.of(id), List.of(embedding), List.of(segment)); return id; }
    public List<String> addAll(List<Embedding> embeddings) { var ids = generateIds(embeddings.size()); mutate(s -> s.addAll(ids, embeddings, null)); return ids; }
    public void addAll(List<String> ids, List<Embedding> embeddings, List<TextSegment> segments) { mutate(s -> s.addAll(ids, embeddings, segments)); }
    public void removeAll(Collection<String> ids) { mutate(s -> s.removeAll(ids)); }
    public void removeAll(Filter filter) { mutate(s -> s.removeAll(filter)); }
    public void removeAll() { mutate(InMemoryEmbeddingStore::removeAll); }
    public EmbeddingSearchResult<TextSegment> search(EmbeddingSearchRequest request) { return delegate.search(request); }
}
