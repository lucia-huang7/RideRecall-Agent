package com.llmagentsystem.util;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

public final class Hashing {
    private Hashing() {}
    public static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException ex) { throw new IllegalStateException(ex); }
    }
    public static String key(String... parts) {
        StringBuilder encoded = new StringBuilder();
        for (String part : parts) {
            String text = part == null ? "" : part;
            encoded.append(text.length()).append(':').append(text);
        }
        return sha256(encoded.toString());
    }
}
