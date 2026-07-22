package com.kinderuber.tool;

import com.kinderuber.util.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.util.*;

/** Shared by every specialist in one request. A durable marker is required before any write. */
public final class ToolExecutionGuard {
    @FunctionalInterface public interface BeforeWrite { void saveUnknownReceipt(String tool); }
    private final BeforeWrite before;
    private final Set<String> attempted = new HashSet<>();
    private boolean writeAttempted;
    public ToolExecutionGuard(BeforeWrite before) { this.before = before; }
    public static ToolExecutionGuard readOnly() { return new ToolExecutionGuard(null); }
    public boolean permitsWrites() { return before != null; }
    public synchronized boolean writeAttempted() { return writeAttempted; }
    public synchronized void beforeWrite(String name, Map<String, Object> args) throws Exception {
        if (before == null) throw new IllegalStateException("write_receipt_required");
        String operation = Hashing.key(name, new ObjectMapper().enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS).writeValueAsString(args));
        if (attempted.contains(operation)) throw new IllegalStateException("write_already_attempted");
        // Do not execute anything if the receipt cannot be persisted.
        before.saveUnknownReceipt(name);
        writeAttempted = true; attempted.add(operation);
    }
}
