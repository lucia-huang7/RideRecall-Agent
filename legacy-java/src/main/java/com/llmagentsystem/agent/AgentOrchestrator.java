package com.llmagentsystem.agent;

import com.llmagentsystem.intent.IntentCategory;
import com.llmagentsystem.intent.IntentRecognizer;
import com.llmagentsystem.intent.IntentResult;
import com.llmagentsystem.intent.UrgencyLevel;
import com.llmagentsystem.trace.RequestTraceStore;
import com.llmagentsystem.trace.RequestToolTrace;
import com.llmagentsystem.trace.ToolCallTrace;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;

@Service
public class AgentOrchestrator {

    private final IntentRecognizer intentRecognizer;
    private final ResponseComposer composer;
    private final Map<AgentType, List<BaseAgent>> pool;
    private final RequestTraceStore traceStore;
    private final Map<IntentCategory, AgentType> routing = new EnumMap<>(IntentCategory.class);

    public AgentOrchestrator(IntentRecognizer intentRecognizer, Map<AgentType, List<BaseAgent>> pool, RequestTraceStore traceStore) {
        this(intentRecognizer, pool, traceStore, (com.llmagentsystem.llm.LlmGateway) null);
    }
    public AgentOrchestrator(IntentRecognizer intentRecognizer, Map<AgentType, List<BaseAgent>> pool, RequestTraceStore traceStore, com.llmagentsystem.llm.LlmGateway llm) {
        this(intentRecognizer, pool, traceStore, new ResponseComposer(llm, null, AgentProfiles.defaults()));
    }
    @org.springframework.beans.factory.annotation.Autowired
    public AgentOrchestrator(IntentRecognizer intentRecognizer, Map<AgentType, List<BaseAgent>> pool, RequestTraceStore traceStore,
                             ResponseComposer composer) {
        this.composer = composer;
        this.intentRecognizer = intentRecognizer;
        this.pool = pool;
        this.traceStore = traceStore;
        routing.put(IntentCategory.TECHNICAL, AgentType.TECHNICAL);
        routing.put(IntentCategory.TECHNICAL_LOGIN, AgentType.TECHNICAL);
        routing.put(IntentCategory.TECHNICAL_CRASH, AgentType.TECHNICAL);
        routing.put(IntentCategory.BILLING, AgentType.BILLING);
        routing.put(IntentCategory.REFUND, AgentType.BILLING);
        routing.put(IntentCategory.INVOICE, AgentType.BILLING);
        routing.put(IntentCategory.PAYMENT_ISSUE, AgentType.BILLING);
        routing.put(IntentCategory.ACCOUNT, AgentType.BILLING);
        routing.put(IntentCategory.ACCOUNT_SECURITY, AgentType.BILLING);
        routing.put(IntentCategory.ESCALATION, AgentType.ESCALATION);
        routing.put(IntentCategory.HUMAN_HANDOFF, AgentType.ESCALATION);
    }

    public OrchestratorResult run(AgentRequest request) {
        return run(request, List.of());
    }

    public OrchestratorResult run(AgentRequest request, List<ToolCallTrace> externalToolCalls) {
        Instant start = Instant.now();
        AgentRequest req = request;
        if (req.intent() == null) {
            IntentResult intentResult = intentRecognizer.recognize(req.message(), req.history());
            req = req.withIntent(intentResult);
        }

        if (needsClarification(req)) {
            OrchestratorResult result = new OrchestratorResult(
                    req.requestId(),
                    "我还不能确定您要处理的是哪类问题。请补充一下是订单物流、退款账单、账户资料，还是技术故障？",
                    AgentType.GENERAL,
                    req.intent(),
                    false,
                    Duration.between(start, Instant.now()).toMillis(),
                    List.of(AgentType.GENERAL),
                    AgentType.GENERAL,
                    List.of(),
                    collectToolNames(externalToolCalls),
                    collectToolCalls(externalToolCalls),
                    "低置信度 OTHER 意图，先澄清用户需求",
                    req.intentConfidence()
            );
            recordTrace(req, result);
            return result;
        }

        RoutingDecision decision = routeDecision(req);
        if (decision.multiAgent()) {
            return runParallel(req, decision, externalToolCalls);
        }

        AgentResponse response = execute(req, decision.primaryAgent());
        boolean escalated = response.escalate()
                || req.urgency() == UrgencyLevel.CRITICAL
                || req.intent() == IntentCategory.ESCALATION
                || req.intent() == IntentCategory.HUMAN_HANDOFF;
        OrchestratorResult result = new OrchestratorResult(
                req.requestId(),
                response.content(),
                response.agentType(),
                req.intent(),
                escalated,
                Duration.between(start, Instant.now()).toMillis(),
                List.of(response.agentType()),
                decision.primaryAgent(),
                List.of(),
                collectToolNames(externalToolCalls, response),
                collectToolCalls(externalToolCalls, response),
                decision.reason(),
                decision.confidence(),
                response.success() ? (response.toolCalls().stream().anyMatch(t -> !t.success()) ? "partial" : "success") : "failed",
                !response.success(), response.success() ? null : "agent_unavailable"
        );
        recordTrace(req, result);
        return result;
    }

