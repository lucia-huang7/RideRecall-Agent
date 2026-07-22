package com.kinderuber.util;

import java.time.Duration;
import java.util.concurrent.*;

public final class Tasks {
    private Tasks() {}
    // Virtual threads do not keep the application alive; per-operation budgets cancel the underlying task.
    private static final ExecutorService EXECUTOR = Executors.newVirtualThreadPerTaskExecutor();
    public static <T> T within(Duration timeout, Callable<T> operation) {
        Future<T> future = EXECUTOR.submit(operation);
        try { return future.get(Math.max(1, timeout.toMillis()), TimeUnit.MILLISECONDS); }
        catch (InterruptedException ex) { Thread.currentThread().interrupt(); throw new IllegalStateException("interrupted", ex); }
        catch (TimeoutException ex) { throw new IllegalStateException("timeout", ex); }
        catch (ExecutionException ex) {
            if (ex.getCause() instanceof RuntimeException runtime) throw runtime;
            throw new IllegalStateException(ex.getCause());
        } finally { if (!future.isDone()) future.cancel(true); }
    }
}
