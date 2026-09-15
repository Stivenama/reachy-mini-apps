/** Bootstrap: wire the shell header to the hash router and mount views on demand. */

import { ROUTES } from "./constants.js";
import { createRouter } from "./router.js";
import { hidePersonalityBadge, mountPersonalityBadge, showPersonalityBadge } from "./personality-badge.js";
import { $ } from "./ui.js";
import { mountHomeView } from "./views/home.js";
import { mountTalkView } from "./views/talk.js";
import { mountTimelineView } from "./views/timeline.js";
import { mountChatView } from "./views/chat.js";

function applyEmbeddedTheme() {
  const theme = new URLSearchParams(window.location.search).get("theme");
  if (theme !== "dark" && theme !== "light") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

function boot() {
  applyEmbeddedTheme();

  const outlet = $("#view-outlet");
  if (!outlet) {
    console.error("#view-outlet missing from index.html");
    return;
  }

  const router = createRouter(
    {
      [ROUTES.TALK]: (ctx) => mountTalkView(ctx),
      [ROUTES.PERSONALITIES]: (ctx) => mountHomeView({ ...ctx, navigate: router.navigate }),
      [ROUTES.TIMELINE]: (ctx) => mountTimelineView(ctx),
      [ROUTES.CHAT]: (ctx) => mountChatView(ctx),
    },
    { fallback: ROUTES.TALK, outlet, onRouteChange: syncNavForRoute }
  );

  const brand = $('[data-action="go-home"]');
  if (brand) {
    brand.addEventListener("click", (event) => {
      event.preventDefault();
      router.navigate(ROUTES.TALK);
    });
  }

  const personalityBadge = $('[data-action="open-personalities"]');
  if (personalityBadge) {
    personalityBadge.addEventListener("click", () => router.navigate(ROUTES.PERSONALITIES));
  }

  mountPersonalityBadge(document);

  function syncNavForRoute(route = router.currentRoute() || ROUTES.TALK) {
    const routeName = route.split("?")[0];
    document.querySelectorAll(".hub__nav-btn").forEach((button) => {
      const isActive = button.dataset.route === "talk" ? routeName === ROUTES.TALK : button.getAttribute("href") === routeName;
      button.classList.toggle("is-active", isActive);
      if (isActive) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (routeName === ROUTES.TALK) showPersonalityBadge();
    else hidePersonalityBadge();
  }
  router.start();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}