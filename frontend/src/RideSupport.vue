<template>
  <div class="ride-layout">
    <aside class="desktop-intro">
      <a :href="homePath" class="wordmark">RideRecall-Agent</a>
      <div>
        <p class="eyebrow">SUPPORT, WITHOUT THE RUNAROUND</p>
        <h1>A better way<br />forward.</h1>
        <p>One trip. One conversation.<br />A clear path to resolution.</p>
        <div class="intro-points">
          <span><Icon name="check" /> Your context stays with you</span
          ><span><Icon name="check" /> Every decision has a reason</span
          ><span><Icon name="check" /> A human, when you need one</span>
        </div>
      </div>
      <div class="desktop-foot">
        <span class="demo-dot"></span>
        {{
          isDemo ? "Local demo · No API key needed" : "Authenticated workspace"
        }}
        <a v-if="canSpecialist" href="#/agent"
          >Open Agent desk <Icon name="northeast"
        /></a>
      </div>
    </aside>

    <main
      class="phone-app"
      :class="{
        'chat-screen': route.page === 'chat',
        'desk-screen': route.page === 'agent',
      }"
    >
      <header class="app-header">
        <button
          v-if="!['trips', 'help', 'account', 'agent'].includes(route.page)"
          class="icon-button"
          aria-label="Go back"
          @click="back"
        >
          <Icon name="chevron" class="reverse" />
        </button>
        <a v-else class="mobile-brand" :href="homePath">RideRecall-Agent</a>
        <span
          v-if="!['trips', 'help', 'account', 'agent'].includes(route.page)"
          class="header-label"
          >{{
            route.page === "chat"
              ? "Your conversation"
              : route.page === "new"
                ? "Trip support"
                : route.page === "trip"
                  ? "Trip details"
                  : route.page === "manage"
                    ? "Control room"
                    : "Case details"
          }}</span
        >
        <div class="header-actions">
          <button
            class="theme-toggle"
            :aria-pressed="theme === 'rainbow'"
            aria-label="Toggle Rainbow Mode"
            :title="
              theme === 'rainbow'
                ? 'Switch to black and white'
                : 'Switch to Rainbow Mode'
            "
            @click="setTheme(theme === 'rainbow' ? 'mono' : 'rainbow')"
          >
            <span class="theme-swatch" aria-hidden="true"></span></button
          ><button
            class="avatar"
            aria-label="Open your profile"
            @click="go('/account')"
          >
            {{ identity.subject?.[0]?.toUpperCase() || "J" }}
          </button>
        </div>
      </header>
      <div class="demo-bar">
        <span class="demo-dot"></span>
        {{
          engineStatus.mode === "live"
            ? "LIVE MODEL · DEMO DATA"
            : "LOCAL ENGINE · DEMO DATA"
        }}
        <span>Fictional trips. No real payments.</span>
      </div>
      <div v-if="error" class="error-banner" role="alert">
        <Icon name="alert" /><span>{{ error }}</span
        ><button aria-label="Dismiss error" @click="error = ''">×</button>
      </div>
      <div v-if="notice" class="toast" role="status">{{ notice }}</div>

      <div v-if="denied" class="empty-state">
        <Icon name="alert" />
        <h2>Access restricted</h2>
        <p>This page requires a different account role.</p>
        <button class="primary" @click="go('/account')">View account</button>
      </div>
      <div
        v-else-if="
          loading &&
          ((canRider && !trips.length) ||
            (['case', 'chat'].includes(route.page) && !currentCase))
        "
        class="empty-state"
      >
        <span class="spinner"></span>
        <h2>Getting things ready</h2>
        <p>Connecting to your local support service.</p>
      </div>
      <div
        v-else-if="
          canRider &&
          !trips.length &&
          !['account', 'agent', 'manage'].includes(route.page)
        "
        class="empty-state"
      >
        <Icon name="alert" />
        <h2>Let's reconnect</h2>
        <p>Your trips could not be loaded.</p>
        <button class="primary" @click="refresh">Try again</button>
      </div>

      <RouterView v-else />

      <nav
        v-if="route.page !== 'chat'"
        class="bottom-nav"
        aria-label="Main navigation"
      >
        <button
          v-if="canRider"
          :class="{ active: ['trips', 'trip', 'new'].includes(route.page) }"
          @click="go('/trips')"
        >
          <Icon name="route" /><span>Trips</span></button
        ><button
          v-if="canRider"
          :class="{ active: ['help', 'case'].includes(route.page) }"
          @click="go('/help')"
        >
          <Icon name="chat" /><span
            >Help<span v-if="activeCaseCount" class="nav-count">{{
              activeCaseCount
            }}</span></span
          ></button
        ><button
          :class="{
            active: ['account', 'agent', 'manage'].includes(route.page),
          }"
          @click="go('/account')"
        >
          <Icon name="sliders" /><span>Account</span>
        </button>
      </nav>
    </main>

    <div
      v-if="sheet"
      class="sheet-backdrop"
      @click.self="sheet = ''"
      @keydown.esc="sheet = ''"
    >
      <section
        ref="sheetEl"
        class="bottom-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sheet-title"
        tabindex="-1"
        @keydown.tab="trapFocus"
      >
        <div class="sheet-handle"></div>
        <button
          class="sheet-close icon-button"
          aria-label="Close dialog"
          @click="sheet = ''"
        >
          <Icon name="close" />
        </button>
        <template v-if="sheet === 'trips'"
          ><p class="eyebrow">LET'S START HERE</p>
          <h2 id="sheet-title">Which trip?</h2>
          <button
            v-for="t in trips"
            :key="t.id"
            class="case-list-item"
            @click="
              sheet = '';
              start(t);
            "
          >
            <Icon name="route" />
            <div>
              <strong>{{ t.destination }}</strong
              ><small>{{ t.date }} · {{ money(t.total) }}</small>
            </div>
            <Icon name="chevron" /></button
        ></template>
        <template v-else-if="sheet === 'evidence'"
          ><p class="eyebrow">KEEP IT ALL TOGETHER</p>
          <h2 id="sheet-title">Add evidence</h2>
          <p>
            A note, screenshot, or receipt can help a specialist understand what
            happened.
          </p>
          <label class="field-label" for="evidenceNote">Your note</label
          ><textarea
            id="evidenceNote"
            v-model="evidenceNote"
            rows="4"
            maxlength="4000"
            placeholder="What should we know?"
          ></textarea
          ><button
            class="primary"
            :disabled="busy || !evidenceNote.trim()"
            @click="addNote"
          >
            Save note <Icon name="plus" /></button
          ><label class="upload-button"
            ><Icon name="upload" />{{
              busy ? "Uploading…" : "Attach a screenshot or document"
            }}<input
              type="file"
              accept="image/png,image/jpeg,application/pdf"
              :disabled="busy"
              @change="upload"
          /></label>
          <p class="footnote">
            PNG, JPEG, or PDF · Up to 3 MB · Saved locally. Use fictional
            information.
          </p></template
        >
        <template v-else-if="sheet === 'policy'"
          ><p class="eyebrow">TRANSPARENT BY DESIGN</p>
          <h2 id="sheet-title">{{ currentCase.policy.title }}</h2>
          <span class="status-pill">{{ currentCase.policy.version }}</span>
          <p class="policy-copy">{{ currentCase.policy.text }}</p>
          <div v-if="isDemo" class="agent-note">
            <Icon name="book" />
            <p>
              This is a fictional policy written for this project. It is not
              Uber policy or a promise of eligibility.
            </p>
          </div></template
        >
        <template v-else-if="sheet === 'confirm' && currentCase.proposal"
          ><p class="eyebrow">YOU'RE IN CONTROL</p>
          <h2 id="sheet-title">Review your adjustment</h2>
          <div class="adjustment-amount">
            {{ money(currentCase.proposal.amount) }}
          </div>
          <p>{{ currentCase.proposal.reason }}</p>
          <button class="primary" :disabled="busy" @click="act('confirm')">
            {{ busy ? "Recording…" : "Confirm adjustment"
            }}<Icon name="check" /></button
          ><button class="secondary" @click="sheet = ''">Not now</button>
          <div v-if="isDemo" class="agent-note">
            <Icon name="alert" />
            <p>
              Confirming records a simulated adjustment. No money moves and Uber
              is not contacted.
            </p>
          </div>
        </template>
        <template v-else-if="sheet === 'trace'"
          ><p class="eyebrow">ACTUAL EXECUTION RECORD</p>
          <h2 id="sheet-title">How this review worked</h2>
          <template v-if="latestRun"
            ><span class="status-pill"
              >{{
                latestRun.mode === "demo"
                  ? "Offline model adapter"
                  : "Live model"
              }}
              · {{ latestRun.status }}</span
            >
            <p>
              {{
                latestRun.agent_mode === "multi"
                  ? "Optional multi-agent routing"
                  : latestRun.agent_mode === "single"
                    ? "Single-agent support"
                    : "Historical agent configuration"
              }}
              ran with intent recognition, policy retrieval and durable case
              context. Offline mode simulates model outputs; it does not
              simulate tool execution.
            </p>
            <h3>Intent fusion</h3>
            <div class="trace-row">
              <strong>{{ latestRun.intent }}</strong
              ><span>{{
                latestRun.embedding_source || "Local vector branch"
              }}</span>
            </div>
            <div
              v-for="(score, source) in latestRun.source_scores"
              :key="source"
              class="trace-row"
            >
              <span>{{
                source === "llm"
                  ? latestRun.mode === "demo"
                    ? "Simulated model branch"
                    : "Model branch"
                  : source === "embedding"
                    ? "Local vector branch"
                    : "Pattern branch"
              }}</span
              ><strong>{{ Number(score).toFixed(3) }}</strong>
            </div>
            <p class="footnote">
              Fusion scores are not calibrated probabilities or accuracy
              measurements.
            </p>
            <h3>Roles actually executed</h3>
            <span v-for="a in latestRun.agents" :key="a" class="role-chip">{{
              latestRun.roles[a] || a
            }}</span>
            <p>{{ latestRun.routing_reason }}</p>
            <h3>Executed tool calls</h3>
            <div
              v-for="(t, index) in latestRun.tools"
              :key="index"
              class="trace-row"
            >
              <code>{{ t.tool_name || t.event }}</code
              ><span
                >{{ t.agent_type }} ·
                {{
                  t.success && t.result_success !== false
                    ? "Success"
                    : "Needs attention"
                }}
                · {{ t.latency_ms }} ms</span
              >
            </div>
            <h3>Retrieval passes</h3>
            <div
              v-for="(q, index) in latestRun.retrievals"
              :key="index"
              class="trace-row"
            >
              <span>{{ q.scope }} · {{ q.documents }} sources</span
              ><strong>{{
                q.reranked ? "Rewritten + reranked" : "Retrieved"
              }}</strong>
            </div>
            <h3>Retrieved sources</h3>
            <details
              v-for="d in latestRun.citations"
              :key="d.document_id + ':' + d.chunk"
              class="source-record"
            >
              <summary>{{ d.title }}</summary>
              <p>{{ d.content }}</p>
              <code>{{ d.document_id }}:{{ d.chunk }}</code
              ><small
                >Version {{ d.version.slice(0, 12) }} · Similarity
                {{ d.score }}</small
              >
            </details>
            <template v-if="latestRun.memory"
              ><h3>Case memory</h3>
              <p>
                {{ latestRun.memory.scope }} ·
                {{ latestRun.memory.recent_count }} recent messages ·
                {{ latestRun.memory.episodic_count }} retrieved episodes
              </p>
              <p class="footnote">
                {{ latestRun.memory.backend }}. {{ latestRun.memory.mode }}.
                Rider claims remain unverified.
              </p>
              <h3>Saved conversation summary</h3>
              <p>
                {{
                  latestRun.memory.summary ||
                  "Compression starts after 15 messages. Recent context is already available."
                }}
              </p>
              <h3>Tracked rider information</h3>
              <div
                v-for="(values, key) in latestRun.memory.state.entities"
                :key="key"
                class="trace-row"
              >
                <span>{{
                  key === "order_id" ? "Linked trip" : key.replaceAll("_", " ")
                }}</span
                ><strong>{{ values.join(", ") || "Not provided" }}</strong>
              </div>
              <p class="footnote">
                Amounts above are rider claims, not verified charges. The linked
                trip stays fixed.
              </p></template
            >
            <h3>Loaded Skills</h3>
            <p>{{ latestRun.skills.join(", ") }}</p>
            <p class="footnote">
              {{ latestRun.model }} · {{ latestRun.latency_ms }} ms ·
              {{ currentCase.engine_runs.length }} saved runs
            </p></template
          >
          <p v-else>
            This older case has no integrated analysis yet. Send a new message
            in the conversation to run the support pipeline.
          </p>
          <button
            v-if="canOperator"
            class="secondary"
            :disabled="busy"
            @click="runEvaluation"
          >
            Run pipeline checks <Icon name="chart" /></button
        ></template>
        <template v-else-if="sheet === 'evaluation'"
          ><p class="eyebrow">PIPELINE CHECKS</p>
          <h2 id="sheet-title">Verify the execution.</h2>
          <p v-if="busy">
            Running single-turn and multi-turn memory, correction, isolation,
            and handoff checks…
          </p>
          <template v-else-if="evaluationReport"
            ><span class="status-pill"
              >{{ evaluationReport.passed }} /
              {{ evaluationReport.total }} integration cases passed</span
            >
            <p>{{ evaluationReport.caveat }}</p>
            <p v-if="evaluationReport.skipped" class="footnote">
              {{ evaluationReport.skipped }} checks are not applicable to this
              catalog. No result was inferred for missing trip categories.
            </p>
            <div
              v-for="item in evaluationReport.cases"
              :key="item.name"
              class="evaluation-row"
            >
              <strong>{{ item.name }}</strong
              ><span>{{
                item.status === "not_applicable"
                  ? "Not applicable"
                  : item.passed
                    ? "Passed"
                    : "Needs attention"
              }}</span
              ><small
                >{{ item.intent }} · {{ item.agents.join(" + ") }} ·
                {{ item.sources }} sources</small
              >
            </div>
            <p class="footnote">
              IntentEvaluator · Fixture accuracy
              {{ (evaluationReport.intent_metrics.accuracy * 100).toFixed(0) }}%
              · This is not a real-world quality claim.
            </p>
            <button class="secondary" @click="runEvaluation">
              Run again <Icon name="refresh" /></button
          ></template>
          <p v-else>
            Unable to complete the checks. Close this dialog to see the error.
          </p></template
        >
        <template v-else
          ><p class="eyebrow">INDEPENDENT PROTOTYPE</p>
          <h2 id="sheet-title">A clearer path to resolution.</h2>
          <p>
            A ride-support workspace for disputes, evidence and specialist
            handoff. Not affiliated with Uber. Single agent with durable case
            context is the default. Multi-agent routing is optional; offline
            experiments found no task-completion gain. Offline mode runs with a
            deterministic local model adapter. Live model mode is opt-in.
          </p>
          <p>
            Cases and attachments persist in a local SQLite database. Demo
            session isolation and a public Agent desk are for role-play only,
            not production security.
          </p></template
        >
      </section>
    </div>
  </div>
</template>
<script setup>
import { provide } from "vue";
import { RouterView } from "vue-router";
import Icon from "./components/AppIcon.vue";
import { money } from "./lib/rideApi.js";
import { useRideWorkspace } from "./composables/useRideWorkspace.js";
const props = defineProps({ identity: { type: Object, required: true } });
const emit = defineEmits(["sign-out"]);
const workspace = useRideWorkspace(props, emit);
provide("rideWorkspace", workspace);
const {
  isDemo,
  canRider,
  canSpecialist,
  canOperator,
  homePath,
  denied,
  theme,
  setTheme,
  trips,
  currentCase,
  loading,
  busy,
  error,
  notice,
  sheet,
  sheetEl,
  activeCaseCount,
  evidenceNote,
  engineStatus,
  evaluationReport,
  latestRun,
  route,
  go,
  back,
  runEvaluation,
  refresh,
  start,
  act,
  addNote,
  upload,
  trapFocus,
} = workspace;
</script>
