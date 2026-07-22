package com.kinderuber.memory;

import org.junit.jupiter.api.Test;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MemoryMaintenanceTest {
    @Test void submissionDuringRunIsDrainedAndSameUserNeverRunsConcurrently() throws Exception {
        var memory=mock(MemoryManager.class); var entered=new CountDownLatch(1);var release=new CountDownLatch(1);var done=new CountDownLatch(2);
        var calls=new AtomicInteger();var active=new AtomicInteger();var max=new AtomicInteger();
        doAnswer(inv->{int n=active.incrementAndGet();max.accumulateAndGet(n,Math::max);try {if(calls.incrementAndGet()==1){entered.countDown();assertTrue(release.await(3,TimeUnit.SECONDS));}} finally{active.decrementAndGet();done.countDown();}return null;}).when(memory).maintain(anyString(),anyString());
        var maintenance=new MemoryMaintenance(memory);
        try {maintenance.submit("u","c");assertTrue(entered.await(3,TimeUnit.SECONDS));maintenance.submit("u","c");release.countDown();assertTrue(done.await(3,TimeUnit.SECONDS));assertEquals(2,calls.get());assertEquals(1,max.get());}
        finally {release.countDown();maintenance.close();}
    }
    @Test void laterConversationSurvivesFailedMaintenance() throws Exception {
        var memory=mock(MemoryManager.class);var entered=new CountDownLatch(1);var release=new CountDownLatch(1);var done=new CountDownLatch(1);
        doAnswer(inv->{entered.countDown();release.await(3,TimeUnit.SECONDS);throw new IllegalStateException("fixture");}).when(memory).maintain("u","first");
        doAnswer(inv->{done.countDown();return null;}).when(memory).maintain("u","second");
        var maintenance=new MemoryMaintenance(memory);
        try {maintenance.submit("u","first");assertTrue(entered.await(3,TimeUnit.SECONDS));maintenance.submit("u","second");release.countDown();assertTrue(done.await(3,TimeUnit.SECONDS));}
        finally {release.countDown();maintenance.close();}
    }
}
