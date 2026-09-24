import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  loadAgentMode,
  saveAgentMode,
  analysisPayload,
} from "../lib/agentMode.js";
import { loadTheme, saveTheme } from "../lib/theme.js";
import { roleHome, allowsRoute } from "../lib/auth.js";
import {
  request,
  money,
  statusLabel,
  downloadEvidence,
  latestCase,
  mergeCaseSummaries,
} from "../lib/rideApi.js";
import { useRoute, useRouter } from "vue-router";
export function useRideWorkspace(props, emit) {
  const identity = computed(() => props.identity);
  function signOut() {
    emit("sign-out");
  }

  const isDemo = computed(() => props.identity.auth_mode === "demo");
  const canRider = computed(() => props.identity.roles.includes("rider"));
  const canSpecialist = computed(() =>
    props.identity.roles.includes("specialist"),
  );
  const canOperator = computed(() => props.identity.roles.includes("operator"));
  const homePath = computed(() => roleHome(props.identity.roles));
  const denied = computed(
    () => !isDemo.value && !allowsRoute(props.identity.roles, route.value.page),
  );
  const theme = ref(loadTheme());
  function setTheme(value) {
    theme.value = saveTheme(value);
  }
  function syncTheme() {
    setTheme(loadTheme());
  }
  const trips = ref([]),
    cases = ref([]),
    currentCase = ref(null),
    queue = ref([]),
    selectedAgentId = ref(""),
    loading = ref(true),
    busy = ref(false),
    error = ref(""),
    notice = ref(""),
    sheet = ref(""),
    sheetEl = ref(null);
  const casesTotal = ref(0),
    casesNext = ref(null),
    activeCaseCount = ref(0),
    featuredActive = ref(null),
    tripCaseRecord = ref(null),
    queueTotal = ref(0),
    queueNext = ref(null),
    agentOptions = ref([]);
  const description = ref(""),
    topic = ref("other"),
    draft = ref(""),
    evidenceNote = ref(""),
    staffReply = ref("");
  const agentMode = ref(loadAgentMode());
  watch(agentMode, saveAgentMode);
  const engineStatus = ref({ mode: "demo" }),
    evaluationReport = ref(null);
  const latestRun = computed(() => currentCase.value?.engine_runs?.at(-1));
  const routerRoute = useRoute();
  const router = useRouter();
  const route = computed(() => ({
    page: routerRoute.meta.page || "trips",
    id: routerRoute.params.id,
  }));
  const topics = [
    {
      id: "cancellation",
      label: "Cancellation fee",
      text: "My driver never picked me up, but I was charged a cancellation fee. I would like this reviewed.",
    },
    {
      id: "fare",
      label: "Unexpected fare",
      text: "My final fare was higher than the estimate. Please check the difference.",
    },
    {
      id: "payment",
      label: "Payment issue",
      text: "I see two amounts on my statement. Can you check whether I was charged twice?",
    },
    { id: "other", label: "Something else", text: "" },
  ];
  let generation = 0,
    timer,
    toastTimer,
    previousFocus;

  function go(path) {
    router.push(path);
  }
  function back() {
    if (route.value.page === "chat") openCase(route.value.id);
    else if (route.value.page === "new") go("/trip/" + route.value.id);
    else if (route.value.page === "case") go("/help");
    else go("/trips");
  }
  const trip = computed(() =>
    trips.value.find(
      (t) =>
        t.id ===
        (["case", "chat"].includes(route.value.page)
          ? currentCase.value?.trip_id
          : route.value.id),
    ),
  );
  const activeCases = computed(() =>
    featuredActive.value ? [featuredActive.value] : [],
  );
  const tripCase = computed(() =>
    tripCaseRecord.value?.trip_id === trip.value?.id &&
    tripCaseRecord.value.status !== "resolved"
      ? tripCaseRecord.value
      : cases.value.find(
          (c) => c.trip_id === trip.value?.id && c.status !== "resolved",
        ),
  );
  const agentCase = ref(null);
  const nextStep = computed(() => {
    const c = currentCase.value;
    if (!c) return {};
    return (
      {
        needs_review: {
          title: "Your side matters.",
          text: "The records do not tell the whole story. Add evidence or ask a specialist to review the conflict.",
        },
        awaiting_confirmation: {
          title: "A fairer fare is ready.",
          text: "Review the proposed adjustment. Nothing changes until you confirm.",
        },
        waiting_for_agent: {
          title: "You are in the review queue.",
          text: "Your full case is attached. No specialist has accepted it yet. Try the Agent desk to continue this simulation.",
        },
        in_review: {
          title: "Alex has your case.",
          text: "Your simulated specialist can see the complete history. You can keep adding information.",
        },
        awaiting_rider: {
          title: "One charge. Two records.",
          text: "A pending authorization is separate from a settled payment. Tell us if your statement shows something different.",
        },
        resolved: {
          title: c.adjustment
            ? `${money(c.adjustment.amount)} demo adjustment recorded.`
            : "Your case has an outcome.",
          text: c.adjustment
            ? "No real money moved. Your full history remains available."
            : "Read the explanation in your conversation. You can request another review if needed.",
        },
      }[c.status] || {
        title: "Review in progress",
        text: "Your information is saved.",
      }
    );
  });
  function time(value) {
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  }
  function toast(text) {
    notice.value = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (notice.value = ""), 3500);
  }
  async function run(fn) {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    try {
      await fn();
    } catch (e) {
      const message =
        e.name === "TimeoutError"
          ? "The request timed out. Retry to safely check the same operation."
          : e.message;
      if ([409, 412, 428].includes(e.status)) await refresh();
      error.value = message;
    } finally {
      busy.value = false;
    }
  }
  async function runEvaluation() {
    sheet.value = "evaluation";
    evaluationReport.value = null;
    await run(async () => {
      evaluationReport.value = await request("/api/agent/evaluations", {
        agent: true,
        body: {},
      });
    });
  }
  let queueGeneration = 0,
    agentGeneration = 0;
  function adoptCase(record, force = false) {
    if (force || route.value.id === record.id)
      currentCase.value = latestCase(currentCase.value, record);
  }
  function adoptIndex(page, append = false) {
    cases.value = append
      ? mergeCaseSummaries(cases.value, page.items)
      : page.items;
    casesNext.value = page.next_cursor;
    casesTotal.value = page.total;
    activeCaseCount.value = page.active_total;
    featuredActive.value = page.featured_active;
  }
  async function loadCases() {
    adoptIndex(await request("/api/cases/index"));
  }
  async function loadMoreCases() {
    await run(async () => {
      if (casesNext.value)
        adoptIndex(
          await request(
            "/api/cases/index?cursor=" + encodeURIComponent(casesNext.value),
          ),
          true,
        );
    });
  }
  async function loadAgentDetail(id) {
    const token = ++agentGeneration;
    const detail = await request("/api/agent/cases/" + id, { agent: true });
    if (token !== agentGeneration || selectedAgentId.value !== id) return;
    if (
      agentCase.value?.id === id &&
      (agentCase.value.revision ?? 0) > (detail.case.revision ?? 0)
    )
      return;
    agentCase.value = detail.case;
    agentOptions.value = detail.available_actions;
  }
  async function selectAgent(id) {
    await run(async () => {
      selectedAgentId.value = id;
      agentCase.value = null;
      agentOptions.value = [];
      await loadAgentDetail(id);
    });
  }
  async function loadQueue({ reset = false, more = false } = {}) {
    const token = ++queueGeneration;
    const cursor = more ? queueNext.value : null;
    if (more && !cursor) return;
    const page = await request(
      "/api/agent/cases/index" +
        (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""),
      { agent: true },
    );
    if (token !== queueGeneration) return;
    const initial = !queue.value.length;
    queue.value = reset
      ? page.items
      : mergeCaseSummaries(queue.value, page.items);
    queueTotal.value = page.total;
    if (reset || more || initial) queueNext.value = page.next_cursor;
    if (!selectedAgentId.value && queue.value.length)
      selectedAgentId.value = queue.value[0].id;
    if (selectedAgentId.value) await loadAgentDetail(selectedAgentId.value);
  }

  async function refresh() {
    const token = ++generation;
    loading.value = true;
    error.value = "";
    try {
      if (denied.value) return;
      const [t, c, h, linked] = await Promise.all([
        canRider.value ? request("/api/trips") : [],
        canRider.value ? request("/api/cases/index") : null,
        request("/health"),
        canRider.value && ["trip", "new"].includes(route.value.page)
          ? request(
              "/api/cases/index?limit=1&trip_id=" +
                encodeURIComponent(route.value.id),
            )
          : null,
      ]);
      if (token !== generation) return;
      trips.value = t;
      if (c) adoptIndex(c);
      tripCaseRecord.value = linked?.items[0] || null;
      engineStatus.value = {
        ...h,
        mode: h.mode || props.identity.model_mode || "demo",
      };
      if (["case", "chat"].includes(route.value.page)) {
        const detail = await request("/api/cases/" + route.value.id);
        if (token === generation) adoptCase(detail);
      }
      if (route.value.page === "agent" && canSpecialist.value)
        await loadQueue({ reset: true });
    } catch (e) {
      if (token === generation) error.value = e.message;
    } finally {
      if (token === generation) loading.value = false;
    }
  }
  async function start(t) {
    await run(async () => {
      const page = await request(
        "/api/cases/index?limit=1&trip_id=" + encodeURIComponent(t.id),
      );
      const existing = page.items[0];
      if (existing) {
        openCase(existing.id);
        return;
      }
      topic.value = t.issue;
      description.value = topics.find((o) => o.id === t.issue).text;
      go("/new/" + t.id);
    });
  }
  function selectTopic(option) {
    topic.value = option.id;
    description.value = option.text;
  }
  function openCase(id) {
    go("/case/" + id);
  }
  async function createCase(human = false) {
    if (description.value.trim().length < 5) {
      error.value = "Please describe what happened before continuing.";
      return;
    }
    await run(async () => {
      let c = await request("/api/cases", {
        body: analysisPayload(
          {
            trip_id: trip.value.id,
            description: description.value,
            topic: topic.value,
          },
          agentMode.value,
        ),
      });
      if (human)
        c = await request("/api/cases/" + c.id + "/actions", {
          body: { action: "handoff" },
          revision: c.revision,
        });
      adoptCase(c, true);
      await loadCases();
      openCase(c.id);
    });
  }
  async function act(action, text = "") {
    await run(async () => {
      adoptCase(
        await request("/api/cases/" + currentCase.value.id + "/actions", {
          body: analysisPayload({ action, text }, agentMode.value),
          revision: currentCase.value.revision,
        }),
      );
      sheet.value = "";
      await loadCases();
      await scrollChat();
    });
  }
  async function sendMessage() {
    const text = draft.value.trim();
    if (!text) return;
    await act("message", text);
    if (!error.value) draft.value = "";
  }
  async function addNote() {
    await act("evidence", evidenceNote.value);
    if (!error.value) {
      evidenceNote.value = "";
      toast("Your evidence is saved to this case.");
    }
  }
  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 3 * 1024 * 1024) {
      error.value = "Choose an attachment smaller than 3 MB.";
      event.target.value = "";
      return;
    }
    await run(async () => {
      adoptCase(
        await request("/api/cases/" + currentCase.value.id + "/attachments", {
          file,
          revision: currentCase.value.revision,
        }),
      );
      sheet.value = "";
      toast("Attachment saved locally.");
    });
    event.target.value = "";
  }
  async function download(item, agent = false) {
    await run(() =>
      downloadEvidence(
        agent ? agentCase.value.id : currentCase.value.id,
        item,
        agent,
      ),
    );
  }
  async function staffAct(action) {
    await run(async () => {
      await request("/api/agent/cases/" + agentCase.value.id + "/actions", {
        agent: true,
        body: { action, text: staffReply.value },
        revision: agentCase.value.revision,
      });
      staffReply.value = "";
      await loadQueue();
      toast(
        action === "accept"
          ? "Case accepted. Full context is available."
          : "Your response is saved.",
      );
    });
  }
  async function scrollChat() {
    await nextTick();
    document
      .querySelector(".messages")
      ?.scrollTo({ top: 999999, behavior: "smooth" });
  }
  async function navigate() {
    sheet.value = "";
    currentCase.value = null;
    error.value = "";
    await refresh();
    if (route.value.page === "chat") await scrollChat();
    else window.scrollTo(0, 0);
  }
  function trapFocus(event) {
    const els = [
      ...sheetEl.value.querySelectorAll(
        'button:not(:disabled),input:not(:disabled),textarea,a[href],[tabindex="0"]',
      ),
    ].filter((e) => e.getClientRects().length);
    if (!els.length) return;
    const first = els[0],
      last = els.at(-1);
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === sheetEl.value)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  watch(sheet, async (value) => {
    if (value) {
      previousFocus = document.activeElement;
      await nextTick();
      sheetEl.value?.focus();
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
      previousFocus?.focus();
    }
  });
  onMounted(async () => {
    window.addEventListener("storage", syncTheme);

    await refresh();
    if (route.value.page === "chat") await scrollChat();
    timer = setInterval(async () => {
      if (busy.value || sheet.value || denied.value) return;
      try {
        if (route.value.page === "agent") await loadQueue();
        else if (["case", "chat"].includes(route.value.page)) {
          const id = route.value.id;
          const c = await request("/api/cases/" + id);
          if (route.value.id === id) adoptCase(c);
        }
      } catch {
        /* Foreground requests surface actionable errors. */
      }
    }, 4000);
  });
  onUnmounted(() => {
    window.removeEventListener("storage", syncTheme);

    clearInterval(timer);
    clearTimeout(toastTimer);
    document.body.style.overflow = "";
  });

  watch(() => routerRoute.fullPath, navigate);
  return {
    identity,
    signOut,
    isDemo,
    canRider,
    canSpecialist,
    canOperator,
    homePath,
    denied,
    theme,
    setTheme,
    syncTheme,
    trips,
    cases,
    currentCase,
    queue,
    selectedAgentId,
    loading,
    busy,
    error,
    notice,
    sheet,
    sheetEl,
    casesTotal,
    casesNext,
    activeCaseCount,
    featuredActive,
    tripCaseRecord,
    queueTotal,
    queueNext,
    agentOptions,
    description,
    topic,
    draft,
    evidenceNote,
    staffReply,
    agentMode,
    engineStatus,
    evaluationReport,
    latestRun,
    route,
    topics,
    go,
    back,
    trip,
    activeCases,
    tripCase,
    agentCase,
    nextStep,
    time,
    toast,
    run,
    runEvaluation,
    queueGeneration,
    agentGeneration,
    adoptCase,
    adoptIndex,
    loadCases,
    loadMoreCases,
    loadAgentDetail,
    selectAgent,
    loadQueue,
    refresh,
    start,
    selectTopic,
    openCase,
    createCase,
    act,
    sendMessage,
    addNote,
    upload,
    download,
    staffAct,
    scrollChat,
    navigate,
    trapFocus,
  };
}
