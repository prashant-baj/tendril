/** Agent name (registry `name`, or "orchestrator") -> Material icon, for the "How this was
 * decided" trace timeline (PA-05). Kept in the frontend, not the backend, so the orchestrator
 * doesn't need to know about UI concerns — mirrors `plant-health.util.ts`'s pattern. */
export function specialistIcon(agent: string): string {
  switch (agent) {
    case 'vision':
      return 'photo_camera';
    case 'agronomy':
      return 'eco';
    case 'irrigation':
      return 'water_drop';
    case 'pest_disease':
      return 'pest_control';
    case 'pruning':
      return 'content_cut';
    case 'orchestrator':
      return 'hub';
    default:
      return 'smart_toy';
  }
}

/** Registry `name` (snake_case, e.g. "pest_disease") -> a readable label ("Pest Disease"). */
export function specialistLabel(agent: string): string {
  if (agent === 'orchestrator') {
    return 'Orchestrator';
  }
  return agent
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
