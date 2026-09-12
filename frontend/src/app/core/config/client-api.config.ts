import { InjectionToken } from '@angular/core';

/**
 * Base URL of the deployed Client API (`ClientApiUrl` output of `infra/stacks/client_api_stack.py`).
 * No build-time environment-file pipeline exists yet, so this is a plain DI token with a
 * placeholder default — override it via a provider in `app.config.ts` once a real API Gateway
 * URL is known post-deploy.
 */
export const CLIENT_API_BASE_URL = new InjectionToken<string>('CLIENT_API_BASE_URL', {
  providedIn: 'root',
  factory: () => '__CLIENT_API_BASE_URL__',
});
