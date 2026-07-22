package com.kinderuber.evaluation;

import com.kinderuber.agent.AgentOrchestrator;
import com.kinderuber.agent.AgentRequest;
import com.kinderuber.api.dto.EvalRunRequest;
import com.kinderuber.config.KinderUberProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kinderuber.intent.IntentRecognizer;
import com.kinderuber.intent.IntentResult;
import org.springframework.stereotype.Service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class EndToEndEvaluator {

    private final com.kinderuber.llm.LlmGateway llm;
    private final com.kinderuber.agent.AgentAiService aiService;
    private final com.kinderuber.skill.SkillManager skills;
    private final com.kinderuber.tool.ToolRegistry tools;
    private final com.kinderuber.knowledge.KnowledgeBaseService knowledge;
    private final com.kinderuber.intent.IntentEmbedding embedding;
    private final LLMJudge judge;
    private final ObjectMapper objectMapper;
    private final KinderUberProperties properties;
    private final String modelIdentity;

    public EndToEndEvaluator(com.kinderuber.agent.AgentAiService aiService, com.kinderuber.llm.LlmGateway llm, com.kinderuber.skill.SkillManager skills,
            com.kinderuber.tool.ToolRegistry tools, com.kinderuber.knowledge.KnowledgeBaseService knowledge,
            com.kinderuber.intent.IntentEmbedding embedding, LLMJudge judge, ObjectMapper mapper, KinderUberProperties properties,
            org.springframework.core.env.Environment env) {
        this.aiService = aiService; this.llm = llm; this.skills = skills; this.tools = tools; this.knowledge = knowledge;
        this.embedding = embedding; this.judge = judge; this.objectMapper = mapper; this.properties = properties;
        this.modelIdentity = env.getProperty("spring.profiles.active", "anthropic") + ":" + env.getProperty("langchain4j.anthropic.chat-model.model-name", "default")
                + ":" + env.getProperty("langchain4j.deepseek.chat-model.model-name", "default") + ":" + env.getProperty("EVAL_JUDGE_MAX_TOKENS", "4096") + ":" + env.getProperty("EVAL_JUDGE_MODEL", "default") + ":" + env.getProperty("INTENT_EMBEDDING_PROVIDER", "auto") + ":judge-v3";
    }

    public Map<String, Object> run(EvalRunRequest request) { return run(request, ignored -> {}); }

    public synchronized Map<String, Object> run(EvalRunRequest request, java.util.function.Consumer<Map<String, Object>> progress) {
        IntentRecognizer intentRecognizer = new IntentRecognizer(llm, objectMapper, embedding);
        var isolatedTools = tools.fork();
        new com.kinderuber.tool.KnowledgeToolRegistration(isolatedTools, new com.kinderuber.tool.KnowledgeToolManager(knowledge, llm, objectMapper)).register();
        new com.kinderuber.tool.RoleToolRegistration(isolatedTools).register();
        var isolatedAi = aiService.withTools(isolatedTools);
        Map<com.kinderuber.agent.AgentType, List<com.kinderuber.agent.BaseAgent>> pool = Map.of(
                com.kinderuber.agent.AgentType.GENERAL, List.of(new com.kinderuber.agent.GeneralAgent(isolatedAi, skills)),
                com.kinderuber.agent.AgentType.TECHNICAL, List.of(new com.kinderuber.agent.TechnicalAgent(isolatedAi, skills)),
                com.kinderuber.agent.AgentType.BILLING, List.of(new com.kinderuber.agent.BillingAgent(isolatedAi, skills)),
                com.kinderuber.agent.AgentType.ESCALATION, List.of(new com.kinderuber.agent.EscalationAgent(isolatedAi, skills)));
        AgentOrchestrator orchestrator = new AgentOrchestrator(intentRecognizer, pool, new com.kinderuber.trace.RequestTraceStore(),
                new com.kinderuber.agent.ResponseComposer(llm, skills, aiService.profiles()));
        List<EvalRunRequest.IntentCase> intentCases = request != null && request.intentCases() != null
                ? request.intentCases()
                : defaultIntentCases();
        List<EvalRunRequest.DialogCase> dialogCases = request != null && request.dialogCases() != null
                ? request.dialogCases()
                : defaultDialogCases();
        int totalCases = intentCases.size() + dialogCases.stream().mapToInt(c -> c.turns() == null || c.turns().isEmpty() ? 1 : c.turns().size()).sum();
        progress.accept(Map.of("completed", 0, "total", totalCases));
        String suiteId;
        try { suiteId = com.kinderuber.util.Hashing.key(objectMapper.writeValueAsString(intentCases), objectMapper.writeValueAsString(dialogCases), modelIdentity,
                judge.fingerprint(), aiService.profiles().fingerprint()); }
        catch (Exception ex) { throw new IllegalArgumentException("Invalid evaluation suite", ex); }
        List<Map<String, Object>> results = new ArrayList<>();
        List<String> predictions = new ArrayList<>();
        List<String> groundTruth = new ArrayList<>();
        long intentCorrect = 0;
        for (EvalRunRequest.IntentCase c : intentCases) {
            if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("cancelled");
            IntentResult result = intentRecognizer.recognize(c.message(), intentHistory(c));
            String predicted = result.intent().name().toLowerCase(Locale.ROOT);
            predictions.add(predicted);
            groundTruth.add(c.expectedIntent());
            boolean passed = predicted.equals(c.expectedIntent());
            if (passed) {
                intentCorrect++;
            }
            results.add(Map.of(
                    "test_id", "intent_" + results.size(),
                    "passed", passed,
                    "scores", Map.of("accuracy", passed ? 1.0 : 0.0),
                    "metadata", Map.of("message", c.message(), "expected", c.expectedIntent(), "predicted", predicted)
            ));
        }
        progress.accept(Map.of("completed", results.size(), "total", totalCases));
        for (EvalRunRequest.DialogCase c : dialogCases) {
            List<String> turns = c.turns() != null && !c.turns().isEmpty() ? c.turns() : List.of(c.question());
            List<Map<String, String>> history = new ArrayList<>();
            String convId = c.conversationId() == null ? "eval_" + UUID.randomUUID() : c.conversationId();
            String userId = c.userId() == null ? "eval_user" : c.userId();
            Map<String, Object> state = Map.of();
            int turnIndex = 0;
            for (String turn : turns) {
                if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("cancelled");
                IntentResult intent = intentRecognizer.recognize(turn, history);
                state = com.kinderuber.memory.ConversationState.merge(state, intent.entities(), intent.intentGroup(), turn, "eval-" + turnIndex);
                @SuppressWarnings("unchecked") Map<String, List<String>> entities = (Map<String, List<String>>) state.get("entities");
                var merged = new IntentResult(intent.intent(), intent.confidence(), intent.urgency(), intent.intentGroup(), entities, intent.reasoning(), intent.latencyMs(), intent.sourceScores(), intent.embeddingSource());
                AgentRequest turnRequest = AgentRequest.of(turn, userId, convId, "[业务状态]\n" + state, history, merged);
                var response = orchestrator.run(turnRequest);
                Map<String, Boolean> checks = new java.util.LinkedHashMap<>();
                if (++turnIndex == turns.size()) {
                    if (c.expectedAgent() != null) checks.put("agent", c.expectedAgent().equals(response.agentType().name().toLowerCase(Locale.ROOT)));
                    if (c.expectedIntent() != null) checks.put("intent", response.intent() != null && c.expectedIntent().equals(response.intent().name().toLowerCase(Locale.ROOT)));
                    if (c.requiredTools() != null && !c.requiredTools().isEmpty()) checks.put("tools", response.toolsUsed().containsAll(c.requiredTools()));
                    if (c.expectedEntities() != null && !c.expectedEntities().isEmpty()) checks.put("entities", c.expectedEntities().entrySet().stream().allMatch(e -> entities.getOrDefault(e.getKey(), List.of()).containsAll(e.getValue())));
                }
                QualityScores scores = response.status().equals("failed") ? new QualityScores(0,0,0,0,true,"agent_failed") : judge.judge(turn, response.response(), history + "\n" + String.join("\n", turnRequest.evidence()), c.referenceAnswer(), c.evidence());
                results.add(Map.of(
                        "test_id", "dialog_" + results.size(),
                        "passed", response.status().equals("success") && checks.values().stream().allMatch(Boolean::booleanValue) && !scores.judgeFailed() && scores.overall() >= 0.75,
                        "scores", scores.judgeFailed() ? Map.of() : Map.of(
                                "overall", round(scores.overall()),
                                "relevance", scores.relevance(),
                                "accuracy", scores.accuracy(),
                                "completeness", scores.completeness(),
                                "helpfulness", scores.helpfulness()
                        ),
                        "metadata", Map.of(
                                "question", turn,
                                "response", response.response(),
                                "agent_type", response.agentType().name().toLowerCase(Locale.ROOT),
                                "checks", checks,
                                "judge_failed", scores.judgeFailed(),
                                "judge_error", scores.error() == null ? "" : scores.error(),
                                "judge_few_shot_version", EvaluationFixtures.version(),
                                "judge_few_shot_count", EvaluationFixtures.exampleCount(),
                                "ground_truth_provided", c.referenceAnswer() != null || c.evidence() != null,
                                "scope", "orchestrator"
                        )
                ));
                progress.accept(Map.of("completed", results.size(), "total", totalCases));
                history.add(Map.of("role", "user", "content", turn));
                history.add(Map.of("role", "assistant", "content", response.response()));
            }
        }
        long failures = results.stream().filter(r -> ((Map<?, ?>) r.get("scores")).isEmpty()).count();
        long valid = results.size() - failures;
        long passed = results.stream().filter(r -> Boolean.TRUE.equals(r.get("passed"))).count();
        double intentAccuracy = intentCases.isEmpty() ? 0.0 : (double) intentCorrect / intentCases.size();
        Map<String, Object> avgScores = new HashMap<>();
        avgScores.put("intent_accuracy", intentCases.isEmpty() ? null : round(intentAccuracy));
        avgScores.put("macro_f1", intentCases.isEmpty() ? null : round(macroF1(predictions, groundTruth)));
        avgScores.put("dialog_overall", results.stream().anyMatch(r -> String.valueOf(r.get("test_id")).startsWith("dialog_") && !((Map<?, ?>) r.get("scores")).isEmpty()) ? round(avgDialogOverall(results)) : null);
        for (String metric : List.of("relevance", "accuracy", "completeness", "helpfulness")) {
            var values = results.stream().filter(r -> String.valueOf(r.get("test_id")).startsWith("dialog_"))
                    .map(r -> (Map<?, ?>) r.get("scores")).map(m -> m.get(metric)).filter(Number.class::isInstance).map(Number.class::cast).toList();
            avgScores.put(metric, values.isEmpty() ? null : round(values.stream().mapToDouble(Number::doubleValue).average().orElseThrow()));
        }
        Map<String, Object> comparison = new HashMap<>(avgScores);
        comparison.put("pass_rate", valid == 0 ? null : round((double) passed / valid));
        String status = results.isEmpty() ? "empty" : valid == 0 ? "failed" : failures > 0 ? "partial" : "complete";
        List<String> regressions = status.equals("complete") ? detectRegressions(comparison, suiteId) : List.of();
        Map<String, Object> report = new java.util.LinkedHashMap<>();
        report.put("scope", "regression");
        report.put("judge_few_shot_version", EvaluationFixtures.version());
        report.put("judge_few_shot_count", EvaluationFixtures.exampleCount());
        report.put("valid_total", valid); report.put("evaluation_errors", failures);
        report.put("status", status); report.put("suite_id", suiteId);
        report.put("pass_rate", valid == 0 ? null : round((double) passed / valid));
        report.put("total", results.size()); report.put("valid", valid); report.put("judge_failed", failures); report.put("passed", passed);
        report.put("avg_scores", avgScores); report.put("per_class", perClassMetrics(predictions, groundTruth));
        report.put("regressions", regressions); report.put("recommendations", status.equals("complete") ? recommendations(avgScores, regressions, passed < valid) : List.of("评测未完整完成，请检查失败用例；无有效评分时无法判断质量"));
        report.put("results", results);
        if (status.equals("complete") && regressions.isEmpty()) saveBaseline(report);
        return report;
    }

    private List<Map<String, String>> intentHistory(EvalRunRequest.IntentCase test) {
        if (test.history() != null && !test.history().isEmpty()) return test.history();
        if (test.context() != null && test.context().get("history") instanceof List<?> history)
            return objectMapper.convertValue(history, new TypeReference<>() {});
        return List.of();
    }

    private List<EvalRunRequest.IntentCase> defaultIntentCases() { return EvaluationFixtures.intentCases(); }

    private List<EvalRunRequest.DialogCase> defaultDialogCases() { return EvaluationFixtures.dialogCases(); }

    private double round(double value) {
        return Math.round(value * 10000.0) / 10000.0;
    }

    private double macroF1(List<String> predictions, List<String> groundTruth) {
        Map<String, Map<String, Double>> perClass = perClassMetrics(predictions, groundTruth);
        return perClass.values().stream().mapToDouble(m -> m.getOrDefault("f1", 0.0)).average().orElse(0.0);
    }

    private Map<String, Map<String, Double>> perClassMetrics(List<String> predictions, List<String> groundTruth) {
        Set<String> labels = new HashSet<>();
        labels.addAll(predictions);
        labels.addAll(groundTruth);
        Map<String, Map<String, Double>> metrics = new HashMap<>();
        for (String label : labels) {
            int tp = 0;
            int fp = 0;
            int fn = 0;
            for (int i = 0; i < predictions.size(); i++) {
                boolean p = label.equals(predictions.get(i));
                boolean g = label.equals(groundTruth.get(i));
                if (p && g) tp++;
                if (p && !g) fp++;
                if (!p && g) fn++;
            }
            double precision = tp + fp == 0 ? 0.0 : (double) tp / (tp + fp);
            double recall = tp + fn == 0 ? 0.0 : (double) tp / (tp + fn);
            double f1 = precision + recall == 0 ? 0.0 : 2 * precision * recall / (precision + recall);
            metrics.put(label, Map.of("precision", round(precision), "recall", round(recall), "f1", round(f1)));
        }
        return metrics;
    }

    @SuppressWarnings("unchecked")
    private double avgDialogOverall(List<Map<String, Object>> results) {
        return results.stream()
                .filter(r -> String.valueOf(r.get("test_id")).startsWith("dialog_"))
                .map(r -> (Map<String, Object>) r.get("scores"))
                .filter(scores -> scores.containsKey("overall"))
                .mapToDouble(scores -> ((Number) scores.get("overall")).doubleValue())
                .average()
                .orElse(0.0);
    }

    @SuppressWarnings("unchecked")
    private List<String> detectRegressions(Map<String, Object> currentScores, String suiteId) {
        Path path = suiteBaseline(suiteId);
        if (!Files.exists(path)) path = Path.of(properties.getEval().getBaselinePath());
        if (!Files.exists(path)) {
            return List.of();
        }
        try {
            Map<String, Object> previous = objectMapper.readValue(path.toFile(), new TypeReference<>() {
            });
            if (!suiteId.equals(previous.get("suite_id")) || !"complete".equals(previous.get("status")) || (previous.get("regressions") instanceof List<?> r && !r.isEmpty())) return List.of();
            Map<String, Object> prevScores = new HashMap<>((Map<String, Object>) previous.getOrDefault("avg_scores", Map.of()));
            prevScores.put("pass_rate", previous.get("pass_rate"));
            List<String> regressions = new ArrayList<>();
            for (Map.Entry<String, Object> entry : currentScores.entrySet()) {
                Object prev = prevScores.get(entry.getKey());
                if (prev instanceof Number p && entry.getValue() instanceof Number c && p.doubleValue() > 0) {
                    double delta = (c.doubleValue() - p.doubleValue()) / p.doubleValue();
                    if (delta < -0.05) {
                        regressions.add(entry.getKey() + ": " + round(p.doubleValue()) + " -> " + round(c.doubleValue()));
                    }
                }
            }
            return regressions;
        } catch (Exception ex) {
            return List.of();
        }
    }

    private Path suiteBaseline(String suiteId) {
        if (!suiteId.matches("[a-f0-9]{64}")) throw new IllegalArgumentException("Invalid suite ID");
        Path current = Path.of(properties.getEval().getBaselinePath());
        return current.resolveSibling(current.getFileName() + ".suites").resolve(suiteId + ".json");
    }

    private void saveBaseline(Map<String, Object> report) {
        if (!"complete".equals(report.get("status")) || (report.get("regressions") instanceof List<?> r && !r.isEmpty())) return;
        try {
            Path path = Path.of(properties.getEval().getBaselinePath());
            com.kinderuber.util.AtomicJson.write(objectMapper, suiteBaseline((String) report.get("suite_id")), report);
            com.kinderuber.util.AtomicJson.write(objectMapper, path, report);
        } catch (Exception ignored) {
        }
    }

    private List<String> recommendations(Map<String, Object> scores, List<String> regressions, boolean failedChecks) {
        List<String> recs = new ArrayList<>();
        if (scores.get("intent_accuracy") instanceof Number value && value.doubleValue() < .9)
            recs.add("补充低准确率意图类别的样本和 Few-shot 示例");
        for (String metric : List.of("relevance", "accuracy", "completeness", "helpfulness"))
            if (scores.get(metric) instanceof Number value && value.doubleValue() < .75) recs.add(metric + " 偏低，请检查逐轮回复、参考资料与工具证据");
        if (failedChecks) recs.add("存在未通过用例，请检查执行状态、确定性业务断言和逐轮质量评分");
        if (!regressions.isEmpty()) recs.add("发现评测回归，已保留此前有效基线，请检查最近 prompt 或检索逻辑变更");
        if (recs.isEmpty()) recs.add("本次有效评测指标均达标");
        return recs;
    }
}
