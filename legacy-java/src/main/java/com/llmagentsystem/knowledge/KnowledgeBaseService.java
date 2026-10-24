package com.llmagentsystem.knowledge;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import jakarta.annotation.PostConstruct;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

@Service
public class KnowledgeBaseService {

    private static final Logger log = LoggerFactory.getLogger(KnowledgeBaseService.class);

    @org.springframework.beans.factory.annotation.Value("${KNOWLEDGE_SEED_DEMO:true}") private boolean seedDemo = true;
    @org.springframework.beans.factory.annotation.Value("${LLM_AGENT_SYSTEM_ENV:development}") private String environment = "development";
    private final LlmAgentSystemProperties properties;
    private final SemanticKnowledgeIndex semanticIndex;
    private final ObjectMapper objectMapper;
    private record IndexSnapshot(long revision, List<KnowledgeDocument> documents,
            Map<String, Map<String, Integer>> frequencies, Map<String, Integer> lengths,
            Map<String, Integer> documentFrequencies, double averageLength) {}
    private volatile IndexSnapshot index = new IndexSnapshot(0, List.of(), Map.of(), Map.of(), Map.of(), 1);
    private final TextChunker splitter = new TextChunker(500, 80);

    public KnowledgeBaseService(LlmAgentSystemProperties properties, SemanticKnowledgeIndex semanticIndex, ObjectMapper objectMapper) {
        this.properties = properties;
        this.semanticIndex = semanticIndex;
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void init() {
        loadPersistedDocuments();
        if (index.documents().isEmpty() && seedDemo && !environment.equalsIgnoreCase("production")) {
            addDocuments(defaultDocuments());
        }
    }

    public synchronized int addDocuments(List<Map<String, String>> inputDocs) {
        Map<String, List<KnowledgeDocument>> prepared = new LinkedHashMap<>();
        Map<String, String> legacyTitles = new HashMap<>();
        for (Map<String, String> input : inputDocs) {
            String title = input.get("title"), content = input.get("content"), explicit = input.get("document_id");
            if (title == null || title.isBlank() || content == null || content.isBlank())
                throw new IllegalArgumentException("文档标题和内容不能为空");
            if (explicit != null && explicit.isBlank()) throw new IllegalArgumentException("document_id 不能为空");
            String identity = com.llmagentsystem.util.Hashing.key(explicit == null ? "title" : "explicit", explicit == null ? title : explicit);
            String version = com.llmagentsystem.util.Hashing.sha256(content);
            List<String> chunks = splitter.split(content);
            List<KnowledgeDocument> replacement = new ArrayList<>();
            for (int i = 0; i < chunks.size(); i++) {
                Map<String, Object> metadata = new HashMap<>();
                metadata.put("title", title);
                metadata.put("document_id", identity);
                metadata.put("version", version);
                metadata.put("chunk_index", i);
                metadata.put("total_chunks", chunks.size());
                replacement.add(new KnowledgeDocument(identity + ":" + i, title, chunks.get(i), i, metadata, embed(chunks.get(i))));
            }
            prepared.put(identity, replacement);
            if (explicit == null) legacyTitles.put(identity, title);
        }
        List<KnowledgeDocument> next = new ArrayList<>(index.documents());
        prepared.forEach((identity, replacement) -> {
            next.removeIf(existing -> identity.equals(existing.metadata().get("document_id"))
                    || (!existing.metadata().containsKey("document_id") && existing.title().equals(legacyTitles.get(identity))));
            next.addAll(replacement);
        });
        // Persist before publishing the new snapshot: failed imports cannot report success.
        persistDocuments(next);
        IndexSnapshot nextIndex = buildIndex(index.revision() + 1, next);
        index = nextIndex;
        semanticIndex.refresh(nextIndex.documents(), nextIndex.revision());
        return prepared.values().stream().mapToInt(List::size).sum();
    }

    public long revision() { return index.revision(); }

    public List<SearchResult> search(String query, int topK) {
        if (topK < 1 || topK > 20) throw new IllegalArgumentException("top_k 必须在 1 到 20 之间");
        if (query == null || query.isBlank()) {
            return List.of();
        }
        IndexSnapshot snapshot = index;
        Map<String, Double> bm25 = bm25Scores(query, snapshot);
        Map<String, Double> semantic = semanticIndex.scores(query, 20, snapshot.revision());
        Map<String, Double> vector = semantic == null ? vectorScores(query, snapshot.documents()) : normalize(semantic);
        Map<String, Double> fused = new HashMap<>();
        snapshot.documents().forEach(doc -> {
            double score = properties.getRag().getBm25Weight() * bm25.getOrDefault(doc.id(), 0.0)
                    + properties.getRag().getVectorWeight() * vector.getOrDefault(doc.id(), 0.0);
            fused.put(doc.id(), score);
        });
        return snapshot.documents().stream()
                .sorted(Comparator.comparingDouble((KnowledgeDocument d) -> fused.getOrDefault(d.id(), 0.0)).reversed())
                .limit(topK)
                .map(doc -> new SearchResult(
                        doc.id(),
                        doc.title(),
                        doc.content(),
                        round(fused.getOrDefault(doc.id(), 0.0)),
                        doc.chunkIndex(),
                        doc.metadata()
                ))
                .filter(result -> result.score() > 0.0)
                .toList();
    }

    public int docCount() {
        return index.documents().size();
    }

    private void loadPersistedDocuments() {
        Path path = Path.of(properties.getStorage().getKnowledgePath());
        if (!Files.exists(path)) {
            return;
        }
        try {
            List<StoredKnowledgeDocument> stored = objectMapper.readValue(path.toFile(), new TypeReference<>() {
            });
            List<KnowledgeDocument> loaded = new ArrayList<>();
            for (StoredKnowledgeDocument item : stored) {
                if (item.content() == null || item.content().isBlank()) {
                    continue;
                }
                KnowledgeDocument doc = new KnowledgeDocument(
                        item.id() == null || item.id().isBlank() ? md5(item.title() + item.chunkIndex() + item.content()) : item.id(),
                        item.title() == null || item.title().isBlank() ? "未命名文档" : item.title(),
                        item.content(),
                        item.chunkIndex(),
                        item.metadata() == null ? Map.of() : item.metadata(),
                        embed(item.content())
                );
                loaded.removeIf(existing -> existing.id().equals(doc.id()));
                loaded.add(doc);
            }
            index = buildIndex(index.revision() + 1, loaded);
            semanticIndex.refresh(index.documents(), index.revision());
            log.info("Loaded {} persisted knowledge chunks from {}", loaded.size(), path);
        } catch (Exception ex) {
            throw new IllegalStateException("知识库文件损坏或无法读取，请先恢复数据文件：" + path, ex);
        }
    }

    private void persistDocuments(List<KnowledgeDocument> snapshot) {
        Path path = Path.of(properties.getStorage().getKnowledgePath());
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            List<StoredKnowledgeDocument> stored = snapshot.stream()
                    .map(doc -> new StoredKnowledgeDocument(doc.id(), doc.title(), doc.content(), doc.chunkIndex(), doc.metadata()))
                    .toList();
            com.llmagentsystem.util.AtomicJson.write(objectMapper, path, stored);
        } catch (Exception ex) {
            throw new IllegalStateException("知识库持久化失败", ex);
        }
    }

