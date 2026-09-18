import { describe, expect, it } from 'vitest';
import { resolveHookConfig, resolveProjectHookConfig } from '../hooks/fast-jev.js';

function host(policy?: unknown, env: Record<string, string> = {}) {
  return {
    fs: { exists: async () => policy !== undefined, read: async () => JSON.stringify(policy) },
    env: { get: async (name: string) => env[name] },
    settings: { read: async () => ({ env }) },
  };
}

describe('project-scoped compaction policy', () => {
  it('is inert without a project policy', async () => {
    expect(await resolveProjectHookConfig(host(), resolveHookConfig({}))).toBeNull();
  });
  it('requires explicit approval of the broader data scope', async () => {
    await expect(resolveProjectHookConfig(host({
      version:1, enabled:true, provider:'typesafe', approved_data_scope:'metadata_only'
    }), resolveHookConfig({}))).rejects.toThrow(/data scope/);
  });
  it('reads project thresholds and a custom key environment variable', async () => {
    const cfg=await resolveProjectHookConfig(host({
      version:1, enabled:true, provider:'typesafe',
      approved_data_scope:'conversation_text_and_tool_inputs',
      api_key_env:'CUSTOM_JEV_KEY', keep_threshold:0.61,
      compact_at_percent:72, preserve_recent_messages:8
    }, {CUSTOM_JEV_KEY:'fixture-secret'}), resolveHookConfig({}));
    expect(cfg?.apiKey).toBe('fixture-secret');
    expect(cfg?.keepThreshold).toBe(0.61);
    expect(cfg?.compactAtPercent).toBe(72);
    expect(cfg?.preserveRecentMessages).toBe(8);
  });
  it('is inert when the policy is disabled', async () => {
    expect(await resolveProjectHookConfig(host({
      version:1, enabled:false, provider:'typesafe',
      approved_data_scope:'conversation_text_and_tool_inputs'
    }), resolveHookConfig({}))).toBeNull();
  });
});
