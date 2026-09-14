# frontend/

Angular PWA (see [ADR-0003](../docs/architecture/ADRs/0003-frontend-angular-and-design-system.md))
implementing the "Tendril App" design: Home, Capture/Diagnose, Goal detail, Tasks, Garden, and
Activity, behind a responsive shell (desktop nav rail / mobile bottom nav at the 900px
breakpoint).

Data is currently **mocked in-memory** — there's no backend yet (ADR-0004). Every data service
(`core/services/*.ts`) is defined against an abstract-class DI token (`GardenApi`, `GoalApi`,
`TaskApi`, `ActivityApi`) with a `Mock*` implementation bound in `app.config.ts`; swapping in a
real `Http*`/WebSocket implementation later shouldn't require touching any component.

## Structure

- `core/models/` — TypeScript types mirroring the domain ERD (`docs/architecture/architecture.md` §2).
- `core/services/` — the mock data layer described above, plus `LayoutService` (the
  wide/narrow breakpoint) and `CaptureService` (the capture screen's idle → analyzing → found
  phase machine).
- `shell/` — `AppShellComponent` (root layout) + `NavRailComponent` / `BottomNavComponent` /
  `TopBarComponent`.
- `shared/components/` — presentational building blocks used across pages (`icon`, `chip`,
  `task-row`, `goal-card`, `plant-card`/`plant-row`, `trace-entry`, `finding-card`,
  `plan-task-card`, `follow-up-row`, `activity-item`).
- `features/` — one folder per routed screen (`home`, `capture`, `goal-detail`, `tasks`,
  `garden`, `activity`).
- `styles/_tokens.scss` — design tokens (colors, radii, shadows, fonts) as CSS custom
  properties, lifted 1:1 from the design mockup. Light theme only for now (dark theme is a
  roadmap item per ADR-0003).

## Requirements

Node 22. The Angular CLI is pinned to v19 (`npx @angular/cli@19 ...` / `npx ng ...`) because the
installed Node (22.18.0) doesn't yet meet Angular 20+'s `^22.12.0` minimum — re-pin to a newer
major once Node is upgraded.

## Commands

```bash
npm install
npx ng serve              # dev server at http://localhost:4200
npx ng build               # production build -> dist/tendril-web
npx ng test --no-watch     # unit tests (Karma + Jasmine)
```

**Running tests:** Karma needs a real Chrome/Chromium binary. Set `CHROME_BIN` if one isn't on
your PATH already (`karma.conf.js` uses a `--no-sandbox` launcher, needed in some containers/CI):

```bash
CHROME_BIN=/path/to/chrome npx ng test --no-watch
```

## Known placeholders

- **PWA icons** (`public/icons/*.png`) are a generated placeholder (green circle + a leaf glyph),
  not real brand assets — swap them when the design system gets one.
- **Camera capture** is a static visual placeholder, matching the mockup exactly. Real
  `getUserMedia`/file-upload wiring to S3 (ADR-0003 action item 3, ADR-0004's presigned-URL
  flow) is separate, backend-dependent work.
- Only the tomato goal (`g1`) has full detail data in `MockGoalApi` — every goal card links
  there regardless of which goal was clicked, matching the original design mockup (it does the
  same). Real per-goal detail is backend work, not a design-porting concern.
