package com.llmagentsystem.evaluation;

import com.llmagentsystem.api.dto.ConversationEvalRequest;
import com.llmagentsystem.llm.PromptBudget;
import com.llmagentsystem.memory.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;
import java.time.Instant;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ConversationEvaluatorTest {
    ConversationMessage message(MessageRole role, String text, Map<String,Object> metadata) {
        return new ConversationMessage(role, text, Instant.now(), metadata);
    }
    ConversationEvalRequest request(int limit) { return new ConversationEvalRequest("forged-user", "conv", limit, "参考答案", "核验资料"); }

    @Test void scoresOriginalAnswersWithPriorHistoryAndEvidenceWithoutWritingOrReplaying() {
        var store = mock(ConversationStore.class); var judge = mock(LLMJudge.class);
        when(judge.judge(anyString(), anyString(), anyString(), anyString(), anyString())).thenReturn(new QualityScores(1, .9, .8, .9, false, null));
        var saved = List.of(message(MessageRole.USER,"我想退款，订单号12345",Map.of()),
                message(MessageRole.ASSISTANT,"原始回复一",Map.of("evidence",List.of("第一轮工具核验"))),
                message(MessageRole.USER,"下一步怎么办",Map.of()),
                message(MessageRole.ASSISTANT,"原始回复二",Map.of("request_id","r2","status","success","evidence",List.of("当时查询到的退款政策"))));
        when(store.transcript("authenticated-user","conv")).thenReturn(new ConversationStore.Transcript(saved,"conversation_history",false));
        var evaluator = new ConversationEvaluator(store, judge, new ObjectMapper(), PromptBudget.defaults());
        var snapshot = evaluator.prepare(request(1),"authenticated-user");
        assertEquals(1,snapshot.turns().size()); assertEquals(2,snapshot.availableTurns());
        var report=evaluator.run(snapshot,ignored->{});
        verify(judge).judge(eq("下一步怎么办"),eq("原始回复二"),argThat(c -> c.contains("订单号12345") && c.contains("原始回复一") && c.contains("第一轮工具核验") && c.contains("当时查询到的退款政策")),eq("参考答案"),eq("核验资料"));
        verify(store).transcript("authenticated-user","conv"); verifyNoMoreInteractions(store);
        assertEquals("conversation",report.get("scope")); assertEquals(1L,report.get("valid_total"));
        assertFalse(((Map<?,?>)report.get("avg_scores")).containsKey("intent_accuracy"));
        assertEquals(List.of(),report.get("regressions"));
    }
    @Test void failedScoringIsExcludedAndOriginalAnswersRemainInResult() {
        var store=mock(ConversationStore.class); var judge=mock(LLMJudge.class);
        when(judge.judge(anyString(),anyString(),anyString(),any(),any())).thenReturn(new QualityScores(0,0,0,0,true,"model_timeout"));
        when(store.transcript("forged-user","conv")).thenReturn(new ConversationStore.Transcript(List.of(message(MessageRole.USER,"问题",Map.of()),message(MessageRole.ASSISTANT,"已有回复",Map.of())),"working_memory_legacy",true));
        var evaluator=new ConversationEvaluator(store,judge,new ObjectMapper(),PromptBudget.defaults());
        var report=evaluator.run(evaluator.prepare(request(20),null),ignored->{});
        assertEquals("failed",report.get("status")); assertEquals(0L,report.get("valid_total")); assertNull(report.get("pass_rate"));
        assertEquals(Map.of(),report.get("avg_scores")); assertEquals(true,report.get("source_truncated"));
        var result=(Map<?,?>)((List<?>)report.get("results")).getFirst();
        assertEquals("已有回复",((Map<?,?>)result.get("metadata")).get("response")); assertTrue(((Map<?,?>)result.get("scores")).isEmpty());
    }
    @Test void rejectsTranscriptWithoutACompletePair() {
        var store=mock(ConversationStore.class);
        when(store.transcript(anyString(),anyString())).thenReturn(new ConversationStore.Transcript(List.of(message(MessageRole.USER,"只有问题",Map.of())),"conversation_history",false));
        var evaluator=new ConversationEvaluator(store,mock(LLMJudge.class),new ObjectMapper(),PromptBudget.defaults());
        assertEquals(400,assertThrows(ResponseStatusException.class,()->evaluator.prepare(request(20),null)).getStatusCode().value());
    }
    @Test void jobResultsAreScopedToTheSubmittingAccount() throws Exception {
        var regression=mock(EndToEndEvaluator.class); var conversation=mock(ConversationEvaluator.class);
        when(regression.run(any(),any())).thenReturn(Map.of("status","empty"));
        var jobs=new EvaluationJobs(regression,conversation);
        try {
            String id=jobs.submit(null,"alice").get("job_id").toString();
            assertEquals(404,assertThrows(ResponseStatusException.class,()->jobs.get(id,"bob")).getStatusCode().value());
            assertEquals(id,jobs.get(id,"alice").get("job_id"));
        } finally { jobs.close(); }
    }
}
