package com.kinderuber.evaluation;

import com.kinderuber.api.dto.EvalRunRequest;
import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;

@Service
public class EvaluationJobs {
    private static final class Job {
        final String owner;
        Job(String owner) { this.owner = owner; }
        final String id = UUID.randomUUID().toString();
        final Instant created = Instant.now();
        volatile String status = "queued", error = "";
        volatile Map<String, Object> progress = Map.of("completed", 0, "total", 0), result;
    }
    private final EndToEndEvaluator evaluator;
    private final ConversationEvaluator conversationEvaluator;
    private final Map<String, Job> jobs = new LinkedHashMap<>();
    private final ThreadPoolExecutor executor = new ThreadPoolExecutor(1, 1, 30, TimeUnit.SECONDS, new ArrayBlockingQueue<>(7), r -> { Thread t = new Thread(r, "evaluation-jobs"); t.setDaemon(true); return t; }, new ThreadPoolExecutor.AbortPolicy());
    public EvaluationJobs(EndToEndEvaluator evaluator, ConversationEvaluator conversationEvaluator) {
        this.evaluator = evaluator; this.conversationEvaluator = conversationEvaluator;
    }
    public Map<String, Object> submit(EvalRunRequest request, String owner) {
        return enqueue(owner, progress -> evaluator.run(request, progress));
    }
    public Map<String, Object> submitConversation(com.kinderuber.api.dto.ConversationEvalRequest request, String owner) {
        // Capture before queueing so concurrent chats cannot change what the user submitted for scoring.
        var snapshot = conversationEvaluator.prepare(request, owner);
        return enqueue(owner, progress -> conversationEvaluator.run(snapshot, progress));
    }
    private synchronized Map<String, Object> enqueue(String owner,
            java.util.function.Function<java.util.function.Consumer<Map<String, Object>>, Map<String, Object>> action) {
        Job job = new Job(owner);
        jobs.put(job.id, job);
        try {
            executor.execute(() -> {
                job.status = "running";
                try {
                    job.result = com.kinderuber.util.Tasks.within(Duration.ofMinutes(30), () -> action.apply(p -> job.progress = p));
                    job.status = "completed";
                } catch (Exception ex) { job.status = "failed"; job.error = ex.getClass().getSimpleName(); }
            });
        } catch (RejectedExecutionException ex) { jobs.remove(job.id); throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "评测队列已满"); }
        while (jobs.size() > 50) {
            String oldest = jobs.values().stream().filter(j -> Set.of("completed", "failed").contains(j.status)).map(j -> j.id).findFirst().orElse(null);
            if (oldest == null) break;
            jobs.remove(oldest);
        }
        return Map.of("job_id", job.id, "status", "queued");
    }
    public synchronized Map<String, Object> get(String id, String owner) {
        Job job = jobs.get(id);
        if (job == null || !Objects.equals(job.owner, owner)) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "评测任务不存在或已过期");
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("job_id", id); response.put("status", job.status); response.put("created_at", job.created.toString());
        response.put("progress", job.progress); response.put("error", job.error); response.put("result", job.result);
        return response;
    }
    @PreDestroy public void close() { executor.shutdownNow(); }
}
