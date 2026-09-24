package com.llmagentsystem.tool;

import java.time.Duration;
import java.time.Instant;

/** Each admitted call carries a generation; stale completions cannot reset a newer circuit. */
public class CircuitBreaker {
    public record Permit(long generation, boolean probe) {}
    private final int threshold;
    private final Duration recovery;
    private CircuitState state = CircuitState.CLOSED;
    private int failures;
    private Instant openedAt;
    private long generation;
    public CircuitBreaker(int threshold, Duration recovery) { this.threshold = threshold; this.recovery = recovery; }
    public synchronized Permit acquire() {
        if (state == CircuitState.CLOSED) return new Permit(generation, false);
        if (state == CircuitState.OPEN && !Instant.now().isBefore(openedAt.plus(recovery))) {
            state = CircuitState.HALF_OPEN;
            return new Permit(generation, true);
        }
        return null;
    }
    public synchronized void success(Permit permit) {
        if (permit == null || permit.generation != generation) return;
        failures = 0;
        if (permit.probe) { state = CircuitState.CLOSED; openedAt = null; generation++; }
    }
    public synchronized void failure(Permit permit) {
        if (permit == null || permit.generation != generation) return;
        if (++failures >= threshold || permit.probe) {
            state = CircuitState.OPEN;
            openedAt = Instant.now();
            generation++;
        }
    }
    public synchronized CircuitState state() { return state; }
}
