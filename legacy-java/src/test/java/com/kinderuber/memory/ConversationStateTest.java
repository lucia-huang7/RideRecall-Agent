package com.kinderuber.memory;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class ConversationStateTest {
    @Test void structuredScopeKeysCannotCollideAtColonBoundaries() {
        assertNotEquals(ConversationStore.scope("a:b", "c"), ConversationStore.scope("a", "b:c"));
    }
    @Test void followupInheritsEntitiesButNewOrderAndDomainResetThem() {
        var initial = ConversationState.merge(Map.of(), Map.of("order_id", List.of("123"), "amount", List.of("50")), "billing", "退款", "r1");
        var followup = ConversationState.merge(initial, Map.of("order_id", List.of()), "other", "多久", "r2");
        assertEquals(List.of("123"), ((Map<?, ?>) followup.get("entities")).get("order_id"));
        var changed = ConversationState.merge(initial, Map.of("order_id", List.of("456")), "query", "另一笔订单", "r3");
        assertFalse(((Map<?, ?>) changed.get("entities")).containsKey("amount"));
        var technical = ConversationState.merge(initial, Map.of("error_code", List.of("401")), "technical", "登录故障", "r4");
        assertFalse(((Map<?, ?>) technical.get("entities")).containsKey("order_id"));
    }
}