    private IndexSnapshot buildIndex(long revision, List<KnowledgeDocument> documents) {
        Map<String, Map<String, Integer>> frequencies = new HashMap<>();
        Map<String, Integer> lengths = new HashMap<>(), documentFrequencies = new HashMap<>();
        for (KnowledgeDocument doc : documents) {
            List<String> terms = tokenize(doc.content());
            Map<String, Integer> counts = new HashMap<>();
            terms.forEach(term -> counts.merge(term, 1, Integer::sum));
            frequencies.put(doc.id(), Map.copyOf(counts)); lengths.put(doc.id(), terms.size());
            counts.keySet().forEach(term -> documentFrequencies.merge(term, 1, Integer::sum));
        }
        double average = lengths.values().stream().mapToInt(Integer::intValue).average().orElse(1);
        return new IndexSnapshot(revision, List.copyOf(documents), Map.copyOf(frequencies), Map.copyOf(lengths),
                Map.copyOf(documentFrequencies), Math.max(1, average));
    }

    private Map<String, Double> bm25Scores(String query, IndexSnapshot snapshot) {
        List<String> queryTerms = tokenize(query);
        Map<String, Double> scores = new HashMap<>();
        for (KnowledgeDocument doc : snapshot.documents()) {
            var terms = snapshot.frequencies().get(doc.id());
            double score = 0;
            for (String term : queryTerms) {
                int tf = terms.getOrDefault(term, 0);
                if (tf == 0) continue;
                int df = snapshot.documentFrequencies().getOrDefault(term, 0);
                double idf = Math.log(1 + (snapshot.documents().size() - df + 0.5) / (df + 0.5));
                double denominator = tf + 1.5 * (1 - 0.75 + 0.75 * snapshot.lengths().get(doc.id()) / snapshot.averageLength());
                score += idf * (tf * 2.5) / denominator;
            }
            scores.put(doc.id(), score);
        }
        return normalize(scores);
    }

