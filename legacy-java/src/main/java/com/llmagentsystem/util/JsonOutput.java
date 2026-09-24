package com.llmagentsystem.util;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class JsonOutput {
    private JsonOutput() {}
    /** Finds a complete JSON value even inside prose or a Markdown fence. */
    public static JsonNode parse(ObjectMapper mapper, String text) {
        if (text == null || text.isBlank()) throw new IllegalArgumentException("empty_output");
        for (int start = 0; start < text.length(); start++) {
            char first = text.charAt(start);
            if (first != '{' && first != '[') continue;
            boolean quoted = false, escaped = false;
            int depth = 0;
            for (int end = start; end < text.length(); end++) {
                char c = text.charAt(end);
                if (quoted) {
                    if (escaped) escaped = false;
                    else if (c == '\\') escaped = true;
                    else if (c == '"') quoted = false;
                } else if (c == '"') quoted = true;
                else if (c == '{' || c == '[') depth++;
                else if (c == '}' || c == ']') {
                    if (--depth == 0) {
                        try { return mapper.readTree(text.substring(start, end + 1)); }
                        catch (Exception ignored) { break; }
                    }
                }
            }
        }
        throw new IllegalArgumentException("invalid_or_truncated_json");
    }
}
