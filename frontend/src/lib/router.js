import { createRouter, createWebHashHistory } from "vue-router";
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/trips" },
    {
      path: "/trips",
      component: () => import("../views/TripsView.vue"),
      meta: { page: "trips" },
    },
    {
      path: "/trip/:id",
      component: () => import("../views/TripDetails.vue"),
      meta: { page: "trip" },
    },
    {
      path: "/new/:id",
      component: () => import("../views/NewCase.vue"),
      meta: { page: "new" },
    },
    {
      path: "/case/:id",
      component: () => import("../views/CaseDetails.vue"),
      meta: { page: "case" },
    },
    {
      path: "/case/:id/chat",
      component: () => import("../views/CaseChat.vue"),
      meta: { page: "chat" },
    },
    {
      path: "/help",
      component: () => import("../views/HelpView.vue"),
      meta: { page: "help" },
    },
    {
      path: "/account",
      component: () => import("../views/AccountView.vue"),
      meta: { page: "account" },
    },
    {
      path: "/manage",
      component: () => import("../views/ControlRoom.vue"),
      meta: { page: "manage" },
    },
    {
      path: "/agent",
      component: () => import("../views/AgentDesk.vue"),
      meta: { page: "agent" },
    },
    { path: "/:pathMatch(.*)*", redirect: "/trips" },
  ],
});
