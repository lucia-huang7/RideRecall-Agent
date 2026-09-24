package com.llmagentsystem.llm;

import com.llmagentsystem.tool.ToolDefinition;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.agent.tool.ToolSpecification;
import dev.langchain4j.model.chat.request.json.*;
import java.util.*;

/** Maps supported object schemas without silently dropping validation constraints. */
public final class ToolSpecifications {
    private static final Set<String> ROOT_KEYS = Set.of("type", "description", "properties", "required", "additionalProperties",
            "definitions", "$defs", "$schema", "$id", "$comment", "title");
    private ToolSpecifications() {}
    public static ToolSpecification from(ToolDefinition tool, ObjectMapper mapper) {
        JsonNode schema = mapper.valueToTree(tool.inputSchema());
        if (!schema.isObject()) throw new IllegalArgumentException("Tool schema must be an object: " + tool.name());
        schema.fieldNames().forEachRemaining(key -> {
            if (!ROOT_KEYS.contains(key)) throw new IllegalArgumentException("Unsupported root tool schema keyword " + key + ": " + tool.name());
        });
        if (schema.has("type") && !schema.path("type").asText().equals("object"))
            throw new IllegalArgumentException("Tool parameters must have object type: " + tool.name());
        if (schema.has("additionalProperties") && !schema.path("additionalProperties").isBoolean())
            throw new IllegalArgumentException("Schema-valued additionalProperties is unsupported: " + tool.name());
        var parameters = JsonObjectSchema.builder();
        if (schema.path("description").isTextual()) parameters.description(schema.path("description").asText());
        schema.path("properties").fields().forEachRemaining(e -> parameters.addProperty(e.getKey(), raw(e.getValue())));
        List<String> required = new ArrayList<>(); schema.path("required").forEach(n -> required.add(n.asText()));
        parameters.required(required);
        if (schema.path("additionalProperties").isBoolean()) parameters.additionalProperties(schema.path("additionalProperties").asBoolean());
        Map<String, JsonSchemaElement> definitions = new LinkedHashMap<>();
        schema.path("definitions").fields().forEachRemaining(e -> definitions.put(e.getKey(), raw(e.getValue())));
        schema.path("$defs").fields().forEachRemaining(e -> {
            if (definitions.containsKey(e.getKey())) throw new IllegalArgumentException("Duplicate tool schema definition: " + e.getKey());
            definitions.put(e.getKey(), raw(e.getValue()));
        });
        if (!definitions.isEmpty()) parameters.definitions(definitions);
        return ToolSpecification.builder().name(tool.name()).description(tool.description()).parameters(parameters.build()).build();
    }
    private static JsonSchemaElement raw(JsonNode value) {
        // LangChain4j emits root definitions using the draft-07 spelling.
        return JsonRawSchema.from(value.toString().replace("#/$defs/", "#/definitions/"));
    }
}
