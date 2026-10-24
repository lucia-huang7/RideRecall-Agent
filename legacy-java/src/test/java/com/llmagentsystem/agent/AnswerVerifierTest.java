package com.llmagentsystem.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class AnswerVerifierTest {
    @Test void malformedJsonIsUnavailableRatherThanAnExplicitRejection() {
        for (String raw : new String[]{"{}", "not json", "{\"pass\":\"true\",\"grounded\":true,\"need_escalation\":false}"}) {
            var result = new AnswerVerifier((s,u,t,m) -> raw, new ObjectMapper()).verify("问题", "回答", "");
            assertFalse(result.available()); assertFalse(result.accepted());
        }
    }
    @Test void bothQualityAndGroundingMustPass() {
        var result = new AnswerVerifier((s,u,t,m) -> "{\"pass\":true,\"grounded\":false,\"need_escalation\":false}", new ObjectMapper()).verify("问题", "回答", "");
        assertTrue(result.available()); assertFalse(result.accepted());
    }
}
