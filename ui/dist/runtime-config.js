// Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// W28K-1409 F-1409-5 — Scheduler MCP runtime config. The WebUI authenticates with
// a username/password COOKIE session (AUTH_MODE: "cookie"), NOT an api-key.

window.__RUNTIME_CONFIG__ = {
  ENV: "dev",
  API_BASE_URL: `${window.location.origin}/api`,
  MCP_BASE_URL: `${window.location.origin}/mcp`,
  A2A_BASE_URL: `${window.location.origin}/a2a`,
  AUTH_MODE: "cookie",
  SESSION_TIMEOUT_MINUTES: 30,
  APP_VERSION: "",
  PRODUCT_NAME: "Cloud-Dog Scheduler MCP",
  PRODUCT_DESCRIPTION: "Scheduling control plane on AJOBS — schedules, chains, runs, jobs, registry and audit over API / MCP / A2A boundaries.",
  ...(window.__RUNTIME_CONFIG__ || {})
};
