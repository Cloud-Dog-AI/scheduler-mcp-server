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

window.__RUNTIME_CONFIG__ = {
  ENV: "dev",
  API_BASE_URL: `${window.location.origin}/api`,
  MCP_BASE_URL: `${window.location.origin}/mcp`,
  A2A_BASE_URL: `${window.location.origin}/a2a`,
  AUTH_MODE: "api_key",
  SESSION_TIMEOUT_MINUTES: 30,
  APP_VERSION: "",
  PRODUCT_NAME: "Cloud-Dog File MCP",
  PRODUCT_DESCRIPTION: "Language-neutral filesystem and document-manipulation tools for automation and agent workflows; exposes tools over an MCP/JSON-RPC-style boundary.",
  AUDIT_LOG_PATH: "working/test-env-st/audit.log.jsonl",
  DEFAULT_BROWSE_PATH: "src",
  PROFILE_STORE_PATH: "working/ui-file-mcp/storage-profiles.json",
  ...(window.__RUNTIME_CONFIG__ || {})
};
