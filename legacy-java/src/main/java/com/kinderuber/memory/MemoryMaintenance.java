package com.kinderuber.memory;

import org.springframework.stereotype.Service;
import jakarta.annotation.PreDestroy;
import java.util.*;
import java.util.concurrent.*;

/** One worker per user; submissions during a run remain pending for the next pass. */
@Service
public class MemoryMaintenance {
    private final MemoryManager memory;
    private final ThreadPoolExecutor executor = new ThreadPoolExecutor(2, 2, 30, TimeUnit.SECONDS, new ArrayBlockingQueue<>(64), r -> { Thread t = new Thread(r, "memory-maintenance"); t.setDaemon(true); return t; }, new ThreadPoolExecutor.AbortPolicy());
    private final Map<String, LinkedHashSet<String>> pending = new HashMap<>();
    private boolean closed;
    public MemoryMaintenance(MemoryManager memory) { this.memory = memory; }
    public synchronized void submit(String user, String conv) {
        if (closed) return;
        var conversations = pending.get(user);
        if (conversations != null) { if (conversations.size() >= 64 && !conversations.contains(conv)) conversations.removeFirst(); conversations.add(conv); return; }
        pending.put(user, new LinkedHashSet<>(List.of(conv)));
        try { executor.execute(() -> drain(user)); }
        catch (RejectedExecutionException ex) { pending.remove(user); }
    }
    private void drain(String user) {
        while (true) {
            String conv;
            synchronized (this) {
                var conversations = pending.get(user);
                if (closed || conversations == null || conversations.isEmpty()) { pending.remove(user); return; }
                conv = conversations.removeFirst();
            }
            try { memory.maintain(user, conv); }
            catch (RuntimeException ignored) { /* A failed update must not strand later submissions. */ }
        }
    }
    @PreDestroy public synchronized void close() { closed = true; pending.clear(); executor.shutdownNow(); }
}
