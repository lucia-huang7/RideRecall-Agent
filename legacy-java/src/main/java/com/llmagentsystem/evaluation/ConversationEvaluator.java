package com.llmagentsystem.evaluation;

import com.llmagentsystem.api.dto.ConversationEvalRequest;
import com.llmagentsystem.llm.PromptBudget;
import com.llmagentsystem.memory.*;
import com.llmagentsystem.util.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import java.util.*;
import java.util.function.Consumer;

/** Scores a frozen transcript. Never invokes the agent, tools, or memory maintenance. */
@Service
public class ConversationEvaluator {
    private final ConversationStore conversations;
    private final LLMJudge judge;
    private final ObjectMapper mapper;
    private final PromptBudget budget;
    public ConversationEvaluator(ConversationStore conversations, LLMJudge judge, ObjectMapper mapper, PromptBudget budget) {
        this.conversations = conversations; this.judge = judge; this.mapper = mapper; this.budget = budget;
    }
    public record Turn(ConversationMessage question, ConversationMessage answer, List<ConversationMessage> history) {}
    public record Snapshot(String conversationId, String source, boolean sourceTruncated, int availableTurns,
                           List<Turn> turns, String reference, String evidence) {}

    public Snapshot prepare(ConversationEvalRequest request, String authenticatedUser) {
        String user = authenticatedUser == null ? request.userIdOrDefault() : authenticatedUser;
        var transcript = conversations.transcript(user, request.conversationId());
        List<Turn> pairs = new ArrayList<>();
        List<ConversationMessage> history = new ArrayList<>();
        ConversationMessage question = null;
        for (var message : transcript.messages()) {
            if (message.role() == MessageRole.USER) question = message;
            else if (message.role() == MessageRole.ASSISTANT && question != null) {
                if (!question.content().isBlank() && !message.content().isBlank())
                    pairs.add(new Turn(question, message, List.copyOf(history.subList(0, Math.max(0, history.size() - 1)))));
                question = null;
            }
            if (message.role() == MessageRole.USER || message.role() == MessageRole.ASSISTANT) history.add(message);
        }
        if (pairs.isEmpty()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "会话中没有可评测的完整问答");
        int start = Math.max(0, pairs.size() - request.limit());
        return new Snapshot(request.conversationId(), transcript.source(), transcript.truncated(), pairs.size(),
                List.copyOf(pairs.subList(start, pairs.size())), request.referenceAnswer(), request.evidence());
    }

