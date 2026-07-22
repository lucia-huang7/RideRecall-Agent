package com.kinderuber.knowledge;

import java.util.*;

/** Sentence-aware Unicode slicing, independent of any AI framework. */
public final class TextChunker {
    private final int size, overlap;
    public TextChunker(int size, int overlap) {
        if (size < 2 || overlap < 0 || overlap >= size) throw new IllegalArgumentException("Invalid chunk size/overlap");
        this.size = size; this.overlap = overlap;
    }
    public List<String> split(String text) {
        if (text == null || text.isBlank()) return List.of();
        int[] points = text.replace("\r\n", "\n").replace('\r', '\n').strip().codePoints().toArray();
        List<String> chunks = new ArrayList<>();
        for (int start = 0; start < points.length;) {
            int end = Math.min(start + size, points.length);
            if (end < points.length) {
                // Prefer a complete sentence near the window end, while guaranteeing progress.
                for (int i = end - 1; i >= start + Math.max(overlap + 1, size / 2); i--) {
                    if ("。！？!?；;\n".indexOf(points[i]) >= 0 || (points[i] == '.' && (i + 1 == points.length || Character.isWhitespace(points[i + 1])))) { end = i + 1; break; }
                }
            }
            String chunk = new String(points, start, end - start).strip();
            if (!chunk.isEmpty()) chunks.add(chunk);
            if (end == points.length) break;
            start = Math.max(start + 1, end - overlap);
        }
        return List.copyOf(chunks);
    }
}
