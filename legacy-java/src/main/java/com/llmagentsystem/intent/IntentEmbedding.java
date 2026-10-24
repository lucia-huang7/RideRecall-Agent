package com.llmagentsystem.intent;

import com.llmagentsystem.util.*;
import com.fasterxml.jackson.databind.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import java.net.URI;
import java.net.http.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.locks.ReentrantLock;

@Service
public class IntentEmbedding {
    public static final String MODEL = "BAAI/bge-m3";
    private final String provider, key;
    private final ObjectMapper mapper;
    private final HttpClient client;
    private final URI endpoint;
    private final ReentrantLock lock = new ReentrantLock();
    private volatile Map<IntentCategory, List<double[]>> templates;
    private volatile String fingerprint = "", error = "";
    private volatile Instant retryAt = Instant.MIN;
    public record Match(IntentCategory intent, double confidence, String source) {}
    @org.springframework.beans.factory.annotation.Autowired
    public IntentEmbedding(@Value("${INTENT_EMBEDDING_PROVIDER:auto}") String provider,
                           @Value("${SILICONFLOW_API_KEY:}") String key, ObjectMapper mapper) {
        this(provider, key, mapper, URI.create("https://api.siliconflow.cn/v1/embeddings"));
    }
    IntentEmbedding(String provider, String key, ObjectMapper mapper, URI endpoint) {
        if (!Set.of("local", "auto", "siliconflow").contains(provider)) throw new IllegalArgumentException("Invalid INTENT_EMBEDDING_PROVIDER");
        this.provider = provider; this.key = key.strip(); this.mapper = mapper; this.endpoint = endpoint;
        this.client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build();
    }
    private boolean configured() { return !provider.equals("local") && !key.isBlank(); }
    public Map<String, Object> status() {
        return Map.of("configured_provider", provider, "online_configured", configured(), "model", configured() ? MODEL : "local-ngram-256", "cooldown", Instant.now().isBefore(retryAt), "last_error", error);
    }
    public Match match(String message, Map<IntentCategory, List<String>> input) {
        Map<IntentCategory, List<String>> snapshot = new TreeMap<>(input);
        if (configured() && !Instant.now().isBefore(retryAt)) {
            try { return Tasks.within(Duration.ofSeconds(8), () -> online(message, snapshot)); }
            catch (Exception ex) { error = ex.getClass().getSimpleName(); retryAt = Instant.now().plusSeconds(60); }
        }
        Map<IntentCategory, List<double[]>> local = new EnumMap<>(IntentCategory.class);
        snapshot.forEach((category, texts) -> local.put(category, texts.stream().map(IntentEmbedding::localVector).toList()));
        return best(localVector(message), local, "local-ngram");
    }
    private Match online(String message, Map<IntentCategory, List<String>> snapshot) throws Exception {
        String hash = Hashing.sha256(mapper.writeValueAsString(snapshot));
        Map<IntentCategory, List<double[]>> groups;
        lock.lockInterruptibly();
        try {
            if (Instant.now().isBefore(retryAt)) throw new IllegalStateException("cooldown");
            if (templates == null || !fingerprint.equals(hash)) {
                List<String> texts = snapshot.values().stream().flatMap(List::stream).toList();
                List<double[]> vectors = new ArrayList<>();
                for (int i = 0; i < texts.size(); i += 32) vectors.addAll(request(texts.subList(i, Math.min(i + 32, texts.size()))));
                int dim = vectors.getFirst().length;
                if (vectors.stream().anyMatch(v -> v.length != dim)) throw new IllegalArgumentException("template_dimension_mismatch");
                Map<IntentCategory, List<double[]>> generated = new EnumMap<>(IntentCategory.class);
                int offset = 0;
                for (var entry : snapshot.entrySet()) { generated.put(entry.getKey(), List.copyOf(vectors.subList(offset, offset + entry.getValue().size()))); offset += entry.getValue().size(); }
                templates = Map.copyOf(generated); fingerprint = hash;
            }
            groups = templates;
        } finally { lock.unlock(); }
        double[] query = request(List.of(message)).getFirst();
        if (groups.values().stream().flatMap(List::stream).anyMatch(v -> v.length != query.length)) {
            templates = null; throw new IllegalArgumentException("query_dimension_mismatch");
        }
        error = "";
        return best(query, groups, "siliconflow/" + MODEL);
    }
    private List<double[]> request(List<String> texts) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(endpoint).timeout(Duration.ofSeconds(6))
                .header("Authorization", "Bearer " + key).header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(Map.of("model", MODEL, "input", texts, "encoding_format", "float")))).build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) throw new IllegalStateException("http_" + response.statusCode());
        return validate(mapper.readTree(response.body()).path("data"), texts.size());
    }
    static List<double[]> validate(JsonNode rows, int count) {
        if (!rows.isArray() || rows.size() != count) throw new IllegalArgumentException("invalid_embedding_count");
        double[][] result = new double[count][];
        int dimension = -1;
        for (JsonNode row : rows) {
            if (!row.path("index").isIntegralNumber()) throw new IllegalArgumentException("invalid_embedding_index");
            int index = row.path("index").asInt(-1);
            JsonNode vector = row.path("embedding");
            if (index < 0 || index >= count || result[index] != null || !vector.isArray() || vector.isEmpty()) throw new IllegalArgumentException("invalid_embedding_row");
            if (dimension < 0) dimension = vector.size();
            if (dimension != vector.size()) throw new IllegalArgumentException("invalid_embedding_dimension");
            double[] values = new double[dimension]; double norm = 0;
            for (int i = 0; i < dimension; i++) {
                if (!vector.get(i).isNumber() || !Double.isFinite(vector.get(i).asDouble())) throw new IllegalArgumentException("invalid_embedding_value");
                values[i] = vector.get(i).asDouble(); norm += values[i] * values[i];
            }
            if (!Double.isFinite(norm) || norm <= 0) throw new IllegalArgumentException("invalid_embedding_norm");
            norm = Math.sqrt(norm); for (int i = 0; i < dimension; i++) values[i] /= norm;
            result[index] = values;
        }
        return Arrays.asList(result);
    }
    private Match best(double[] query, Map<IntentCategory, List<double[]>> groups, String source) {
        IntentCategory best = IntentCategory.OTHER; double confidence = 0;
        for (var entry : groups.entrySet()) for (double[] vector : entry.getValue()) {
            double score = 0; for (int i = 0; i < query.length; i++) score += query[i] * vector[i];
            if (score > confidence) { confidence = score; best = entry.getKey(); }
        }
        return new Match(best, Math.min(1, confidence), source);
    }
    static double[] localVector(String text) {
        double[] vector = new double[256];
        String normalized = text == null ? "" : text.toLowerCase(Locale.ROOT).strip();
        for (int n = 1; n <= 3; n++) for (int i = 0; i + n <= normalized.length(); i++) {
            int hash = normalized.substring(i, i + n).hashCode(); vector[Math.floorMod(hash, vector.length)] += (hash & 1) == 0 ? 1 : -1;
        }
        double norm = Math.sqrt(Arrays.stream(vector).map(v -> v * v).sum());
        if (norm > 0) for (int i = 0; i < vector.length; i++) vector[i] /= norm;
        return vector;
    }
}