    public Map<String, Object> run(Snapshot snapshot, Consumer<Map<String, Object>> progress) {
        List<Map<String, Object>> results = new ArrayList<>();
        progress.accept(Map.of("completed", 0, "total", snapshot.turns().size()));
        for (Turn turn : snapshot.turns()) {
            if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("cancelled");
            Map<String, Object> saved = turn.answer().metadata() == null ? Map.of() : turn.answer().metadata();
            String history;
            try {
                history = mapper.writeValueAsString(turn.history().stream()
                        .map(this::historyEntry).toList());
            } catch (Exception ex) { throw new IllegalStateException("invalid_transcript", ex); }
            String proof = Objects.toString(saved.get("evidence"), "");
            String boundedHistory = history;
            int omitted = 0;
            while (budget.estimateTokenCountInText(boundedHistory) > 6000 && turn.history().size() - omitted > 2) {
                omitted += 2;
                try {
                    boundedHistory = mapper.writeValueAsString(turn.history().subList(omitted, turn.history().size()).stream()
                            .map(this::historyEntry).toList());
                } catch (Exception ex) { throw new IllegalStateException("invalid_transcript", ex); }
            }
            String context = "[原始会话此前的消息]\n" + budget.truncate(boundedHistory, 6000)
                    + "\n[当时的背景信息，仅作上下文，用户陈述不代表核验事实]\n" + budget.truncate(Objects.toString(saved.get("context"), ""), 2000)
                    + "\n[当时的成功工具执行证据，空值代表无留存证据]\n" + budget.truncate(proof, 3000);
            QualityScores score = judge.judge(turn.question().content(), turn.answer().content(), context, snapshot.reference(), snapshot.evidence());
            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("question", turn.question().content()); metadata.put("response", turn.answer().content());
            metadata.put("request_id", saved.getOrDefault("request_id", "")); metadata.put("agent_type", saved.getOrDefault("agent_type", ""));
            metadata.put("intent", saved.getOrDefault("intent", "")); metadata.put("timestamp", turn.answer().timestamp());
            metadata.put("judge_failed", score.judgeFailed()); metadata.put("judge_error", Objects.toString(score.error(), ""));
            metadata.put("judge_few_shot_version", EvaluationFixtures.version()); metadata.put("judge_few_shot_count", EvaluationFixtures.exampleCount());
            metadata.put("tool_evidence_available", hasEvidence(turn.answer()) || turn.history().stream().anyMatch(this::hasEvidence));
            metadata.put("context_truncated", budget.estimateTokenCountInText(history) > 6000);
            metadata.put("scope", "conversation");
            boolean completed = saved.get("status") == null || saved.get("status").equals("success");
            results.add(Map.of("test_id", "conversation_turn_" + (results.size() + 1), "passed", completed && !score.judgeFailed() && score.overall() >= .75,
                    "scores", score.judgeFailed() ? Map.of() : scores(score), "metadata", metadata));
            progress.accept(Map.of("completed", results.size(), "total", snapshot.turns().size()));
        }
        long errors = results.stream().filter(r -> ((Map<?, ?>) r.get("scores")).isEmpty()).count();
        long valid = results.size() - errors, passed = results.stream().filter(r -> Boolean.TRUE.equals(r.get("passed"))).count();
        Map<String, Object> averages = new LinkedHashMap<>();
        if (valid > 0) for (String key : List.of("relevance", "accuracy", "completeness", "helpfulness", "overall")) {
            double average = results.stream().map(r -> (Map<?, ?>) r.get("scores")).filter(s -> s.containsKey(key))
                    .mapToDouble(s -> ((Number) s.get(key)).doubleValue()).average().orElseThrow();
            averages.put(key.equals("overall") ? "dialog_overall" : key, round(average));
        }
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("scope", "conversation"); report.put("conv_id", snapshot.conversationId()); report.put("source", snapshot.source());
        report.put("source_truncated", snapshot.sourceTruncated()); report.put("available_turns", snapshot.availableTurns()); report.put("evaluated_turns", results.size());
        report.put("status", valid == 0 ? "failed" : errors > 0 ? "partial" : "complete");
        report.put("total", results.size()); report.put("valid", valid); report.put("valid_total", valid); report.put("passed", passed);
        report.put("judge_failed", errors); report.put("evaluation_errors", errors); report.put("pass_rate", valid == 0 ? null : round((double) passed / valid));
        report.put("avg_scores", averages); report.put("results", results); report.put("per_class", Map.of()); report.put("regressions", List.of());
        report.put("regression_note", "真实会话评分不计算意图准确率，不写入固定回归用例基线。");
        report.put("judge_few_shot_version", EvaluationFixtures.version()); report.put("judge_few_shot_count", EvaluationFixtures.exampleCount());
        report.put("snapshot_id", Hashing.key(snapshot.conversationId(), results.toString(), judge.fingerprint()));
        List<String> notes = new ArrayList<>();
        notes.add("评分使用保存的原始问答和当时留存的证据；未重新生成回复或执行业务工具。缺少参考答案时，评分不能代替业务事实核验。");
        if (snapshot.sourceTruncated()) notes.add("旧会话可能已被压缩，或记录已超过保留上限；仅评测当前保留的原文。");
        if (snapshot.availableTurns() > results.size()) notes.add("本次评测最近 " + results.size() + " 轮，当前保留 " + snapshot.availableTurns() + " 轮完整问答。");
        if (errors > 0) notes.add("部分评分失败，失败项不计入平均分，请查看逐轮错误。");
        report.put("recommendations", notes);
        return report;
    }
    private Map<String, Object> historyEntry(ConversationMessage message) {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("role", message.role().name().toLowerCase(Locale.ROOT)); entry.put("content", message.content());
        if (hasEvidence(message)) entry.put("tool_evidence", message.metadata().get("evidence"));
        return entry;
    }
    private boolean hasEvidence(ConversationMessage message) {
        return message.role() == MessageRole.ASSISTANT && message.metadata() != null && message.metadata().get("evidence") instanceof List<?> list
                && list.stream().anyMatch(value -> value != null && !value.toString().isBlank());
    }
    private Map<String, Double> scores(QualityScores score) {
        return Map.of("relevance", score.relevance(), "accuracy", score.accuracy(), "completeness", score.completeness(), "helpfulness", score.helpfulness(), "overall", round(score.overall()));
    }
    private double round(double value) { return Math.round(value * 10000.0) / 10000.0; }
}
