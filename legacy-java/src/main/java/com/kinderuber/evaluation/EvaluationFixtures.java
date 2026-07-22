package com.kinderuber.evaluation;

import com.kinderuber.api.dto.EvalRunRequest;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;

/** Versioned scoring calibration and regression cases shared with the Python project. */
public final class EvaluationFixtures {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final JsonNode DATA = load();
    private EvaluationFixtures() {}
    private static JsonNode load() {
        try (var input = EvaluationFixtures.class.getResourceAsStream("/evaluation/customer-service-v1.json")) {
            if (input == null) throw new IllegalStateException("Missing evaluation fixtures");
            return MAPPER.readTree(input);
        } catch (Exception ex) { throw new IllegalStateException("Invalid evaluation fixtures", ex); }
    }
    public static String version() { return DATA.path("version").asText(); }
    public static int exampleCount() { return DATA.path("judgeExamples").size(); }
    public static String examples() { return DATA.path("judgeExamples").toPrettyString(); }
    public static List<EvalRunRequest.IntentCase> intentCases() {
        return MAPPER.convertValue(DATA.path("intentCases"), new com.fasterxml.jackson.core.type.TypeReference<>() {});
    }
    public static List<EvalRunRequest.DialogCase> dialogCases() {
        return MAPPER.convertValue(DATA.path("dialogCases"), new com.fasterxml.jackson.core.type.TypeReference<>() {});
    }
}