    private Map<String, Double> vectorScores(String query, List<KnowledgeDocument> documents) {
        double[] queryVec = embed(query);
        Map<String, Double> scores = new HashMap<>();
        for (KnowledgeDocument doc : documents) scores.put(doc.id(), Math.max(0.0, cosine(queryVec, doc.embedding())));
        return normalize(scores);
    }

    private Map<String, Double> normalize(Map<String, Double> scores) {
        double max = scores.values().stream().mapToDouble(Double::doubleValue).max().orElse(0.0);
        if (max <= 0.0) {
            return scores;
        }
        Map<String, Double> normalized = new HashMap<>();
        scores.forEach((id, score) -> normalized.put(id, score / max));
        return normalized;
    }

    private List<String> tokenize(String text) {
        String normalized = text == null ? "" : text.toLowerCase(Locale.ROOT).replaceAll("[\\p{Punct}\\s]+", " ");
        List<String> tokens = new ArrayList<>();
        for (String part : normalized.split("\\s+")) {
            if (!part.isBlank()) {
                tokens.add(part);
            }
        }
        for (int n = 2; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                String gram = normalized.substring(i, i + n).trim();
                if (!gram.isBlank()) {
                    tokens.add(gram);
                }
            }
        }
        return tokens;
    }

    private double[] embed(String text) {
        double[] vector = new double[256];
        Set<String> grams = new HashSet<>(tokenize(text));
        for (String gram : grams) {
            int hash = gram.hashCode();
            int idx = Math.floorMod(hash, vector.length);
            vector[idx] += (hash & 1) == 0 ? 1.0 : -1.0;
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

    private String md5(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("MD5").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception ex) {
            return Integer.toHexString(value.hashCode());
        }
    }

    private double round(double value) {
        return Math.round(value * 10000.0) / 10000.0;
    }

    private List<Map<String, String>> defaultDocuments() {
        List<Map<String, String>> docs = new ArrayList<>();
        docs.add(doc("退款政策", "用户在购买后 7 天内可以申请无理由退款。退款申请提交后，系统会在 1-3 个工作日内审核。审核通过后，款项将在 5-7 个工作日内退回原支付账户。商品已发货时，需要先完成退货流程。"));
        docs.add(doc("订单查询", "用户可以通过订单号查询订单状态。订单状态包括待支付、已支付、已发货、运输中、已签收、已完成。物流信息通常在发货后 24 小时内更新。"));
        docs.add(doc("账户安全", "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。如果忘记密码，可以通过绑定手机号或邮箱重置。发现异常登录时系统会锁定账户。"));
        docs.add(doc("技术故障排查", "应用崩溃请尝试清除缓存后重启应用。登录失败 401 表示认证失败。500 服务器错误通常是服务端问题，请稍后重试或联系技术支持。"));
        docs.add(doc("会员与积分", "每消费 1 元累积 1 积分。100 积分可抵扣 1 元。会员等级包括普通会员、银卡会员和金卡会员，积分有效期为 1 年。"));
        docs.add(doc("配送说明", "标准配送 3-5 个工作日送达，订单满 99 元免运费。加急配送 1-2 个工作日送达。同城配送可当日达或次日达。"));
        return docs;
    }

    private Map<String, String> doc(String title, String content) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("title", title);
        map.put("content", content);
        return map;
    }

    private record StoredKnowledgeDocument(
            String id,
            String title,
            String content,
            int chunkIndex,
            Map<String, Object> metadata
    ) {
    }
}