    public Optional<RequestToolTrace> getToolTrace(String requestId) {
        return traceStore.find(requestId);
    }

    public List<RequestToolTrace> getRecentToolTraces(int limit) {
        return traceStore.recent(limit);
    }

    private OrchestratorResult runParallel(AgentRequest req, RoutingDecision decision, List<ToolCallTrace> externalToolCalls) {
        Instant start = Instant.now();
        List<AgentType> targets = decision.agentTypes();
        List<AgentResponse> responses;
        try (var executor = java.util.concurrent.Executors.newVirtualThreadPerTaskExecutor()) {
            List<java.util.concurrent.Future<AgentResponse>> futures = targets.stream().map(type -> executor.submit(() -> execute(req, type))).toList();
            responses = new ArrayList<>();
            try { for (var future : futures) responses.add(future.get()); }
            catch (InterruptedException ex) {
                futures.forEach(f -> f.cancel(true)); Thread.currentThread().interrupt(); throw new IllegalStateException("interrupted", ex);
            } catch (java.util.concurrent.ExecutionException ex) { throw new IllegalStateException(ex.getCause()); }
        }
        String content = composer.compose(req, responses, decision.primaryAgent());
        boolean allFailed = responses.stream().noneMatch(AgentResponse::success);
        boolean escalate = responses.stream().anyMatch(AgentResponse::escalate);
        List<AgentType> agentTypes = responses.stream()
                .filter(AgentResponse::success)
                .map(AgentResponse::agentType)
                .toList();
        OrchestratorResult result = new OrchestratorResult(
                req.requestId(),
                content,
                decision.primaryAgent(),
                req.intent(),
                escalate,
                Duration.between(start, Instant.now()).toMillis(),
                agentTypes.isEmpty() ? targets : agentTypes,
                decision.primaryAgent(),
                decision.supportingAgents(),
                collectToolNames(externalToolCalls, responses),
                collectToolCalls(externalToolCalls, responses),
                decision.reason(),
                decision.confidence(),
                allFailed ? "failed" : (responses.stream().allMatch(AgentResponse::success) && responses.stream().flatMap(r -> r.toolCalls().stream()).allMatch(ToolCallTrace::success) ? "success" : "partial"),
                allFailed, allFailed ? "agents_unavailable" : null
        );
        recordTrace(req, result);
        return result;
    }

    private AgentType route(IntentCategory intent, UrgencyLevel urgency) {
        if (urgency == UrgencyLevel.CRITICAL) {
            return AgentType.ESCALATION;
        }
        AgentType target = routing.get(intent);
        if (target != null && pool.containsKey(target)) {
            return target;
        }
        return AgentType.GENERAL;
    }

