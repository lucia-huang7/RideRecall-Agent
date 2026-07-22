package com.kinderuber.evaluation;

import com.kinderuber.api.dto.EvalRunRequest;
import com.kinderuber.config.KinderUberProperties;
import com.kinderuber.intent.IntentEmbedding;
import com.kinderuber.knowledge.KnowledgeBaseService;
import com.kinderuber.skill.SkillManager;
import com.kinderuber.tool.ToolRegistry;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class EvaluationTest {
    private com.kinderuber.agent.AgentAiService aiService() {
        var model = new dev.langchain4j.model.chat.ChatModel() {
            @Override public dev.langchain4j.model.chat.response.ChatResponse doChat(dev.langchain4j.model.chat.request.ChatRequest request) {
                return dev.langchain4j.model.chat.response.ChatResponse.builder().aiMessage(dev.langchain4j.data.message.AiMessage.from("回复"))
                        .finishReason(dev.langchain4j.model.output.FinishReason.STOP).build();
            }
        };
        return new com.kinderuber.agent.AgentAiService(model, new ToolRegistry(new ObjectMapper()), new ObjectMapper());
    }
    @TempDir Path dir;
    @Test void calibrationExamplesReachJudgeAndMatchPythonFixtures() {
        var prompt = new java.util.concurrent.atomic.AtomicReference<String>();
        com.kinderuber.llm.LlmGateway llm = (s,u,t,m) -> {
            prompt.set(s + u);
            return "{\"relevance\":1,\"accuracy\":1,\"completeness\":1,\"helpfulness\":1}";
        };
        assertFalse(new LLMJudge(llm, new ObjectMapper()).judge("我的问题", "原始回复", "原始历史").judgeFailed());
        assertEquals(5, EvaluationFixtures.exampleCount());
        assertEquals("customer-service-v1", EvaluationFixtures.version());
        for (String label : List.of("有依据的完整回答", "诚实说明边界并给出下一步", "相关但笼统的回答", "编造已完成的操作", "遗漏多轮已知信息"))
            assertTrue(prompt.get().contains(label));
        assertTrue(prompt.get().contains("我的问题")); assertTrue(prompt.get().contains("原始回复")); assertTrue(prompt.get().contains("原始历史"));
        assertEquals(11, EvaluationFixtures.intentCases().size());
        assertEquals("logistics", EvaluationFixtures.intentCases().getFirst().expectedIntent());
        assertEquals(5, EvaluationFixtures.dialogCases().size());
        assertTrue(EvaluationFixtures.dialogCases().stream().allMatch(c -> c.referenceAnswer() != null && c.evidence() != null));
        assertEquals(3, EvaluationFixtures.dialogCases().getLast().turns().size());
    }
    @Test void judgeParsesFencesAndNestedScores() {
        var judge = new LLMJudge((s,u,t,m) -> "说明\n```json\n{\"scores\":{\"relevance\":1,\"accuracy\":0.8,\"completeness\":0.7,\"helpfulness\":0.9}}\n```", new ObjectMapper());
        var score = judge.judge("question", "response", ""); assertFalse(score.judgeFailed()); assertEquals(.85, score.overall(), 1e-6);
    }
    @Test void langChainAuthenticationFailureIsNotRetried() {
        var attempts = new java.util.concurrent.atomic.AtomicInteger();
        com.kinderuber.llm.LlmGateway llm = (s,u,t,m) -> {
            attempts.incrementAndGet(); throw new dev.langchain4j.exception.AuthenticationException("fixture authentication failure");
        };
        var result = new LLMJudge(llm, new ObjectMapper()).judge("q", "a", "");
        assertTrue(result.judgeFailed()); assertEquals(1, attempts.get());
    }
    @Test void judgeRejectsBooleanMissingAndOutOfRangeScores() {
        for (String invalid : List.of("{}", "{\"relevance\":true}", "{\"relevance\":2,\"accuracy\":0.8,\"completeness\":0.7,\"helpfulness\":0.9}", "not json")) {
            var judge = new LLMJudge((s,u,t,m) -> invalid, new ObjectMapper()); assertTrue(judge.judge("q", "a", "").judgeFailed());
        }
    }
    @Test void failedScoringIsExcludedAndDoesNotOverwriteBaseline() throws Exception {
        var mapper = new ObjectMapper(); var properties = new KinderUberProperties();
        Path baseline = dir.resolve("baseline.json"); properties.getEval().setBaselinePath(baseline.toString()); Files.writeString(baseline, "preserved");
        com.kinderuber.llm.LlmGateway llm = (s,u,t,m) -> "回复";
        var evaluator = new EndToEndEvaluator(aiService(), llm, mock(SkillManager.class), new ToolRegistry(mapper), mock(KnowledgeBaseService.class), new IntentEmbedding("local", "", mapper), new LLMJudge(llm, mapper), mapper, properties, new MockEnvironment());
        // No matching skills are needed; avoid returning null from the prompt lookup.
        SkillManager skills = mock(SkillManager.class); when(skills.promptFor(anyString(), anyString())).thenReturn("");
        evaluator = new EndToEndEvaluator(aiService(), llm, skills, new ToolRegistry(mapper), mock(KnowledgeBaseService.class), new IntentEmbedding("local", "", mapper), new LLMJudge(llm, mapper), mapper, properties, new MockEnvironment());
        var report = evaluator.run(new EvalRunRequest(List.of(), List.of(new EvalRunRequest.DialogCase("你好", null, null, null))));
        assertEquals("failed", report.get("status")); assertEquals(0L, report.get("valid")); assertNull(report.get("pass_rate"));
        assertEquals("preserved", Files.readString(baseline));
        Map<?, ?> test = (Map<?, ?>) ((List<?>) report.get("results")).getFirst(); assertTrue(((Map<?, ?>) test.get("scores")).isEmpty());
    }
    @Test void qualityScoreCannotOverrideFailedContractChecks() {
        var mapper = new ObjectMapper(); var properties = new KinderUberProperties(); properties.getEval().setBaselinePath(dir.resolve("baseline.json").toString());
        com.kinderuber.llm.LlmGateway llm = (s,u,t,m) -> u.contains("评分") ? "{\"relevance\":1,\"accuracy\":1,\"completeness\":1,\"helpfulness\":1}" : "{\"intent\":\"refund\",\"confidence\":0.99}";
        SkillManager skills = mock(SkillManager.class); when(skills.promptFor(anyString(), anyString())).thenReturn("");
        var evaluator = new EndToEndEvaluator(aiService(), llm, skills, new ToolRegistry(mapper), mock(KnowledgeBaseService.class), new IntentEmbedding("local", "", mapper), new LLMJudge(llm, mapper), mapper, properties, new MockEnvironment());
        var test = new EvalRunRequest.DialogCase("申请退款", null, null, null, null, null, "technical", null, List.of("required-but-not-called"), Map.of());
        var report = evaluator.run(new EvalRunRequest(List.of(), List.of(test)));
        assertEquals("complete", report.get("status"));
        Map<?, ?> result = (Map<?, ?>) ((List<?>) report.get("results")).getFirst();
        assertEquals(false, result.get("passed")); assertEquals(1.0, ((Map<?, ?>) result.get("scores")).get("overall"));
    }
    @Test void emptySuiteIsReportedAsEmpty() {
        var mapper = new ObjectMapper(); var props = new KinderUberProperties(); props.getEval().setBaselinePath(dir.resolve("baseline.json").toString());
        com.kinderuber.llm.LlmGateway llm = (s,u,t,m) -> "{}";
        var evaluator = new EndToEndEvaluator(aiService(), llm, mock(SkillManager.class), new ToolRegistry(mapper), mock(KnowledgeBaseService.class), new IntentEmbedding("local", "", mapper), new LLMJudge(llm, mapper), mapper, props, new MockEnvironment());
        assertEquals("empty", evaluator.run(new EvalRunRequest(List.of(), List.of())).get("status"));
    }
    @Test void regressedScoresNeverReplaceGoodBaselineAndDialogOnlyMetricsAreNotInvented() throws Exception {
        var mapper=new ObjectMapper();var props=new KinderUberProperties();Path baseline=dir.resolve("preserved.json");props.getEval().setBaselinePath(baseline.toString());
        var value=new java.util.concurrent.atomic.AtomicReference<Double>(1.0);
        com.kinderuber.llm.LlmGateway llm=(a,b,c,d)->b.contains("评分") ? "{\"relevance\":"+value.get()+",\"accuracy\":"+value.get()+",\"completeness\":"+value.get()+",\"helpfulness\":"+value.get()+"}" : "{\"intent\":\"greeting\",\"confidence\":0.99}";
        SkillManager skills=mock(SkillManager.class);when(skills.promptFor(anyString(),anyString())).thenReturn("");
        var evaluator=new EndToEndEvaluator(aiService(),llm,skills,new ToolRegistry(mapper),mock(KnowledgeBaseService.class),new IntentEmbedding("local","",mapper),new LLMJudge(llm,mapper),mapper,props,new MockEnvironment());
        var suite=new EvalRunRequest(List.of(),List.of(new EvalRunRequest.DialogCase("你好",null,null,null)));
        var first=evaluator.run(suite);assertNull(((Map<?,?>)first.get("avg_scores")).get("intent_accuracy"));
        assertFalse(first.get("recommendations").toString().contains("意图"));String original=Files.readString(baseline);
        value.set(.5);var low=evaluator.run(suite);assertFalse(((List<?>)low.get("regressions")).isEmpty());assertEquals(original,Files.readString(baseline));
        // A different suite must not destroy the first suite's persisted comparison point.
        value.set(1.0);evaluator.run(new EvalRunRequest(List.of(),List.of(new EvalRunRequest.DialogCase("谢谢",null,null,null))));
        value.set(.6);assertFalse(((List<?>)evaluator.run(suite).get("regressions")).isEmpty());
    }
    @Test void fallingPassRateIsDetectedEvenWhenQualityMeansDropLessThanFivePercent() {
        var mapper=new ObjectMapper();var props=new KinderUberProperties();props.getEval().setBaselinePath(dir.resolve("rate.json").toString());
        var value=new java.util.concurrent.atomic.AtomicReference<Double>(.76);
        com.kinderuber.llm.LlmGateway llm=(a,b,c,d)->b.contains("评分") ? "{\"relevance\":"+value.get()+",\"accuracy\":"+value.get()+",\"completeness\":"+value.get()+",\"helpfulness\":"+value.get()+"}" : "{\"intent\":\"greeting\",\"confidence\":0.99}";
        SkillManager skills=mock(SkillManager.class);when(skills.promptFor(anyString(),anyString())).thenReturn("");
        var evaluator=new EndToEndEvaluator(aiService(),llm,skills,new ToolRegistry(mapper),mock(KnowledgeBaseService.class),new IntentEmbedding("local","",mapper),new LLMJudge(llm,mapper),mapper,props,new MockEnvironment());
        var suite=new EvalRunRequest(List.of(),List.of(new EvalRunRequest.DialogCase("你好",null,null,null)));
        evaluator.run(suite);value.set(.74);assertTrue(evaluator.run(suite).get("regressions").toString().contains("pass_rate"));
    }

}
