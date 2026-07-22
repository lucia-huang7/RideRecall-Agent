package com.kinderuber.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import java.time.Duration;
import java.util.*;
import java.util.function.Function;

@Component
public class AgentProfiles {
    private final Map<AgentType, AgentProfile> profiles;
    private final ComposerSettings composer;
    public record ComposerSettings(String model, double temperature, int maxOutputTokens, Duration timeout) {
        public ComposerSettings {
            if (!Double.isFinite(temperature) || temperature < 0 || temperature > 2 || maxOutputTokens < 64 || maxOutputTokens > 8192
                    || timeout.isNegative() || timeout.isZero() || timeout.compareTo(Duration.ofSeconds(60)) > 0)
                throw new IllegalArgumentException("Invalid response composer parameters");
        }
    }
    @org.springframework.beans.factory.annotation.Autowired
    public AgentProfiles(Environment environment) { this(environment::getProperty); }
    private AgentProfiles(Function<String, String> configuration) {
        Map<AgentType, AgentProfile> resolved = new EnumMap<>(AgentType.class);
        try (var input = AgentProfiles.class.getResourceAsStream("/agents/profiles.json")) {
            if (input == null) throw new IllegalStateException("Missing agent role profiles");
            for (var profile : new ObjectMapper().readValue(input, AgentProfile[].class)) {
                String name = profile.agentType().name();
                resolved.put(profile.agentType(), profile.configured(value(configuration, name, "MODEL", profile.model()),
                        Double.parseDouble(value(configuration, name, "TEMPERATURE", String.valueOf(profile.temperature()))),
                        Integer.parseInt(value(configuration, name, "MAX_TOKENS", String.valueOf(profile.maxOutputTokens())))));
            }
        } catch (Exception ex) { throw new IllegalArgumentException("Failed to load agent role configuration", ex); }
        if (!resolved.keySet().equals(EnumSet.allOf(AgentType.class))) throw new IllegalArgumentException("Missing agent profile");
        profiles = Collections.unmodifiableMap(resolved);
        composer = new ComposerSettings(value(configuration, "COMPOSER", "MODEL", ""),
                Double.parseDouble(value(configuration, "COMPOSER", "TEMPERATURE", "0.1")),
                Integer.parseInt(value(configuration, "COMPOSER", "MAX_TOKENS", "1000")),
                Duration.ofSeconds(Long.parseLong(value(configuration, "COMPOSER", "TIMEOUT_S", "8"))));
    }
    public static AgentProfiles defaults() { return new AgentProfiles(key -> null); }
    private String value(Function<String, String> configuration, String role, String setting, String fallback) {
        String property = switch (setting) { case "MAX_TOKENS" -> "max-output-tokens"; case "TIMEOUT_S" -> "timeout-seconds"; default -> setting.toLowerCase(Locale.ROOT); };
        String value = configuration.apply("kinderuber.agents." + role.toLowerCase(Locale.ROOT) + "." + property);
        if (value == null) value = configuration.apply("KINDER_UBER_" + role + "_" + setting);
        return value == null || value.isBlank() ? fallback : value.strip();
    }
    public AgentProfile forType(AgentType type) { return profiles.get(type); }
    public ComposerSettings composer() { return composer; }
    public String fingerprint() { return com.kinderuber.util.Hashing.key(profiles.toString(), composer.toString()); }
}
