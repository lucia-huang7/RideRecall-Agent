package com.kinderuber.intent;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class UrgencyTest {
    private IntentRecognizer recognizer() {
        var mapper=new ObjectMapper();return new IntentRecognizer((s,u,t,m)->"{\"intent\":\"refund\",\"confidence\":0.99}",mapper,new IntentEmbedding("local","",mapper));
    }
    @Test void negatedUrgencyDoesNotTriggerHumanEscalation() {
        var recognizer=recognizer();
        for(String message:List.of("不紧急，我只咨询退款政策","这个问题并不紧急","无需马上处理","This is not urgent","This is not an emergency","no asap","It's not now"))
            assertEquals(UrgencyLevel.LOW,recognizer.recognize(message,List.of()).urgency(),message);
    }
    @Test void detectsRealUrgencyWithWordBoundariesAndPreservesPositiveClauses() {
        var recognizer=recognizer();
        for(String message:List.of("紧急问题","urgent help","an emergency","需要立刻处理","不紧急的是退款，但登录故障需要立刻处理"))
            assertEquals(UrgencyLevel.CRITICAL,recognizer.recognize(message,List.of()).urgency(),message);
        assertEquals(UrgencyLevel.LOW,recognizer.recognize("nowadays I have questions",List.of()).urgency());
        assertEquals(UrgencyLevel.HIGH,recognizer.recognize("please help now",List.of()).urgency());
        assertEquals(UrgencyLevel.MEDIUM,recognizer.recognize("这周处理即可",List.of()).urgency());
    }
}