    private RoutingDecision routeDecision(AgentRequest req) {
        if (req.urgency() == UrgencyLevel.CRITICAL) {
            return new RoutingDecision(AgentType.ESCALATION, List.of(), "紧急度为 CRITICAL，触发升级路由", 1.0);
        }
        if (req.intent() == IntentCategory.ESCALATION || req.intent() == IntentCategory.HUMAN_HANDOFF) {
            String intent = req.intent() == null ? "unknown" : req.intent().name().toLowerCase(Locale.ROOT);
            return new RoutingDecision(
                    AgentType.ESCALATION,
                    List.of(),
                    "意图为 " + intent + "，触发升级路由",
                    Math.max(req.intentConfidence(), 0.8)
            );
        }

        Map<AgentType, Double> scores = domainScores(req);
        Map<AgentType, Double> availableScores = new EnumMap<>(AgentType.class);
        scores.forEach((agentType, score) -> {
            if (agentType == AgentType.GENERAL || (pool.containsKey(agentType) && pool.get(agentType).stream().anyMatch(a -> a.stats().available()))) {
                availableScores.put(agentType, score);
            }
        });
        if (availableScores.isEmpty()) {
            return new RoutingDecision(AgentType.GENERAL, List.of(), "无可用专属 Agent，降级到 GeneralAgent", 0.1);
        }

        List<Map.Entry<AgentType, Double>> ordered = availableScores.entrySet().stream()
                .sorted(Map.Entry.<AgentType, Double>comparingByValue().reversed())
                .toList();
        AgentType primary = ordered.getFirst().getKey();
        double primaryScore = ordered.getFirst().getValue();
        List<AgentType> supportingAgents = collaborationTargets(req).stream().filter(t -> t != primary && availableScores.containsKey(t)).toList();
        if (supportingAgents.isEmpty()) supportingAgents = ordered.stream()
                .skip(1)
                .filter(entry -> entry.getKey() != AgentType.GENERAL)
                .filter(entry -> entry.getValue() >= 0.45 && entry.getValue() >= primaryScore * 0.55)
                .map(Map.Entry::getKey)
                .toList();
        return new RoutingDecision(
                primary,
                supportingAgents,
                routingReason(req, availableScores, primary, supportingAgents),
                round(Math.min(primaryScore, 1.0))
        );
    }

