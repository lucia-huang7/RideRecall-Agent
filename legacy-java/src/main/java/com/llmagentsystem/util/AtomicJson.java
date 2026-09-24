package com.llmagentsystem.util;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.*;

public final class AtomicJson {
    private AtomicJson() {}
    public static void write(ObjectMapper mapper, Path path, Object value) throws IOException {
        Path target = path.toAbsolutePath();
        Files.createDirectories(target.getParent());
        Path temporary = Files.createTempFile(target.getParent(), ".llmagentsystem-", ".json");
        try {
            mapper.writerWithDefaultPrettyPrinter().writeValue(temporary.toFile(), value);
            try { Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); }
            catch (AtomicMoveNotSupportedException ex) { Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING); }
        } finally { Files.deleteIfExists(temporary); }
    }
}
