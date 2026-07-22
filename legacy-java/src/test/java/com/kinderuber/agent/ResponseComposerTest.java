package com.kinderuber.agent;

import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class ResponseComposerTest {
    AgentResponse response(AgentType type,String content,boolean ok) {return new AgentResponse(type,content,ok,1,1,false,"",true,false,false,"");}
    AgentRequest request() {return AgentRequest.of("登录故障和账单问题","u","c","",List.of());}
    @Test void failurePreservesPrimaryAndSecondaryAnswersWithoutInternalTags() {
        var composer=new ResponseComposer((s,u,t,m)->{throw new IllegalStateException("fixture_unavailable");},null,AgentProfiles.defaults());
        String result=composer.compose(request(),List.of(response(AgentType.TECHNICAL,"技术处理建议",true),response(AgentType.BILLING,"账单核验建议",true)),AgentType.BILLING);
        assertTrue(result.indexOf("账单核验建议")<result.indexOf("技术处理建议"));assertTrue(result.contains("补充说明"));
        assertFalse(result.contains("Agent"));assertFalse(result.contains("[billing"));assertFalse(result.contains("主处理"));
    }
    @Test void singleOrFailedResponsesDoNotTriggerAnUnnecessaryModelCall() {
        AtomicInteger calls=new AtomicInteger();var composer=new ResponseComposer((s,u,t,m)->{calls.incrementAndGet();return "unused";},null,AgentProfiles.defaults());
        assertEquals("有效回复",composer.compose(request(),List.of(response(AgentType.GENERAL,"有效回复",true)),AgentType.GENERAL));
        composer.compose(request(),List.of(response(AgentType.GENERAL,"失败回复",false)),AgentType.GENERAL);assertEquals(0,calls.get());
    }
    @Test void composerReceivesConflictRulesAndItsConfiguredBudget() {
        var settings=new AgentProfiles(new MockEnvironment().withProperty("KINDER_UBER_COMPOSER_MAX_TOKENS","650"));
        var composer=new ResponseComposer((s,u,t,m)->{
            assertTrue(s.contains("若结论冲突"));assertTrue(s.contains("不得新增"));assertTrue(u.contains("主处理角色：billing"));assertEquals(650,m);return "整合后的结果";
        },null,settings);
        assertEquals("整合后的结果",composer.compose(request(),List.of(response(AgentType.TECHNICAL,"技术意见",true),response(AgentType.BILLING,"账单意见",true)),AgentType.BILLING));
    }
}
