# ADR-0003: Frontend framework (Angular) & Design System

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Project owner / lead engineer

## Context

Tendril must be a **complete, usable application — not a demo**. Users create and manage gardens and plants, **capture or upload photos and documents** (video in the future), submit goals, review and **approve plans (human-in-the-loop)**, track progress over weeks, and receive/act on reminders. This calls for:

- A structured, maintainable single-page app that can grow feature-by-feature.
- **Media capture** (camera + file upload) from the browser/device.
- A **coherent design system** for consistency, accessibility, and a polished experience — Design and Presentation are heavily weighted, and a real product needs a real UI language.
- Mobile-friendly delivery without immediately building native apps.

## Decision

- Build the frontend as an **Angular** single-page app, delivered as a **PWA** (installable, camera/file access, offline-friendly).
- Establish a **use-case-specific Design System** — design tokens (color, type, spacing, motion), theming (light/dark, an organic/garden aesthetic), and a component library layered on Angular Material / CDK — **authored with Cowork (Claude) assistance** and tuned to the gardening domain.
- Ship real-app capabilities from the start: media capture/upload, garden & plant management, goal submission, plan review/approval, task management (plant- and garden-level), timeline/history, a notifications inbox, localization, and accessibility.

## Options Considered

### Option A: Angular + PWA + design system (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium-High — opinionated but batteries-included |
| Ecosystem / tooling | Strong — router, forms, HttpClient, DI, RxJS, Material/CDK, PWA tooling |
| Media / PWA support | Good — service worker, camera & file APIs via PWA |
| Team familiarity | High (preferred stack) |
| Fit for a "real app" | High — structured, scales with features |

**Pros:** TypeScript-first and highly structured (good for a growing product and AI-assisted development); RxJS is a natural fit for **reactive, bidirectional** streams from the backend; first-class PWA support for camera/upload without app stores; Angular Material/CDK gives a solid base to build the design system on.
**Cons:** Heavier framework and larger bundles; steeper learning curve than lighter options.

### Option B: React (+ PWA)

**Pros:** Largest ecosystem; flexible; abundant component libraries.
**Cons:** More assembly/decisions for a structured app; not the team's preferred stack.

### Option C: Vue (+ PWA)

**Pros:** Lightweight, approachable.
**Cons:** Smaller enterprise ecosystem; not preferred.

### Option D: Native / cross-platform (Flutter, React Native)

**Pros:** Best-in-class camera/offline UX.
**Cons:** Separate stack and more effort; a PWA meets MVP media needs. Revisit for a native app later.

## Trade-off Analysis

The decision optimizes for a **maintainable, structured, real application** with strong reactive-data support and low-friction media capture — not for the smallest bundle. Angular's opinionated structure and RxJS make the long-running, event-driven UX (plan proposals and follow-ups arriving asynchronously — see ADR-0004) natural to model, and PWA delivery covers camera/upload and installability without native app overhead. A design-system-first approach front-loads some effort but pays back in consistency, accessibility, and demo polish, and pairs well with Cowork-assisted generation of tokens and components.

## Consequences

- **Easier:** Consistent, accessible UI; maintainable growth; reactive binding to real-time backend events; camera/upload via PWA; a single codebase for web + mobile web.
- **Harder:** Larger bundle / performance budgeting; up-front design-system investment; PWA camera UX is good but not fully native.
- **To revisit:** A native app and in-app **video** capture; whether Material is retained or replaced as the component base as the design system matures.

## Action Items

1. [ ] Scaffold the Angular workspace with PWA support (service worker, manifest).
2. [ ] Define the **design system** with Cowork assistance: tokens, theming (light/dark, garden aesthetic), and a base component set on Angular Material/CDK.
3. [ ] Implement **media capture/upload** (camera + gallery + document/PDF); direct-to-S3 via presigned URLs (see ADR-0004); reserve a path for video.
4. [ ] Build core flows: garden/plant management, goal submission, **plan review & approval (HITL)**, task lists (plant/garden), timeline/history, notifications inbox.
5. [ ] Wire reactive data to the backend's real-time channel (ADR-0004) using RxJS.
6. [ ] Establish accessibility (WCAG) and **localization/i18n** baselines for reach.
