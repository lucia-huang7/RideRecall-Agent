package com.kinderuber.tool;

import com.kinderuber.llm.ToolSpecifications;
import com.kinderuber.trace.ToolCallTrace;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.agent.tool.ToolExecutionRequest;
import dev.langchain4j.agent.tool.ToolSpecification;
import dev.langchain4j.service.tool.*;
import java.util.*;

/** One invocation's adapter: shared registry policies, isolated evidence, traces and limits. */
public final class RegistryToolProvider implements ToolProvider {
    private final ToolRegistry registry;
    private final ObjectMapper mapper;
    private final String agent;
    private final List<String> evidence;
    private final List<ToolCallTrace> traces;
    private final com.kinderuber.llm.PromptBudget budget;
    private final com.kinderuber.agent.AgentRequest request;

    public RegistryToolProvider(ToolRegistry registry, ObjectMapper mapper, String agent,
                                List<String> evidence, List<ToolCallTrace> traces, com.kinderuber.llm.PromptBudget budget) {
        this(registry, mapper, agent, evidence, traces, budget, null);
    }
    public RegistryToolProvider(ToolRegistry registry, ObjectMapper mapper, String agent, List<String> evidence,
                                List<ToolCallTrace> traces, com.kinderuber.llm.PromptBudget budget, com.kinderuber.agent.AgentRequest request) {
        this.registry = registry; this.mapper = mapper; this.agent = agent;
        this.evidence = evidence; this.traces = traces;
        this.budget = budget;
        this.request = request;
    }

    @Override public ToolProviderResult provideTools(ToolProviderRequest request) {
        Map<ToolSpecification, ToolExecutor> tools = new LinkedHashMap<>();
        for (ToolDefinition tool : registry.available(agent)) {
            if (!tool.readOnly() && (this.request == null || !this.request.operations().permitsWrites())) continue;
            tools.put(ToolSpecifications.from(tool, mapper), (call, memoryId) -> execute(call));
        }
        return ToolProviderResult.builder().addAll(tools).build();
    }

    public String execute(ToolExecutionRequest call) {
        if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("interrupted");
        if (traces.size() >= 32) throw new IllegalStateException("tool_call_limit");
        ToolResult<Object> result;
        Map<String, Object> arguments;
        try {
            arguments = mapper.readValue(call.arguments(), new TypeReference<>() {});
            if (arguments == null) throw new IllegalArgumentException("null_arguments");
        } catch (Exception ex) {
            arguments = null;
        }
        result = arguments == null
                ? new ToolResult<>(false, Map.of(), call.name(), "invalid_arguments", false, 0, false)
                : registry.call(call.name(), arguments, agent, request);
        String payload = budget.toolResult(json(result));
        if (result.success()) evidence.add(payload);
        Map<String, Object> descriptor = registry.descriptor(call.name());
        traces.add(new ToolCallTrace(call.name(), result.success(), false, result.cached(), result.reranked(), result.latencyMs(), result.error(),
                String.valueOf(descriptor.getOrDefault("source", "local")), String.valueOf(descriptor.getOrDefault("server", "")),
                String.valueOf(descriptor.getOrDefault("remote_name", ""))));
        return payload;
    }

    private String json(Object value) {
        try { return mapper.writeValueAsString(value); }
        catch (Exception ex) { throw new IllegalStateException("tool_result_serialization_failed", ex); }
    }
}
