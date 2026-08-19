# frontend/

Angular PWA (see [ADR-0003](../docs/architecture/ADRs/0003-frontend-angular-and-design-system.md)) with a Cowork-built design system and media capture/upload.

## Scaffold (run once, on your machine)

```bash
# From repo root; requires Node 22 + Angular CLI (npm i -g @angular/cli)
ng new tendril-web --directory frontend --routing --style scss --ssr false
cd frontend
ng add @angular/pwa            # installable PWA + service worker
ng add @angular/material       # base for the design system
```

Then wire the real-time channel (WebSocket, ADR-0004) via RxJS and implement the design tokens/components. Feature stories will cover the app screens.
