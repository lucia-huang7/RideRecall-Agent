package com.kinderuber.monitor;

import com.kinderuber.agent.AgentOrchestrator;
import com.kinderuber.config.KinderUberProperties;
import com.kinderuber.tool.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MonitorTest {
    @Test void alertsResolveWhenRecentWindowRecovers() {
        var orchestrator = mock(AgentOrchestrator.class); var knowledge = mock(KnowledgeToolManager.class);
        when(knowledge.stats()).thenReturn(Map.of());
        when(orchestrator.stats()).thenReturn(Map.of("technical_0", Map.of("total", 5L, "window_samples", 5, "success_rate", .4, "avg_ms", 8000)));
        var monitor = new PerformanceMonitor(orchestrator, knowledge, new ToolRegistry(new ObjectMapper()), new KinderUberProperties(), new SimpleMeterRegistry());
        monitor.collect(); assertFalse(((List<?>) monitor.summary().get("active_alerts")).isEmpty());
        when(orchestrator.stats()).thenReturn(Map.of("technical_0", Map.of("total", 6L, "window_samples", 1, "success_rate", 1, "avg_ms", 10)));
        monitor.collect(); assertTrue(((List<?>) monitor.summary().get("active_alerts")).isEmpty());
        assertFalse(((List<?>) monitor.summary().get("resolved_alerts")).isEmpty());
    }
    @Test void zScoreNeedsEnoughSamplesAndDetectsOutlier() {
        assertEquals(0, PerformanceMonitor.zScore(List.of(1.0, 2.0), 100));
        assertTrue(PerformanceMonitor.zScore(List.of(99., 100., 101., 100., 101., 99., 100., 101., 99., 100.), 140) > 3);
    }
}