    private Map<AgentType, Double> domainScores(AgentRequest req) {
        String msg = req.message() == null ? "" : req.message().toLowerCase(Locale.ROOT);
        Map<AgentType, Double> scores = new EnumMap<>(AgentType.class);
        scores.put(AgentType.GENERAL, 0.1);
        scores.put(AgentType.TECHNICAL, 0.0);
        scores.put(AgentType.BILLING, 0.0);

        if (List.of(
                IntentCategory.QUERY,
                IntentCategory.ORDER_STATUS,
                IntentCategory.LOGISTICS,
                IntentCategory.REQUEST,
                IntentCategory.COMPLAINT,
                IntentCategory.GREETING,
                IntentCategory.FEEDBACK,
                IntentCategory.OTHER
        ).contains(req.intent())) {
            scores.merge(AgentType.GENERAL, 0.55, Double::sum);
        }

        if (List.of(
                IntentCategory.TECHNICAL,
                IntentCategory.TECHNICAL_LOGIN,
                IntentCategory.TECHNICAL_CRASH
        ).contains(req.intent())) {
            scores.merge(AgentType.TECHNICAL, 0.75, Double::sum);
        }

        if (List.of(
                IntentCategory.BILLING,
                IntentCategory.ACCOUNT,
                IntentCategory.ACCOUNT_SECURITY,
                IntentCategory.REFUND,
                IntentCategory.INVOICE,
                IntentCategory.PAYMENT_ISSUE
        ).contains(req.intent())) {
            scores.merge(AgentType.BILLING, 0.75, Double::sum);
        }

        long technicalHits = countHits(msg, "崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401", "验证码");
        long billingHits = countHits(msg, "退款", "退货", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice", "多扣");
        long generalHits = countHits(msg, "订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助");

        scores.merge(AgentType.TECHNICAL, Math.min(0.45, technicalHits * 0.18), Double::sum);
        scores.merge(AgentType.BILLING, Math.min(0.45, billingHits * 0.18), Double::sum);
        scores.merge(AgentType.GENERAL, Math.min(0.35, generalHits * 0.12), Double::sum);

        Map<String, List<String>> entities = req.entities() == null ? Map.of() : req.entities();
        if (!entities.getOrDefault("error_code", List.of()).isEmpty()) {
            scores.merge(AgentType.TECHNICAL, 0.2, Double::sum);
        }
        if (!entities.getOrDefault("amount", List.of()).isEmpty()) {
            scores.merge(AgentType.BILLING, 0.15, Double::sum);
        }
        if (!entities.getOrDefault("order_id", List.of()).isEmpty()) {
            scores.merge(AgentType.GENERAL, 0.1, Double::sum);
        }

        scores.replaceAll((agentType, score) -> round(score));
        return scores;
    }

    private String routingReason(
            AgentRequest req,
            Map<AgentType, Double> scores,
            AgentType primaryAgent,
            List<AgentType> supportingAgents
    ) {
        String scoreText = scores.entrySet().stream()
                .sorted(Map.Entry.<AgentType, Double>comparingByValue().reversed())
                .map(entry -> entry.getKey().name().toLowerCase(Locale.ROOT) + "=" + String.format(Locale.ROOT, "%.2f", entry.getValue()))
                .reduce((left, right) -> left + ", " + right)
                .orElse("");
        String supportText = supportingAgents.stream()
                .map(agent -> agent.name().toLowerCase(Locale.ROOT))
                .reduce((left, right) -> left + ", " + right)
                .orElse("none");
        String intent = req.intent() == null ? "unknown" : req.intent().name().toLowerCase(Locale.ROOT);
        return "intent=" + intent
                + ", group=" + (req.intentGroup() == null ? "unknown" : req.intentGroup())
                + ", primary=" + primaryAgent.name().toLowerCase(Locale.ROOT)
                + ", supporting=" + supportText
                + ", scores=[" + scoreText + "]";
    }

    private List<AgentType> collaborationTargets(AgentRequest request) {
        String msg = request.message() == null ? "" : request.message().toLowerCase(Locale.ROOT);
        LinkedHashSet<AgentType> targets = new LinkedHashSet<>();
        if (List.of(IntentCategory.TECHNICAL, IntentCategory.TECHNICAL_LOGIN, IntentCategory.TECHNICAL_CRASH).contains(request.intent()) || containsAny(msg, "崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401")) {
            targets.add(AgentType.TECHNICAL);
        }
        if (List.of(IntentCategory.BILLING, IntentCategory.ACCOUNT, IntentCategory.ACCOUNT_SECURITY, IntentCategory.REFUND, IntentCategory.INVOICE, IntentCategory.PAYMENT_ISSUE).contains(request.intent())
                || containsAny(msg, "退款", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice")) {
            targets.add(AgentType.BILLING);
        }
        return new ArrayList<>(targets.stream().filter(pool::containsKey).toList());
    }

    private boolean containsAny(String text, String... keywords) {
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private long countHits(String text, String... keywords) {
        long hits = 0;
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                hits++;
            }
        }
        return hits;
    }

    private boolean needsClarification(AgentRequest req) {
        if (req.intent() != IntentCategory.OTHER) {
            return false;
        }
        String text = req.message() == null ? "" : req.message().trim();
        if (text.length() <= 2) {
            return false;
        }
        return req.intentConfidence() < 0.5;
    }

    private AgentResponse execute(AgentRequest req, AgentType agentType) {
        if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("interrupted");
        if (agentType == AgentType.ESCALATION) return pool.getOrDefault(AgentType.ESCALATION, List.of()).stream().findFirst()
                .orElseGet(() -> new EscalationAgent(null, null)).handle(req);
        BaseAgent agent = availableAgent(agentType, req.requestId()).orElseGet(() -> availableAgent(AgentType.GENERAL, req.requestId()).orElse(null));
        if (agent == null) {
            return new AgentResponse(AgentType.GENERAL, "服务暂时不可用，请稍后重试。", false, 0.0, 0, false, "", false, false, false, "service unavailable");
        }
        AgentResponse response = agent.handle(req);
        if (!response.success() && agentType != AgentType.GENERAL && !req.operations().writeAttempted()) {
            AgentResponse fallback = availableAgent(AgentType.GENERAL, req.requestId()).map(a -> a.handle(req)).orElse(response);
            List<ToolCallTrace> attempts = new ArrayList<>(response.toolCalls());
            attempts.add(new ToolCallTrace("agent_fallback", false, true, false, false, response.latencyMs(), "specialist_failed"));
            attempts.addAll(fallback.toolCalls());
            return new AgentResponse(fallback.agentType(), fallback.content(), fallback.success(), fallback.confidence(), fallback.latencyMs(), fallback.escalate(), "", fallback.toolSuccess(), false, false, fallback.toolError(), attempts);
        }
        return response;
    }

    private void recordTrace(AgentRequest req, OrchestratorResult result) {
        traceStore.record(new RequestToolTrace(
                result.requestId(),
                Instant.now().toString(),
                "chat",
                req.userId(),
                req.conversationId(),
                result.intent() == null ? null : result.intent().name().toLowerCase(Locale.ROOT),
                req.intentGroup(),
                result.agentType().name().toLowerCase(Locale.ROOT),
                result.primaryAgent() == null ? null : result.primaryAgent().name().toLowerCase(Locale.ROOT),
                result.supportingAgents().stream().map(a -> a.name().toLowerCase(Locale.ROOT)).toList(),
                result.toolsUsed(),
                result.toolCalls(),
                result.toolCalls().stream().anyMatch(t -> t.success() && (t.toolName().equals("search_knowledge_base") || t.toolName().equals("knowledge_search"))),
                result.escalated(),
                result.latencyMs()
        ));
    }

    private List<ToolCallTrace> collectToolCalls(List<ToolCallTrace> externalToolCalls, AgentResponse... responses) {
        List<ToolCallTrace> calls = new ArrayList<>();
        if (externalToolCalls != null) {
            calls.addAll(externalToolCalls);
        }
        if (responses != null) {
            for (AgentResponse response : responses) {
                if (response == null) {
                    continue;
                }
                calls.addAll(response.toolCalls());
                if (response.toolName() != null && !response.toolName().isBlank()) {
                    calls.add(toTrace(response, response.toolName()));
                }
            }
        }
        return calls;
    }

    private List<ToolCallTrace> collectToolCalls(List<ToolCallTrace> externalToolCalls, List<AgentResponse> responses) {
        return collectToolCalls(externalToolCalls, responses == null ? null : responses.toArray(new AgentResponse[0]));
    }

    private List<String> collectToolNames(List<ToolCallTrace> externalToolCalls, AgentResponse... responses) {
        return collectToolCalls(externalToolCalls, responses).stream()
                .map(ToolCallTrace::toolName)
                .filter(name -> name != null && !name.isBlank())
                .distinct()
                .toList();
    }

    private List<String> collectToolNames(List<ToolCallTrace> externalToolCalls, List<AgentResponse> responses) {
        return collectToolCalls(externalToolCalls, responses).stream()
                .map(ToolCallTrace::toolName)
                .filter(name -> name != null && !name.isBlank())
                .distinct()
                .toList();
    }

    private ToolCallTrace toTrace(AgentResponse response, String toolName) {
        return new ToolCallTrace(
                toolName == null ? "" : toolName,
                response.success(),
                !response.success(),
                response.toolCached(),
                response.toolReranked(),
                response.latencyMs(),
                response.toolError() == null ? "" : response.toolError()
        );
    }

    private Optional<BaseAgent> availableAgent(AgentType type, String requestId) {
        return pool.getOrDefault(type, List.of()).stream().sorted(Comparator.comparingDouble((BaseAgent a) -> a.stats().routingScore()).reversed())
                .filter(a -> a.stats().admit(requestId)).findFirst();
    }

    private Optional<BaseAgent> bestAgent(AgentType agentType) {
        return pool.getOrDefault(agentType, List.of()).stream()
                .max(Comparator.comparingDouble(a -> a.stats().routingScore()));
    }

    public Map<String, Object> stats() {
        Map<String, Object> result = new HashMap<>();
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                BaseAgent agent = agents.get(i);
                Map<String, Object> data = new HashMap<>();
                data.put("total", agent.stats().total());
                data.put("window_samples", agent.stats().windowSamples());
                data.put("recovery_state", agent.stats().recoveryState());
                data.put("success_rate", round(agent.stats().successRate()));
                data.put("avg_ms", round(agent.stats().avgLatencyMs()));
                data.put("monitor_penalty", round(agent.stats().monitorPenalty()));
                data.put("routing_score", round(agent.stats().routingScore()));
                result.put(type.name().toLowerCase(Locale.ROOT) + "_" + i, data);
            }
        });
        return result;
    }

    @SuppressWarnings("unchecked")
    public void updateRoutingPenalties(Map<String, Double> penalties) {
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                agents.get(i).stats().setMonitorPenalty(penalties.getOrDefault(type.name().toLowerCase(Locale.ROOT) + "_" + i, 0.0));
            }
        });
    }

    private double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }

    private record RoutingDecision(
            AgentType primaryAgent,
            List<AgentType> supportingAgents,
            String reason,
            double confidence
    ) {
        private List<AgentType> agentTypes() {
            List<AgentType> result = new ArrayList<>();
            result.add(primaryAgent);
            result.addAll(supportingAgents);
            return result;
        }

        private boolean multiAgent() {
            return !supportingAgents.isEmpty();
        }
    }
}
