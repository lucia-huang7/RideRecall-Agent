package com.kinderuber.monitor;

import com.kinderuber.agent.AgentOrchestrator;
import com.kinderuber.config.KinderUberProperties;
import com.kinderuber.tool.*;
import io.micrometer.core.instrument.*;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import java.time.*;
import java.util.*;

@Service
public class PerformanceMonitor {
    private final AgentOrchestrator orchestrator;
    private final KnowledgeToolManager knowledge;
    private final ToolRegistry tools;
    private final KinderUberProperties properties;
    private final Map<String, Map<String, Object>> active = new LinkedHashMap<>();
    private final Deque<Map<String, Object>> history = new ArrayDeque<>();
    private final Map<String, Deque<Double>> latencyHistory = new HashMap<>();
    private final Map<String, Long> lastTotals = new HashMap<>();
    private final RestClient http;
    private volatile double agentSuccessRate = 1, toolSuccessRate = 1;
    public PerformanceMonitor(AgentOrchestrator orchestrator, KnowledgeToolManager knowledge, ToolRegistry tools, KinderUberProperties properties, MeterRegistry metrics) {
        this.orchestrator = orchestrator; this.knowledge = knowledge; this.tools = tools; this.properties = properties;
        Gauge.builder("kinderuber_agent_success_rate", () -> agentSuccessRate).register(metrics);
        Gauge.builder("kinderuber_tool_success_rate", () -> toolSuccessRate).register(metrics);
        var factory = new org.springframework.http.client.JdkClientHttpRequestFactory(java.net.http.HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build());
        factory.setReadTimeout(Duration.ofSeconds(3)); http = RestClient.builder().requestFactory(factory).build();
    }
    @Scheduled(initialDelay = 10000, fixedDelay = 10000)
    public synchronized void collect() {
        Map<String, Double> penalties = new HashMap<>();
        Set<String> observed = new HashSet<>();
        agentSuccessRate = 1;
        orchestrator.stats().forEach((name, data) -> {
            Map<?, ?> stat = (Map<?, ?>) data;
            double rate = number(stat.get("success_rate"), 1), latency = number(stat.get("avg_ms"), 0);
            agentSuccessRate = Math.min(agentSuccessRate, rate);
            if (number(stat.get("window_samples"), 0) < 5) { penalties.put(name, 0.0); return; }
            double penalty = Math.min(.9, Math.max(0, .9 - rate) * 2 + Math.min(.4, Math.max(0, latency - 3000) / 10000));
            penalties.put(name, penalty);
            if (rate < properties.getMonitor().getSuccessRateThreshold()) alert("agent_success_rate:" + name, rate, properties.getMonitor().getSuccessRateThreshold(), observed);
            if (latency > properties.getMonitor().getLatencyMsThreshold()) alert("agent_avg_ms:" + name, latency, properties.getMonitor().getLatencyMsThreshold(), observed);
            long total = ((Number) stat.get("total")).longValue();
            if (lastTotals.getOrDefault(name, -1L) != total) {
                Deque<Double> prior = latencyHistory.computeIfAbsent(name, ignored -> new ArrayDeque<>());
                if (zScore(prior, latency) > 3) alert("agent_latency_zscore:" + name, latency, 3, observed);
                prior.addLast(latency); if (prior.size() > 100) prior.removeFirst();
                lastTotals.put(name, total);
            } else if (active.containsKey("agent_latency_zscore:" + name)) observed.add("agent_latency_zscore:" + name);
        });
        toolSuccessRate = tools.stats().values().stream().map(v -> (Map<?, ?>) v).mapToDouble(v -> number(v.get("success_rate"), 1)).min().orElse(1);
        for (String key : new ArrayList<>(active.keySet())) if (!observed.contains(key)) {
            Map<String, Object> resolved = new LinkedHashMap<>(active.remove(key)); resolved.put("resolved", true); resolved.put("resolved_at", Instant.now().toString());
            history.addLast(resolved); if (history.size() > 100) history.removeFirst(); webhook(resolved);
        }
        orchestrator.updateRoutingPenalties(penalties);
    }
    public static double zScore(Collection<Double> baseline, double value) {
        if (baseline.size() < 10) return 0;
        double mean = baseline.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        double deviation = Math.sqrt(baseline.stream().mapToDouble(x -> (x - mean) * (x - mean)).average().orElse(0));
        return deviation < 1e-9 ? 0 : Math.abs(value - mean) / deviation;
    }
    private void alert(String key, double value, double threshold, Set<String> observed) {
        observed.add(key);
        if (!active.containsKey(key)) {
            Map<String, Object> alert = Map.of("metric", key, "value", value, "threshold", threshold, "resolved", false, "created_at", Instant.now().toString());
            active.put(key, alert); webhook(alert);
        }
    }
    private void webhook(Map<String, Object> alert) {
        String url = properties.getMonitor().getWebhookUrl();
        if (url == null || url.isBlank()) return;
        try { http.post().uri(url).body(alert).retrieve().toBodilessEntity(); } catch (Exception ignored) {}
    }
    public synchronized Map<String, Object> summary() {
        return Map.of("agent_stats", orchestrator.stats(), "tool_stats", tools.stats(), "knowledge_stats", knowledge.stats(),
                "active_alerts", List.copyOf(active.values()), "resolved_alerts", List.copyOf(history),
                "suggestions", active.isEmpty() ? List.of() : List.of(Map.of("title", "检测到运行异常", "action", "检查低成功率、高延迟 Agent 和外部工具连接。", "priority", 7)));
    }
    private double number(Object value, double fallback) { return value instanceof Number n ? n.doubleValue() : fallback; }
}
