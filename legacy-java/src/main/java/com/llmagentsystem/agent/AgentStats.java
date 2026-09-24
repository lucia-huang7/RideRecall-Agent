package com.llmagentsystem.agent;

import java.time.*;
import java.util.*;

public class AgentStats {
    private record Sample(Instant time, boolean success, long latency) {}
    private final Deque<Sample> window = new ArrayDeque<>();
    private final Clock clock;
    private long total;
    private double penalty;
    private double fallbackThreshold = .5;
    private long cooldownSeconds = 60;
    private Instant retryAt = Instant.MIN;
    private String probe;
    public AgentStats() { this(Clock.systemUTC()); }
    AgentStats(Clock clock) { this.clock = clock; }
    private void prune() { while (!window.isEmpty() && (window.size() > 50 || window.getFirst().time.isBefore(clock.instant().minusSeconds(300)))) window.removeFirst(); }
    public synchronized void configure(double threshold, long cooldown) {
        if (!Double.isFinite(threshold) || threshold < .01 || threshold > .9 || cooldown < 1 || cooldown > 3600)
            throw new IllegalArgumentException("Invalid monitor recovery parameters");
        fallbackThreshold = threshold; cooldownSeconds = cooldown;
    }
    public synchronized boolean available() {
        prune(); return probe == null && !clock.instant().isBefore(retryAt);
    }
    public synchronized boolean admit(String requestId) {
        prune();
        if (probe != null) return false;
        if (clock.instant().isBefore(retryAt)) return false;
        if (!retryAt.equals(Instant.MIN)) probe = requestId;
        return true;
    }
    public synchronized void record(String requestId, boolean ok, long latency) {
        total++;
        boolean owner = requestId != null && requestId.equals(probe);
        if (owner) {
            probe = null;
            if (ok) { window.clear(); retryAt = Instant.MIN; penalty = 0; }
            else retryAt = clock.instant().plusSeconds(cooldownSeconds);
        }
        window.addLast(new Sample(clock.instant(), ok, Math.max(0, latency))); prune();
        if (retryAt.equals(Instant.MIN) && window.size() >= 5 && successRate() < 0.5) retryAt = clock.instant().plusSeconds(cooldownSeconds);
    }
    public void record(boolean ok, long latency) { record(null, ok, latency); }
    public synchronized long total() { return total; }
    public synchronized int windowSamples() { prune(); return window.size(); }
    public synchronized double successRate() { prune(); return window.isEmpty() ? 1 : window.stream().filter(Sample::success).count() / (double) window.size(); }
    public synchronized double avgLatencyMs() { prune(); return window.stream().mapToLong(Sample::latency).average().orElse(0); }
    public synchronized double routingScore() { return (successRate() * 0.7 + 0.3 / (1 + avgLatencyMs() / 1000)) * (1 - penalty); }
    public synchronized double monitorPenalty() { return penalty; }
    public synchronized void setMonitorPenalty(double value) {
        penalty = windowSamples() < 5 ? 0 : Math.min(0.9, Math.max(0, value));
        if (penalty >= fallbackThreshold && retryAt.equals(Instant.MIN)) retryAt = clock.instant().plusSeconds(cooldownSeconds);
    }
    public synchronized String recoveryState() { return probe != null ? "probing" : clock.instant().isBefore(retryAt) ? "cooldown" : retryAt.equals(Instant.MIN) ? "healthy" : "probe_ready"; }
}
